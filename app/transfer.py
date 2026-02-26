"""File transfer engine for DeckBridge.

Handles upload and download via SFTP with:
- Cancellable transfers using threading.Event
- Atomic writes (temp file + os.replace)
- Resume support for interrupted transfers
- Per-item progress callbacks
- Overwrite prompting via callback
"""

from __future__ import annotations

import logging
import math
import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

CHUNK_SIZE = 256 * 1024          # 256 KB per read/write call
NUM_STREAMS = 4                  # Parallel SFTP channels for large files
PARALLEL_THRESHOLD = 10 * 1024 * 1024  # Only parallelise files >= 10 MB

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TransferDirection(Enum):
    """Direction of a file transfer."""

    UPLOAD = auto()
    DOWNLOAD = auto()
    LOCAL_COPY = auto()   # local filesystem copy via shutil
    REMOTE_COPY = auto()  # SSH "cp -r" on the remote host


class TransferStatus(Enum):
    """Lifecycle state of a TransferItem."""

    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETE = auto()
    FAILED = auto()
    CANCELLED = auto()
    SKIPPED = auto()


# ---------------------------------------------------------------------------
# TransferItem
# ---------------------------------------------------------------------------


@dataclass
class TransferItem:
    """Represents one file in the transfer queue."""

    source_path: str
    dest_path: str
    direction: TransferDirection
    file_size: int
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    bytes_transferred: int = 0
    status: TransferStatus = TransferStatus.PENDING
    error: str | None = None
    start_time: float | None = None
    end_time: float | None = None

    @property
    def progress_fraction(self) -> float:
        """Fraction of the file transferred (0.0 – 1.0)."""
        if self.file_size <= 0:
            return 1.0
        return min(1.0, self.bytes_transferred / self.file_size)

    @property
    def speed_mbps(self) -> float:
        """Current transfer speed in MB/s, or 0 if not yet started."""
        if self.start_time is None or self.bytes_transferred == 0:
            return 0.0
        elapsed = (self.end_time or time.monotonic()) - self.start_time
        if elapsed <= 0:
            return 0.0
        return (self.bytes_transferred / elapsed) / (1024 * 1024)

    @property
    def eta_seconds(self) -> float | None:
        """Estimated seconds remaining, or None if speed is unknown."""
        speed = self.speed_mbps
        if speed <= 0 or self.file_size <= 0:
            return None
        remaining_bytes = self.file_size - self.bytes_transferred
        return (remaining_bytes / (speed * 1024 * 1024))


# ---------------------------------------------------------------------------
# TransferQueue
# ---------------------------------------------------------------------------


