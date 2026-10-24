"""Render every fixture to docs/preview/<name>.png, plus overview.png side by side. No AWS calls."""

import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambdas" / "renderer"))

from render import H, W, font, render  # noqa: E402

out = ROOT / "docs" / "preview"
out.mkdir(parents=True, exist_ok=True)
pngs = {}
ORDER = ["normal", "over_budget", "anomaly", "missing_data"]  # overview order; others go last
fixtures = sorted((ROOT / "fixtures").glob("*.json"))
for fixture in sorted(fixtures, key=lambda p: ORDER.index(p.stem) if p.stem in ORDER else 99):
    pngs[fixture.stem] = png = render(json.loads(fixture.read_text()))
    (out / f"{fixture.stem}.png").write_bytes(png)
    print(f"{out / fixture.stem}.png  {len(png) / 1024:.0f} KiB")

# Overview for the README/article: half size, 2 per row, captioned. Not a Kindle image.
w, h, pad, cap = W // 2, H // 2, 32, 56
cols = 2
rows = -(-len(pngs) // cols)
sheet = Image.new("L", (cols * w + (cols + 1) * pad, rows * (h + cap) + (rows + 1) * pad), 0xEE)
d = ImageDraw.Draw(sheet)
for i, (name, png) in enumerate(pngs.items()):
    x = pad + (i % cols) * (w + pad)
    y = pad + (i // cols) * (h + cap + pad)
    sheet.paste(Image.open(io.BytesIO(png)).resize((w, h), Image.Resampling.LANCZOS), (x, y))
    d.rectangle((x - 1, y - 1, x + w, y + h), outline=0x88)
    d.text((x, y + h + 14), name.replace("_", " "), font=font(30, True), fill=0)
sheet.save(out / "overview.png", optimize=True)
print(f"{out / 'overview.png'}")
