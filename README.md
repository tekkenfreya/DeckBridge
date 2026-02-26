# DeckBridge

A cross-platform desktop application for transferring files between your PC and Steam Deck over SSH/SFTP — no technical knowledge required.

## Features

- **Auto Device Discovery** — finds your Steam Deck on the local network via mDNS or subnet scan
- **Dual-Pane File Browser** — side-by-side PC and Steam Deck file explorer with sortable columns
- **Drag-and-Drop Transfers** — queue multiple files with per-file and overall progress tracking
- **Connection Profiles** — save multiple devices, SSH key support, secure credential storage via OS keyring
- **Steam Deck Quick-Navigate** — one-click shortcuts to Steam library, Flatpak apps, SD card, and save data
- **Transfer Resume** — interrupted transfers continue from where they left off
- **First-Time Setup Wizard** — guided SSH setup with copy-paste commands

## Requirements

- Python 3.10+
- Windows, macOS, or Linux
- Steam Deck with SSH enabled (`sudo systemctl enable --now sshd`)

## Installation

```bash
pip install -r requirements.txt
python main.py
```

## Build (Standalone Executable)

```bash
python scripts/build.py
```

Produces a single-file executable in `dist/` via PyInstaller.

## Running Tests

```bash
pytest tests/ -v --tb=short
```

## Tech Stack

| Layer | Technology |
|---|---|
| UI | Python 3.10+ Tkinter (ttk) |
| SSH/SFTP | paramiko |
| Network scan | socket, concurrent.futures |
| Config storage | JSON (~/.deckbridge/) |
| Credentials | keyring (OS secure storage) |
| Packaging | PyInstaller |

## Steam Deck Setup

Enable SSH on your Steam Deck:

1. Switch to Desktop Mode
2. Open a terminal (Konsole)
3. Run: `sudo systemctl enable --now sshd`
4. Set a password: `passwd`
5. Note your IP address: `ip addr`

DeckBridge will handle the rest.

## Project Structure

```
DeckBridge/
├── main.py               # Entry point
├── requirements.txt
├── app/
│   ├── config.py         # Settings and profile management
│   ├── connection.py     # SSH/SFTP connection lifecycle
│   ├── discovery.py      # Network scan and mDNS lookup
│   ├── transfer.py       # File transfer engine and queue
│   ├── ui/               # Tkinter UI components
│   └── utils/            # Path helpers and image loader
├── assets/icons/         # UI icons
├── scripts/              # Build and utility scripts
└── tests/                # Unit tests
```
