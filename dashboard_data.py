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


def get_summary(db_path: str = "ksc_demo.db", date_from: str | None = None,
                 date_to: str | None = None, station_id: int | None = None) -> dict:
    if date_from is None and date_to is None:
        date_from = date_to = datetime.now().strftime("%Y-%m-%d")
    with connect(db_path) as conn:
        runs = conn.execute(
            "SELECT status FROM collection_runs WHERE "
            "(:date_from IS NULL OR run_date >= :date_from) "
            "AND (:date_to IS NULL OR run_date <= :date_to) "
            "AND (:station_id IS NULL OR hub_id = :station_id)",
            {"date_from": date_from, "date_to": date_to, "station_id": station_id},
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
        "date_from": date_from,
        "date_to": date_to,
        "total_runs": total_runs,
        "delivered": delivered,
        "sla_breaches": sla_breaches,
        "in_progress": in_progress,
        "vehicles": vehicles,
        "hubs": hubs,
        "farmers": farmers,
    }


def get_runs_per_hub(db_path: str = "ksc_demo.db", date_from: str | None = None,
                      date_to: str | None = None, station_id: int | None = None) -> list[dict]:
    if date_from is None and date_to is None:
        date_from = date_to = datetime.now().strftime("%Y-%m-%d")
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT h.name AS hub, COUNT(*) AS runs
            FROM collection_runs cr
            JOIN hubs h ON h.id = cr.hub_id
            WHERE (:date_from IS NULL OR cr.run_date >= :date_from)
              AND (:date_to IS NULL OR cr.run_date <= :date_to)
              AND (:station_id IS NULL OR cr.hub_id = :station_id)
            GROUP BY h.name
            ORDER BY h.name
            """,
            {"date_from": date_from, "date_to": date_to, "station_id": station_id},
        ).fetchall()
        return [dict(r) for r in rows]


def summarize_outcomes(summary: dict) -> list[dict]:
    """Delivered/breach/in-progress as percentages of total_runs, for the
    donut-plus-legend-list panel on the dashboard."""
    total = summary["total_runs"] or 1
    items = [
        ("Delivered on time", summary["delivered"], "green"),
        ("SLA breach", summary["sla_breaches"], "red"),
        ("In progress", summary["in_progress"], "orange"),
    ]
    return [
        {"label": label, "count": count, "pct": round(count / total * 100), "color": color}
        for label, count, color in items
    ]


def summarize_by_product(report_rows: list[dict]) -> list[dict]:
    """Collapses the hub+product+unit report rows down to one row per
    product (summed quantity, summed run count), with each product's
    share of total run activity — the data behind the "collection mix"
    donut. Quantity units differ by product (litres vs kg), so the
    percentage is based on run count, not quantity, to stay meaningful
    across products."""
    agg: dict[str, dict] = {}
    for row in report_rows:
        product = row["product"]
        bucket = agg.setdefault(product, {"product": product, "unit": row["unit"], "quantity": 0.0, "runs": 0})
        bucket["quantity"] += row["total_quantity"]
        bucket["runs"] += row["runs"]

    total_runs = sum(b["runs"] for b in agg.values()) or 1
    result = [
        {**bucket, "pct": round(bucket["runs"] / total_runs * 100)}
        for bucket in agg.values()
    ]
    result.sort(key=lambda b: b["runs"], reverse=True)
    return result


def get_runs_trend(db_path: str = "ksc_demo.db", station_id: int | None = None,
                    days: int = 14) -> list[dict]:
    """Run count per day for the most recent `days` simulated days,
    oldest first — the data behind the Collection-runs KPI sparkline.
    Independent of the dashboard's date-range filter: it always shows
    recent history for context, not the filtered window."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT run_date, COUNT(*) AS runs
            FROM collection_runs
            WHERE (:station_id IS NULL OR hub_id = :station_id)
            GROUP BY run_date
            ORDER BY run_date DESC
            LIMIT :days
            """,
            {"station_id": station_id, "days": days},
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


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
        farmers = [r["name"] for r in conn.execute("SELECT name FROM farmers ORDER BY name")]
    return {"hubs": hubs, "products": products, "statuses": statuses, "dates": dates, "farmers": farmers}


def _in_clause(column: str, values: list[str], param_prefix: str) -> tuple[str, dict]:
    """Builds a parameterized `column IN (:p0, :p1, ...)` fragment for a
    variable-length list — sqlite3 can't bind a Python list directly to a
    single placeholder, so each value gets its own named parameter."""
    placeholders = ", ".join(f":{param_prefix}{i}" for i in range(len(values)))
    params = {f"{param_prefix}{i}": v for i, v in enumerate(values)}
    return f"{column} IN ({placeholders})", params


def _concat_match_clause(column: str, values: list[str], param_prefix: str) -> tuple[str, dict]:
    """Builds a parameterized "any of these values appears in this
    GROUP_CONCAT'd column" fragment, for HAVING clauses against the
    per-run product/farmer lists (a run can carry several of each)."""
    ors = []
    params = {}
    for i, v in enumerate(values):
        key = f"{param_prefix}{i}"
        ors.append(f"',' || IFNULL({column}, '') || ',' LIKE '%,' || :{key} || ',%'")
        params[key] = v
    return "(" + " OR ".join(ors) + ")", params


def get_runs_filtered(db_path: str = "ksc_demo.db", hubs: list[str] | None = None,
                       products: list[str] | None = None, statuses: list[str] | None = None,
                       farmers: list[str] | None = None, date_from: str | None = None,
                       date_to: str | None = None, station_id: int | None = None) -> list[dict]:
    """Run-level listing with optional filters, for the Collection Runs page.
    Each of hubs/products/statuses/farmers is a list — an empty/None list
    means "no filter on this dimension", multiple values means "match any
    of these" (multi-select checkboxes, not a single dropdown choice).
    station_id scopes to one station regardless of the hub filter above —
    used to restrict non-admin users to their own station's runs. No date
    filter (the default) means "every run ever simulated", since history
    accumulates rather than getting wiped."""
    hubs, products, statuses, farmers = hubs or [], products or [], statuses or [], farmers or []
    where = ["1=1"]
    params: dict = {}

    if hubs:
        clause, p = _in_clause("h.name", hubs, "hub")
        where.append(clause); params.update(p)
    if statuses:
        clause, p = _in_clause("cr.status", statuses, "status")
        where.append(clause); params.update(p)
    if date_from:
        where.append("cr.run_date >= :date_from"); params["date_from"] = date_from
    if date_to:
        where.append("cr.run_date <= :date_to"); params["date_to"] = date_to
    if station_id:
        where.append("cr.hub_id = :station_id"); params["station_id"] = station_id

    having = []
    if products:
        clause, p = _concat_match_clause("products", products, "product")
        having.append(clause); params.update(p)
    if farmers:
        clause, p = _concat_match_clause("farmers", farmers, "farmer")
        having.append(clause); params.update(p)

    query = f"""
        SELECT cr.id, cr.run_date, cr.start_time, cr.delivery_time, cr.status,
               v.plate_or_tag AS vehicle, h.name AS hub,
               GROUP_CONCAT(DISTINCT ci.product) AS products,
               GROUP_CONCAT(DISTINCT f.name) AS farmers,
               COUNT(ci.id) AS item_count
        FROM collection_runs cr
        JOIN vehicles v ON v.id = cr.vehicle_id
        JOIN hubs h ON h.id = cr.hub_id
        LEFT JOIN collection_items ci ON ci.run_id = cr.id
        LEFT JOIN farmers f ON f.id = ci.farmer_id
        WHERE {' AND '.join(where)}
        GROUP BY cr.id
        {'HAVING ' + ' AND '.join(having) if having else ''}
        ORDER BY cr.id DESC
    """
    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def compare_by_product(db_path: str = "ksc_demo.db", products: list[str] | None = None,
                        date_from: str | None = None, date_to: str | None = None,
                        station_id: int | None = None) -> list[dict]:
    """Per-product totals (quantity, runs, SLA breaches), scoped to the
    same date/station context as the runs list — the data behind the
    "compare products" chart when 2+ products are checked."""
    products = products or []
    if not products:
        return []
    in_clause, params = _in_clause("ci.product", products, "p")
    params.update({"date_from": date_from, "date_to": date_to, "station_id": station_id})
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT ci.product, ci.unit, SUM(ci.quantity) AS quantity,
                   COUNT(DISTINCT ci.run_id) AS runs,
                   COUNT(DISTINCT CASE WHEN cr.status = 'sla_breach' THEN cr.id END) AS sla_breaches
            FROM collection_items ci
            JOIN collection_runs cr ON cr.id = ci.run_id
            WHERE {in_clause}
              AND (:date_from IS NULL OR cr.run_date >= :date_from)
              AND (:date_to IS NULL OR cr.run_date <= :date_to)
              AND (:station_id IS NULL OR cr.hub_id = :station_id)
            GROUP BY ci.product, ci.unit
            ORDER BY ci.product
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def compare_by_farmer(db_path: str = "ksc_demo.db", farmer_names: list[str] | None = None,
                       date_from: str | None = None, date_to: str | None = None,
                       station_id: int | None = None) -> list[dict]:
    """Per-farmer totals, same shape as compare_by_product — the data
    behind the "compare clients" chart when 2+ farmers are checked."""
    farmer_names = farmer_names or []
    if not farmer_names:
        return []
    in_clause, params = _in_clause("f.name", farmer_names, "f")
    params.update({"date_from": date_from, "date_to": date_to, "station_id": station_id})
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT f.name AS farmer, ci.unit, SUM(ci.quantity) AS quantity,
                   COUNT(DISTINCT ci.run_id) AS runs,
                   COUNT(DISTINCT CASE WHEN cr.status = 'sla_breach' THEN cr.id END) AS sla_breaches
            FROM collection_items ci
            JOIN farmers f ON f.id = ci.farmer_id
            JOIN collection_runs cr ON cr.id = ci.run_id
            WHERE {in_clause}
              AND (:date_from IS NULL OR cr.run_date >= :date_from)
              AND (:date_to IS NULL OR cr.run_date <= :date_to)
              AND (:station_id IS NULL OR cr.hub_id = :station_id)
            GROUP BY f.name, ci.unit
            ORDER BY f.name
            """,
            params,
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


