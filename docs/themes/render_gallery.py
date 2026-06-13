#!/usr/bin/env python3
"""Regenerate docs/themes/gallery.png from the registered themes.

Renders every theme's iGPU and NPU panels with illustrative values and montages
them into a single gallery image. The values are illustrative only; the metric
columns, units, state values, and numbers are identical across all themes.

Dev-only dependencies (not required to run xdna-top):

    pip install cairosvg pillow

Usage:

    python docs/themes/render_gallery.py
"""

from __future__ import annotations

from pathlib import Path

import cairosvg
from PIL import Image
from rich.console import Console
from rich.rule import Rule

from xdna_top.gauge import GpuState
from xdna_top.main import THEMES, create_igpu_panel, create_npu_panel

OUT_DIR = Path(__file__).resolve().parent
COLS = 2
PAD = 24
BACKDROP = (13, 17, 23, 255)  # GitHub dark canvas
CONTEXTS = [
    {"pid": 93941, "ctx_id": 1, "submissions": 1042, "completions": 1042, "status": "Active"}
]


def _render_theme_png(name: str) -> Image.Image:
    theme = THEMES[name]
    console = Console(record=True, width=64)
    console.print(Rule(f"xdna-top --theme {name}", style="white"))
    console.print(
        create_igpu_panel(
            busy_pct=64,
            power_w=38.4,
            state=GpuState.PREFILL_BURST,
            busy_history=[10, 20, 35, 50, 64, 58, 70],
            power_history=[20, 25, 30, 34, 38, 36, 39],
            theme=theme,
        )
    )
    console.print(
        create_npu_panel(npu_active=True, contexts=CONTEXTS, xrt_error=False, theme=theme)
    )
    svg = console.export_svg(title=f"xdna-top — {name}")
    png_bytes = cairosvg.svg2png(bytestring=svg.encode(), scale=2.0)
    from io import BytesIO

    return Image.open(BytesIO(png_bytes)).convert("RGBA")


def main() -> None:
    images = [_render_theme_png(name) for name in sorted(THEMES)]
    cell_w = max(im.width for im in images)
    cell_h = max(im.height for im in images)
    rows = (len(images) + COLS - 1) // COLS
    width = COLS * cell_w + (COLS + 1) * PAD
    height = rows * cell_h + (rows + 1) * PAD

    gallery = Image.new("RGBA", (width, height), BACKDROP)
    for index, image in enumerate(images):
        row, col = divmod(index, COLS)
        x = PAD + col * (cell_w + PAD) + (cell_w - image.width) // 2
        y = PAD + row * (cell_h + PAD) + (cell_h - image.height) // 2
        gallery.alpha_composite(image, (x, y))

    out_path = OUT_DIR / "gallery.png"
    gallery.convert("RGB").save(out_path)
    print(f"wrote {out_path} ({gallery.width}x{gallery.height})")


if __name__ == "__main__":
    main()
