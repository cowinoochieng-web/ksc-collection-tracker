"""
collection_tracker.py

Core logic for the First-Mile Collection & Fleet Dispatch Tracker.

Models the real workflow described in KSC's operations: a Songa
e-mobility vehicle does a collection run, picking up dairy/banana/
vegetable produce from several farmers, then delivers it to a hub.

Two things this adds beyond simple record-keeping, aimed squarely at
what the IT & Systems Developer role is meant to support:

1. Cold-chain SLA tracking: dairy is time-sensitive. Each product has
   an assumed maximum time-to-hub window (SLA_MINUTES below - these
   are illustrative placeholders; a real deployment would source them
   from KSC's actual quality/ops team). A run that exceeds its
   tightest product's window is flagged sla_breach.

2. Live fleet monitoring via Traccar: while a run is in progress, we
   can poll the assigned vehicle's GPS position. A vehicle reporting
   ~0 speed for an extended stretch generates an "idle" alert -
   useful for spotting breakdowns, battery swaps taking too long, or
   route problems before they become a spoiled batch of milk.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from db import connect
from traccar_client import build_client

# Illustrative cold-chain / freshness windows, in minutes, from
# collection to hub delivery. Real thresholds should come from KSC's
# quality team - dairy is the tightest given spoilage risk.
SLA_MINUTES = {
    "dairy": 120,
    "vegetable": 300,
    "banana": 480,
}


def start_run(vehicle_id: int, hub_id: int, db_path: str = "ksc_demo.db") -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO collection_runs (run_date, vehicle_id, hub_id, start_time, status) "
            "VALUES (date('now'), ?, ?, datetime('now'), 'in_progress')",
            (vehicle_id, hub_id),
        )
        return cur.lastrowid


def log_collection(run_id: int, farmer_id: int, product: str, quantity: float, unit: str,
                    db_path: str = "ksc_demo.db") -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO collection_items (run_id, farmer_id, product, quantity, unit) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, farmer_id, product, quantity, unit),
        )


def _tightest_sla(conn, run_id: int) -> Optional[int]:
    rows = conn.execute(
        "SELECT DISTINCT product FROM collection_items WHERE run_id = ?", (run_id,)
    ).fetchall()
    windows = [SLA_MINUTES.get(r["product"]) for r in rows if r["product"] in SLA_MINUTES]
    return min(windows) if windows else None


def complete_run(run_id: int, db_path: str = "ksc_demo.db") -> str:
    """Mark a run delivered, checking it against its tightest product SLA."""
    with connect(db_path) as conn:
        run = conn.execute("SELECT * FROM collection_runs WHERE id = ?", (run_id,)).fetchone()
        start = datetime.fromisoformat(run["start_time"])
        now = datetime.now()
        elapsed_minutes = (now - start).total_seconds() / 60

        sla = _tightest_sla(conn, run_id)
        status = "delivered"
        if sla is not None and elapsed_minutes > sla:
            status = "sla_breach"

        conn.execute(
            "UPDATE collection_runs SET delivery_time = ?, status = ? WHERE id = ?",
            (now.isoformat(timespec="seconds"), status, run_id),
        )
        return status


def check_vehicle_idle(vehicle_id: int, idle_speed_threshold_minutes: int = 10,
                        db_path: str = "ksc_demo.db") -> Optional[str]:
    """
    Poll Traccar for a vehicle's current position. Returns an alert
    message if it looks stationary mid-route, else None.
    Uses MockTraccarClient automatically if no live credentials are set
    (see traccar_client.build_client).
    """
    client = build_client()
    with connect(db_path) as conn:
        vehicle = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()

    position = client.get_position(vehicle["traccar_device_id"])
    if position is None:
        return f"No Traccar position available for {vehicle['plate_or_tag']}."

    if position.is_idle:
        return (
            f"ALERT: {vehicle['plate_or_tag']} appears stationary "
            f"(last fix {position.fix_time:%H:%M}, near {position.latitude:.4f}, "
            f"{position.longitude:.4f}) — check for breakdown, battery swap delay, or route issue."
        )
    return None


def daily_report(run_date: Optional[str] = None, db_path: str = "ksc_demo.db") -> list[dict]:
    """
    Aggregate collected volume by hub and value chain for a given day
    (defaults to today), plus a count of SLA breaches - the numbers a
    daily ops standup, or a synced Google Sheet / Smartsheet, would want.
    """
    with connect(db_path) as conn:
        date_filter = run_date or datetime.now().strftime("%Y-%m-%d")
        rows = conn.execute(
            """
            SELECT h.name AS hub, ci.product, ci.unit,
                   SUM(ci.quantity) AS total_quantity,
                   COUNT(DISTINCT cr.id) AS runs,
                   SUM(CASE WHEN cr.status = 'sla_breach' THEN 1 ELSE 0 END) AS sla_breaches
            FROM collection_items ci
            JOIN collection_runs cr ON cr.id = ci.run_id
            JOIN hubs h ON h.id = cr.hub_id
            WHERE cr.run_date = ?
            GROUP BY h.name, ci.product, ci.unit
            ORDER BY h.name, ci.product
            """,
            (date_filter,),
        ).fetchall()
        return [dict(r) for r in rows]
