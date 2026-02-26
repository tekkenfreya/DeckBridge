"""First-time setup wizard for DeckBridge.

Walks the user through enabling SSH on their Steam Deck and testing
the connection before any file transfers are attempted.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from app.connection import SSHConnection
from app.ui.components import CopyableText, SpinnerLabel, Tooltip

logger = logging.getLogger(__name__)

_DARK_BG = "#1b2838"
_DARK_FG = "#c7d5e0"
_DARK_ACCENT = "#1a9fff"
_DARK_ENTRY = "#263448"
_DARK_BORDER = "#374e6a"
_DARK_SUCCESS = "#5ba85a"
_DARK_ERROR = "#e05c5c"


# ---------------------------------------------------------------------------
# Base step
# ---------------------------------------------------------------------------


class WizardStep(ttk.Frame):
    """Base class for all wizard steps.

    Subclasses must implement :meth:`build` to add their widgets and may
    override :meth:`on_enter` / :meth:`on_leave`.
    """

    title: str = "Step"

    def __init__(self, master: tk.Widget, **kwargs) -> None:
        """Initialise and build the step."""
        super().__init__(master, **kwargs)
        self.configure(padding=24)
        self.build()

    def build(self) -> None:
        """Populate the step's widgets.  Called once during ``__init__``."""

    def on_enter(self) -> None:
        """Called when the step becomes visible."""

    def on_leave(self) -> bool:
        """Called when the user wants to advance past this step.

        Returns:
            ``True`` to allow navigation; ``False`` to block (and show an error).
        """
        return True


# ---------------------------------------------------------------------------
# Step 1 — Welcome
# ---------------------------------------------------------------------------


class WelcomeStep(WizardStep):
    """Welcome screen with a brief overview of DeckBridge."""

    title = "Welcome"

    def build(self) -> None:
        """Build the welcome step layout."""
        ttk.Label(
            self,
            text="Welcome to DeckBridge",
            font=("TkDefaultFont", 20, "bold"),
            foreground=_DARK_ACCENT,
        ).pack(pady=(0, 12))

        ttk.Label(
            self,
            text=(
                "DeckBridge lets you transfer files between your PC and Steam Deck\n"
                "over Wi-Fi — no USB cable or technical knowledge required.\n\n"
                "This short wizard will help you enable SSH on your Steam Deck\n"
                "and connect to it for the first time."
            ),
            justify=tk.CENTER,
            font=("TkDefaultFont", 12),
        ).pack(pady=8)

        # ASCII art / logo placeholder
        ttk.Label(
            self,
            text=(
                "  ╔══════════════════════════╗\n"
                "  ║  PC  ◄──────────►  DECK  ║\n"
                "  ╚══════════════════════════╝"
            ),
            font=("Courier", 12),
            foreground=_DARK_ACCENT,
            justify=tk.CENTER,
        ).pack(pady=16)

        ttk.Label(
            self,
            text="Click Next to get started.",
            font=("TkDefaultFont", 11),
        ).pack()


# ---------------------------------------------------------------------------
# Step 2 — Enable SSH
# ---------------------------------------------------------------------------


class EnableSSHStep(WizardStep):
    """Instructs the user to enable SSH on their Steam Deck."""

    title = "Enable SSH"

    def build(self) -> None:
        """Build the SSH enable instructions."""
        ttk.Label(
            self,
            text="Step 1 — Enable SSH on your Steam Deck",
            font=("TkDefaultFont", 16, "bold"),
            foreground=_DARK_ACCENT,
        ).pack(anchor=tk.W, pady=(0, 12))

        instructions = [
            ("1. On your Steam Deck, press the Steam button and open Settings."),
            ("2. Go to System → Enable Developer Mode (toggle On)."),
            ("3. Open the Desktop Mode:\n   Steam button → Power → Switch to Desktop."),
            ("4. Open a terminal (Konsole) and run the commands below."),
            ("5. Set a password for the 'deck' user (you'll need this to connect):"),
        ]
        for text in instructions:
            ttk.Label(self, text=text, justify=tk.LEFT, wraplength=600).pack(
                anchor=tk.W, pady=2
            )

        ttk.Label(
            self,
            text="Set a password for the deck user:",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor=tk.W, pady=(8, 0))
        self._add_command_box("passwd")

        ttk.Label(
            self,
            text="Enable and start the SSH service:",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor=tk.W, pady=(8, 0))
        self._add_command_box("sudo systemctl enable --now sshd")

        ttk.Label(
            self,
            text=(
                "Note: You only need to do this once. SSH stays enabled after reboots."
            ),
            foreground="#a8b5c2",
        ).pack(anchor=tk.W, pady=(12, 0))

    def _add_command_box(self, command: str) -> None:
        """Add a read-only text box with a Copy button for *command*."""
        CopyableText(self, text=command, height=1).pack(
            fill=tk.X, pady=2, padx=0
        )


