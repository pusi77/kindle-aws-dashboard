import base64
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def serve(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    path = Path(__file__).parents[1] / "serve" / "handler.py"
    spec = importlib.util.spec_from_file_location("serve_handler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "get_token", lambda: "s3cret")
    monkeypatch.setattr(module, "get_png", lambda: b"\x89PNG fake")
    return module


def event(auth=None, battery=None, method="GET"):
    headers = {}
    if auth is not None:
        headers["authorization"] = auth
    if battery is not None:
        headers["x-kindle-battery"] = battery
    return {"requestContext": {"http": {"method": method}}, "headers": headers}


def metrics(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if "_aws" in line]


def test_valid_token_returns_png_and_battery_metric(serve, capsys):
    r = serve.handler(event("Bearer s3cret", "84"), None)
    assert r["statusCode"] == 200
    assert r["headers"]["Content-Type"] == "image/png"
    assert base64.b64decode(r["body"]) == b"\x89PNG fake"
    assert [m["Battery"] for m in metrics(capsys)] == [84]


@pytest.mark.parametrize("auth", [None, "", "Bearer wrong", "s3cret", "Bearer s3cret "])
def test_bad_token_is_rejected_without_metric(serve, capsys, auth):
    assert serve.handler(event(auth, "84"), None) == {"statusCode": 401}
    out = capsys.readouterr().out
    assert "s3cret" not in out and "_aws" not in out


@pytest.mark.parametrize("battery", ["101", "-1", "abc", "", "8 4"])
def test_invalid_battery_is_ignored(serve, capsys, battery):
    assert serve.handler(event("Bearer s3cret", battery), None)["statusCode"] == 200
    assert metrics(capsys) == []


def test_only_get(serve):
    assert serve.handler(event("Bearer s3cret", method="POST"), None) == {"statusCode": 405}


def test_missing_image_is_503(serve, monkeypatch):
    from botocore.exceptions import ClientError

    def missing():
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    monkeypatch.setattr(serve, "get_png", missing)
    assert serve.handler(event("Bearer s3cret"), None) == {"statusCode": 503}


def test_token_is_fetched_on_first_request(serve, monkeypatch):
    """Regression: the cache used to look fresh while time.monotonic() was under the TTL, which
    is the case in a new Lambda microVM, so the token stayed empty and "Bearer " was accepted."""
    path = Path(__file__).parents[1] / "serve" / "handler.py"
    spec = importlib.util.spec_from_file_location("serve_fresh", path)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    monkeypatch.setenv("TOKEN_PARAMETER", "/kindle-dashboard/token")
    monkeypatch.setattr(fresh.time, "monotonic", lambda: 1.0)  # just booted
    monkeypatch.setattr(
        fresh._ssm, "get_parameter", lambda **kw: {"Parameter": {"Value": "s3cret"}}
    )
    monkeypatch.setattr(fresh, "get_png", lambda: b"png")
    assert fresh.handler(event("Bearer "), None) == {"statusCode": 401}
    assert fresh.handler(event("Bearer s3cret"), None)["statusCode"] == 200


def test_empty_token_never_authorises(serve, monkeypatch):
    monkeypatch.setattr(serve, "get_token", lambda: "")
    assert serve.handler(event("Bearer "), None) == {"statusCode": 401}
