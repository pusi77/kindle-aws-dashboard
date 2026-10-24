import io
import json
import struct
from pathlib import Path

import pytest
from PIL import Image

from render import LEVELS, H, W, render

FIXTURES = sorted((Path(__file__).parents[2] / "fixtures").glob("*.json"))


def chunks(png: bytes):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    while pos < len(png):
        length, kind = struct.unpack(">I4s", png[pos : pos + 8])
        yield kind, png[pos + 8 : pos + 8 + length]
        pos += 12 + length


def assert_kindle_png(png: bytes):
    found = dict(chunks(png))
    width, height, depth, colour_type = struct.unpack(">IIBB", found[b"IHDR"][:10])
    assert (width, height) == (W, H) == (1072, 1448)
    assert depth == 8
    assert colour_type == 0  # greyscale, no alpha
    assert b"tRNS" not in found and b"PLTE" not in found
    img = Image.open(io.BytesIO(png))
    assert img.mode == "L"
    assert set(img.get_flattened_data()) <= LEVELS
    assert len(LEVELS) == 16


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_fixture_renders_in_kindle_format(fixture):
    assert_kindle_png(render(json.loads(fixture.read_text())))


@pytest.mark.parametrize("data", [{}, {"costs": "garbage", "budgets": [{}], "kindle": 3}])
def test_bad_data_still_renders(data):
    assert_kindle_png(render(data))


def test_fixtures_exist():
    assert {p.stem for p in FIXTURES} >= {"normal", "over_budget", "anomaly", "missing_data"}
