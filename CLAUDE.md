# DeckBridge — Development Rules & Standards

## Project Overview
**SteamDeck Bridge** — A beginner-friendly Python Tkinter desktop app for Windows/Mac/Linux that enables gamers to transfer files between their PC and Steam Deck over SSH/SFTP with zero technical knowledge required.

---

## AI/Model Configuration
- **Temperature:** 0 — deterministic, reproducible output only
- **Hallucination rate:** 0% — never fabricate file paths, SSH commands, API responses, or Steam Deck system details; all commands and paths must be verified against real SteamOS documentation
- When uncertain about a Steam Deck path or behavior, surface the uncertainty explicitly rather than guessing

---

## Architecture

### Tech Stack
| Layer | Technology |
|---|---|
| UI | Python 3.10+ Tkinter (ttk widgets preferred) |
| SSH/SFTP | `paramiko` |
| Network scan | `socket`, `concurrent.futures` (threadpool) |
| Config storage | JSON files (no database — flat-file only) |
| Packaging | PyInstaller (single-file executable) |
| Threading | `threading` + `queue` — never block the main UI thread |

### Directory Structure
```
DeckBridge/
├── CLAUDE.md
├── main.py                  # Entry point — bootstraps app, splash screen
├── requirements.txt
├── assets/
│   └── icons/               # All UI icons (PNG, lazy-loaded)
├── app/
│   ├── __init__.py
│   ├── config.py            # Settings, profile management (JSON)
│   ├── connection.py        # SSH/SFTP connection lifecycle
│   ├── discovery.py         # Network scan, mDNS lookup
│   ├── transfer.py          # File transfer engine, queue
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── wizard.py        # First-time setup wizard
│   │   ├── main_window.py   # Dual-pane file browser
│   │   ├── pane.py          # Single pane (PC or Deck) reusable widget
│   │   ├── toolbar.py       # Quick-navigate shortcuts bar
│   │   ├── progress.py      # Transfer progress dialog
│   │   └── components.py    # Shared reusable widgets
│   └── utils/
│       ├── __init__.py
│       ├── image_loader.py  # Lazy image loader
│       └── path_helpers.py  # Cross-platform path normalization
└── tests/
    ├── test_discovery.py
    ├── test_transfer.py
    └── test_config.py
```

---

## Coding Standards

### General Rules
- **Python 3.10+** — use match/case where appropriate, use `|` union types in type hints
- All public functions and classes **must have type hints**
- All public functions and classes **must have docstrings** (one-liner minimum)
- Maximum line length: **100 characters**
- Use `black` for formatting, `ruff` for linting — zero warnings tolerated
- No `print()` in production code — use the `logging` module exclusively
- Log levels: `DEBUG` for SSH/SFTP internals, `INFO` for user actions, `WARNING` for recoverable errors, `ERROR` for failures

### Threading & Concurrency
- **All network operations must run in background threads** — never on the main Tkinter thread
- Use `queue.Queue` to pass results back to the UI from worker threads
- UI updates from threads must always use `widget.after(0, callback)` — never call Tkinter methods directly from a non-main thread
- Connection keepalive must use a daemon thread
- File transfers must be cancellable — use `threading.Event` stop signals

### Error Handling
- Every SSH/SFTP call must be wrapped in try/except with specific exception types (`paramiko.SSHException`, `paramiko.AuthenticationException`, `socket.timeout`, `OSError`)
- Never swallow exceptions silently — always log and surface meaningful error messages to the user in the UI
- Distinguish between: auth failure / timeout / host unreachable / permission denied
- Failed transfers must not corrupt destination files — use temp file + atomic rename pattern

### Security
- **Never store passwords in plaintext** — use `keyring` library for OS credential storage
- SSH keys: support `~/.ssh/id_rsa`, `~/.ssh/id_ed25519` by default; allow user-specified path
- Validate all remote paths before SFTP operations to prevent path traversal
- Scan results must only operate on port 22 — do not scan other ports
- Enforce host key verification with known_hosts; prompt user on unknown host, never auto-accept silently

### Configuration & Persistence
- All user settings stored in `~/.deckbridge/config.json`
- Connection profiles stored in `~/.deckbridge/profiles.json`
- First-time setup flag stored in `~/.deckbridge/setup_complete` (presence of file = done)
- Never write credentials to JSON — delegate to `keyring`
- Config schema must be validated on load; corrupt config must trigger a safe reset with user warning, not a crash

---

## Image & Asset Rules (Lazy Loading)
- **All images must be lazy-loaded** — do not preload icons at import time
- Use `app/utils/image_loader.py` with an `ImageCache` class:
  - Load `PhotoImage` only when the widget first becomes visible
  - Cache loaded images in a module-level dict to avoid garbage collection and redundant disk reads
  - Images not yet loaded display a placeholder (blank/spinner)
- Icons must be bundled as PNG, not embedded as base64 strings
- Scale icons at load time to target size — do not bundle multiple sizes
- Never hold `PhotoImage` objects only in local scope (Tkinter GC bug — always assign to a persistent attribute or cache)

---