# ---------------------------------------------------------------------------
# Step 3 — Connection details
# ---------------------------------------------------------------------------


class ConnectionStep(WizardStep):
    """Collects hostname, username, and auth type from the user."""

    title = "Connection"

    def build(self) -> None:
        """Build the connection form."""
        ttk.Label(
            self,
            text="Step 2 — Enter your Steam Deck connection details",
            font=("TkDefaultFont", 16, "bold"),
            foreground=_DARK_ACCENT,
        ).pack(anchor=tk.W, pady=(0, 16))

        form = ttk.Frame(self)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)

        # Hostname / IP
        ttk.Label(form, text="Hostname / IP:").grid(row=0, column=0, sticky=tk.E, padx=(0, 8), pady=6)
        self.host_var = tk.StringVar(value="steamdeck.local")
        host_entry = ttk.Entry(form, textvariable=self.host_var, width=35)
        host_entry.grid(row=0, column=1, sticky=tk.EW, pady=6)
        Tooltip(host_entry, "Try 'steamdeck.local' or enter the IP shown in the Steam Deck network settings")

        # Username
        ttk.Label(form, text="Username:").grid(row=1, column=0, sticky=tk.E, padx=(0, 8), pady=6)
        self.user_var = tk.StringVar(value="deck")
        ttk.Entry(form, textvariable=self.user_var, width=35).grid(row=1, column=1, sticky=tk.EW, pady=6)

        # Auth type
        ttk.Label(form, text="Authentication:").grid(row=2, column=0, sticky=tk.E, padx=(0, 8), pady=6)
        self.auth_var = tk.StringVar(value="password")
        auth_frame = ttk.Frame(form)
        auth_frame.grid(row=2, column=1, sticky=tk.W, pady=6)
        ttk.Radiobutton(auth_frame, text="Password", variable=self.auth_var, value="password",
                        command=self._toggle_auth).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Radiobutton(auth_frame, text="SSH Key", variable=self.auth_var, value="key",
                        command=self._toggle_auth).pack(side=tk.LEFT)

        # Password field (shown for password auth)
        ttk.Label(form, text="Password:").grid(row=3, column=0, sticky=tk.E, padx=(0, 8), pady=6)
        self.password_var = tk.StringVar()
        self._password_entry = ttk.Entry(form, textvariable=self.password_var, show="•", width=35)
        self._password_entry.grid(row=3, column=1, sticky=tk.EW, pady=6)

        # Key path field (hidden for password auth)
        ttk.Label(form, text="Key file:").grid(row=4, column=0, sticky=tk.E, padx=(0, 8), pady=6)
        self.key_path_var = tk.StringVar()
        self._key_entry = ttk.Entry(form, textvariable=self.key_path_var, width=35)
        self._key_entry.grid(row=4, column=1, sticky=tk.EW, pady=6)
        self._key_entry.grid_remove()
        # Also hide the label when not needed
        form.grid_slaves(row=4, column=0)[0].grid_remove()

        self._error_label = ttk.Label(self, text="", foreground=_DARK_ERROR)
        self._error_label.pack(anchor=tk.W, pady=(8, 0))

    def _toggle_auth(self) -> None:
        """Show/hide the password or key path field based on auth type."""
        form = self._password_entry.master
        if self.auth_var.get() == "password":
            self._password_entry.grid()
            self._key_entry.grid_remove()
            # Show/hide row labels
            for widget in form.grid_slaves(row=3, column=0):
                widget.grid()
            for widget in form.grid_slaves(row=4, column=0):
                widget.grid_remove()
        else:
            self._password_entry.grid_remove()
            self._key_entry.grid()
            for widget in form.grid_slaves(row=3, column=0):
                widget.grid_remove()
            for widget in form.grid_slaves(row=4, column=0):
                widget.grid()

    def on_leave(self) -> bool:
        """Validate that host and username are non-empty."""
        if not self.host_var.get().strip():
            self._error_label.configure(text="Hostname / IP cannot be empty.")
            return False
        if not self.user_var.get().strip():
            self._error_label.configure(text="Username cannot be empty.")
            return False
        self._error_label.configure(text="")
        return True

    def get_connection_params(self) -> dict[str, str | None]:
        """Return the form values as a dict for constructing an SSHConnection."""
        return {
            "host": self.host_var.get().strip(),
            "username": self.user_var.get().strip(),
            "auth_type": self.auth_var.get(),
            "key_path": self.key_path_var.get().strip() or None,
            "password": self.password_var.get(),
        }


