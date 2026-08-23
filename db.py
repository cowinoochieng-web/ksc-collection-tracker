"""
db.py

SQLite schema and helpers for the First-Mile Collection & Fleet
Dispatch Tracker.

SQLite is used here purely so this prototype runs with zero external
dependencies. The schema below is deliberately written in
vanilla, portable SQL so it maps directly onto MariaDB/MySQL (or an
ERPNext doctype set) with no real changes beyond the connection layer -
that migration path is described in README.md.
"""

import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS hubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    county TEXT NOT NULL,
    latitude REAL,
    longitude REAL
);

CREATE TABLE IF NOT EXISTS farmers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    hub_id INTEGER NOT NULL REFERENCES hubs(id),
    value_chain TEXT NOT NULL CHECK (value_chain IN ('dairy', 'banana', 'vegetable'))
);

CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY,
    plate_or_tag TEXT NOT NULL UNIQUE,
    traccar_device_id TEXT NOT NULL,
    home_hub_id INTEGER NOT NULL REFERENCES hubs(id)
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY,
    run_date TEXT NOT NULL,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    hub_id INTEGER NOT NULL REFERENCES hubs(id),
    start_time TEXT NOT NULL,
    delivery_time TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'delivered', 'sla_breach'))
);

CREATE TABLE IF NOT EXISTS collection_items (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES collection_runs(id),
    farmer_id INTEGER NOT NULL REFERENCES farmers(id),
    product TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'field_staff'
        CHECK (role IN ('admin', 'station_lead', 'field_staff')),
    station_id INTEGER REFERENCES hubs(id)
);

-- Per-user overrides on top of the role's default menu access. A row
-- here always wins over the role default for that (user, menu_key)
-- pair; no row means "use the role default". See permissions.py.
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    menu_key TEXT NOT NULL,
    allowed INTEGER NOT NULL CHECK (allowed IN (0, 1)),
    UNIQUE(user_id, menu_key)
);
"""


@contextmanager
def connect(db_path: str = "ksc_demo.db"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = "ksc_demo.db"):
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
