"""
admin_data.py

Read/write queries backing the admin-side management pages: Stations,
Fleet Management, and Users. Kept separate from dashboard_data.py
(read-only reporting) and collection_tracker.py (collection-run domain
logic) so each module has one clear job.
"""

from __future__ import annotations

from werkzeug.security import generate_password_hash

from db import connect


# ---------- Stations (hubs) ----------

def list_stations(db_path: str = "ksc_demo.db") -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT h.id, h.name, h.county, h.latitude, h.longitude,
                   (SELECT COUNT(*) FROM vehicles v WHERE v.home_hub_id = h.id) AS vehicle_count,
                   (SELECT COUNT(*) FROM farmers f WHERE f.hub_id = h.id) AS farmer_count
            FROM hubs h
            ORDER BY h.name
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_station(db_path: str, station_id: int) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM hubs WHERE id = ?", (station_id,)).fetchone()
        return dict(row) if row else None


def create_station(db_path: str, name: str, county: str,
                    latitude: float | None, longitude: float | None) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO hubs (name, county, latitude, longitude) VALUES (?, ?, ?, ?)",
            (name, county, latitude, longitude),
        )
        return cur.lastrowid


def update_station(db_path: str, station_id: int, name: str, county: str,
                    latitude: float | None, longitude: float | None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE hubs SET name = ?, county = ?, latitude = ?, longitude = ? WHERE id = ?",
            (name, county, latitude, longitude, station_id),
        )


# ---------- Fleet (vehicles) ----------

def list_vehicles(db_path: str = "ksc_demo.db", station_id: int | None = None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT v.id, v.plate_or_tag, v.traccar_device_id, v.home_hub_id, h.name AS station
            FROM vehicles v
            JOIN hubs h ON h.id = v.home_hub_id
            WHERE (:station_id IS NULL OR v.home_hub_id = :station_id)
            ORDER BY v.plate_or_tag
            """,
            {"station_id": station_id},
        ).fetchall()
        return [dict(r) for r in rows]


def get_vehicle(db_path: str, vehicle_id: int) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
        return dict(row) if row else None


def create_vehicle(db_path: str, plate_or_tag: str, traccar_device_id: str, home_hub_id: int) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO vehicles (plate_or_tag, traccar_device_id, home_hub_id) VALUES (?, ?, ?)",
            (plate_or_tag, traccar_device_id, home_hub_id),
        )
        return cur.lastrowid


def update_vehicle(db_path: str, vehicle_id: int, plate_or_tag: str,
                    traccar_device_id: str, home_hub_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE vehicles SET plate_or_tag = ?, traccar_device_id = ?, home_hub_id = ? WHERE id = ?",
            (plate_or_tag, traccar_device_id, home_hub_id, vehicle_id),
        )


# ---------- Users ----------

def list_users(db_path: str = "ksc_demo.db") -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.email, u.role, u.station_id, h.name AS station
            FROM users u
            LEFT JOIN hubs h ON h.id = u.station_id
            ORDER BY u.username
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_user(db_path: str, user_id: int) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def username_or_email_taken(db_path: str, username: str, email: str,
                             exclude_user_id: int | None = None) -> bool:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM users
            WHERE (username = ? OR (email IS NOT NULL AND email != '' AND email = ?))
              AND (? IS NULL OR id != ?)
            """,
            (username, email, exclude_user_id, exclude_user_id),
        ).fetchone()
        return row is not None


def create_user(db_path: str, username: str, email: str, password: str,
                 role: str, station_id: int | None) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (username, email, password_hash, role, station_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, email or None, generate_password_hash(password), role, station_id),
        )
        return cur.lastrowid


def update_user(db_path: str, user_id: int, email: str, role: str,
                 station_id: int | None, new_password: str | None = None) -> None:
    with connect(db_path) as conn:
        if new_password:
            conn.execute(
                "UPDATE users SET email = ?, role = ?, station_id = ?, password_hash = ? WHERE id = ?",
                (email or None, role, station_id, generate_password_hash(new_password), user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET email = ?, role = ?, station_id = ? WHERE id = ?",
                (email or None, role, station_id, user_id),
            )