def get_farmer_report(db_path: str = "ksc_demo.db", station_id: int | None = None,
                       date_from: str | None = None, date_to: str | None = None,
                       value_chain: str | None = None) -> list[dict]:
    """Per-farmer ("client") breakdown of what they've supplied — the
    answer to "which client brought what": total volume, how many
    collections, and when they last delivered. Farmers with zero
    collections in the filtered window are excluded (matches on runs,
    not the farmer roster). No date filter means "all time"."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.name, h.name AS station, f.value_chain, ci.unit,
                   SUM(ci.quantity) AS total_quantity,
                   COUNT(DISTINCT ci.run_id) AS collections,
                   MAX(cr.run_date) AS last_collection
            FROM collection_items ci
            JOIN farmers f ON f.id = ci.farmer_id
            JOIN hubs h ON h.id = f.hub_id
            JOIN collection_runs cr ON cr.id = ci.run_id
            WHERE (:station_id IS NULL OR f.hub_id = :station_id)
              AND (:date_from IS NULL OR cr.run_date >= :date_from)
              AND (:date_to IS NULL OR cr.run_date <= :date_to)
              AND (:value_chain IS NULL OR f.value_chain = :value_chain)
            GROUP BY f.id, h.name, f.value_chain, ci.unit
            ORDER BY total_quantity DESC
            """,
            {"station_id": station_id, "date_from": date_from, "date_to": date_to,
             "value_chain": value_chain},
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
