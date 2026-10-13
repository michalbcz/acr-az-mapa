"""Offline tests (no browser, no network):  python -m pytest -q"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from acr_capture import cli
from acr_capture.cookies import (
    describe_state, has_session_cookies, import_cookies, load_state, parse_cookie_file, parse_cookie_header,
)
from acr_capture.extract import find_hidden_links, normalize_date
from acr_capture.records import merge_record, slug_for, target_filename, write_into_report
from acr_capture.services import SERVICES, is_forbidden_click, service_key_for_url
from acr_capture.targets import collect

FIX = Path(__file__).parent / "fixtures"


def test_service_detection():
    assert service_key_for_url("https://www.facebook.com/aktivnizalohy") == "facebook"
    assert service_key_for_url("https://m.facebook.com/x") == "facebook"
    assert service_key_for_url("https://x.com/ArmadaCR") == "x"
    assert service_key_for_url("https://twitter.com/ArmadaCR") == "x"
    assert service_key_for_url("https://www.instagram.com/do_armady/") == "instagram"
    assert service_key_for_url("https://www.youtube.com/@AZTVcz") == "youtube"
    assert service_key_for_url("https://acr.mo.gov.cz/") == "web"
    assert service_key_for_url("https://notfacebook.com/") == "web"


def test_forbidden_clicks():
    assert is_forbidden_click("Přidat se ke skupině")
    assert is_forbidden_click("Join group")
    assert is_forbidden_click("Sledovat")
    assert not is_forbidden_click("Povolit všechny soubory cookie")


def test_cookie_editor_import(tmp_path):
    dest, info = import_cookies(FIX / "sample_cookie_editor_export.json", "facebook", tmp_path)
    assert info["format"] == "json_cookie_array"
    assert info["read"] == 4 and info["kept"] == 3  # google cookie filtered out
    assert info["session_ok"]
    st = load_state(dest)
    assert st and {c["name"] for c in st["cookies"]} == {"c_user", "xs", "fr"}
    fr = next(c for c in st["cookies"] if c["name"] == "fr")
    assert fr["expires"] == -1 and fr["sameSite"] == "Lax"
    assert (dest.stat().st_mode & 0o777) == 0o600
    d = describe_state(dest, SERVICES["facebook"])
    assert d["session_cookies_ok"] and "FAKE" not in json.dumps(d)


def test_netscape_import(tmp_path):
    cookies, _, fmt = parse_cookie_file(FIX / "sample_netscape.txt")
    assert fmt == "netscape_cookies_txt" and len(cookies) == 3
    ct0 = next(c for c in cookies if c["name"] == "ct0")
    assert ct0["httpOnly"] is True
    dest, info = import_cookies(FIX / "sample_netscape.txt", "x", tmp_path)
    assert info["kept"] == 2 and info["session_ok"]


def test_cookie_header():
    cookies = parse_cookie_header("Cookie: sessionid=FAKE; csrftoken=abc", ".instagram.com")
    assert has_session_cookies(cookies, SERVICES["instagram"])
    assert all(c["secure"] and c["sameSite"] == "None" for c in cookies)


def test_storage_state_roundtrip(tmp_path):
    src = tmp_path / "state.json"
    src.write_text(json.dumps({"cookies": [{"name": "li_at", "value": "FAKE", "domain": ".linkedin.com",
                                            "path": "/", "expires": -1, "httpOnly": True, "secure": True,
                                            "sameSite": "None"}], "origins": []}))
    dest, info = import_cookies(src, "linkedin", tmp_path / "secrets")
    assert info["format"] == "playwright_storage_state" and info["session_ok"]


def test_dates():
    now = datetime(2026, 10, 5, 10, 0)
    assert normalize_date("2. 10.", now) == "2026-10-02"
    assert normalize_date("14h", now) == "2026-10-04"
    assert normalize_date("3 d", now) == "2026-10-02"
    assert normalize_date("Včera", now) == "2026-10-04"
    assert normalize_date("5. října", now) == "2026-10-05"
    assert normalize_date("28. 12.", now) == "2025-12-28"
    assert normalize_date("12.9.2026", now) == "2026-09-12"
    assert normalize_date("nonsense", now) is None


def test_slug_and_filename():
    assert slug_for("https://www.facebook.com/CasopisATM") == "facebook-com-casopisatm"
    assert slug_for("https://14plogp.mo.gov.cz/aktivni-zaloha") == "14plogp-aktivni-zaloha"
    old = {"url": "u", "slug": "facebook-X-loggedin",
           "screenshot_path": "/workspace/acr-map/report/screenshots/facebook-X-loggedin.jpg"}
    assert target_filename("https://www.facebook.com/X", old) == ("facebook-X-loggedin", "facebook-X-loggedin.jpg")
    assert target_filename("https://x.com/new", None) == ("x-com-new", "x-com-new.png")


def test_merge_protects_previous_capture():
    old = {"url": "u", "status": "ok", "last_post_date": "2026-09-01", "last_post_title": "A", "notes": "n1",
           "officiality_hint": "official"}
    rec, accept = merge_record(old, {"url": "u", "status": "login_wall", "captured_at": "2026-10-05T10:00"})
    assert not accept and rec["status"] == "ok" and "playwright_refresh_failed=login_wall" in rec["notes"]
    rec, accept = merge_record(old, {"url": "u", "status": "ok", "last_post_title": "Undated guess",
                                     "last_post_date": None, "notes": "playwright"})
    assert accept and rec["last_post_title"] == "A" and rec["officiality_hint"] == "official"
    rec, accept = merge_record(old, {"url": "u", "status": "ok", "last_post_title": "B",
                                     "last_post_date": "2026-10-04", "notes": "playwright"})
    assert rec["last_post_title"] == "B" and rec["last_post_date"] == "2026-10-04"


def test_write_into_report(tmp_path):
    root = tmp_path / "report"
    (root / "data").mkdir(parents=True)
    (root / "screenshots").mkdir()
    (root / "data" / "captures.jsonl").write_text(json.dumps(
        {"url": "https://x.com/a", "slug": "x-com-a", "screenshot_path": "screenshots/x-com-a.png", "status": "ok"}))
    (root / "screenshots" / "x-com-a.png").write_bytes(b"old")
    run_shots = tmp_path / "run" / "screenshots"
    run_shots.mkdir(parents=True)
    (run_shots / "x-com-a.png").write_bytes(b"new" * 500)
    s = write_into_report(root, [{"url": "https://x.com/a", "slug": "x-com-a",
                                  "screenshot_path": "screenshots/x-com-a.png", "status": "ok"}], run_shots)
    assert s["appended"] == 1 and s["screenshots_replaced"] == 1 and s["archived"] == 1
    lines = (root / "data" / "captures.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["url"] == "https://x.com/a"
    assert (root / "screenshots" / "x-com-a.png").read_bytes().startswith(b"new")
    assert list((root / "data").glob("captures.jsonl.bak-pre-playwright-*"))


def test_hidden_links():
    html = '<a href="https://www.facebook.com/groups/123">FB</a> <a href="https://t.me/abc">tg</a>' \
           '<a href="https://www.facebook.com/sharer/sharer.php?u=x">share</a>'
    links = find_hidden_links(html, "https://example.cz/")
    assert {l["type"] for l in links} == {"facebook_group", "telegram"}


def test_targets_collect():
    t = collect([Path(__file__).parent.parent / "targets" / "urls.json"], [], only=["social"])
    assert t and all(x.service != "web" for x in t)
    assert t[0].service == "facebook"  # social priority order


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    assert "import-cookies" in capsys.readouterr().out