# ---------------------------------------------------------------------------
# Step 4 — Test Connection
# ---------------------------------------------------------------------------


class TestConnectionStep(WizardStep):
    """Runs a background thread to test the SSH connection."""

    title = "Test Connection"

    def __init__(self, master: tk.Widget, get_params_cb: Callable[[], dict], **kwargs) -> None:
        """Initialise with a callback to retrieve connection parameters."""
        self._get_params = get_params_cb
        self._connection = None
        super().__init__(master, **kwargs)

    def build(self) -> None:
        """Build the test connection UI."""
        ttk.Label(
            self,
            text="Step 3 — Test your connection",
            font=("TkDefaultFont", 16, "bold"),
            foreground=_DARK_ACCENT,
        ).pack(anchor=tk.W, pady=(0, 12))

        ttk.Label(
            self,
            text=(
                "Click the button below to verify that DeckBridge can reach your\n"
                "Steam Deck. Make sure the Deck is powered on and connected to\n"
                "the same Wi-Fi network as this computer."
            ),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 16))

        btn_row = ttk.Frame(self)
        btn_row.pack(anchor=tk.W)
        self._test_btn = ttk.Button(btn_row, text="Test Connection", command=self._run_test)
        self._test_btn.pack(side=tk.LEFT)
        Tooltip(self._test_btn, "Attempt to connect to your Steam Deck using the details you entered")

        self._spinner = SpinnerLabel(btn_row, text="", foreground=_DARK_FG, font=("TkDefaultFont", 14))
        self._spinner.pack(side=tk.LEFT, padx=(8, 0))

        self._result_label = ttk.Label(self, text="", font=("TkDefaultFont", 12))
        self._result_label.pack(anchor=tk.W, pady=(12, 0))

        self._detail_label = ttk.Label(self, text="", foreground="#a8b5c2", wraplength=580)
        self._detail_label.pack(anchor=tk.W, pady=(4, 0))

        self._success = False

    def on_enter(self) -> None:
        """Reset result state when the step becomes visible."""
        self._success = False
        self._result_label.configure(text="", foreground=_DARK_FG)
        self._detail_label.configure(text="")

    def _run_test(self) -> None:
        """Start the connection test in a background thread."""
        self._test_btn.configure(state=tk.DISABLED)
        self._result_label.configure(text="Connecting…", foreground=_DARK_FG)
        self._detail_label.configure(text="")
        self._spinner.start()

        params = self._get_params()
        t = threading.Thread(target=self._test_worker, args=(params,), daemon=True)
        t.start()

    def _test_worker(self, params: dict) -> None:
        """Worker thread: attempt SSH connect, report back via after()."""
        from app.connection import SSHConnection, UnknownHostError
        import paramiko
        import socket as _socket

        conn = SSHConnection(
            host=params["host"],
            port=22,
            username=params["username"],
            auth_type=params["auth_type"],
            key_path=params.get("key_path"),
        )
        if params.get("password"):
            conn.store_password(params["password"])

        try:
            conn.connect()
            # Keep the connection alive — it will be wired directly into the
            # main window so the user doesn't have to reconnect after setup.
            self.after(0, self._on_success, conn)
        except UnknownHostError as exc:
            # Show fingerprint dialog so user can verify and trust the host.
            self.after(0, self._on_unknown_host, exc, params)
        except paramiko.AuthenticationException:
            self.after(0, self._on_failure, "Authentication failed",
                       "Wrong password or key. Check your credentials and try again.")
        except (_socket.timeout, TimeoutError):
            self.after(0, self._on_failure, "Connection timed out",
                       "Make sure the Steam Deck is on and SSH is enabled.")
        except OSError as exc:
            # WinError 10038 and similar socket-cleanup errors are suppressed in
            # connection.py; if one still leaks, give a plain message.
            msg = str(exc)
            if "10038" in msg or "not a socket" in msg.lower():
                msg = (
                    "Could not reach the Steam Deck.\n"
                    "Check the IP address and make sure SSH is enabled on the Deck."
                )
            self.after(0, self._on_failure, "Network error", msg)
        except Exception as exc:
            self.after(0, self._on_failure, "Unexpected error", str(exc))

    # ------------------------------------------------------------------
    # Unknown-host fingerprint dialog
    # ------------------------------------------------------------------

    def _on_unknown_host(self, exc, params: dict) -> None:
        """Show a fingerprint confirmation dialog (main thread).

        Asks the user whether to trust the Steam Deck's host key.  On
        acceptance, saves to known_hosts and retries the connection.
        """
        self._spinner.stop()
        self._test_btn.configure(state=tk.NORMAL)

        dialog = tk.Toplevel(self)
        dialog.title("Verify Steam Deck Identity")
        dialog.configure(background=_DARK_BG)
        dialog.resizable(False, False)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="New host — verify before connecting",
            font=("TkDefaultFont", 13, "bold"),
            foreground=_DARK_ACCENT,
            padding=(16, 12),
        ).pack(anchor=tk.W)

        ttk.Label(
            dialog,
            text=(
                "DeckBridge has never connected to this Steam Deck before.\n"
                "Verify the fingerprint below matches your device, then click Trust."
            ),
            wraplength=420,
            justify=tk.LEFT,
            padding=(16, 0),
        ).pack(anchor=tk.W)

        info_frame = ttk.Frame(dialog, padding=(16, 8))
        info_frame.pack(fill=tk.X)
        info_frame.columnconfigure(1, weight=1)

        for row, (label, value) in enumerate([
            ("Host:", exc.hostname or params.get("host", "")),
            ("Key type:", exc.key_type or "unknown"),
            ("Fingerprint (MD5):", exc.fingerprint or "unknown"),
        ]):
            ttk.Label(info_frame, text=label, font=("TkDefaultFont", 10, "bold")).grid(
                row=row, column=0, sticky=tk.E, padx=(0, 8), pady=3
            )
            ttk.Label(
                info_frame,
                text=value,
                font=("Courier", 10),
                foreground=_DARK_ACCENT,
            ).grid(row=row, column=1, sticky=tk.W, pady=3)

        ttk.Label(
            dialog,
            text=(
                "If you don't recognise this fingerprint, click Cancel and\n"
                "verify you entered the correct IP address."
            ),
            foreground="#a8b5c2",
            wraplength=420,
            justify=tk.LEFT,
            padding=(16, 4),
        ).pack(anchor=tk.W)

        btn_frame = ttk.Frame(dialog, padding=(16, 12))
        btn_frame.pack(fill=tk.X)

        def _on_trust() -> None:
            dialog.destroy()
            self._result_label.configure(
                text="Saving host key and retrying…", foreground=_DARK_FG
            )
            self._test_btn.configure(state=tk.DISABLED)
            self._spinner.start()
            t = threading.Thread(
                target=self._accept_host_and_retry,
                args=(exc, params),
                daemon=True,
            )
            t.start()

        def _on_cancel() -> None:
            dialog.destroy()
            self._on_failure(
                "Host not trusted",
                "The Steam Deck's identity was not accepted. "
                "Re-check the IP address and try again.",
            )

        ttk.Button(btn_frame, text="Cancel", command=_on_cancel).pack(side=tk.LEFT)
        ttk.Button(
            btn_frame,
            text="Trust this device",
            style="Accent.TButton",
            command=_on_trust,
        ).pack(side=tk.RIGHT)

        # Centre dialog over parent
        dialog.update_idletasks()
        px = self.winfo_rootx() + self.winfo_width() // 2 - dialog.winfo_width() // 2
        py = self.winfo_rooty() + self.winfo_height() // 2 - dialog.winfo_height() // 2
        dialog.geometry(f"+{px}+{py}")

    def _accept_host_and_retry(self, exc, params: dict) -> None:
        """Background thread: save the host key then retry the connection."""
        from app.connection import SSHConnection, UnknownHostError, accept_host_key
        import paramiko
        import socket as _socket

        if exc.key and exc.hostname:
            try:
                accept_host_key(exc.hostname, exc.key)
            except Exception as save_exc:
                self.after(0, self._on_failure, "Could not save host key", str(save_exc))
                return

        conn = SSHConnection(
            host=params["host"],
            port=22,
            username=params["username"],
            auth_type=params["auth_type"],
            key_path=params.get("key_path"),
        )
        if params.get("password"):
            conn.store_password(params["password"])

        try:
            conn.connect()
            self.after(0, self._on_success, conn)
        except paramiko.AuthenticationException:
            self.after(0, self._on_failure, "Authentication failed",
                       "Wrong password or key. Check your credentials and try again.")
        except (_socket.timeout, TimeoutError):
            self.after(0, self._on_failure, "Connection timed out",
                       "Make sure the Steam Deck is on and SSH is enabled.")
        except OSError as exc2:
            self.after(0, self._on_failure, "Network error", str(exc2))
        except Exception as exc2:
            self.after(0, self._on_failure, "Unexpected error", str(exc2))

    def _on_success(self, conn) -> None:
        """Handle a successful test result (main thread)."""
        self._spinner.stop()
        self._test_btn.configure(state=tk.NORMAL)
        self._result_label.configure(text="✓ Connection successful!", foreground=_DARK_SUCCESS)
        self._detail_label.configure(text="Your Steam Deck is reachable. Click Next to finish.")
        self._success = True
        self._connection = conn

    def _on_failure(self, title: str, detail: str) -> None:
        """Handle a failed test result (main thread)."""
        self._spinner.stop()
        self._test_btn.configure(state=tk.NORMAL)
        self._result_label.configure(text=f"✗ {title}", foreground=_DARK_ERROR)
        self._detail_label.configure(text=detail)
        self._success = False

    def on_leave(self) -> bool:
        """Block advancement if the connection test has not passed."""
        if not self._success:
            self._result_label.configure(
                text="Please run the connection test successfully before continuing.",
                foreground=_DARK_ERROR,
            )
            return False
        return True

    def get_connection(self) -> SSHConnection | None:
        """Return the SSHConnection used during the successful test."""
        return self._connection


