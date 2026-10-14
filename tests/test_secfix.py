"""Security-fix regressions (2026-10-14 audit):
1. Parser injection: dashboard form values are sanitized before they are
   spliced into the manual-pick pipe string, and manual.parse_manual is
   first-occurrence-wins so a spliced tail cannot override capper/source.
2. POST /log-pick token gate (PARLAY_LOGPICK_TOKEN) + 64 KiB body cap.
3. Redirect guard: cross-host / scheme-downgrade redirects are refused.
"""
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_data_sec"

import dashboard  # noqa: E402
import db as dbmod  # noqa: E402
import manual  # noqa: E402
import netutil  # noqa: E402

DB = "/tmp/parlay_test_data_sec/testsec.db"
TEST_TOKEN = "TEST-ONLY-token-not-the-production-one"
TEST_PORT = 18931


def fresh_conn():
    if os.path.exists(DB):
        os.remove(DB)
    return dbmod.connect(DB)


def _seed_event(conn, espn_id, hours_from_now=5):
    now = datetime.now(timezone.utc)
    commence = (now + timedelta(hours=hours_from_now)).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO events(espn_id,sport,home,away,commence_time,status,is_final,updated_at) "
        "VALUES(?,?,?,?,?,?,0,?)",
        (espn_id, "NHL", "Toronto Maple Leafs", "Boston Bruins", commence, "pre", commence))
    conn.commit()


def _set_token(value):
    if value is None:
        os.environ.pop(dashboard.LOGPICK_TOKEN_ENV, None)
    else:
        os.environ[dashboard.LOGPICK_TOKEN_ENV] = value


# ---------------- FIX 1: parser injection ----------------

def test_parse_manual_first_occurrence_wins():
    p = manual.parse_manual(
        "PICK | capper=Jake | sport=NHL | market=moneyline | team=X | "
        "capper=Attacker | source=Covers")
    assert p["capper"] == "Jake"
    assert p["source_label"] == "Manual forward-in"  # default — Covers never lands


def test_parse_manual_normal_line_unchanged():
    p = manual.parse_manual(
        "PICK | capper=HNICaps | source=Forum | sport=NFL | market=total | "
        "selection=Over 47.5 | line=47.5 | odds=-110")
    assert p["capper"] == "HNICaps" and p["source_label"] == "Forum"
    assert p["selection"] == "Over 47.5" and p["line"] == 47.5


def test_handle_log_pick_rejects_pipe_injection():
    conn = fresh_conn()
    raised = False
    try:
        dashboard.handle_log_pick(conn, {
            "sport": "NHL", "market": "moneyline",
            "team": "Leafs | capper=Attacker | source=Covers",
            "odds": "-150", "line": "", "selection": ""})
    except ValueError:
        raised = True
    assert raised, "injected team value must raise ValueError"
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


def test_handle_log_pick_rejects_bad_values():
    conn = fresh_conn()
    base = {"sport": "NHL", "market": "moneyline", "team": "Toronto Maple Leafs",
            "odds": "-110", "line": "", "selection": ""}
    for bad in ({"sport": "NBA"}, {"market": "teaser"}, {"odds": "abc"},
                {"line": "1|x"}, {"selection": "Over\ncapper=Evil"},
                {"team": "Leafs=capper"}):
        form = dict(base)
        form.update(bad)
        try:
            dashboard.handle_log_pick(conn, form)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


def test_handle_log_pick_happy_path_still_works():
    conn = fresh_conn()
    _seed_event(conn, "sec1")
    pid, status, _eid = dashboard.handle_log_pick(conn, {
        "sport": "NHL", "market": "moneyline", "team": "Toronto Maple Leafs",
        "odds": "-115", "line": "", "selection": ""})
    assert status == "TRACKED"
    name = conn.execute(
        "SELECT s.name FROM sources s JOIN picks p ON p.source_id=s.id WHERE p.id=?",
        (pid,)).fetchone()[0]
    assert name == "Jake"


# ---------------- FIX 2: token gate + FIX 4: threaded server/body cap ------

def _post(port, form=None, raw=None):
    data = raw if raw is not None else urllib.parse.urlencode(form or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/log-pick", data=data,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _start_server():
    t = threading.Thread(target=dashboard.serve,
                         args=(lambda: dbmod.connect(DB), TEST_PORT), daemon=True)
    t.start()
    for _ in range(50):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{TEST_PORT}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    raise RuntimeError("test server did not come up")


def test_log_pick_token_gate_and_body_cap():
    fresh_conn()  # create schema in DB file before the server connects
    conn = dbmod.connect(DB)
    _seed_event(conn, "sec9")
    conn.close()
    _set_token(TEST_TOKEN)
    try:
        _start_server()
        form = {"sport": "NHL", "market": "moneyline",
                "team": "Toronto Maple Leafs", "odds": "-110"}
        code, _ = _post(TEST_PORT, form)                    # no token -> 403
        assert code == 403, code
        code, _ = _post(TEST_PORT, dict(form, t="wrong"))   # wrong token -> 403
        assert code == 403, code
        code, body = _post(TEST_PORT, dict(form, t=TEST_TOKEN))
        assert code == 200 and "Pick filed" in body, (code, body)
        code, _ = _post(TEST_PORT, raw=b"x" * (dashboard.MAX_POST_BYTES + 1))
        assert code == 413, code                            # oversized body
    finally:
        _set_token(None)


def test_log_pick_form_embeds_token_only_when_set():
    _set_token(TEST_TOKEN)
    try:
        form_html = dashboard._log_pick_form()
        assert 'name="t"' in form_html and TEST_TOKEN in form_html
    finally:
        _set_token(None)
    form_html = dashboard._log_pick_form()
    assert 'name="t"' not in form_html


# ---------------- FIX 3: redirect guard ----------------

def test_redirect_guard_same_host_only():
    h = netutil.SameHostRedirectHandler()
    req = urllib.request.Request("https://example.com/a")
    ok = h.redirect_request(req, None, 302, "Found", {}, "https://example.com/b")
    assert ok is not None and ok.full_url == "https://example.com/b"
    rel = h.redirect_request(req, None, 302, "Found", {}, "/b")
    assert rel is not None and rel.full_url == "https://example.com/b"
    cross = h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/b")
    assert cross is None
    sub = h.redirect_request(req, None, 302, "Found", {}, "https://sub.example.com/b")
    assert sub is None
    down = h.redirect_request(req, None, 302, "Found", {}, "http://example.com/b")
    assert down is None
