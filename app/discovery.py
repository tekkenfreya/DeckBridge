"""Network discovery engine for DeckBridge.

Tries mDNS (``steamdeck.local``) first, then falls back to a parallel
TCP port-22 subnet scan.  All I/O runs on daemon threads; results are
surfaced via callbacks that callers should dispatch to the UI with
``widget.after(0, ...)``.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_MDNS_HOSTNAME = "steamdeck.local"
_MDNS_TIMEOUT = 2.0  # seconds
_SCAN_TIMEOUT = 1.0  # per-host TCP connect timeout
_SCAN_PORT = 22
_MAX_WORKERS = 50
_SCAN_HOST_MIN = 1
_SCAN_HOST_MAX = 254


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class DiscoveredDevice:
    """Represents a Steam Deck found on the network."""

    hostname: str
    ip: str
    response_ms: float
    via: str  # "mdns" | "scan"


# ---------------------------------------------------------------------------
# DiscoveryEngine
# ---------------------------------------------------------------------------


class DiscoveryEngine:
    """Discovers Steam Deck devices on the local network.

    Usage::

        engine = DiscoveryEngine(
            on_device_found=lambda d: root.after(0, handle_device, d),
            on_scan_complete=lambda n: root.after(0, handle_done, n),
            on_error=lambda m: root.after(0, handle_error, m),
        )
        engine.start()
        # ...
        engine.cancel()

    Callbacks are invoked from a background thread — dispatch to the main
    thread using ``widget.after(0, ...)``.
    """

    def __init__(
        self,
        on_device_found: Callable[[DiscoveredDevice], None] | None = None,
        on_scan_complete: Callable[[int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Initialise the engine with optional result callbacks."""
        self.on_device_found = on_device_found
        self.on_scan_complete = on_scan_complete
        self.on_error = on_error

        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._found_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin discovery in a daemon thread."""
        if self._worker_thread and self._worker_thread.is_alive():
            logger.warning("Discovery already running")
            return
        self._stop_event.clear()
        self._found_count = 0
        self._worker_thread = threading.Thread(
            target=self._run,
            name="discovery-worker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("Discovery started")

    def cancel(self) -> None:
        """Signal the engine to stop scanning and shut down the executor."""
        logger.info("Discovery cancel requested")
        self._stop_event.set()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
        if self._worker_thread:
            self._worker_thread.join(timeout=3)
        logger.info("Discovery cancelled")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main discovery logic: mDNS first, subnet scan as fallback."""
        try:
            device = self._try_mdns()
            if device:
                self._emit_device(device)
                # mDNS succeeded — skip subnet scan
                self._emit_complete()
                return

            if self._stop_event.is_set():
                return

            # mDNS failed — fall back to subnet scan
            subnet = self._detect_subnet()
            if not subnet:
                self._emit_error("Could not detect local subnet")
                return

            if self._stop_event.is_set():
                return

            logger.info("Starting subnet scan on %s.0/24", subnet)
            self._scan_subnet(subnet)
            self._emit_complete()
        except Exception as exc:
            logger.exception("Unhandled error in discovery")
            self._emit_error(str(exc))

    def _try_mdns(self) -> DiscoveredDevice | None:
        """Resolve ``steamdeck.local`` via mDNS with a 2-second timeout.

        Returns a :class:`DiscoveredDevice` on success, ``None`` on failure.
        """
        logger.debug("Trying mDNS for %s", _MDNS_HOSTNAME)
        try:
            start = time.monotonic()
            ip = socket.getaddrinfo(
                _MDNS_HOSTNAME,
                None,
                proto=socket.IPPROTO_TCP,
            )[0][4][0]
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.info("mDNS resolved %s → %s in %.1f ms", _MDNS_HOSTNAME, ip, elapsed_ms)
            return DiscoveredDevice(
                hostname=_MDNS_HOSTNAME,
                ip=ip,
                response_ms=round(elapsed_ms, 1),
                via="mdns",
            )
        except (socket.gaierror, OSError) as exc:
            logger.debug("mDNS lookup failed: %s", exc)
            return None

    def _detect_subnet(self) -> str | None:
        """Detect the local subnet base (e.g. "192.168.1") using a UDP trick.

        Connects a UDP socket to 8.8.8.8:80 to determine the outgoing
        interface IP, then strips the last octet.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip: str = s.getsockname()[0]
            parts = local_ip.rsplit(".", 1)
            if len(parts) != 2:
                return None
            subnet = parts[0]
            logger.debug("Detected subnet: %s (from local IP %s)", subnet, local_ip)
            return subnet
        except OSError as exc:
            logger.warning("Subnet detection failed: %s", exc)
            return None

    def _scan_subnet(self, base: str) -> None:
        """Probe all 254 hosts on the *base*.x subnet in parallel.

        Uses a thread pool with up to 50 workers.  Stops early if the
        stop event is set.
        """
        ips = [f"{base}.{i}" for i in range(_SCAN_HOST_MIN, _SCAN_HOST_MAX + 1)]

        self._executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="scan")
        futures = {self._executor.submit(self._probe_host, ip): ip for ip in ips}

        try:
            for future in as_completed(futures):
                if self._stop_event.is_set():
                    break
                result = future.result()
                if result:
                    self._emit_device(result)
        finally:
            self._executor.shutdown(wait=False)
            self._executor = None

    def _probe_host(self, ip: str) -> DiscoveredDevice | None:
        """TCP-probe *ip*:22; return a :class:`DiscoveredDevice` if port is open.

        Also attempts a reverse PTR lookup to obtain a hostname.
        """
        if self._stop_event.is_set():
            return None
        try:
            start = time.monotonic()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(_SCAN_TIMEOUT)
                s.connect((ip, _SCAN_PORT))
            elapsed_ms = (time.monotonic() - start) * 1000

            # Attempt reverse PTR lookup
            try:
                hostname = socket.gethostbyaddr(ip)[0]
            except (socket.herror, OSError):
                hostname = ip

            logger.debug("Found SSH host: %s (%s) in %.1f ms", hostname, ip, elapsed_ms)
            return DiscoveredDevice(
                hostname=hostname,
                ip=ip,
                response_ms=round(elapsed_ms, 1),
                via="scan",
            )
        except (socket.timeout, ConnectionRefusedError, OSError):
            return None

    # ------------------------------------------------------------------
    # Callback helpers
    # ------------------------------------------------------------------

    def _emit_device(self, device: DiscoveredDevice) -> None:
        """Invoke the on_device_found callback."""
        self._found_count += 1
        if self.on_device_found:
            try:
                self.on_device_found(device)
            except Exception:
                logger.exception("Exception in on_device_found callback")

    def _emit_complete(self) -> None:
        """Invoke the on_scan_complete callback with the total found count."""
        if self.on_scan_complete:
            try:
                self.on_scan_complete(self._found_count)
            except Exception:
                logger.exception("Exception in on_scan_complete callback")

    def _emit_error(self, message: str) -> None:
        """Invoke the on_error callback."""
        if self.on_error:
            try:
                self.on_error(message)
            except Exception:
                logger.exception("Exception in on_error callback")
