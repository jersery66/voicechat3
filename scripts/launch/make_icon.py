#!/usr/bin/env python
"""Turn a checkerboard-preview JPEG into a transparent multi-size .ico.

The source is a screenshot-style export: the "transparent" area is a light
grey / white checkerboard baked into the JPEG, not real alpha. Simple colour
keying fails because the artwork's white bands match the white cells exactly,
so the background is identified by the one colour it alone contains: the
neutral 197 grey of every other cell.

The share of 197-grey pixels inside a window slightly wider than two cells is
about 0.47 anywhere in the background and about 0 anywhere in the artwork --
the ribbon deviates from that grey everywhere, red, pale pink and white alike.
Thresholding that share therefore separates the two without ever looking at
the artwork's own colours, and because the filter is symmetric the threshold
lands on the true silhouette edge.

    python scripts/launch/make_icon.py SRC.jpg --png icon.png --ico icon.ico
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage


CELL_GREY = 197.0
WINDOW = 41            # just over two 20.5 px checkerboard cells
THRESHOLD_RATIO = 0.6  # fraction of the background's own share of dark cells
MIN_ARTWORK_PX = 5000


def build_alpha(rgb: np.ndarray) -> np.ndarray:
    """Return a boolean mask that is True on artwork, False on background."""
    channels = rgb.astype(np.float32)
    saturation = channels.max(axis=2) - channels.min(axis=2)
    grey = channels.mean(axis=2)

    # Only the checkerboard contains this exact neutral grey; the red outline,
    # the pink gradient and the white bands all sit well away from it.
    dark_cell = (np.abs(grey - CELL_GREY) < 16) & (saturation < 15)
    share = ndimage.uniform_filter(
        dark_cell.astype(np.float32), size=WINDOW, mode="nearest"
    )

    # Anchor the threshold to the background's measured share so the script
    # survives a lighter or darker export of the same artwork.
    reference = share[share > 0.3]
    background_share = float(np.median(reference)) if reference.size else 0.47
    threshold = background_share * THRESHOLD_RATIO

    artwork = share <= threshold
    artwork = ndimage.binary_closing(artwork, np.ones((5, 5)))
    artwork = ndimage.binary_opening(artwork, np.ones((5, 5)))

    labels, count = ndimage.label(artwork)
    sizes = ndimage.sum(artwork, labels, index=np.arange(1, count + 1))
    keep = np.flatnonzero(sizes >= MIN_ARTWORK_PX) + 1
    artwork = np.isin(labels, keep)

    # Pinholes from JPEG ringing inside the bands.
    filled = ndimage.binary_fill_holes(artwork)
    holes = filled & ~artwork
    hole_labels, hole_count = ndimage.label(holes)
    hole_sizes = ndimage.sum(holes, hole_labels, index=np.arange(1, hole_count + 1))
    artwork |= np.isin(hole_labels, np.flatnonzero(hole_sizes < 80) + 1)

    # Trim the soft JPEG blend that sits a couple of pixels outside the
    # silhouette, otherwise the icon carries a pale halo on dark wallpapers.
    artwork = ndimage.binary_erosion(artwork, np.ones((5, 5)))
    artwork = ndimage.binary_dilation(artwork, np.ones((3, 3)))
    return artwork


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--png", type=Path, default=None)
    parser.add_argument("--ico", type=Path, default=None)
    parser.add_argument("--size", type=int, default=1024, help="square canvas size")
    parser.add_argument("--pad", type=float, default=0.02, help="padding ratio")
    args = parser.parse_args()

    image = Image.open(args.source).convert("RGB")
    # The export carries a one-pixel light frame around the canvas; it is not
    # part of the artwork and would otherwise pin the crop box to the full image.
    margin = max(1, min(image.size) // 200)
    image = image.crop((margin, margin, image.width - margin, image.height - margin))
    rgb = np.asarray(image)

    artwork = build_alpha(rgb)
    ys, xs = np.nonzero(artwork)
    top, bottom = int(ys.min()), int(ys.max()) + 1
    left, right = int(xs.min()), int(xs.max()) + 1
    print(f"artwork bbox: x={left}..{right} y={top}..{bottom} of {image.size}")

    rgba = np.dstack([rgb, np.where(artwork, 255, 0).astype(np.uint8)])
    cropped = Image.fromarray(rgba).crop((left, top, right, bottom))

    side = max(cropped.size)
    pad = int(round(side * args.pad))
    canvas_side = side + pad * 2
    canvas = Image.new("RGBA", (canvas_side, canvas_side), (0, 0, 0, 0))
    canvas.paste(
        cropped,
        ((canvas_side - cropped.width) // 2, (canvas_side - cropped.height) // 2),
    )
    canvas = canvas.resize((args.size, args.size), Image.LANCZOS)

    if args.png:
        args.png.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(args.png)
        print(f"wrote {args.png}")

    if args.ico:
        args.ico.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(
            args.ico,
            format="ICO",
            sizes=[
                (16, 16),
                (24, 24),
                (32, 32),
                (48, 48),
                (64, 64),
                (128, 128),
                (256, 256),
            ],
        )
        print(f"wrote {args.ico}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
