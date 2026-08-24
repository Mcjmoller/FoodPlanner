"""
Persistent store for FoodPlanner.

Google Sheets remains the automation's inbox/outbox, but it is a poor backing
store for an interactive app: every read is a network round-trip that needs
service-account credentials, and it is unavailable offline. This module keeps
pantry, buying list, generated plans and run history in the SQLite file that
already holds the deals cache, so the web app works with no Google dependency.

Sheets is still imported on demand (see import_from_sheets) so the phone-friendly
workflow keeps working - the sheet is a source you can pull from, not the truth.
"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "deals_cache.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pantry (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    qty        REAL,
    unit       TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS buying_list (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE,
    done     INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    schedule   TEXT NOT NULL,
    shopping   TEXT NOT NULL,
    savings    REAL NOT NULL DEFAULT 0,
    source     TEXT NOT NULL DEFAULT 'unknown'
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL NOT NULL,
    finished_at REAL,
    status      TEXT NOT NULL,
    deals_found INTEGER DEFAULT 0,
    detail      TEXT
);
"""


@contextmanager
def connect():
    """Yields a row-addressable connection, committing on clean exit."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


# ------------------------------------------------------------------
#  Pantry
# ------------------------------------------------------------------

def get_pantry():
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pantry ORDER BY name COLLATE NOCASE")]


def upsert_pantry_item(name, qty=None, unit=None):
    name = name.strip()
    if not name:
        raise ValueError("pantry item needs a name")
    with connect() as conn:
        conn.execute(
            """INSERT INTO pantry (name, qty, unit, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET qty=excluded.qty,
                                               unit=excluded.unit,
                                               updated_at=excluded.updated_at""",
            (name, qty, unit, time.time()),
        )


def delete_pantry_item(item_id):
    with connect() as conn:
        conn.execute("DELETE FROM pantry WHERE id = ?", (item_id,))


def pantry_as_lines():
    """
    Renders the pantry the way the engine's deduction logic expects: "Name 4 stk".
    Keeping the formatting here means the engine keeps its existing string parsing
    and does not need to know where the pantry is stored.
    """
    lines = []
    for item in get_pantry():
        if item["qty"]:
            qty = int(item["qty"]) if float(item["qty"]).is_integer() else item["qty"]
            lines.append(f"{item['name']} {qty} {item['unit'] or 'stk'}".strip())
        else:
            lines.append(item["name"])
    return lines


# ------------------------------------------------------------------
#  Buying list
# ------------------------------------------------------------------

def get_buying_list(include_done=True):
    sql = "SELECT * FROM buying_list"
    if not include_done:
        sql += " WHERE done = 0"
    sql += " ORDER BY done, name COLLATE NOCASE"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql)]


def add_buying_item(name):
    name = name.strip()
    if not name:
        raise ValueError("buying list item needs a name")
    with connect() as conn:
        conn.execute(
            "INSERT INTO buying_list (name, added_at) VALUES (?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (name, time.time()),
        )


def toggle_buying_item(item_id):
    with connect() as conn:
        conn.execute("UPDATE buying_list SET done = 1 - done WHERE id = ?", (item_id,))


def delete_buying_item(item_id):
    with connect() as conn:
        conn.execute("DELETE FROM buying_list WHERE id = ?", (item_id,))


def buying_as_lines():
    return [i["name"] for i in get_buying_list(include_done=False)]


# ------------------------------------------------------------------
#  Plans
# ------------------------------------------------------------------

def save_plan(schedule, shopping, savings, source="rule-based"):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO plans (created_at, schedule, shopping, savings, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (time.time(), json.dumps(schedule, ensure_ascii=False),
             json.dumps(shopping, ensure_ascii=False), savings, source),
        )
        return cur.lastrowid


def _hydrate_plan(row):
    if row is None:
        return None
    plan = dict(row)
    plan["schedule"] = json.loads(plan["schedule"])
    plan["shopping"] = json.loads(plan["shopping"])
    plan["created"] = datetime.fromtimestamp(plan["created_at"])
    return plan


def get_latest_plan():
    # Tie-break on id: time.time() is only ~15ms granular on Windows, so two
    # plans saved in the same tick share a created_at and "latest" would
    # otherwise be whichever row SQLite happened to return first.
    with connect() as conn:
        return _hydrate_plan(conn.execute(
            "SELECT * FROM plans ORDER BY created_at DESC, id DESC LIMIT 1").fetchone())


def get_plan(plan_id):
    with connect() as conn:
        return _hydrate_plan(
            conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone())


def list_plans(limit=20):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, savings, source FROM plans "
            "ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "created": datetime.fromtimestamp(r["created_at"])} for r in rows]


# ------------------------------------------------------------------
#  Runs
# ------------------------------------------------------------------

def start_run():
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (time.time(),))
        return cur.lastrowid


def finish_run(run_id, status, deals_found=0, detail=None):
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, deals_found = ?, detail = ? "
            "WHERE id = ?",
            (time.time(), status, deals_found, detail, run_id))


def list_runs(limit=10):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        run = dict(r)
        run["started"] = datetime.fromtimestamp(run["started_at"])
        run["duration"] = (run["finished_at"] - run["started_at"]) if run["finished_at"] else None
        out.append(run)
    return out


# ------------------------------------------------------------------
#  One-time migration off Google Sheets / the old JSON fallback
# ------------------------------------------------------------------

def seed_from_fallback_json():
    """
    Imports the pantry/buying cache that the pipeline used to write, so an existing
    install arrives with its lists intact. Safe to call repeatedly - inserts are
    ON CONFLICT DO NOTHING and it never overwrites an already-populated pantry.
    """
    path = os.path.join(DATA_DIR, "pantry_buying_fallback.json")
    if not os.path.exists(path):
        return 0
    if get_pantry() or get_buying_list():
        return 0

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    imported = 0
    for line in data.get("pantry", []):
        name, qty, unit = parse_pantry_line(line)
        if name:
            upsert_pantry_item(name, qty, unit)
            imported += 1
    for name in data.get("buy", []):
        if name.strip():
            add_buying_item(name)
            imported += 1
    return imported


def parse_pantry_line(line):
    """
    Splits "Æg 6 stk" into ("Æg", 6.0, "stk"). Falls back to (name, None, None)
    when no quantity is present, which the engine treats as "have some, unknown
    amount" rather than assuming a count.
    """
    import re
    line = (line or "").strip()
    if not line:
        return None, None, None
    m = re.search(r"^(.*?)\s*(\d+(?:[.,]\d+)?)\s*([a-zA-ZæøåÆØÅ]+)?\.?$", line)
    if m and m.group(2):
        name = m.group(1).strip(" ()-,")
        qty = float(m.group(2).replace(",", "."))
        unit = (m.group(3) or "stk").lower()
        if name:
            return name, qty, unit
    return line, None, None
