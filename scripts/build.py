"""DeckBridge build script.

Orchestrates:
  1. Icon generation  (calls generate_icon.main() in-process)
  2. PyInstaller one-file EXE build
  3. Output verification

Usage::

    python scripts/build.py
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Locate project root relative to this script
PROJECT_ROOT = Path(__file__).parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
PNG_PATH = ASSETS_DIR / "icons" / "app_icon.png"
ICO_PATH = ASSETS_DIR / "app_icon.ico"
DIST_EXE = PROJECT_ROOT / "dist" / "DeckBridge.exe"


def _step1_generate_icon() -> None:
    """Generate app_icon.png and app_icon.ico via generate_icon.main()."""
    logger.info("Step 1 — Generating icon …")

    # Add scripts/ to sys.path so the sibling module is importable
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    import generate_icon  # noqa: PLC0415

    generate_icon.main()

    # Validate outputs
    if not PNG_PATH.is_file():
        logger.error("PNG not created: %s", PNG_PATH)
        sys.exit(1)
    if not ICO_PATH.is_file():
        logger.error("ICO not created: %s", ICO_PATH)
        sys.exit(1)

    logger.info("Icon files verified ✓")


def _step2_pyinstaller() -> None:
    """Run PyInstaller to produce a single-file EXE."""
    logger.info("Step 2 — Running PyInstaller …")

    # Platform separator for --add-data
    sep = ";" if sys.platform == "win32" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        f"--icon={ICO_PATH}",
        "--name=DeckBridge",
        f"--add-data={ASSETS_DIR}{sep}assets",
        "--hidden-import=keyring.backends.Windows",
        "--hidden-import=keyring.backends.fail",
        "--hidden-import=keyring.backends.null",
        "--hidden-import=pkg_resources",
        "--hidden-import=paramiko",
        "--hidden-import=_cffi_backend",
        "--collect-all=tkinterdnd2",
        "--clean",
        "--noconfirm",
        str(PROJECT_ROOT / "main.py"),
    ]

    logger.info("Command: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        logger.error("PyInstaller failed (exit code %d)", result.returncode)
        sys.exit(result.returncode)

    logger.info("PyInstaller completed ✓")


def _step3_verify() -> None:
    """Confirm the EXE was produced and report its size."""
    logger.info("Step 3 — Verifying output …")

    if not DIST_EXE.is_file():
        logger.error("Expected EXE not found: %s", DIST_EXE)
        sys.exit(1)

    size_mb = DIST_EXE.stat().st_size / (1024 * 1024)
    print(f"\nBuild complete!\n  {DIST_EXE}\n  Size: {size_mb:.1f} MB")


def main() -> None:
    """Run all three build steps sequentially."""
    _step1_generate_icon()
    _step2_pyinstaller()
    _step3_verify()


if __name__ == "__main__":
    main()
