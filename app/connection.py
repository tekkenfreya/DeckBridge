"""SSH/SFTP connection lifecycle management for DeckBridge.

Implements a state machine with keepalive, exponential-backoff reconnect,
and full error classification.  All methods that touch the network are safe
to call from background threads.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

import keyring
import paramiko
from paramiko import SFTPAttributes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

StateChangeCallback = Callable[["ConnectionState", Optional[str]], None]

_KEYRING_SERVICE = "DeckBridge"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class UnknownHostError(Exception):
    """Raised when the remote host key is not in known_hosts.

    Carries the fingerprint and key so the caller can prompt the user and
    optionally save it to known_hosts via :func:`accept_host_key`.
    """

    def __init__(
        self,
        message: str,
        hostname: str = "",
        key_type: str = "",
        fingerprint: str = "",
        key: paramiko.PKey | None = None,
    ) -> None:
        """Initialise with optional host-key metadata."""
        super().__init__(message)
        self.hostname = hostname
        self.key_type = key_type
        self.fingerprint = fingerprint
        self.key = key


class ConnectionError(Exception):  # noqa: A001  (shadows built-in intentionally)
    """Raised when an SFTP/SSH operation is attempted on a non-connected client."""


# ---------------------------------------------------------------------------
# Host-key policy
# ---------------------------------------------------------------------------


class _CapturingPolicy(paramiko.MissingHostKeyPolicy):
    """Raises UnknownHostError with fingerprint info instead of silently rejecting."""

    def missing_host_key(
        self,
        client: paramiko.SSHClient,
        hostname: str,
        key: paramiko.PKey,
    ) -> None:
        """Capture fingerprint and raise :exc:`UnknownHostError`."""
        raw = key.get_fingerprint()
        fingerprint = ":".join(f"{b:02x}" for b in raw)
        raise UnknownHostError(
            f"Host '{hostname}' is not in known_hosts.\n"
            f"Key type: {key.get_name()}\n"
            f"Fingerprint (MD5): {fingerprint}",
            hostname=hostname,
            key_type=key.get_name(),
            fingerprint=fingerprint,
            key=key,
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _close_client_safely(client: paramiko.SSHClient) -> None:
    """Close *client* without raising — suppresses WinError 10038 on Windows."""
    try:
        client.close()
    except Exception:
        pass  # Suppress WSAENOTSOCK (WinError 10038) and similar cleanup noise


def accept_host_key(hostname: str, key: paramiko.PKey) -> None:
    """Append *key* for *hostname* to ``~/.ssh/known_hosts`` and save.

    Creates the file and ``.ssh/`` directory if they do not exist.
    Safe to call from any thread (no Tkinter interaction).
    """
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    known_hosts_path = ssh_dir / "known_hosts"

    host_keys = paramiko.HostKeys(str(known_hosts_path)) if known_hosts_path.exists() else paramiko.HostKeys()
    host_keys.add(hostname, key.get_name(), key)
    host_keys.save(str(known_hosts_path))
    logger.info("Saved host key for %s to known_hosts", hostname)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class ConnectionState(Enum):
    """States for the SSH connection lifecycle."""

    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    ERROR = auto()


# ---------------------------------------------------------------------------
# SSHConnection
# ---------------------------------------------------------------------------

_RECONNECT_BASE_DELAY = 2  # seconds
_RECONNECT_MAX_RETRIES = 3
_KEEPALIVE_INTERVAL = 30  # seconds
_KEEPALIVE_CHECK_INTERVAL = 5  # seconds between transport liveness checks


class SSHConnection:
    """Manages a single SSH/SFTP connection to a remote Steam Deck.

    Thread-safety:
    - ``_lock`` protects all state transitions.
    - Network operations run in daemon threads; UI callbacks are dispatched
      via ``widget.after(0, ...)`` by the caller.
    """

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "deck",
        auth_type: str = "password",
        key_path: str | None = None,
        timeout: float = 15.0,
        on_state_change: StateChangeCallback | None = None,
    ) -> None:
        """Initialise connection parameters (does NOT connect yet).

        Args:
            host: Hostname or IP of the Steam Deck.
            port: SSH port (default 22).
            username: SSH username (default "deck").
            auth_type: "password" or "key".
            key_path: Path to private key file (used when auth_type="key").
            timeout: Connection timeout in seconds.
            on_state_change: Callback invoked on every state transition.
                Called with ``(new_state, optional_message)``.
        """
        self.host = host
        self.port = port
        self.username = username
        self.auth_type = auth_type
        self.key_path = key_path
        self.timeout = timeout
        self._on_state_change = on_state_change

        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._state = ConnectionState.DISCONNECTED
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._keepalive_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        """Current connection state (thread-safe read)."""
        with self._lock:
            return self._state

    def _set_state(self, new_state: ConnectionState, message: str | None = None) -> None:
        """Update state and fire the state-change callback (must hold lock)."""
        self._state = new_state
        logger.debug(
            "Connection state → %s%s",
            new_state.name,
            f" ({message})" if message else "",
        )
        if self._on_state_change:
            try:
                self._on_state_change(new_state, message)
            except Exception:
                logger.exception("Exception in on_state_change callback")

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish SSH and SFTP connections.

        Raises:
            UnknownHostError: Host key is not in known_hosts (carries fingerprint).
            paramiko.AuthenticationException: Wrong credentials.
            socket.timeout: Connection timed out.
            OSError: Network-level failure.
        """
        with self._lock:
            if self._state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
                logger.debug("connect() called but already %s", self._state.name)
                return
            self._set_state(ConnectionState.CONNECTING)

        try:
            self._do_connect()
        except Exception as exc:
            with self._lock:
                self._set_state(ConnectionState.ERROR, str(exc))
            raise

    def _do_connect(self) -> None:
        """Internal connection logic — called without holding the lock."""
        logger.info("Connecting to %s@%s:%d", self.username, self.host, self.port)

        client = paramiko.SSHClient()
        known_hosts_path = Path.home() / ".ssh" / "known_hosts"
        if known_hosts_path.exists():
            client.load_host_keys(str(known_hosts_path))

        # Use our capturing policy so unknown-host raises UnknownHostError with
        # fingerprint data rather than a bare SSHException (and avoids WinError 10038
        # from RejectPolicy's cleanup path on Windows).
        client.set_missing_host_key_policy(_CapturingPolicy())

        connect_kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.timeout,
            "allow_agent": True,
            "look_for_keys": self.auth_type == "key",
        }

        if self.auth_type == "password":
            password = keyring.get_password(_KEYRING_SERVICE, self._profile_key)
            if password:
                connect_kwargs["password"] = password
        elif self.auth_type == "key" and self.key_path:
            connect_kwargs["key_filename"] = self.key_path
        else:
            default_keys = [
                str(Path.home() / ".ssh" / "id_rsa"),
                str(Path.home() / ".ssh" / "id_ed25519"),
            ]
            connect_kwargs["key_filename"] = [k for k in default_keys if Path(k).exists()]

        try:
            client.connect(**connect_kwargs)
        except UnknownHostError:
            # Already well-formed — clean up and re-raise.
            _close_client_safely(client)
            raise
        except paramiko.BadHostKeyException as exc:
            _close_client_safely(client)
            raise UnknownHostError(
                f"Host key mismatch for {self.host} — check ~/.ssh/known_hosts",
                hostname=self.host,
            ) from exc
        except paramiko.AuthenticationException:
            _close_client_safely(client)
            raise
        except (paramiko.SSHException, socket.timeout, OSError) as exc:
            _close_client_safely(client)
            raise

        # Tune the transport for high-throughput file transfers:
        # - 64 MB window so large writes don't stall waiting for ACKs
        # - Disable automatic rekeying (would pause mid-transfer for large files)
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(_KEEPALIVE_INTERVAL)
            transport.default_window_size = 64 * 1024 * 1024  # 64 MB
            transport.packetizer.REKEY_BYTES = pow(2, 40)
            transport.packetizer.REKEY_TIME = pow(2, 40)

        sftp = client.open_sftp()

        with self._lock:
            self._client = client
            self._sftp = sftp
            self._stop_event.clear()
            self._set_state(ConnectionState.CONNECTED)

        self._start_keepalive_thread()
        logger.info("Connected to %s", self.host)

    def disconnect(self) -> None:
        """Gracefully close SFTP and SSH channels and stop keepalive."""
        with self._lock:
            self._stop_event.set()
            if self._sftp:
                try:
                    self._sftp.close()
                except Exception:
                    pass
                self._sftp = None
            if self._client:
                _close_client_safely(self._client)
                self._client = None
            self._set_state(ConnectionState.DISCONNECTED)

        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_thread.join(timeout=_KEEPALIVE_CHECK_INTERVAL + 1)
        self._keepalive_thread = None
        logger.info("Disconnected from %s", self.host)

    # ------------------------------------------------------------------
    # Keepalive
    # ------------------------------------------------------------------

    def _start_keepalive_thread(self) -> None:
        """Spawn a daemon thread to monitor transport liveness."""
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            name=f"keepalive-{self.host}",
            daemon=True,
        )
        self._keepalive_thread.start()

    def _keepalive_loop(self) -> None:
        """Periodically check transport health; trigger reconnect if dead."""
        logger.debug("Keepalive thread started for %s", self.host)
        while not self._stop_event.wait(timeout=_KEEPALIVE_CHECK_INTERVAL):
            with self._lock:
                if self._state != ConnectionState.CONNECTED:
                    break
                transport = self._client.get_transport() if self._client else None
                alive = transport is not None and transport.is_active()

            if not alive:
                logger.warning("Transport for %s lost — initiating reconnect", self.host)
                self._auto_reconnect()
                break
        logger.debug("Keepalive thread exiting for %s", self.host)

    # ------------------------------------------------------------------
    # Auto-reconnect
    # ------------------------------------------------------------------

    def _auto_reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff (max 3 retries)."""
        with self._lock:
            self._set_state(ConnectionState.RECONNECTING)

        delay = _RECONNECT_BASE_DELAY
        for attempt in range(1, _RECONNECT_MAX_RETRIES + 1):
            if self._stop_event.is_set():
                logger.info("Reconnect cancelled (stop event set)")
                return
            logger.info(
                "Reconnect attempt %d/%d for %s (wait %ds)",
                attempt,
                _RECONNECT_MAX_RETRIES,
                self.host,
                delay,
            )
            time.sleep(delay)
            try:
                self._do_connect()
                logger.info("Reconnected to %s on attempt %d", self.host, attempt)
                return
            except Exception as exc:
                logger.warning("Reconnect attempt %d failed: %s", attempt, exc)
                delay *= 2

        with self._lock:
            self._set_state(
                ConnectionState.ERROR,
                f"Could not reconnect to {self.host} after {_RECONNECT_MAX_RETRIES} attempts",
            )
        logger.error("All reconnect attempts exhausted for %s", self.host)

    # ------------------------------------------------------------------
    # SFTP operations
    # ------------------------------------------------------------------

    @property
    def _profile_key(self) -> str:
        """Keyring account key for this connection (user@host)."""
        return f"{self.username}@{self.host}"

    def get_sftp(self) -> paramiko.SFTPClient:
        """Return the active SFTP client.

        Raises:
            ConnectionError: If not currently connected.
        """
        with self._lock:
            if self._state != ConnectionState.CONNECTED or self._sftp is None:
                raise ConnectionError(
                    f"Not connected to {self.host} (state: {self._state.name})"
                )
            return self._sftp

    def get_transport(self) -> paramiko.Transport:
        """Return the underlying paramiko Transport for opening extra SFTP channels.

        Raises:
            ConnectionError: If not currently connected.
        """
        with self._lock:
            if self._client is None or self._state != ConnectionState.CONNECTED:
                raise ConnectionError(
                    f"Not connected to {self.host} (state: {self._state.name})"
                )
            transport = self._client.get_transport()
            if transport is None:
                raise ConnectionError("SSH transport unavailable")
            return transport

    def list_directory(self, remote_path: str) -> list[SFTPAttributes]:
        """List the contents of *remote_path* on the remote host.

        Args:
            remote_path: Absolute POSIX path on the Steam Deck.

        Returns:
            List of ``SFTPAttributes`` objects (one per entry).

        Raises:
            ConnectionError: If not connected.
            ValueError: If *remote_path* fails validation.
            paramiko.SSHException: On SFTP protocol errors.
            OSError: On permission denied or path-not-found.
        """
        from app.utils.path_helpers import validate_remote_path

        if not validate_remote_path(remote_path):
            raise ValueError(f"Invalid remote path: {remote_path!r}")

        sftp = self.get_sftp()
        try:
            entries: list[SFTPAttributes] = sftp.listdir_attr(remote_path)
            logger.debug("Listed %d entries in %s", len(entries), remote_path)
            return entries
        except OSError as exc:
            logger.warning("list_directory(%r) failed: %s", remote_path, exc)
            raise

    def execute_command(self, command: str) -> tuple[str, str, int]:
        """Execute *command* on the remote host and return (stdout, stderr, exit_code).

        Raises:
            ConnectionError: If not connected.
            paramiko.SSHException: On protocol errors.
        """
        with self._lock:
            if self._state != ConnectionState.CONNECTED or self._client is None:
                raise ConnectionError(
                    f"Not connected to {self.host} (state: {self._state.name})"
                )
            client = self._client

        try:
            _, stdout, stderr = client.exec_command(command, timeout=30)
            exit_code = stdout.channel.recv_exit_status()
            return (
                stdout.read().decode("utf-8", errors="replace"),
                stderr.read().decode("utf-8", errors="replace"),
                exit_code,
            )
        except paramiko.SSHException as exc:
            logger.error("exec_command(%r) failed: %s", command, exc)
            raise

    # ------------------------------------------------------------------
    # Credential helpers
    # ------------------------------------------------------------------

    def store_password(self, password: str) -> None:
        """Store *password* in the OS keyring for this connection."""
        keyring.set_password(_KEYRING_SERVICE, self._profile_key, password)
        logger.debug("Password stored in keyring for %s", self._profile_key)

    def delete_password(self) -> None:
        """Remove the stored password from the OS keyring."""
        try:
            keyring.delete_password(_KEYRING_SERVICE, self._profile_key)
        except keyring.errors.PasswordDeleteError:
            pass
        logger.debug("Password deleted from keyring for %s", self._profile_key)
