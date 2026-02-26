"""Profile management dialogs for DeckBridge.

Provides :class:`EditProfileDialog` for editing an existing profile's fields
and :class:`ProfileManagerDialog` for listing, adding, editing, and deleting
connection profiles.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

logger = logging.getLogger(__name__)

_DARK_BG = "#1b2838"
_DARK_FG = "#c7d5e0"
_DARK_ACCENT = "#1a9fff"
_DARK_ENTRY = "#263448"
_DARK_BORDER = "#374e6a"

_KEYRING_SERVICE = "DeckBridge"


# ---------------------------------------------------------------------------
# EditProfileDialog
# ---------------------------------------------------------------------------


class EditProfileDialog(tk.Toplevel):
    """Modal form for editing an existing connection profile.

    Does **not** handle "Add" — new profiles are created through the wizard.
    """

    def __init__(
        self,
        master: tk.Widget,
        config: Any,
        profile: dict[str, Any],
        on_save: Callable[[], None] | None = None,
    ) -> None:
        """Build the edit form pre-filled with *profile* data."""
        super().__init__(master)
        self._config = config
        self._profile = dict(profile)
        self._on_save = on_save
        self._old_name: str = profile.get("name", "")

        self.title("Edit Profile")
        self.resizable(False, False)
        self.grab_set()
        self.configure(background=_DARK_BG)

        self._build_form()
        self._center_on_parent(master)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_form(self) -> None:
        """Construct all form widgets."""
        pad = {"padx": 8, "pady": 4}
        body = ttk.Frame(self, padding=16)
        body.pack(fill=tk.BOTH, expand=True)

        # Two-column grid
        fields = [
            ("Profile name:", "_name_var"),
            ("Host / IP:", "_host_var"),
            ("Port:", "_port_var"),
            ("Username:", "_user_var"),
        ]

        self._name_var = tk.StringVar(value=self._profile.get("name", ""))
        self._host_var = tk.StringVar(value=self._profile.get("host", ""))
        self._port_var = tk.StringVar(value=str(self._profile.get("port", 22)))
        self._user_var = tk.StringVar(value=self._profile.get("username", "deck"))
        self._auth_var = tk.StringVar(value=self._profile.get("auth_type", "password"))
        self._key_var = tk.StringVar(value=self._profile.get("key_path", ""))
        self._pass_var = tk.StringVar()

        row = 0
        for label_text, var_attr in fields:
            ttk.Label(body, text=label_text, anchor=tk.W).grid(
                row=row, column=0, sticky=tk.W, **pad
            )
            var = getattr(self, var_attr)
            if var_attr == "_port_var":
                widget: tk.Widget = ttk.Spinbox(body, from_=1, to=65535, textvariable=var, width=8)
            else:
                widget = ttk.Entry(body, textvariable=var, width=32)
            widget.grid(row=row, column=1, sticky=tk.EW, **pad)
            row += 1

        # Auth type
        ttk.Label(body, text="Auth type:", anchor=tk.W).grid(
            row=row, column=0, sticky=tk.W, **pad
        )
        self._auth_combo = ttk.Combobox(
            body,
            textvariable=self._auth_var,
            values=["password", "key"],
            state="readonly",
            width=12,
        )
        self._auth_combo.grid(row=row, column=1, sticky=tk.W, **pad)
        self._auth_combo.bind("<<ComboboxSelected>>", self._on_auth_change)
        row += 1

        # Key path row (conditional)
        self._key_label = ttk.Label(body, text="Key path:", anchor=tk.W)
        self._key_label.grid(row=row, column=0, sticky=tk.W, **pad)
        key_frame = ttk.Frame(body)
        key_frame.grid(row=row, column=1, sticky=tk.EW, **pad)
        self._key_entry = ttk.Entry(key_frame, textvariable=self._key_var, width=26)
        self._key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._browse_btn = ttk.Button(key_frame, text="Browse…", command=self._browse_key)
        self._browse_btn.pack(side=tk.LEFT, padx=(4, 0))
        self._key_row = row
        row += 1

        # Password row (conditional)
        self._pass_label = ttk.Label(body, text="New password:", anchor=tk.W)
        self._pass_label.grid(row=row, column=0, sticky=tk.W, **pad)
        self._pass_entry = ttk.Entry(body, textvariable=self._pass_var, show="*", width=32)
        self._pass_entry.grid(row=row, column=1, sticky=tk.EW, **pad)
        self._pass_row = row
        row += 1

        body.columnconfigure(1, weight=1)

        # Hint label
        self._hint = ttk.Label(body, text="", foreground="#e05c5c")
        self._hint.grid(row=row, column=0, columnspan=2, sticky=tk.W, **pad)
        row += 1

        # Buttons
        btn_frame = ttk.Frame(self, padding=(16, 8))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(
            btn_frame, text="Save", style="Accent.TButton", command=self._save
        ).pack(side=tk.RIGHT, padx=4)

        # Initial visibility
        self._on_auth_change()

    def _on_auth_change(self, event: tk.Event | None = None) -> None:  # type: ignore[type-arg]
        """Show/hide key or password fields depending on selected auth type."""
        is_key = self._auth_var.get() == "key"
        if is_key:
            self._key_entry.configure(state=tk.NORMAL)
            self._browse_btn.configure(state=tk.NORMAL)
            self._pass_entry.configure(state=tk.DISABLED)
        else:
            self._key_entry.configure(state=tk.DISABLED)
            self._browse_btn.configure(state=tk.DISABLED)
            self._pass_entry.configure(state=tk.NORMAL)

    def _browse_key(self) -> None:
        """Open a file picker for the SSH key path."""
        path = filedialog.askopenfilename(
            parent=self,
            title="Select SSH private key",
            initialdir="~/.ssh",
        )
        if path:
            self._key_var.set(path)

    def _center_on_parent(self, master: tk.Widget) -> None:
        """Position this dialog over the centre of *master*."""
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        px = master.winfo_rootx() + (master.winfo_width() - w) // 2
        py = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"+{px}+{py}")

    # ------------------------------------------------------------------
    # Save logic
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Validate fields, persist changes, and close the dialog."""
        name = self._name_var.get().strip()
        host = self._host_var.get().strip()

        if not name:
            self._hint.configure(text="Profile name cannot be empty.")
            return
        if not host:
            self._hint.configure(text="Host / IP cannot be empty.")
            return

        try:
            port = int(self._port_var.get())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            self._hint.configure(text="Port must be a number between 1 and 65535.")
            return

        auth_type = self._auth_var.get()
        profile: dict[str, Any] = {
            "name": name,
            "host": host,
            "port": port,
            "username": self._user_var.get().strip() or "deck",
            "auth_type": auth_type,
        }
        if auth_type == "key":
            profile["key_path"] = self._key_var.get().strip()

        # Rename: delete the old profile first if the name changed
        if self._old_name and self._old_name != name:
            self._config.delete_profile(self._old_name)

        self._config.save_profile(profile)
        logger.info("Profile edited: %s", name)

        # Persist password if provided
        password = self._pass_var.get()
        if auth_type == "password" and password:
            try:
                import keyring
                keyring.set_password(_KEYRING_SERVICE, f"{profile['username']}@{host}", password)
            except Exception as exc:
                logger.warning("keyring.set_password failed: %s", exc)

        if self._on_save:
            self._on_save()
        self.destroy()