# ---------------------------------------------------------------------------
# Step 5 — Complete
# ---------------------------------------------------------------------------


class CompleteStep(WizardStep):
    """Final step — marks setup as complete and invokes the completion callback."""

    title = "All Done"

    def __init__(
        self,
        master: tk.Widget,
        on_complete: Callable[[], None],
        **kwargs,
    ) -> None:
        """Initialise with the completion callback."""
        self._on_complete_cb = on_complete
        super().__init__(master, **kwargs)

    def build(self) -> None:
        """Build the completion step UI."""
        ttk.Label(
            self,
            text="You're all set!",
            font=("TkDefaultFont", 20, "bold"),
            foreground=_DARK_SUCCESS,
        ).pack(pady=(0, 16))

        ttk.Label(
            self,
            text=(
                "DeckBridge is now connected to your Steam Deck.\n\n"
                "You can drag and drop files between your PC and Deck,\n"
                "use the quick-navigate shortcuts to jump to common folders,\n"
                "and browse your Steam library files directly.\n\n"
                "Click Finish to open the file browser."
            ),
            justify=tk.CENTER,
            font=("TkDefaultFont", 12),
        ).pack()

    def on_enter(self) -> None:
        """Mark setup as complete when this step is shown."""
        from app.config import ConfigManager
        try:
            ConfigManager().mark_setup_complete()
        except Exception:
            logger.exception("Failed to mark setup complete")

    def on_leave(self) -> bool:
        """Invoke the on_complete callback and allow navigation."""
        self._on_complete_cb()
        return True


