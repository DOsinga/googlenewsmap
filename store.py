"""SQLite-backed cache for googlenewsmap.

Two tables that matter:
  - locations(name → lat/lon, with not_found flag for negative cache)
  - entries(url → title/publisher/description, with FK to locations.name)
"""
import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "news.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    name TEXT PRIMARY KEY,
    lat REAL,
    lon REAL,
    display_name TEXT,
    country_code TEXT,
    source TEXT,
    not_found INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    publisher TEXT,
    publisher_url TEXT,
    published_at INTEGER,
    description TEXT,
    location_name TEXT,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    FOREIGN KEY (location_name) REFERENCES locations(name)
);

CREATE INDEX IF NOT EXISTS idx_entries_last_seen ON entries(last_seen);
CREATE INDEX IF NOT EXISTS idx_entries_location ON entries(location_name);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def normalize(name: str) -> str:
    return name.lower().strip().strip(".,'\"")


def get_location(conn, name: str):
    """Return row for name, or None if not in DB at all."""
    return conn.execute(
        "SELECT * FROM locations WHERE name = ?", (normalize(name),)
    ).fetchone()


def upsert_location(conn, name, lat, lon, display_name, country_code, source, not_found=False):
    conn.execute(
        """INSERT INTO locations
           (name, lat, lon, display_name, country_code, source, not_found, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET
               lat=excluded.lat,
               lon=excluded.lon,
               display_name=excluded.display_name,
               country_code=excluded.country_code,
               source=excluded.source,
               not_found=excluded.not_found,
               updated_at=excluded.updated_at""",
        (normalize(name), lat, lon, display_name, country_code, source,
         1 if not_found else 0, int(time.time())),
    )


def upsert_entry(conn, *, url, title, publisher, publisher_url,
                 published_at, description, location_name):
    """Insert a new entry or bump last_seen on an existing one."""
    now = int(time.time())
    loc = normalize(location_name) if location_name else None
    conn.execute(
        """INSERT INTO entries
           (url, title, publisher, publisher_url, published_at, description,
            location_name, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
               last_seen=excluded.last_seen,
               location_name=COALESCE(entries.location_name, excluded.location_name)""",
        (url, title, publisher, publisher_url, published_at, description,
         loc, now, now),
    )


def recent_entries_with_location(conn, since_ts: int):
    """Pull entries seen since `since_ts` that resolved to a real location."""
    return conn.execute(
        """SELECT e.url, e.title, e.publisher, e.publisher_url,
                  e.published_at, e.last_seen,
                  l.lat, l.lon, l.display_name, l.country_code
           FROM entries e
           JOIN locations l ON l.name = e.location_name
           WHERE e.last_seen >= ? AND l.not_found = 0
           ORDER BY e.published_at DESC""",
        (since_ts,),
    ).fetchall()


def set_meta(conn, key: str, value: str):
    conn.execute(
        """INSERT INTO meta (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )


def get_meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
