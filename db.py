"""SQLite ledger (WAL). Append-only picks: rows are never edited or deleted;
status transitions are the only mutation, and late picks are stored, not dropped."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT, url TEXT,
  status TEXT DEFAULT 'unknown', notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cappers (
  id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES sources(id),
  name TEXT NOT NULL, external_ref TEXT, claimed_record TEXT,
  first_seen TEXT, last_seen TEXT, UNIQUE(source_id, name));
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, espn_id TEXT NOT NULL, sport TEXT NOT NULL,
  home TEXT, away TEXT, commence_time TEXT, status TEXT,
  home_score INTEGER, away_score INTEGER, is_final INTEGER DEFAULT 0,
  updated_at TEXT, UNIQUE(sport, espn_id));
CREATE TABLE IF NOT EXISTS picks (
  id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES sources(id),
  capper_id INTEGER REFERENCES cappers(id), event_id INTEGER REFERENCES events(id),
  sport TEXT, market TEXT NOT NULL, selection TEXT NOT NULL, team TEXT,
  line REAL, odds_claimed INTEGER, odds_available INTEGER, units_claimed REAL,
  posted_at TEXT, captured_at TEXT NOT NULL, commence_time TEXT,
  status TEXT NOT NULL DEFAULT 'TRACKED',  -- TRACKED | EXCLUDED_LATE | PIPELINE_TEST
  exclusion_reason TEXT, raw_text TEXT, raw_hash TEXT, external_ref TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS odds_snapshots (
  id INTEGER PRIMARY KEY, event_id INTEGER REFERENCES events(id),
  captured_at TEXT NOT NULL, provider TEXT DEFAULT 'ESPN/DK',
  home_ml INTEGER, away_ml INTEGER, spread REAL,
  spread_home_price INTEGER, spread_away_price INTEGER,
  total REAL, over_price INTEGER, under_price INTEGER, raw_json TEXT);
CREATE TABLE IF NOT EXISTS closing_lines (
  id INTEGER PRIMARY KEY, event_id INTEGER UNIQUE REFERENCES events(id),
  snapshot_id INTEGER REFERENCES odds_snapshots(id), captured_at TEXT,
  label TEXT, home_ml INTEGER, away_ml INTEGER, spread REAL, total REAL);
CREATE TABLE IF NOT EXISTS grades (
  id INTEGER PRIMARY KEY, pick_id INTEGER UNIQUE REFERENCES picks(id),
  graded_at TEXT NOT NULL, result TEXT NOT NULL,  -- win|loss|push|void|ungraded
  profit_units REAL, closing_odds INTEGER, clv_pct REAL,
  is_pipeline_test INTEGER DEFAULT 0, notes TEXT);
CREATE TABLE IF NOT EXISTS suggestions (
  id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, legs_json TEXT,
  status TEXT DEFAULT 'paper', result TEXT, profit_units REAL, notes TEXT);
CREATE TABLE IF NOT EXISTS ingest_log (
  id INTEGER PRIMARY KEY, ran_at TEXT NOT NULL, source TEXT, ok INTEGER,
  detail TEXT);
CREATE TABLE IF NOT EXISTS watchlist (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, source_label TEXT NOT NULL DEFAULT 'Manual forward-in',
  kind TEXT NOT NULL DEFAULT 'manual', handle TEXT, status TEXT NOT NULL DEFAULT 'watching',
  notes TEXT, created_at TEXT NOT NULL, UNIQUE(name, source_label));
-- History / baseline layer (context ONLY -- never mixed into the picks ledger,
-- never read by records/consensus; see history.py).
CREATE TABLE IF NOT EXISTS hist_events (
  id INTEGER PRIMARY KEY, sport TEXT NOT NULL, espn_id TEXT NOT NULL,
  season_label TEXT, home TEXT, away TEXT, commence_time TEXT, status TEXT,
  home_score INTEGER, away_score INTEGER, is_final INTEGER DEFAULT 0,
  home_ml INTEGER, away_ml INTEGER, spread REAL, total REAL,
  fetched_at TEXT, UNIQUE(sport, espn_id));
CREATE TABLE IF NOT EXISTS hist_results (
  id INTEGER PRIMARY KEY, hist_event_id INTEGER UNIQUE REFERENCES hist_events(id),
  sport TEXT, home_score INTEGER, away_score INTEGER,
  winner_side TEXT,  -- home | away | tie
  total_score REAL, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS baselines (
  league TEXT NOT NULL, scope TEXT NOT NULL, games_n INTEGER,
  computed_at TEXT, metrics_json TEXT, PRIMARY KEY(league, scope));
"""