## UI/UX Standards
- Use `ttk` themed widgets everywhere — avoid raw `tk` widgets except for `Canvas`
- Apply a consistent dark theme (Steam Deck aesthetic) using `ttk.Style`
- Font: system default sans-serif, minimum 11pt for all interactive elements
- All long-running operations must show a progress indicator (spinner or progress bar)
- Destructive actions (delete, overwrite) must require explicit confirmation dialog
- Breadcrumb path bars must be scrollable horizontally when path is long
- All text in code/command boxes (wizard) must be selectable and copyable
- Keyboard navigation must work for all core actions (Tab, Enter, arrow keys)
- The app must be fully resizable; minimum window size: 900×600

### Accessibility
- All buttons must have `tooltip` text
- Status messages must be reflected in a persistent status bar at the bottom
- Error dialogs must include a "Copy error details" button

---

## Feature Implementation Rules

### First-Time Setup Wizard (`ui/wizard.py`)
- Multi-step wizard using a frame-stacking pattern (not separate windows)
- All SSH commands shown in read-only `Text` widgets with a "Copy" button
- "Test Connection" must run in a background thread with a spinner
- On success, write `~/.deckbridge/setup_complete` and never show wizard again
- Include illustrated step descriptions (text-based art or bundled PNG steps)

### Auto Device Discovery (`app/discovery.py`)
- Try `steamdeck.local` via mDNS first (non-blocking, 2s timeout)
- Fall back to subnet scan using `concurrent.futures.ThreadPoolExecutor` (max 50 workers, port 22 only)
- Detect subnet automatically from the host machine's active interface
- Scan must be cancellable mid-way
- Results list shows: hostname, IP address, response time
- Save discovered device to profiles on user selection

### Connection (`app/connection.py`)
- SSH keepalive: `transport.set_keepalive(30)` (30-second interval)
- Auto-reconnect on disconnect with exponential backoff (max 3 retries)
- Connection state machine: `DISCONNECTED → CONNECTING → CONNECTED → ERROR`
- Status indicator widget must reflect state in real time
- Multiple connection profiles stored; active profile highlighted

### Dual-Pane File Browser (`ui/pane.py`)
- Both panes use `ttk.Treeview` with columns: Name, Size, Modified
- All columns sortable (click header to sort, click again to reverse)
- Breadcrumb bar above each pane — clickable path segments
- Double-click folder to navigate in; breadcrumb click to go up
- Remote pane populates via SFTP in a background thread; show spinner during load
- Local pane uses `os.scandir` (fast) in a background thread
- File size display: human-readable (KB, MB, GB)
- Hidden files (dot-files) toggle button

### Steam Deck Quick-Navigate Shortcuts (`ui/toolbar.py`)
- Buttons rendered as a scrollable horizontal toolbar
- Predefined paths:
  - `~/Desktop`
  - `~/Downloads`
  - `~/.local/share/Steam`
  - `~/.var/app`
  - `/run/media/`
  - `~/.local/share/Steam/steamapps/compatdata`
- Clicking a shortcut navigates the remote pane to that path
- If path does not exist on the Deck, show a non-blocking warning (do not crash)

### File Transfer (`app/transfer.py`)
- Drag-and-drop using Tkinter DnD or `tkinterdnd2` library
- Transfer queue: multiple files queued and processed sequentially
- Progress dialog: per-file progress bar + overall queue progress
- Transfer speed display (MB/s), ETA estimate
- Use atomic write: stream to `.tmp` file, rename on completion
- Failed transfers logged and shown in a transfer history panel
- Resume support for interrupted transfers (check partial `.tmp` file size and seek)
- Overwrite prompt if destination file already exists

---

## Testing Standards
- All business logic (discovery, transfer, config, connection) must have unit tests
- Use `pytest` and `pytest-mock`
- Mock all SSH/SFTP calls — never make real network calls in tests
- Target: 80%+ coverage on `app/` (excluding `app/ui/`)
- Run tests with: `pytest tests/ -v --tb=short`

---

## Packaging
- `PyInstaller` one-file build for Windows, Mac, Linux
- `requirements.txt` must be pinned (exact versions)
- Build script: `scripts/build.py`
- Bundled assets must be accessed via `sys._MEIPASS` when frozen

---

## Git Conventions
- Branch naming: `feature/`, `fix/`, `chore/`
- Commit format: `type(scope): short description` — e.g., `feat(transfer): add resume support`
- Never commit: credentials, `.deckbridge/`, `dist/`, `build/`, `__pycache__/`
- `.gitignore` must exclude all of the above
- **No AI attribution of any kind** — no "Co-Authored-By: Claude", no "Generated with Claude Code", no AI tool mentions anywhere in commit messages, PR descriptions, code comments, or changelogs. All commits must read as the work of a solo developer.

---

## Steam Deck Verified Paths (SteamOS 3.x)
These are confirmed real paths — do not invent alternatives:
| Purpose | Path |
|---|---|
| Steam library | `~/.local/share/Steam` |
| Flatpak apps | `~/.var/app` |
| Proton save data | `~/.local/share/Steam/steamapps/compatdata` |
| SD card mount | `/run/media/` |
| Desktop | `~/Desktop` |
| Downloads | `~/Downloads` |
| SSH enable command | `sudo systemctl enable --now sshd` |
| Set password | `passwd` |
