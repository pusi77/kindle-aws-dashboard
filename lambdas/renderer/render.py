"""Draw the dashboard PNG for a Kindle Paperwhite 3 (1072x1448, 16 greys, 300 ppi).

render(data) takes the dict stored as latest.json and never raises for bad or missing data:
each panel that can't be drawn shows "n/a" and the rest of the image is still produced.
"""

import io
import logging
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

W, H = 1072, 1448
M = 48  # outer margin
TZ = ZoneInfo("Europe/Rome")
BLACK, MID, WHITE = 0x00, 0x88, 0xFF
LEVELS = frozenset(range(0, 256, 17))  # the 16 panel greys: 0x00, 0x11 ... 0xFF
FONTS = Path(__file__).parent / "fonts"

# Cost Explorer's service names are long; the rest just lose their "Amazon "/"AWS " prefix.
SHORT_NAMES = {
    "Amazon Elastic Compute Cloud - Compute": "EC2 compute",
    "EC2 - Other": "EC2 other",
    "Amazon Relational Database Service": "RDS",
    "Amazon Simple Storage Service": "S3",
    "Amazon Virtual Private Cloud": "VPC",
    "Amazon Elastic Container Service": "ECS",
    "Amazon Elastic Kubernetes Service": "EKS",
    "Amazon Elastic Load Balancing": "Load Balancing",
    "AmazonCloudWatch": "CloudWatch",
}

log = logging.getLogger(__name__)


@lru_cache
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONTS / ("Inter-Bold.ttf" if bold else "Inter-Medium.ttf"), size)


def text(d, xy, s, size, bold=False, width=None, fill=BLACK, anchor="la"):
    """Draw s at xy, trimmed with an ellipsis to width pixels if given. Returns its width."""
    f = font(size, bold)
    if width is not None and d.textlength(s, font=f) > width:
        while s and d.textlength(s + "…", font=f) > width:
            s = s[:-1]
        s += "…"
    d.text(xy, s, font=f, fill=fill, anchor=anchor)
    return d.textlength(s, font=f)


def money(v: float) -> str:
    return f"${v:,.0f}" if abs(v) >= 1000 else f"${v:,.2f}"


def short(service: str) -> str:
    return SHORT_NAMES.get(service) or service.removeprefix("Amazon ").removeprefix("AWS ")


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(TZ)


def title(d, box, s):
    text(d, box[:2], s, 34)
    return box[1] + 46


def panel_hero(d, box, data):
    c = data["costs"]
    y = title(d, box, "Month to date")
    mtd = money(c["mtd"])
    width = box[2] - box[0]
    size = next(s for s in range(150, 60, -10) if d.textlength(mtd, font=font(s, True)) <= width)
    text(d, (box[0], y), mtd, size, bold=True)
    y += 164
    fc = c.get("forecast")
    text(d, (box[0], y), "Forecast, end of month", 30)
    text(d, (box[0], y + 42), money(fc) if fc is not None else "n/a", 56, bold=True)
    x = box[0] + (box[2] - box[0]) // 2
    text(d, (x, y), "vs same day last month", 30)
    last = c.get("last_month_same_point")
    if not last:
        text(d, (x, y + 42), "n/a", 56, bold=True)
        return
    x += text(d, (x, y + 42), f"{(c['mtd'] - last) / last * 100:+.0f}%", 56, bold=True) + 16
    text(d, (x, y + 98), f"was {money(last)}", 30, anchor="ls")


def panel_status(d, box, data):
    """One band that says "all good" or "look here", from Budgets and Cost Anomaly Detection."""
    budgets, anomalies = data.get("budgets"), data.get("anomalies")
    if budgets is None and anomalies is None:
        raise ValueError("no budget or anomaly data")
    problems, notes = [], []
    worst = max(budgets or [], key=lambda b: b["actual"] / b["limit"], default=None)
    if budgets is None:
        notes.append("Budgets n/a")
    elif worst is None:
        notes.append("No budgets")
    else:
        label = (
            f"{worst['name']}: {worst['actual'] / worst['limit']:.0%} of {money(worst['limit'])}"
        )
        if worst["actual"] >= worst["limit"]:
            problems.append(f"Over budget, {label}")
        elif (worst.get("forecast") or 0) > worst["limit"]:
            problems.append(f"Forecast over budget, {label}")
        else:
            notes.append(f"Budget {label}")
    if anomalies is None:
        notes.append("Anomalies n/a")
    elif anomalies:
        top = max(anomalies, key=lambda a: a["impact"])
        more = f" (+{len(anomalies) - 1} more)" if len(anomalies) > 1 else ""
        problems.append(f"Anomaly: {short(top['service'])} +{money(top['impact'])}{more}")
    else:
        notes.append("No anomalies")

    if problems:
        fg, bg, head = WHITE, BLACK, "LOOK HERE"
    else:
        fg, bg = BLACK, WHITE
        head = "NO ALERTS" if any("n/a" in n for n in notes) else "ALL GOOD"
    d.rounded_rectangle(box, radius=20, fill=bg, outline=BLACK, width=6)
    x, y = box[0] + 32, box[1] + 22
    text(d, (x, y), head, 52, bold=True, fill=fg)
    y += 26
    for line in (problems + notes)[:2]:
        y += 42
        text(d, (x, y), line, 32, width=box[2] - x - 32, fill=fg)


