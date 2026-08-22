"""
app.py

Web dashboard for the First-Mile Collection & Fleet Dispatch Tracker.
Built to demonstrate the same workflow described in README.md through a
UI a non-technical ops/recruitment audience can read at a glance, on
top of the exact same db.py / collection_tracker.py / traccar_client.py
logic used by main.py's console demo.

Run with:  python app.py
Then open: http://127.0.0.1:5050

Auth is intentionally minimal — one seeded demo account (see
seed_data.py / README) behind a session cookie. This is a portfolio
demo, not a system holding real farmer or financial data, so a full
auth stack would be effort spent in the wrong place.
"""

import csv
import io
import os
import secrets
from functools import wraps

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from db import connect, init_db
from seed_data import seed
import collection_tracker as tracker
import dashboard_data as data
from main import run_demo_day

DB_PATH = "ksc_demo.db"

def _load_secret_key() -> str:
    """
    A per-machine secret persisted to disk, so Flask's debug auto-reloader
    (which respawns the process on every file save) doesn't invalidate
    every open session. Fine for a local demo; a real deployment would
    use a properly managed secret instead.
    """
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    path = ".flask_secret"
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


app = Flask(__name__)
app.secret_key = _load_secret_key()


def ensure_data():
    """Seed and simulate a day if the DB doesn't exist yet."""
    if not os.path.exists(DB_PATH):
        init_db(DB_PATH)
        seed(DB_PATH)
        run_demo_day(DB_PATH)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    ensure_data()
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        with connect(DB_PATH) as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user"] = username
            return redirect(request.args.get("next") or url_for("dashboard"))
        error = "Incorrect username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    ensure_data()
    return render_template(
        "dashboard.html",
        active="dashboard",
        summary=data.get_summary(DB_PATH),
        runs_per_hub=data.get_runs_per_hub(DB_PATH),
        report=tracker.daily_report(db_path=DB_PATH),
    )


@app.route("/fleet-map")
@login_required
def fleet_map():
    ensure_data()
    return render_template(
        "fleet_map.html",
        active="fleet",
        hubs=data.get_hubs(DB_PATH),
        fleet=data.get_fleet_status(DB_PATH),
    )


@app.route("/runs")
@login_required
def runs():
    ensure_data()
    filters = {
        "hub": request.args.get("hub") or "",
        "product": request.args.get("product") or "",
        "status": request.args.get("status") or "",
        "run_date": request.args.get("run_date") or "",
    }
    rows = data.get_runs_filtered(
        DB_PATH,
        hub=filters["hub"] or None,
        product=filters["product"] or None,
        status=filters["status"] or None,
        run_date=filters["run_date"] or None,
    )
    return render_template(
        "runs.html",
        active="runs",
        rows=rows,
        filters=filters,
        options=data.get_filter_options(DB_PATH),
    )


@app.route("/runs/export.csv")
@login_required
def export_runs_csv():
    ensure_data()
    rows = data.get_runs_filtered(
        DB_PATH,
        hub=request.args.get("hub") or None,
        product=request.args.get("product") or None,
        status=request.args.get("status") or None,
        run_date=request.args.get("run_date") or None,
    )
    buf = io.StringIO()
    fieldnames = ["id", "run_date", "vehicle", "hub", "products", "item_count",
                  "start_time", "delivery_time", "status"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=collection_runs.csv"},
    )


@app.route("/api/fleet")
@login_required
def api_fleet():
    ensure_data()
    return jsonify(data.get_fleet_status(DB_PATH))


@app.route("/simulate", methods=["POST"])
@login_required
def simulate():
    """Wipe and re-run a fresh simulated day, for the demo button."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db(DB_PATH)
    seed(DB_PATH)
    run_demo_day(DB_PATH)
    return redirect(request.referrer or url_for("dashboard"))


if __name__ == "__main__":
    ensure_data()
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, port=port)