# Stage 2-4 additive columns (existing DBs are migrated in place, never rebuilt).
_SUGGESTION_COLS = {
    "stake_units": "REAL", "fair_decimal": "REAL", "fair_american": "INTEGER",
    "implied_pct": "REAL", "haircut_pct": "REAL", "is_sgp": "INTEGER DEFAULT 0",
    "families": "TEXT", "graded_at": "TEXT", "slip_text": "TEXT",
}


def _migrate(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(suggestions)")}
    for name, typ in _SUGGESTION_COLS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE suggestions ADD COLUMN {name} {typ}")
    conn.commit()


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connect(path=None):
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def raw_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def upsert_source(conn, name, kind, url, status, notes=""):
    conn.execute(
        "INSERT INTO sources(name,kind,url,status,notes,created_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET status=excluded.status, notes=excluded.notes",
        (name, kind, url, status, notes, now_iso()))
    conn.commit()
    return conn.execute("SELECT id FROM sources WHERE name=?", (name,)).fetchone()[0]


def upsert_capper(conn, source_id, name, claimed_record=None, external_ref=None):
    ts = now_iso()
    conn.execute(
        "INSERT INTO cappers(source_id,name,external_ref,claimed_record,first_seen,last_seen) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,name) DO UPDATE SET "
        "claimed_record=COALESCE(excluded.claimed_record,claimed_record), last_seen=excluded.last_seen",
        (source_id, name, external_ref, claimed_record, ts, ts))
    conn.commit()
    return conn.execute("SELECT id FROM cappers WHERE source_id=? AND name=?",
                         (source_id, name)).fetchone()[0]


def insert_pick(conn, pick):
    """Insert a pick, applying the fraud rule: captured_at must be < commence_time.
    Late picks are STORED with status EXCLUDED_LATE and never count in records.
    Picks flagged pipeline_test are stored as PIPELINE_TEST and also never count."""
    captured = pick["captured_at"]
    commence = pick.get("commence_time")
    status = pick.get("status") or "TRACKED"
    reason = pick.get("exclusion_reason")
    if status == "TRACKED" and commence and captured >= commence:
        status, reason = "EXCLUDED_LATE", "captured_at >= commence_time (past-posting guard)"
    conn.execute(
        "INSERT INTO picks(source_id,capper_id,event_id,sport,market,selection,team,line,"
        "odds_claimed,odds_available,units_claimed,posted_at,captured_at,commence_time,status,"
        "exclusion_reason,raw_text,raw_hash,external_ref,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pick.get("source_id"), pick.get("capper_id"), pick.get("event_id"), pick.get("sport"),
         pick["market"], pick["selection"], pick.get("team"), pick.get("line"),
         pick.get("odds_claimed"), pick.get("odds_available"), pick.get("units_claimed"),
         pick.get("posted_at"), captured, commence, status, reason,
         pick.get("raw_text"), raw_hash(pick.get("raw_text") or json.dumps(pick, default=str)),
         pick.get("external_ref"), now_iso()))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0], status


# ---------------- Watchlist (add-a-capper mechanism, Stage 2) ----------------

def add_watchlist(conn, name, source_label="Manual forward-in", kind="manual",
                  handle=None, notes=None):
    """Register a capper Jake wants tracked. Takes effect immediately: the
    manual forward-in path auto-creates any capper name, so a watchlist row is
    the bookkeeping that says 'this name matters' + where their picks come from.
    Adding one = a single CLI command (app.py watch-add) — under a minute."""
    conn.execute(
        "INSERT INTO watchlist(name,source_label,kind,handle,status,notes,created_at) "
        "VALUES(?,?,?,?, 'watching', ?, ?) ON CONFLICT(name,source_label) DO UPDATE SET "
        "kind=excluded.kind, handle=COALESCE(excluded.handle,handle), "
        "notes=COALESCE(excluded.notes,notes)",
        (name, source_label, kind, handle, notes, now_iso()))
    conn.commit()
    return conn.execute("SELECT id FROM watchlist WHERE name=? AND source_label=?",
                        (name, source_label)).fetchone()[0]


def list_watchlist(conn):
    return conn.execute("SELECT * FROM watchlist ORDER BY name").fetchall()