def panel_daily(d, box, data):
    days = data["costs"]["daily"][-14:]
    chart_top = title(d, box, "Daily cost, last 14 days") + 44  # room for the max label
    today = local(data["generated_at"]).date()
    top = max(x["amount"] for x in days) or 1
    base = box[3] - 40  # room for the day labels
    step = (box[2] - box[0]) / len(days)
    for i, x in enumerate(days):
        x0, x1 = box[0] + i * step + 6, box[0] + (i + 1) * step - 6
        h = (base - chart_top) * x["amount"] / top
        day = date.fromisoformat(x["date"])
        if h >= 1:  # today is still growing: grey
            d.rectangle((x0, base - h, x1, base), fill=MID if day == today else BLACK)
        cx = (x0 + x1) / 2
        text(d, (cx, base + 8), str(day.day), 28, anchor="ma")
        if x["amount"] == top:
            text(d, (cx, base - h - 8), money(top), 30, bold=True, anchor="md")
    d.line((box[0], base, box[2], base), fill=BLACK, width=4)


def panel_services(d, box, data):
    rows = data["costs"]["top_services"][:5]
    y = title(d, box, "Top services, month to date")
    top = max((r["amount"] for r in rows), default=0) or 1
    name_w, amount_w = 440, 170
    bar_x0, bar_x1 = box[0] + name_w + 16, box[2] - amount_w - 16
    for r in rows:
        text(d, (box[0], y), short(r["name"]), 30, width=name_w)
        w = (bar_x1 - bar_x0) * r["amount"] / top
        if w >= 1:
            d.rectangle((bar_x0, y + 4, bar_x0 + w, y + 28), fill=BLACK)
        text(d, (box[2], y), money(r["amount"]), 30, bold=True, anchor="ra")
        y += 42


def header(d, data):
    text(d, (M, M), "AWS costs", 48, bold=True)
    try:
        text(d, (W - M, M + 10), local(data["generated_at"]).strftime("%B %Y"), 36, anchor="ra")
    except Exception:  # noqa: BLE001, S110 - the month is decoration
        pass
    d.line((M, M + 76, W - M, M + 76), fill=BLACK, width=4)


def footer(d, data):
    """Render time, Kindle battery and last fetch, and what this dashboard costs."""

    def part(label, fn):
        try:
            return f"{label} {fn()}"
        except Exception:  # noqa: BLE001 - the footer must never break the image
            return f"{label} n/a"

    def seen():  # date only when the last fetch wasn't on the render day (e.g. the 07:00 render)
        k, now = data["kindle"], local(data["generated_at"])
        t = local(k["last_seen"])
        return f"{k['battery']}% " + t.strftime(
            "%H:%M" if t.date() == now.date() else "%-d %b %H:%M"
        )

    parts = [
        part("Updated", lambda: local(data["generated_at"]).strftime("%-d %b %H:%M")),
        part("Kindle", seen),
        # This dashboard's own cost is essentially its Cost Explorer API calls (MTD, $0.01 each).
        part("CE API", lambda: money(data["costs"]["ce_api_mtd"])),
    ]
    y = H - M - 30
    d.line((M, y - 16, W - M, y - 16), fill=BLACK, width=4)
    text(d, (M, y), " · ".join(parts), 28, width=W - 2 * M)


# Stacked top to bottom from y=TOP: (title shown if the panel fails, draw function, height,
# data source whose error message is shown next to "n/a"). Heights + gaps fill the space
# between the header line and the footer line.
TOP, GAP = 144, 20
PANELS = [
    ("Month to date", panel_hero, 336, "costs"),
    ("Budgets and anomalies", panel_status, 190, "budgets"),
    ("Daily cost, last 14 days", panel_daily, 340, "costs"),
    ("Top services, month to date", panel_services, 266, "costs"),
]


def render(data: dict) -> bytes:
    img = Image.new("L", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    header(d, data)
    y0 = TOP
    for name, fn, height, source in PANELS:
        box = (M, y0, W - M, y0 + height)
        y0 += height + GAP
        try:
            fn(d, box, data)
        except Exception as e:  # noqa: BLE001 - one bad source must not cost the whole image
            log.warning("panel %r: n/a (%r)", name, e)
            d.rectangle(box, fill=WHITE)
            reason = (data.get("errors") or {}).get(source, "")
            text(
                d,
                (box[0], title(d, box, name)),
                f"n/a {reason}".strip(),
                40,
                bold=True,
                width=box[2] - box[0],
            )
    footer(d, data)
    return to_png(img)


def to_png(img: Image.Image) -> bytes:
    """Final step: mode L, snapped to the 16 panel levels, saved as PNG colour type 0, no alpha."""
    img = img.convert("L").point(lambda v: (v + 8) // 17 * 17)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