class TransferQueue:
    """Sequential SFTP transfer queue with cancel and progress callbacks.

    A single daemon worker thread processes items one at a time.
    """

    def __init__(
        self,
        connection,
        on_progress: Callable[[TransferItem], None] | None = None,
        on_item_complete: Callable[[TransferItem], None] | None = None,
        on_overwrite_prompt: Callable[[str], bool] | None = None,
    ) -> None:
        """Initialise the queue and start the worker thread.

        Args:
            connection: An ``SSHConnection`` instance.
            on_progress: Called after each chunk is transferred.
            on_item_complete: Called when an item finishes (any status).
            on_overwrite_prompt: Called when destination exists; return True
                to overwrite, False to skip.  If None, overwrites silently.
        """
        self._connection = connection
        self.on_progress = on_progress
        self.on_item_complete = on_item_complete
        self.on_overwrite_prompt = on_overwrite_prompt

        self._queue: queue.Queue[TransferItem] = queue.Queue()
        self._cancel_event = threading.Event()
        self._shutdown_event = threading.Event()
        self._current_item: TransferItem | None = None

        self._worker = threading.Thread(
            target=self._worker_loop,
            name="transfer-worker",
            daemon=True,
        )
        self._worker.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(
        self,
        source_path: str,
        dest_path: str,
        direction: TransferDirection,
    ) -> TransferItem:
        """Add a file to the transfer queue.

        Returns the created :class:`TransferItem` (status: PENDING).
        """
        try:
            if direction in (TransferDirection.UPLOAD, TransferDirection.LOCAL_COPY):
                file_size = os.path.getsize(source_path)
            elif direction == TransferDirection.DOWNLOAD:
                try:
                    sftp = self._connection.get_sftp()
                    file_size = sftp.stat(source_path).st_size or 0
                except Exception:
                    file_size = 0
            else:
                # REMOTE_COPY — size not easily known without an extra SFTP round-trip
                file_size = 0
        except OSError:
            file_size = 0

        item = TransferItem(
            source_path=source_path,
            dest_path=dest_path,
            direction=direction,
            file_size=file_size,
        )
        self._queue.put(item)
        logger.info(
            "Queued %s: %s → %s (%d bytes)",
            direction.name,
            source_path,
            dest_path,
            file_size,
        )
        return item

    def cancel_current(self) -> None:
        """Cancel the currently in-progress transfer."""
        self._cancel_event.set()

    def cancel_all(self) -> None:
        """Cancel the current transfer and drain all pending items."""
        self._cancel_event.set()
        # Drain the queue
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                item.status = TransferStatus.CANCELLED
                self._queue.task_done()
            except queue.Empty:
                break

    def shutdown(self) -> None:
        """Signal the worker to exit after the current item."""
        self._shutdown_event.set()
        self._cancel_event.set()
        # Unblock the worker
        sentinel = None
        self._queue.put(sentinel)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        """Process transfer items sequentially."""
        logger.debug("Transfer worker started")
        while not self._shutdown_event.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if item is None:
                break  # Shutdown sentinel

            self._cancel_event.clear()
            self._current_item = item
            self._process_item(item)
            self._current_item = None
            self._queue.task_done()

        logger.debug("Transfer worker exiting")

    def _process_item(self, item: TransferItem) -> None:
        """Route the item to the appropriate handler based on direction."""
        item.status = TransferStatus.IN_PROGRESS
        item.start_time = time.monotonic()
        try:
            if item.direction == TransferDirection.UPLOAD:
                self._upload(item)
            elif item.direction == TransferDirection.DOWNLOAD:
                self._download(item)
            elif item.direction == TransferDirection.LOCAL_COPY:
                self._local_copy(item)
            elif item.direction == TransferDirection.REMOTE_COPY:
                self._remote_copy(item)
        except Exception as exc:
            if item.status not in (TransferStatus.CANCELLED, TransferStatus.SKIPPED):
                item.status = TransferStatus.FAILED
                item.error = str(exc)
                logger.error("Transfer failed for %r: %s", item.source_path, exc)
        finally:
            item.end_time = time.monotonic()
            if self.on_item_complete:
                try:
                    self.on_item_complete(item)
                except Exception:
                    logger.exception("Exception in on_item_complete callback")

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def _upload(self, item: TransferItem) -> None:
        """Upload a local file or directory to the remote host via SFTP.

        Directories are uploaded recursively.  Single files use a ``.tmp``
        suffix for atomic writes and support resume.
        """
        from app.utils.path_helpers import validate_remote_path

        if not validate_remote_path(item.dest_path):
            raise ValueError(f"Invalid remote destination path: {item.dest_path!r}")

        if Path(item.source_path).is_dir():
            self._upload_directory(item, Path(item.source_path), item.dest_path)
            return

        if item.file_size >= PARALLEL_THRESHOLD:
            self._parallel_upload(item)
            return

        sftp = self._connection.get_sftp()
        tmp_remote = item.dest_path + ".tmp"

        # Check overwrite
        try:
            sftp.stat(item.dest_path)
            # File exists — prompt
            if self.on_overwrite_prompt:
                if not self.on_overwrite_prompt(item.dest_path):
                    item.status = TransferStatus.SKIPPED
                    return
        except OSError:
            pass  # Destination does not exist — proceed

        # Check for existing partial upload (resume)
        resume_offset = 0
        try:
            stat = sftp.stat(tmp_remote)
            resume_offset = stat.st_size or 0
            logger.info("Resuming upload from offset %d for %s", resume_offset, item.source_path)
        except OSError:
            pass

        with open(item.source_path, "rb") as local_fh:
            if resume_offset > 0:
                local_fh.seek(resume_offset)
                item.bytes_transferred = resume_offset

            flags = os.O_WRONLY | os.O_CREAT
            if resume_offset > 0:
                flags |= os.O_APPEND
            else:
                flags |= os.O_TRUNC

            with sftp.open(tmp_remote, "ab" if resume_offset > 0 else "wb") as remote_fh:
                # Pipelined mode lets up to 100 write requests be in-flight at
                # once instead of stop-and-wait per chunk.  close() still flushes
                # all pending ACKs before the rename, so the write is safe.
                remote_fh.set_pipelined(True)
                if resume_offset > 0:
                    remote_fh.seek(resume_offset)
                self._stream_with_progress(local_fh, remote_fh, item)

        if self._cancel_event.is_set():
            item.status = TransferStatus.CANCELLED
            return

        # Atomic rename
        try:
            sftp.rename(tmp_remote, item.dest_path)
        except OSError:
            # rename may fail on some SFTP servers if dest exists; remove then rename
            try:
                sftp.remove(item.dest_path)
                sftp.rename(tmp_remote, item.dest_path)
            except OSError as exc:
                raise OSError(f"Failed to finalise upload: {exc}") from exc

        item.status = TransferStatus.COMPLETE
        logger.info("Upload complete: %s → %s", item.source_path, item.dest_path)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _download(self, item: TransferItem) -> None:
        """Download a remote file or directory to the local filesystem via SFTP.

        Directories are downloaded recursively.  Single files use a ``.tmp``
        suffix for atomic writes and support resume.
        """
        import stat as _stat

        sftp = self._connection.get_sftp()

        # Detect if the remote source is a directory
        try:
            attr = sftp.stat(item.source_path)
            if isinstance(attr.st_mode, int) and _stat.S_ISDIR(attr.st_mode):
                self._download_directory(item, sftp, item.source_path, Path(item.dest_path))
                return
        except OSError:
            pass  # Treat as file

        if item.file_size >= PARALLEL_THRESHOLD:
            self._parallel_download(item)
            return

        tmp_local = item.dest_path + ".tmp"
        dest_path = Path(item.dest_path)

        # Check overwrite
        if dest_path.exists() and self.on_overwrite_prompt:
            if not self.on_overwrite_prompt(item.dest_path):
                item.status = TransferStatus.SKIPPED
                return

        # Resume support
        resume_offset = 0
        tmp_path = Path(tmp_local)
        if tmp_path.exists():
            resume_offset = tmp_path.stat().st_size
            logger.info("Resuming download from offset %d for %s", resume_offset, item.source_path)
            item.bytes_transferred = resume_offset

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        write_mode = "ab" if resume_offset > 0 else "wb"
        with open(tmp_local, write_mode) as local_fh:
            with sftp.open(item.source_path, "rb") as remote_fh:
                # Enable parallel read-ahead — single biggest throughput boost.
                # prefetch() spawns a background thread that pipelines multiple
                # SFTP read requests instead of waiting for each ACK in turn.
                if item.file_size > 0:
                    try:
                        remote_fh.prefetch(item.file_size)
                    except Exception:
                        pass
                if resume_offset > 0:
                    remote_fh.seek(resume_offset)
                self._stream_with_progress(remote_fh, local_fh, item)

        if self._cancel_event.is_set():
            item.status = TransferStatus.CANCELLED
            return

        # Atomic rename
        os.replace(tmp_local, item.dest_path)
        item.status = TransferStatus.COMPLETE
        logger.info("Download complete: %s → %s", item.source_path, item.dest_path)

    # ------------------------------------------------------------------
    # Parallel transfer (IDM-style multi-stream)
    # ------------------------------------------------------------------

    def _make_chunks(self, file_size: int) -> list[tuple[int, int]]:
        """Split *file_size* into (offset, length) pairs for NUM_STREAMS workers."""
        n = min(NUM_STREAMS, max(1, file_size // (4 * 1024 * 1024)))
        chunk = math.ceil(file_size / n)
        return [
            (i * chunk, min(chunk, file_size - i * chunk))
            for i in range(n)
            if i * chunk < file_size
        ]

    def _parallel_download(self, item: TransferItem) -> None:
        """Download a large file using NUM_STREAMS parallel SFTP channels.

        Each channel fetches a separate byte range; chunks are assembled
        locally into the final destination file.
        """
        import paramiko as _paramiko

        src = item.source_path
        dst = Path(item.dest_path)
        chunks = self._make_chunks(item.file_size)

        if dst.exists() and self.on_overwrite_prompt:
            if not self.on_overwrite_prompt(str(dst)):
                item.status = TransferStatus.SKIPPED
                return

        dst.parent.mkdir(parents=True, exist_ok=True)

        part_paths = [str(dst) + f".part{i}" for i in range(len(chunks))]
        errors: list[Exception] = []
        lock = threading.Lock()
        transport = self._connection.get_transport()

        def _fetch(offset: int, length: int, part: str) -> None:
            ch = _paramiko.SFTPClient.from_transport(transport)
            try:
                with ch.open(src, "rb") as rf:
                    rf.seek(offset)
                    rf.prefetch(length)
                    with open(part, "wb") as lf:
                        remaining = length
                        while remaining > 0:
                            if self._cancel_event.is_set():
                                return
                            data = rf.read(min(CHUNK_SIZE, remaining))
                            if not data:
                                break
                            lf.write(data)
                            remaining -= len(data)
                            with lock:
                                item.bytes_transferred += len(data)
                            if self.on_progress:
                                try:
                                    self.on_progress(item)
                                except Exception:
                                    pass
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                ch.close()

        threads = [
            threading.Thread(target=_fetch, args=(off, ln, part), daemon=True)
            for (off, ln), part in zip(chunks, part_paths)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        def _cleanup() -> None:
            for p in part_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass

        if self._cancel_event.is_set():
            _cleanup()
            item.status = TransferStatus.CANCELLED
            return

        if errors:
            _cleanup()
            raise errors[0]

        # Assemble parts in order into a single .tmp file, then atomic rename
        final_tmp = str(dst) + ".tmp"
        with open(final_tmp, "wb") as out:
            for part in part_paths:
                with open(part, "rb") as pf:
                    shutil.copyfileobj(pf, out)
        _cleanup()
        os.replace(final_tmp, str(dst))

        item.status = TransferStatus.COMPLETE
        logger.info(
            "Parallel download complete (%d streams): %s → %s",
            len(chunks), src, dst,
        )

    def _parallel_upload(self, item: TransferItem) -> None:
        """Upload a large file using NUM_STREAMS parallel SFTP channels.

        Each channel uploads a byte range to a remote part file; the Deck
        assembles them with a single ``cat`` command.
        """
        import paramiko as _paramiko
        from app.utils.path_helpers import validate_remote_path

        src = item.source_path
        dst = item.dest_path
        chunks = self._make_chunks(item.file_size)

        sftp = self._connection.get_sftp()

        # Overwrite check against final destination
        try:
            sftp.stat(dst)
            if self.on_overwrite_prompt and not self.on_overwrite_prompt(dst):
                item.status = TransferStatus.SKIPPED
                return
        except OSError:
            pass

        part_paths = [f"{dst}.part{i}" for i in range(len(chunks))]
        errors: list[Exception] = []
        lock = threading.Lock()
        transport = self._connection.get_transport()

        def _send(offset: int, length: int, remote_part: str) -> None:
            ch = _paramiko.SFTPClient.from_transport(transport)
            try:
                with open(src, "rb") as lf:
                    lf.seek(offset)
                    with ch.open(remote_part, "wb") as rf:
                        rf.set_pipelined(True)
                        remaining = length
                        while remaining > 0:
                            if self._cancel_event.is_set():
                                return
                            data = lf.read(min(CHUNK_SIZE, remaining))
                            if not data:
                                break
                            rf.write(data)
                            remaining -= len(data)
                            with lock:
                                item.bytes_transferred += len(data)
                            if self.on_progress:
                                try:
                                    self.on_progress(item)
                                except Exception:
                                    pass
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                ch.close()

        threads = [
            threading.Thread(target=_send, args=(off, ln, part), daemon=True)
            for (off, ln), part in zip(chunks, part_paths)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        def _cleanup_remote() -> None:
            for p in part_paths:
                p_esc = p.replace("'", "'\\''")
                try:
                    sftp.remove(p)
                except OSError:
                    pass

        if self._cancel_event.is_set():
            _cleanup_remote()
            item.status = TransferStatus.CANCELLED
            return

        if errors:
            _cleanup_remote()
            raise errors[0]

        # Assemble parts on the Deck with cat, then atomic rename
        tmp_remote = dst + ".tmp"
        parts_escaped = " ".join(
            "'" + p.replace("'", "'\\''") + "'" for p in part_paths
        )
        tmp_esc = tmp_remote.replace("'", "'\\''")
        cmd = f"cat {parts_escaped} > '{tmp_esc}' && rm -f {parts_escaped}"
        _, stderr, exit_code = self._connection.execute_command(cmd)
        if exit_code != 0:
            _cleanup_remote()
            raise OSError(f"Remote assembly failed: {stderr.strip()}")

        try:
            sftp.rename(tmp_remote, dst)
        except OSError:
            try:
                sftp.remove(dst)
                sftp.rename(tmp_remote, dst)
            except OSError as exc:
                raise OSError(f"Failed to finalise upload: {exc}") from exc

        item.status = TransferStatus.COMPLETE
        logger.info(
            "Parallel upload complete (%d streams): %s → %s",
            len(chunks), src, dst,
        )

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _sftp_makedirs(self, sftp, remote_path: str) -> None:
        """Create *remote_path* and any missing ancestor directories."""
        parts = [p for p in remote_path.split("/") if p]
        cumulative = ""
        for part in parts:
            cumulative = f"{cumulative}/{part}"
            try:
                sftp.mkdir(cumulative)
            except OSError:
                pass  # Already exists — carry on

    def _upload_directory(self, item: TransferItem, src_root: Path, dst_root: str) -> None:
        """Recursively upload a local directory tree to the remote host."""
        sftp = self._connection.get_sftp()

        # Pre-compute total bytes so progress is meaningful
        try:
            item.file_size = sum(
                f.stat().st_size for f in src_root.rglob("*") if f.is_file()
            )
        except OSError:
            item.file_size = 0

        # Create the root destination directory
        self._sftp_makedirs(sftp, dst_root)

        for local_path in sorted(src_root.rglob("*")):
            if self._cancel_event.is_set():
                item.status = TransferStatus.CANCELLED
                return

            rel = str(local_path.relative_to(src_root)).replace(os.sep, "/")
            remote_path = f"{dst_root.rstrip('/')}/{rel}"

            if local_path.is_dir():
                self._sftp_makedirs(sftp, remote_path)
                continue

            # Check overwrite
            try:
                sftp.stat(remote_path)
                if self.on_overwrite_prompt and not self.on_overwrite_prompt(remote_path):
                    continue
            except OSError:
                pass

            tmp_remote = remote_path + ".tmp"
            with open(str(local_path), "rb") as local_fh:
                with sftp.open(tmp_remote, "wb") as remote_fh:
                    remote_fh.set_pipelined(True)
                    self._stream_with_progress(local_fh, remote_fh, item)

            if self._cancel_event.is_set():
                item.status = TransferStatus.CANCELLED
                return

            try:
                sftp.rename(tmp_remote, remote_path)
            except OSError:
                try:
                    sftp.remove(remote_path)
                    sftp.rename(tmp_remote, remote_path)
                except OSError as exc:
                    raise OSError(f"Failed to finalise {local_path.name}: {exc}") from exc

        item.status = TransferStatus.COMPLETE
        logger.info("Directory upload complete: %s → %s", src_root, dst_root)

    def _download_directory(
        self, item: TransferItem, sftp, src_root: str, dst_root: Path
    ) -> None:
        """Recursively download a remote directory tree to the local filesystem."""
        import stat as _stat

        def _walk(remote_dir: str) -> list[tuple[str, bool, int]]:
            """Return (path, is_dir, size) for every entry under remote_dir."""
            results: list[tuple[str, bool, int]] = []
            try:
                for attr in sftp.listdir_attr(remote_dir):
                    full = f"{remote_dir.rstrip('/')}/{attr.filename}"
                    is_dir = bool(attr.st_mode and _stat.S_ISDIR(attr.st_mode))
                    results.append((full, is_dir, attr.st_size or 0))
                    if is_dir:
                        results.extend(_walk(full))
            except OSError as exc:
                logger.warning("Could not list %r: %s", remote_dir, exc)
            return results

        entries = _walk(src_root)
        item.file_size = sum(size for _, is_dir, size in entries if not is_dir)

        dst_root.mkdir(parents=True, exist_ok=True)

        for remote_path, is_dir, _ in entries:
            if self._cancel_event.is_set():
                item.status = TransferStatus.CANCELLED
                return

            rel = remote_path[len(src_root):].lstrip("/")
            local_path = dst_root / Path(rel)

            if is_dir:
                local_path.mkdir(parents=True, exist_ok=True)
                continue

            if local_path.exists() and self.on_overwrite_prompt:
                if not self.on_overwrite_prompt(str(local_path)):
                    continue

            local_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_local = str(local_path) + ".tmp"

            with open(tmp_local, "wb") as local_fh:
                with sftp.open(remote_path, "rb") as remote_fh:
                    try:
                        remote_fh.prefetch()
                    except Exception:
                        pass
                    self._stream_with_progress(remote_fh, local_fh, item)

            if self._cancel_event.is_set():
                item.status = TransferStatus.CANCELLED
                return

            os.replace(tmp_local, str(local_path))

        item.status = TransferStatus.COMPLETE
        logger.info("Directory download complete: %s → %s", src_root, dst_root)

    # ------------------------------------------------------------------
    # Local copy
    # ------------------------------------------------------------------

    def _local_copy(self, item: TransferItem) -> None:
        """Copy a local file or directory to another local path via shutil.

        Prompts for overwrite if the destination already exists.
        """
        import shutil
        from pathlib import Path

        dest = Path(item.dest_path)
        if dest.exists() and self.on_overwrite_prompt:
            if not self.on_overwrite_prompt(item.dest_path):
                item.status = TransferStatus.SKIPPED
                return

        dest.parent.mkdir(parents=True, exist_ok=True)

        src = Path(item.source_path)
        if src.is_dir():
            shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
        else:
            shutil.copy2(str(src), str(dest))

        item.bytes_transferred = item.file_size
        item.status = TransferStatus.COMPLETE
        logger.info("Local copy complete: %s → %s", item.source_path, item.dest_path)

    # ------------------------------------------------------------------
    # Remote copy
    # ------------------------------------------------------------------

    def _remote_copy(self, item: TransferItem) -> None:
        """Copy a file or directory on the remote host using 'cp -r'.

        Validates both paths and shells out via execute_command.
        """
        from app.utils.path_helpers import validate_remote_path

        if not validate_remote_path(item.source_path):
            raise ValueError(f"Invalid remote source path: {item.source_path!r}")
        if not validate_remote_path(item.dest_path):
            raise ValueError(f"Invalid remote destination path: {item.dest_path!r}")

        # Escape single quotes in paths to prevent shell injection
        src_escaped = item.source_path.replace("'", "'\\''")
        dst_escaped = item.dest_path.replace("'", "'\\''")
        cmd = f"cp -r '{src_escaped}' '{dst_escaped}'"

        stdout, stderr, exit_code = self._connection.execute_command(cmd)
        if exit_code != 0:
            raise OSError(stderr.strip() or f"cp exited with code {exit_code}")

        item.bytes_transferred = item.file_size
        item.status = TransferStatus.COMPLETE
        logger.info("Remote copy complete: %s → %s", item.source_path, item.dest_path)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def _stream_with_progress(self, src, dst, item: TransferItem) -> None:
        """Stream bytes from *src* to *dst* in chunks, updating *item*.

        Checks the cancel event after each chunk and stops early if set.
        """
        while True:
            if self._cancel_event.is_set():
                item.status = TransferStatus.CANCELLED
                return
            chunk = src.read(CHUNK_SIZE)
            if not chunk:
                break
            dst.write(chunk)
            item.bytes_transferred += len(chunk)
            if self.on_progress:
                try:
                    self.on_progress(item)
                except Exception:
                    logger.exception("Exception in on_progress callback")
