"""Collect real data with your AWS profile and render it to out/ (gitignored). Read-only.

    AWS_PROFILE=default uv run python scripts/collect_local.py

Makes ~3 Cost Explorer requests ($0.01 each). Never commit out/: it holds real cost data.
"""

import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambdas" / "renderer"))

from collect import Collector, collect  # noqa: E402
from render import render  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
out = ROOT / "out"
out.mkdir(exist_ok=True)

t0 = time.perf_counter()
# The Kindle's battery metric lives where the stack is deployed.
data = collect(Collector(region="eu-west-1"))
t1 = time.perf_counter()
png = render(data)
t2 = time.perf_counter()

(out / "latest.json").write_text(json.dumps(data, indent=2) + "\n")
(out / "latest.png").write_bytes(png)
print(f"collect {t1 - t0:.2f}s, render {t2 - t1:.2f}s, {len(png) / 1024:.0f} KiB")
print(f"Cost Explorer requests: {data['ce_requests']}, errors: {data['errors'] or 'none'}")
print(f"wrote {out / 'latest.json'} and {out / 'latest.png'}")
