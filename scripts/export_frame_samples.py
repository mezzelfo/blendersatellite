"""Export a small, git-friendly selection of rendered frames for visual inspection.

Selection ("mixed: extremes + t_c"): frames 0,1,2 (start), 498..501 (around the
capture instant t_c = 499.5 dt) and 997,998,999 (end), for every satellite.

For each selected frame two files are written:
  * full/ : whole image, alpha composited on black, downscaled 1/4, JPEG q90
  * crop/ : full-resolution CROP x CROP window at a fixed pixel position
            (image centre by default), lossless PNG

Layout of the output folder:
  by_satellite/satellite_XX/{full/frame_NNNN.jpg, crop/frame_NNNN.png}   (real files)
  by_frame/frame_NNNN/{full/satellite_XX.jpg, crop/satellite_XX.png}     (relative symlinks)
  contact_sheet_crop.jpg / contact_sheet_full.jpg                (rows = satellites, cols = frames)

Run (Pillow/numpy are not project dependencies):
  uv run --no-project --with pillow --with numpy python scripts/export_frame_samples.py
"""

import argparse
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

FRAMES = [0, 1, 2, 498, 499, 500, 501, 997, 998, 999]
DOWNSCALE = 4
CROP = 512


def to_rgb_on_black(path: Path) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    return Image.alpha_composite(bg, im).convert("RGB")


def contact_sheet(tiles: dict, sats: list, frames: list, tile_w: int, tile_h: int) -> Image.Image:
    label_w, label_h = 110, 22
    sheet = Image.new("RGB", (label_w + tile_w * len(frames), label_h + tile_h * len(sats)), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for j, f in enumerate(frames):
        draw.text((label_w + j * tile_w + 4, 4), f"frame {f}", fill=(255, 255, 255))
    for i, s in enumerate(sats):
        draw.text((4, label_h + i * tile_h + tile_h // 2), s, fill=(255, 255, 255))
        for j, f in enumerate(frames):
            sheet.paste(tiles[s, f].resize((tile_w, tile_h), Image.LANCZOS), (label_w + j * tile_w, label_h + i * tile_h))
    return sheet


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=Path("output/orbits_melbourne/eevee_full_1000"))
    ap.add_argument("--out", type=Path, default=None, help="default: <run>/frame_samples")
    ap.add_argument("--crop-center", type=int, nargs=2, metavar=("ROW", "COL"), default=None,
                    help="centre of the full-res crop (default: image centre)")
    args = ap.parse_args()

    renders = args.run / "renders"
    out = args.out or args.run / "frame_samples"
    sats = sorted(p.name for p in renders.iterdir() if p.is_dir() and p.name.startswith("satellite_"))

    full_tiles, crop_tiles = {}, {}
    crop_box = None
    for s in sats:
        sat_dir = out / "by_satellite" / s
        for f in FRAMES:
            rgb = to_rgb_on_black(renders / s / f"frame_{f:04d}.png")
            w, h = rgb.size
            if crop_box is None:
                r0, c0 = args.crop_center or (h // 2, w // 2)
                crop_box = (c0 - CROP // 2, r0 - CROP // 2, c0 + CROP // 2, r0 + CROP // 2)
            small = rgb.resize((w // DOWNSCALE, h // DOWNSCALE), Image.LANCZOS)
            crop = rgb.crop(crop_box)
            stem = f"frame_{f:04d}"
            full_tiles[s, f], crop_tiles[s, f] = small, crop
            for kind, ext, img, kw in (("full", "jpg", small, {"quality": 90}), ("crop", "png", crop, {"optimize": True})):
                target = sat_dir / kind / f"{stem}.{ext}"
                target.parent.mkdir(parents=True, exist_ok=True)
                img.save(target, **kw)
                link = out / "by_frame" / stem / kind / f"{s}.{ext}"
                link.parent.mkdir(parents=True, exist_ok=True)
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(os.path.relpath(target, link.parent))
            print(f"{s} {stem} done", flush=True)

    contact_sheet(crop_tiles, sats, FRAMES, 192, 192).save(out / "contact_sheet_crop.jpg", quality=90)
    contact_sheet(full_tiles, sats, FRAMES, 256, 192).save(out / "contact_sheet_full.jpg", quality=90)
    print("crop box (left, top, right, bottom) =", crop_box)


if __name__ == "__main__":
    main()
