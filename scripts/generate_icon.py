"""Generate DeckBridge app icon.

Draws a Steam Deck-inspired icon using Pillow (no external assets required)
and saves both a 256×256 PNG (for Tkinter) and a multi-size ICO (for
PyInstaller / Windows shell).

Usage::

    python scripts/generate_icon.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Colour palette (Steam Deck dark aesthetic)
_BG = "#1b2838"
_DEVICE_BODY = "#c7d5e0"
_SCREEN = "#0d1b2a"
_CONTROL = "#1b2838"
_ACCENT = "#1a9fff"

# Canvas size
_SIZE = 256


def _hex(colour: str) -> tuple[int, int, int]:
    """Convert ``#rrggbb`` to an ``(r, g, b)`` tuple."""
    h = colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _draw_icon() -> "Image.Image":  # type: ignore[name-defined]
    """Construct and return the 256×256 RGBA icon image."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    img = Image.new("RGBA", (_SIZE, _SIZE), (*_hex(_BG), 255))
    draw = ImageDraw.Draw(img)

    cx = _SIZE // 2  # 128

    # ── Device body ──────────────────────────────────────────────────────────
    body_w, body_h = 216, 108
    body_x0 = (cx - body_w // 2)  # 20
    body_y0 = 126
    body_x1 = body_x0 + body_w   # 236
    body_y1 = body_y0 + body_h   # 234
    draw.rounded_rectangle(
        [body_x0, body_y0, body_x1, body_y1],
        radius=28,
        fill=_DEVICE_BODY,
    )

    # Screen inset
    screen_margin = 28
    scr_x0 = body_x0 + screen_margin
    scr_y0 = body_y0 + 14
    scr_x1 = body_x1 - screen_margin
    scr_y1 = body_y1 - 14
    draw.rounded_rectangle(
        [scr_x0, scr_y0, scr_x1, scr_y1],
        radius=6,
        fill=_SCREEN,
    )

    body_mid_y = (body_y0 + body_y1) // 2  # vertical centre of body

    # ── Left thumbstick ───────────────────────────────────────────────────────
    ls_cx = body_x0 + 30
    ls_cy = body_mid_y + 8
    ls_r = 12
    draw.ellipse(
        [ls_cx - ls_r, ls_cy - ls_r, ls_cx + ls_r, ls_cy + ls_r],
        fill=_CONTROL,
    )

    # ── D-pad (cross) ─────────────────────────────────────────────────────────
    dp_cx = body_x0 + 30
    dp_cy = body_mid_y - 20
    dp_arm = 6
    dp_thick = 6
    # Horizontal bar
    draw.rectangle(
        [dp_cx - dp_arm * 2, dp_cy - dp_thick // 2,
         dp_cx + dp_arm * 2, dp_cy + dp_thick // 2],
        fill=_CONTROL,
    )
    # Vertical bar
    draw.rectangle(
        [dp_cx - dp_thick // 2, dp_cy - dp_arm * 2,
         dp_cx + dp_thick // 2, dp_cy + dp_arm * 2],
        fill=_CONTROL,
    )

    # ── Right thumbstick ──────────────────────────────────────────────────────
    rs_cx = body_x1 - 30
    rs_cy = body_mid_y + 8
    rs_r = 12
    draw.ellipse(
        [rs_cx - rs_r, rs_cy - rs_r, rs_cx + rs_r, rs_cy + rs_r],
        fill=_CONTROL,
    )

    # ── ABXY buttons (diamond pattern) ────────────────────────────────────────
    btn_cx = body_x1 - 30
    btn_cy = body_mid_y - 20
    btn_r = 5
    btn_offset = 13
    for dx, dy in [(0, -btn_offset), (btn_offset, 0), (0, btn_offset), (-btn_offset, 0)]:
        bx, by = btn_cx + dx, btn_cy + dy
        draw.ellipse([bx - btn_r, by - btn_r, bx + btn_r, by + btn_r], fill=_CONTROL)

    # ── Steam button (centre top of body) ─────────────────────────────────────
    sb_r = 7
    sb_cx = cx
    sb_cy = body_y0 + 16
    draw.ellipse(
        [sb_cx - sb_r, sb_cy - sb_r, sb_cx + sb_r, sb_cy + sb_r],
        fill=_ACCENT,
    )

    # ── Bridge / wireless arcs ────────────────────────────────────────────────
    # Anchor point: just above the device body
    arc_cx = cx
    arc_cy = body_y0 - 2  # anchor Y

    for radius, width in [(38, 5), (58, 4), (78, 3)]:
        bb = [
            arc_cx - radius, arc_cy - radius,
            arc_cx + radius, arc_cy + radius,
        ]
        # Pillow arc angles: 0° = right (east), clockwise.
        # We want an upward arch: 210° → 330° sweeps the top 120° of the circle.
        draw.arc(bb, start=210, end=330, fill=_ACCENT, width=width)

    return img


def main() -> None:
    """Generate PNG and ICO icon files and write them to ``assets/``."""
    try:
        from PIL import Image  # noqa: PLC0415 — validate Pillow is available
    except ImportError:
        logger.error("Pillow is not installed. Run: pip install Pillow==10.3.0")
        sys.exit(1)

    project_root = Path(__file__).parent.parent
    icons_dir = project_root / "assets" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    png_path = icons_dir / "app_icon.png"
    ico_path = project_root / "assets" / "app_icon.ico"

    img = _draw_icon()

    # Save PNG
    img.save(png_path, format="PNG")
    logger.info("Saved PNG: %s", png_path)

    # Save multi-size ICO
    sizes = [16, 32, 48, 64, 128, 256]
    frames = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[:-1],
    )
    logger.info("Saved ICO: %s", ico_path)

    print(f"Icon generated:\n  PNG -> {png_path}\n  ICO -> {ico_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
