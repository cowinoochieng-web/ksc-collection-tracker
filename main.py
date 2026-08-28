"""
main.py

End-to-end demo: seeds sample data, runs a simulated day of first-mile
collection across KSC's hubs, checks fleet status via Traccar (mocked
unless real credentials are set), and prints/exports a daily report.

Run with:  python main.py
"""

import csv
import os
import random
from datetime import date, timedelta

from db import init_db, connect
from seed_data import seed
import collection_tracker as tracker


def run_demo_day(db_path: str = "ksc_demo.db"):
    with connect(db_path) as conn:
        vehicles = conn.execute("SELECT * FROM vehicles").fetchall()
        farmers_by_hub = {}
        for f in conn.execute("SELECT * FROM farmers").fetchall():
            farmers_by_hub.setdefault(f["hub_id"], []).append(f)

    print("\n=== Starting simulated collection runs ===\n")

    for idx, vehicle in enumerate(vehicles):
        hub_id = vehicle["home_hub_id"]
        run_id = tracker.start_run(vehicle_id=vehicle["id"], hub_id=hub_id, db_path=db_path)
        farmers = farmers_by_hub.get(hub_id, [])

        if idx == 0:
            # Backdate the first run's start_time so this demo can show
            # the SLA-breach path firing, not just the happy path.
            with connect(db_path) as conn:
                conn.execute(
                    "UPDATE collection_runs SET start_time = datetime('now', 'localtime', '-3 hours') "
                    "WHERE id = ?",
                    (run_id,),
                )
            print(f"  [demo] backdating {vehicle['plate_or_tag']}'s run by 3 hours to "
                  f"exercise the SLA-breach check")

        for farmer in farmers:
            product = farmer["value_chain"]
            unit = "litres" if product == "dairy" else "kg"
            quantity = 12.5 if product == "dairy" else 20.0
            tracker.log_collection(run_id, farmer["id"], product, quantity, unit, db_path=db_path)
            print(f"  [{vehicle['plate_or_tag']}] collected {quantity} {unit} of {product} "
                  f"from {farmer['name']}")

        alert = tracker.check_vehicle_idle(vehicle["id"], db_path=db_path)
        if alert:
            print(f"  {alert}")

        status = tracker.complete_run(run_id, db_path=db_path)
        print(f"  -> Run {run_id} for {vehicle['plate_or_tag']} closed as: {status}\n")


def simulate_day(run_date: str, db_path: str = "ksc_demo.db", vary: bool = True) -> None:
    """
    Build one already-closed day of collection runs stamped to `run_date`
    - one run per vehicle, covering its home hub's farmers - using
    simulate_completed_run so backdated days don't misfire the SLA check
    against real wall-clock time. Quantities and durations are randomized
    (unless vary=False) so each simulated day's numbers actually differ,
    the way a real day-to-day operation would. This never touches other
    dates: it only ever adds a new day's rows on top of what's already
    there.
    """
    with connect(db_path) as conn:
        vehicles = conn.execute("SELECT * FROM vehicles").fetchall()
        farmers_by_hub = {}
        for f in conn.execute("SELECT * FROM farmers").fetchall():
            farmers_by_hub.setdefault(f["hub_id"], []).append(f)

    for vehicle in vehicles:
        hub_id = vehicle["home_hub_id"]
        farmers = farmers_by_hub.get(hub_id, [])
        if not farmers:
            continue

        items = []
        for farmer in farmers:
            product = farmer["value_chain"]
            unit = "litres" if product == "dairy" else "kg"
            base = 12.5 if product == "dairy" else 20.0
            quantity = round(base * random.uniform(0.7, 1.3), 1) if vary else base
            items.append((farmer["id"], product, quantity, unit))

        # Every hub always has a dairy farmer, so the tightest SLA in
        # play is dairy's 120-minute window (see SLA_MINUTES) on every
        # run. Stay comfortably under that most of the time, and go
        # well over it ~15% of the time - keeps the breach path visible
        # across a run of simulated days instead of only on the one
        # hand-crafted demo run.
        duration = random.randint(150, 260) if (vary and random.random() < 0.15) \
            else random.randint(20, 100) if vary else 45

        tracker.simulate_completed_run(
            vehicle["id"], hub_id, run_date, items, db_path=db_path, duration_minutes=duration
        )


def backfill_history(db_path: str = "ksc_demo.db", days: int = 13) -> None:
    """Seed `days` days of history before today, so date-range filters
    have real historical spread to show on first load."""
    today = date.today()
    for offset in range(days, 0, -1):
        simulate_day((today - timedelta(days=offset)).isoformat(), db_path=db_path)


def simulate_next_day(db_path: str = "ksc_demo.db") -> str:
    """
    Append one more day on top of whatever's already in the database -
    the "constant" history (everything already simulated) is left
    untouched; only a new day's numbers are added, and those numbers
    vary each time. Used by the dashboard's "Simulate new day" button,
    which used to wipe and restart from scratch - this instead grows
    the dataset the way a real operation's data would.
    """
    with connect(db_path) as conn:
        row = conn.execute("SELECT MAX(run_date) AS d FROM collection_runs").fetchone()
    today = date.today()
    if row["d"] is None:
        next_date = today
    else:
        next_date = max(date.fromisoformat(row["d"]) + timedelta(days=1), today)
    simulate_day(next_date.isoformat(), db_path=db_path)
    return next_date.isoformat()


def print_and_export_report(db_path: str = "ksc_demo.db", csv_path: str = "daily_report.csv"):
    report = tracker.daily_report(db_path=db_path)

    print("=== Daily Collection Report ===\n")
    if not report:
        print("No collections recorded for today.")
        return

    header = f"{'Hub':<14}{'Product':<12}{'Total':>10}  {'Unit':<8}{'Runs':>6}{'SLA Breaches':>15}"
    print(header)
    print("-" * len(header))
    for row in report:
        print(f"{row['hub']:<14}{row['product']:<12}{row['total_quantity']:>10.1f}  "
              f"{row['unit']:<8}{row['runs']:>6}{row['sla_breaches']:>15}")

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=report[0].keys())
        writer.writeheader()
        writer.writerows(report)
    print(f"\nExported to {csv_path} (this is the shape that would sync to a Google Sheet, "
          f"Smartsheet, or an ERPNext report via API).")


if __name__ == "__main__":
    db_path = "ksc_demo.db"
    if os.path.exists(db_path):
        os.remove(db_path)  # fresh run each time for a clean demo

    init_db(db_path)
    seed(db_path)
    run_demo_day(db_path)
    print_and_export_report(db_path)
