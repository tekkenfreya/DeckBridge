"""Tests for app/discovery.py — DiscoveryEngine."""

from __future__ import annotations

import socket
import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

from app.discovery import DiscoveredDevice, DiscoveryEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(**kwargs) -> DiscoveryEngine:
    """Create a DiscoveryEngine with default no-op callbacks unless overridden."""
    defaults = {
        "on_device_found": MagicMock(),
        "on_scan_complete": MagicMock(),
        "on_error": MagicMock(),
    }
    defaults.update(kwargs)
    return DiscoveryEngine(**defaults)


# ---------------------------------------------------------------------------
# mDNS tests
# ---------------------------------------------------------------------------


class TestMDNS:
    def test_mdns_success_returns_device(self) -> None:
        """_try_mdns returns a DiscoveredDevice when hostname resolves."""
        engine = _make_engine()
        with patch("socket.getaddrinfo", return_value=[("", "", "", "", ("192.168.1.50", 0))]):
            device = engine._try_mdns()
        assert device is not None
        assert device.ip == "192.168.1.50"
        assert device.via == "mdns"
        assert device.hostname == "steamdeck.local"

    def test_mdns_failure_returns_none(self) -> None:
        """_try_mdns returns None when the hostname cannot be resolved."""
        engine = _make_engine()
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("not found")):
            device = engine._try_mdns()
        assert device is None

    def test_mdns_success_skips_subnet_scan(self) -> None:
        """When mDNS succeeds, _scan_subnet is never called."""
        found_cb = MagicMock()
        complete_cb = MagicMock()
        engine = _make_engine(on_device_found=found_cb, on_scan_complete=complete_cb)

        with patch("socket.getaddrinfo", return_value=[("", "", "", "", ("192.168.1.50", 0))]):
            with patch.object(engine, "_scan_subnet") as mock_scan:
                engine._run()

        mock_scan.assert_not_called()
        found_cb.assert_called_once()
        complete_cb.assert_called_once_with(1)

    def test_mdns_failure_triggers_subnet_scan(self) -> None:
        """When mDNS fails, _scan_subnet is called."""
        engine = _make_engine()

        with patch("socket.getaddrinfo", side_effect=socket.gaierror("no mdns")):
            with patch.object(engine, "_detect_subnet", return_value="192.168.1"):
                with patch.object(engine, "_scan_subnet") as mock_scan:
                    engine._run()

        mock_scan.assert_called_once_with("192.168.1")


# ---------------------------------------------------------------------------
# Subnet detection
# ---------------------------------------------------------------------------


class TestSubnetDetection:
    def test_strips_last_octet(self) -> None:
        """_detect_subnet returns the base without the last octet."""
        engine = _make_engine()
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.getsockname.return_value = ("192.168.42.17", 0)

        with patch("socket.socket", return_value=mock_sock):
            subnet = engine._detect_subnet()

        assert subnet == "192.168.42"

    def test_returns_none_on_os_error(self) -> None:
        """_detect_subnet returns None when the socket call fails."""
        engine = _make_engine()
        with patch("socket.socket", side_effect=OSError("no network")):
            subnet = engine._detect_subnet()
        assert subnet is None


# ---------------------------------------------------------------------------
# Host probing
# ---------------------------------------------------------------------------


class TestProbeHost:
    def test_probe_success_returns_device(self) -> None:
        """_probe_host returns a DiscoveredDevice when port 22 is open."""
        engine = _make_engine()
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.connect = MagicMock()

        with patch("socket.socket", return_value=mock_sock):
            with patch("socket.gethostbyaddr", return_value=("steamdeck", [], ["10.0.0.5"])):
                device = engine._probe_host("10.0.0.5")

        assert device is not None
        assert device.ip == "10.0.0.5"
        assert device.hostname == "steamdeck"
        assert device.via == "scan"

    def test_probe_timeout_returns_none(self) -> None:
        """_probe_host returns None on connection timeout."""
        engine = _make_engine()
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.connect.side_effect = socket.timeout("timed out")

        with patch("socket.socket", return_value=mock_sock):
            device = engine._probe_host("10.0.0.99")

        assert device is None

    def test_probe_refused_returns_none(self) -> None:
        """_probe_host returns None when the connection is refused."""
        engine = _make_engine()
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.connect.side_effect = ConnectionRefusedError()

        with patch("socket.socket", return_value=mock_sock):
            device = engine._probe_host("10.0.0.200")

        assert device is None

    def test_probe_stop_event_returns_none(self) -> None:
        """_probe_host returns None immediately if the stop event is set."""
        engine = _make_engine()
        engine._stop_event.set()
        device = engine._probe_host("10.0.0.1")
        assert device is None


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_stops_scan(self) -> None:
        """Cancelling mid-scan stops the engine cleanly."""
        probed = []
        barrier = threading.Event()

        def slow_probe(ip: str) -> DiscoveredDevice | None:
            probed.append(ip)
            barrier.wait(timeout=0.5)
            return None

        engine = _make_engine()
        with patch.object(engine, "_probe_host", side_effect=slow_probe):
            with patch.object(engine, "_try_mdns", return_value=None):
                with patch.object(engine, "_detect_subnet", return_value="10.0.0"):
                    engine.start()
                    time.sleep(0.05)  # Let a few probes start
                    engine.cancel()
                    barrier.set()

        # Engine should have stopped — either fully or early
        assert engine._stop_event.is_set()