# ---------------------------------------------------------------------------
# Wizard frame
# ---------------------------------------------------------------------------


class Wizard(ttk.Frame):
    """Multi-step setup wizard using frame-stacking navigation.

    Steps are shown one at a time; the frame itself stays in place while
    only the active step's frame is visible.
    """

    def __init__(
        self,
        master: tk.Widget,
        on_complete: Callable,
        **kwargs,
    ) -> None:
        """Initialise the wizard with all steps.

        Args:
            on_complete: Called with the live ``SSHConnection`` (or ``None``)
                when the user clicks Finish on the last step.
        """
        super().__init__(master, **kwargs)
        self._on_complete = on_complete
        self._current_idx = 0

        self._build_layout()
        self._build_steps()
        self._show_step(0)

    def _finish(self) -> None:
        """Internal callback passed to CompleteStep; saves profile and forwards connection."""
        conn = self._test_step.get_connection()

        # Persist the connection profile so the main window can reconnect later.
        if conn is not None:
            from app.config import ConfigManager
            params = self._connection_step.get_connection_params()
            profile = {
                "name": f"{params['username']}@{params['host']}",
                "host": params["host"],
                "port": 22,
                "username": params["username"],
                "auth_type": params["auth_type"],
                "key_path": params.get("key_path"),
            }
            try:
                ConfigManager().save_profile(profile)
            except Exception:
                logger.exception("Failed to save connection profile")

        self._on_complete(conn)

    def _build_layout(self) -> None:
        """Create the main layout: step indicator, content area, nav buttons."""
        # Step indicator at top
        self._indicator = ttk.Label(
            self,
            text="",
            font=("TkDefaultFont", 10),
            foreground="#6a8fa8",
            padding=(12, 8),
        )
        self._indicator.pack(side=tk.TOP, anchor=tk.W)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # Content area (stacked step frames)
        self._content = ttk.Frame(self)
        self._content.pack(fill=tk.BOTH, expand=True)

        # Bottom navigation
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)
        nav = ttk.Frame(self, padding=(12, 8))
        nav.pack(fill=tk.X, side=tk.BOTTOM)

        self._back_btn = ttk.Button(nav, text="← Back", command=self._go_back)
        self._back_btn.pack(side=tk.LEFT)
        Tooltip(self._back_btn, "Go to the previous step")

        self._next_btn = ttk.Button(
            nav, text="Next →", style="Accent.TButton", command=self._go_next
        )
        self._next_btn.pack(side=tk.RIGHT)
        Tooltip(self._next_btn, "Advance to the next step")

    def _build_steps(self) -> None:
        """Instantiate all wizard steps."""
        self._connection_step = ConnectionStep(self._content)
        self._test_step = TestConnectionStep(
            self._content,
            get_params_cb=self._connection_step.get_connection_params,
        )

        self._steps: list[WizardStep] = [
            WelcomeStep(self._content),
            EnableSSHStep(self._content),
            self._connection_step,
            self._test_step,
            CompleteStep(self._content, on_complete=self._finish),
        ]

        # Place all steps in the same grid cell so they overlap
        for step in self._steps:
            step.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _show_step(self, idx: int) -> None:
        """Make step *idx* visible and update navigation buttons."""
        step = self._steps[idx]

        # Bring the target step to the front
        step.lift()

        step.on_enter()

        # Update indicator
        titles = [s.title for s in self._steps]
        indicator_parts = []
        for i, title in enumerate(titles):
            if i == idx:
                indicator_parts.append(f"[{title}]")
            else:
                indicator_parts.append(title)
        self._indicator.configure(text="  ›  ".join(indicator_parts))

        # Update button states / labels
        self._back_btn.configure(state=tk.NORMAL if idx > 0 else tk.DISABLED)
        is_last = idx == len(self._steps) - 1
        self._next_btn.configure(text="Finish" if is_last else "Next →")

    def _go_next(self) -> None:
        """Advance to the next step, honouring on_leave validation."""
        current_step = self._steps[self._current_idx]
        if not current_step.on_leave():
            return
        if self._current_idx < len(self._steps) - 1:
            self._current_idx += 1
            self._show_step(self._current_idx)

    def _go_back(self) -> None:
        """Go back to the previous step."""
        if self._current_idx > 0:
            self._current_idx -= 1
            self._show_step(self._current_idx)
