"""
dashboard_data.py

Read-only aggregation queries for the web dashboard. Kept separate from
collection_tracker.py so the core domain logic (starting/completing runs,
SLA checks, idle detection) stays independent of anything presentation-
specific.
"""

from __future__ import annotations

from datetime import datetime

from db import connect
from traccar_client import build_client


def get_summary(db_path: str = "ksc_demo.db", run_date: str | None = None,
                 station_id: int | None = None) -> dict:
    date_filter = run_date or datetime.now().strftime("%Y-%m-%d")
    with connect(db_path) as conn:
        runs = conn.execute(
            "SELECT status FROM collection_runs WHERE run_date = :run_date "
            "AND (:station_id IS NULL OR hub_id = :station_id)",
            {"run_date": date_filter, "station_id": station_id},
        ).fetchall()
        vehicles = conn.execute(
            "SELECT COUNT(*) AS c FROM vehicles WHERE (:s IS NULL OR home_hub_id = :s)",
            {"s": station_id},
        ).fetchone()["c"]
        hubs = conn.execute(
            "SELECT COUNT(*) AS c FROM hubs WHERE (:s IS NULL OR id = :s)",
            {"s": station_id},
        ).fetchone()["c"]
        farmers = conn.execute(
            "SELECT COUNT(*) AS c FROM farmers WHERE (:s IS NULL OR hub_id = :s)",
            {"s": station_id},
        ).fetchone()["c"]

    total_runs = len(runs)
    sla_breaches = sum(1 for r in runs if r["status"] == "sla_breach")
    in_progress = sum(1 for r in runs if r["status"] == "in_progress")
    delivered = sum(1 for r in runs if r["status"] == "delivered")

    return {
        "run_date": date_filter,
        "total_runs": total_runs,
        "delivered": delivered,
        "sla_breaches": sla_breaches,
        "in_progress": in_progress,
        "vehicles": vehicles,
        "hubs": hubs,
        "farmers": farmers,
    }


def get_runs_per_hub(db_path: str = "ksc_demo.db", run_date: str | None = None,
                      station_id: int | None = None) -> list[dict]:
    date_filter = run_date or datetime.now().strftime("%Y-%m-%d")
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT h.name AS hub, COUNT(*) AS runs
            FROM collection_runs cr
            JOIN hubs h ON h.id = cr.hub_id
            WHERE cr.run_date = :run_date
              AND (:station_id IS NULL OR cr.hub_id = :station_id)
            GROUP BY h.name
            ORDER BY h.name
            """,
            {"run_date": date_filter, "station_id": station_id},
        ).fetchall()
        return [dict(r) for r in rows]


def get_hubs(db_path: str = "ksc_demo.db", station_id: int | None = None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, county, latitude, longitude FROM hubs
            WHERE (:station_id IS NULL OR id = :station_id)
            ORDER BY name
            """,
            {"station_id": station_id},
        ).fetchall()
        return [dict(r) for r in rows]


def get_filter_options(db_path: str = "ksc_demo.db") -> dict:
    with connect(db_path) as conn:
        hubs = [r["name"] for r in conn.execute("SELECT name FROM hubs ORDER BY name")]
        products = [r["product"] for r in conn.execute(
            "SELECT DISTINCT product FROM collection_items ORDER BY product"
        )]
        statuses = [r["status"] for r in conn.execute(
            "SELECT DISTINCT status FROM collection_runs ORDER BY status"
        )]
        dates = [r["run_date"] for r in conn.execute(
            "SELECT DISTINCT run_date FROM collection_runs ORDER BY run_date DESC"
        )]
    return {"hubs": hubs, "products": products, "statuses": statuses, "dates": dates}


def get_runs_filtered(db_path: str = "ksc_demo.db", hub: str | None = None,
                       product: str | None = None, status: str | None = None,
                       run_date: str | None = None,
                       station_id: int | None = None) -> list[dict]:
    """Run-level listing with optional filters, for the Collection Runs page.
    station_id scopes to one station regardless of the hub filter above —
    used to restrict non-admin users to their own station's runs."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT cr.id, cr.run_date, cr.start_time, cr.delivery_time, cr.status,
                   v.plate_or_tag AS vehicle, h.name AS hub,
                   GROUP_CONCAT(DISTINCT ci.product) AS products,
                   COUNT(ci.id) AS item_count
            FROM collection_runs cr
            JOIN vehicles v ON v.id = cr.vehicle_id
            JOIN hubs h ON h.id = cr.hub_id
            LEFT JOIN collection_items ci ON ci.run_id = cr.id
            WHERE (:hub IS NULL OR h.name = :hub)
              AND (:status IS NULL OR cr.status = :status)
              AND (:run_date IS NULL OR cr.run_date = :run_date)
              AND (:station_id IS NULL OR cr.hub_id = :station_id)
            GROUP BY cr.id
            HAVING (:product IS NULL OR ',' || IFNULL(products, '') || ',' LIKE '%,' || :product || ',%')
            ORDER BY cr.id DESC
            """,
            {"hub": hub or None, "product": product or None,
             "status": status or None, "run_date": run_date or None,
             "station_id": station_id},
        ).fetchall()
        return [dict(r) for r in rows]


def get_farmers(db_path: str = "ksc_demo.db", station_id: int | None = None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.name, f.value_chain, f.hub_id, h.name AS station
            FROM farmers f
            JOIN hubs h ON h.id = f.hub_id
            WHERE (:station_id IS NULL OR f.hub_id = :station_id)
            ORDER BY f.name
            """,
            {"station_id": station_id},
        ).fetchall()
        return [dict(r) for r in rows]


def get_fleet_status(db_path: str = "ksc_demo.db", station_id: int | None = None) -> list[dict]:
    """Live-ish fleet view: each vehicle's latest run plus a Traccar position."""
    client = build_client()
    with connect(db_path) as conn:
        vehicles = conn.execute(
            """
            SELECT v.id, v.plate_or_tag, v.traccar_device_id, h.name AS home_hub
            FROM vehicles v
            JOIN hubs h ON h.id = v.home_hub_id
            WHERE (:station_id IS NULL OR v.home_hub_id = :station_id)
            ORDER BY v.plate_or_tag
            """,
            {"station_id": station_id},
        ).fetchall()

        fleet = []
        for v in vehicles:
            latest_run = conn.execute(
                """
                SELECT status, run_date, hub_id
                FROM collection_runs
                WHERE vehicle_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (v["id"],),
            ).fetchone()

            position = client.get_position(v["traccar_device_id"])
            idle = bool(position and position.is_idle)

            fleet.append({
                "plate": v["plate_or_tag"],
                "home_hub": v["home_hub"],
                "run_status": latest_run["status"] if latest_run else "no runs today",
                "latitude": round(position.latitude, 4) if position else None,
                "longitude": round(position.longitude, 4) if position else None,
                "fix_time": position.fix_time.strftime("%H:%M:%S") if position else None,
                "idle": idle,
            })
        return fleet