# ---------------------------------------------------------------------------
# ProfileManagerDialog
# ---------------------------------------------------------------------------


class ProfileManagerDialog(tk.Toplevel):
    """Dialog listing all saved connection profiles with management actions."""

    def __init__(
        self,
        master: tk.Widget,
        config: Any,
        on_profiles_changed: Callable[[], None] | None = None,
        active_profile_name: str | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        """Build the profile manager dialog."""
        super().__init__(master)
        self._config = config
        self._on_profiles_changed = on_profiles_changed
        self._active_profile_name = active_profile_name
        self._on_disconnect = on_disconnect

        self.title("Manage Profiles")
        self.minsize(520, 320)
        self.resizable(True, False)
        self.grab_set()
        self.configure(background=_DARK_BG)

        self._build_ui()
        self._refresh()
        self._center_on_parent(master)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct the Treeview and button row."""
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        cols = ("name", "host", "username", "auth")
        self._tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")
        self._tree.heading("name", text="Name")
        self._tree.heading("host", text="Host")
        self._tree.heading("username", text="Username")
        self._tree.heading("auth", text="Auth")
        self._tree.column("name", width=160)
        self._tree.column("host", width=160)
        self._tree.column("username", width=100)
        self._tree.column("auth", width=80)

        sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.LEFT, fill=tk.Y)

        # Double-click to edit
        self._tree.bind("<Double-1>", lambda _: self._edit_profile())

        # Button row
        btn_frame = ttk.Frame(self, padding=(12, 8))
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_frame, text="Delete", command=self._delete_profile).pack(
            side=tk.RIGHT, padx=4
        )
        ttk.Button(btn_frame, text="Edit", command=self._edit_profile).pack(
            side=tk.RIGHT, padx=4
        )
        ttk.Button(
            btn_frame, text="Add", style="Accent.TButton", command=self._add_profile
        ).pack(side=tk.RIGHT, padx=4)

    def _center_on_parent(self, master: tk.Widget) -> None:
        """Position this dialog over the centre of *master*."""
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        px = master.winfo_rootx() + (master.winfo_width() - w) // 2
        py = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"+{px}+{py}")

    # ------------------------------------------------------------------
    # Treeview helpers
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Repopulate the Treeview from the current profile list."""
        self._tree.delete(*self._tree.get_children())
        for p in self._config.get_profiles():
            name = p.get("name", "?")
            display_name = f"● {name}" if name == self._active_profile_name else name
            self._tree.insert(
                "",
                tk.END,
                iid=name,
                values=(
                    display_name,
                    p.get("host", ""),
                    p.get("username", ""),
                    p.get("auth_type", "password"),
                ),
            )

    def _selected_name(self) -> str | None:
        """Return the raw profile name of the currently selected row, or None."""
        sel = self._tree.selection()
        if not sel:
            return None
        # iid is the raw name; display name may have the ● prefix
        return sel[0]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _add_profile(self) -> None:
        """Open the setup wizard in a child Toplevel to create a new profile."""
        from app.ui.wizard import Wizard

        top = tk.Toplevel(self)
        top.title("Add Profile — Setup Wizard")
        top.minsize(700, 500)
        top.grab_set()
        top.configure(background=_DARK_BG)

        def _on_add_complete(connection=None) -> None:
            top.destroy()
            self._refresh()
            if self._on_profiles_changed:
                self._on_profiles_changed()

        wizard = Wizard(top, on_complete=_on_add_complete)
        wizard.pack(fill=tk.BOTH, expand=True)

    def _edit_profile(self) -> None:
        """Open EditProfileDialog for the selected profile."""
        name = self._selected_name()
        if not name:
            messagebox.showinfo("No selection", "Select a profile to edit.", parent=self)
            return

        profile = self._config.get_profile(name)
        if not profile:
            messagebox.showerror("Not found", f"Profile '{name}' no longer exists.", parent=self)
            self._refresh()
            return

        def _on_saved() -> None:
            self._refresh()
            if self._on_profiles_changed:
                self._on_profiles_changed()

        EditProfileDialog(self, self._config, profile, on_save=_on_saved)

    def _delete_profile(self) -> None:
        """Confirm, then delete the selected profile."""
        name = self._selected_name()
        if not name:
            messagebox.showinfo("No selection", "Select a profile to delete.", parent=self)
            return

        if not messagebox.askyesno(
            "Delete profile",
            f"Delete profile '{name}'? This cannot be undone.",
            parent=self,
        ):
            return

        # If this is the active profile, disconnect first
        if name == self._active_profile_name and self._on_disconnect:
            self._on_disconnect()

        self._config.delete_profile(name)
        logger.info("Profile deleted via UI: %s", name)

        # If no profiles remain, reset setup so the wizard runs on next launch
        if not self._config.get_profiles():
            self._config.reset_setup()
            messagebox.showinfo(
                "No profiles",
                "All profiles deleted. The setup wizard will run on next launch.",
                parent=self,
            )

        self._refresh()
        if self._on_profiles_changed:
            self._on_profiles_changed()
