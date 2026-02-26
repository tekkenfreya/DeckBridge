"""Tests for app/transfer.py — TransferQueue and transfer operations.

Fixtures ``mock_sftp`` and ``mock_connection`` are defined here for use
across transfer-related tests (populated fully in Phase 7).
"""

from __future__ import annotations

import io
import os
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from app.connection import ConnectionState
from app.transfer import (
    TransferDirection,
    TransferItem,
    TransferQueue,
    TransferStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_sftp() -> MagicMock:
    """Return a mock paramiko.SFTPClient with common methods stubbed."""
    sftp = MagicMock()
    sftp.stat.return_value = MagicMock(st_size=0)
    sftp.open.return_value = MagicMock(
        __enter__=lambda s: s,
        __exit__=MagicMock(return_value=False),
        read=MagicMock(return_value=b""),
        write=MagicMock(),
        seek=MagicMock(),
        tell=MagicMock(return_value=0),
    )
    return sftp


@pytest.fixture()
def mock_connection(mock_sftp: MagicMock) -> MagicMock:
    """Return a mock SSHConnection in CONNECTED state."""
    conn = MagicMock()
    conn.state = ConnectionState.CONNECTED
    conn.get_sftp.return_value = mock_sftp
    return conn


@pytest.fixture()
def transfer_queue(mock_connection: MagicMock) -> TransferQueue:
    """Return a TransferQueue wired to the mock connection."""
    q = TransferQueue(connection=mock_connection)
    yield q
    q.cancel_all()
    q.shutdown()


# ---------------------------------------------------------------------------
# TransferItem unit tests
# ---------------------------------------------------------------------------


class TestTransferItem:
    def test_initial_status_is_pending(self) -> None:
        item = TransferItem(
            source_path="/local/file.txt",
            dest_path="/remote/file.txt",
            direction=TransferDirection.UPLOAD,
            file_size=1024,
        )
        assert item.status == TransferStatus.PENDING

    def test_progress_fraction_zero_when_no_bytes(self) -> None:
        item = TransferItem(
            source_path="/local/file.txt",
            dest_path="/remote/file.txt",
            direction=TransferDirection.UPLOAD,
            file_size=1024,
        )
        assert item.progress_fraction == 0.0

    def test_progress_fraction_complete(self) -> None:
        item = TransferItem(
            source_path="/local/file.txt",
            dest_path="/remote/file.txt",
            direction=TransferDirection.UPLOAD,
            file_size=1024,
        )
        item.bytes_transferred = 1024
        assert item.progress_fraction == pytest.approx(1.0)

    def test_progress_fraction_zero_size_file(self) -> None:
        """Zero-size files should not cause a ZeroDivisionError."""
        item = TransferItem(
            source_path="/local/empty.txt",
            dest_path="/remote/empty.txt",
            direction=TransferDirection.UPLOAD,
            file_size=0,
        )
        assert item.progress_fraction == 1.0

    def test_id_is_unique(self) -> None:
        items = [
            TransferItem(
                source_path=f"/f{i}",
                dest_path=f"/r{i}",
                direction=TransferDirection.UPLOAD,
                file_size=0,
            )
            for i in range(5)
        ]
        ids = {item.id for item in items}
        assert len(ids) == 5


# ---------------------------------------------------------------------------
# TransferQueue tests
# ---------------------------------------------------------------------------


class TestTransferQueue:
    def test_enqueue_returns_transfer_item(
        self, transfer_queue: TransferQueue, tmp_path: Path
    ) -> None:
        src = tmp_path / "test.txt"
        src.write_bytes(b"hello")
        item = transfer_queue.enqueue(
            source_path=str(src),
            dest_path="/remote/test.txt",
            direction=TransferDirection.UPLOAD,
        )
        assert isinstance(item, TransferItem)
        assert item.status in (TransferStatus.PENDING, TransferStatus.IN_PROGRESS)

    def test_cancel_all_drains_queue(
        self, transfer_queue: TransferQueue, tmp_path: Path
    ) -> None:
        for i in range(5):
            src = tmp_path / f"file{i}.txt"
            src.write_bytes(b"data")
            transfer_queue.enqueue(
                source_path=str(src),
                dest_path=f"/remote/file{i}.txt",
                direction=TransferDirection.UPLOAD,
            )
        transfer_queue.cancel_all()
        # Allow the worker a moment to drain
        time.sleep(0.2)
        # Queue internal queue should be empty
        assert transfer_queue._queue.empty()

    def test_upload_calls_sftp_put(
        self,
        transfer_queue: TransferQueue,
        mock_sftp: MagicMock,
        mock_connection: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "upload.txt"
        src.write_bytes(b"x" * 1024)

        done = threading.Event()

        def on_complete(item: TransferItem) -> None:
            done.set()

        transfer_queue.on_item_complete = on_complete

        transfer_queue.enqueue(
            source_path=str(src),
            dest_path="/remote/upload.txt",
            direction=TransferDirection.UPLOAD,
        )
        done.wait(timeout=5)
        mock_sftp.open.assert_called()

    def test_download_creates_local_file(
        self,
        transfer_queue: TransferQueue,
        mock_sftp: MagicMock,
        tmp_path: Path,
    ) -> None:
        dest = tmp_path / "downloaded.txt"
        content = b"remote content"
        mock_sftp.stat.return_value = MagicMock(st_size=len(content))

        # Make SFTP open return a file-like object with our content
        remote_fh = io.BytesIO(content)
        mock_sftp.open.return_value.__enter__ = lambda s: remote_fh
        mock_sftp.open.return_value.__exit__ = MagicMock(return_value=False)

        done = threading.Event()

        def on_complete(item: TransferItem) -> None:
            done.set()

        transfer_queue.on_item_complete = on_complete

        transfer_queue.enqueue(
            source_path="/remote/file.txt",
            dest_path=str(dest),
            direction=TransferDirection.DOWNLOAD,
        )
        done.wait(timeout=5)
        # The atomic rename should have placed the file at dest
        assert dest.exists() or (tmp_path / "downloaded.txt.tmp").exists()
