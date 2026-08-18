"""
main.py

End-to-end demo: seeds sample data, runs a simulated day of first-mile
collection across KSC's hubs, checks fleet status via Traccar (mocked
unless real credentials are set), and prints/exports a daily report.

Run with:  python main.py
"""

import csv
import os

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
                    "UPDATE collection_runs SET start_time = datetime('now', '-3 hours') "
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
