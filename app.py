"""
app.py

Web dashboard for the First-Mile Collection & Fleet Dispatch Tracker.
Built to demonstrate the same workflow described in README.md through a
UI a non-technical ops/recruitment audience can read at a glance, on
top of the exact same db.py / collection_tracker.py / traccar_client.py
logic used by main.py's console demo.

Run with:  python app.py
Then open: http://127.0.0.1:5050

Auth is intentionally minimal — three seeded demo accounts (see
seed_data.py / README) behind a session cookie, with a real role-based
access control layer on top (permissions.py): admin / station_lead /
field_staff, each restricted to certain menus and (for non-admins) to
their own station's data. This is a portfolio demo, not a system
holding real farmer or financial data, so a full auth stack (password
reset, rate limiting, audit log) would be effort spent in the wrong
place — but the access-control *shape* is real, because that's the
part the role is actually about.
"""

import csv
import io
import os
import secrets
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask, Response, abort, flash, g, jsonify, redirect, render_template,
    request, session, url_for,
)
from werkzeug.security import check_password_hash

import admin_data as admin
import collection_tracker as tracker
import dashboard_data as data
import erpnext_client as erp
import permissions as perms
from db import connect, init_db
from main import run_demo_day, backfill_history, simulate_next_day
from seed_data import seed

load_dotenv()

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
    """Seed and simulate a history of days if the DB doesn't exist yet —
    today (via run_demo_day, which deliberately backdates one run to
    demonstrate the SLA-breach path) plus ~13 prior days of randomized
    history, so the date-range filters have real spread to show on first
    load. Never runs again once the DB exists — from then on, only
    simulate_next_day() (the "Simulate new day" button) adds to it."""
    if not os.path.exists(DB_PATH):
        init_db(DB_PATH)
        seed(DB_PATH)
        run_demo_day(DB_PATH)
        backfill_history(DB_PATH)


def current_user():
    """The logged-in user's row, fetched fresh each request (cheap at this
    scale) so a role/station change by an admin takes effect immediately —
    cached on flask.g so one request only hits the DB once."""
    if "user" not in g:
        username = session.get("user")
        g.user = None
        if username:
            with connect(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT * FROM users WHERE username = ?", (username,)
                ).fetchone()
                g.user = dict(row) if row else None
    return g.user


def current_station_id():
    """None for admins (no scoping); the user's own station otherwise."""
    user = current_user()
    if not user or user["role"] == "admin":
        return None
    return user["station_id"]


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def require_menu(menu_key):
    """Gate a route behind a permissions.py menu key — 403s rather than
    hiding the link, so this is real enforcement, not just a hidden nav
    item someone could still reach by URL."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            user = current_user()
            effective = perms.effective_access(DB_PATH, user)
            if not perms.has_access(effective, menu_key):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.context_processor
def inject_nav():
    user = current_user()
    if not user:
        return {}
    effective = perms.effective_access(DB_PATH, user)
    return {
        "nav_menu": perms.visible_top_level(effective),
        "current_user_row": user,
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    ensure_data()
    error = None
    if request.method == "POST":
        identifier = request.form.get("identifier", "")
        password = request.form.get("password", "")
        with connect(DB_PATH) as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ? OR email = ?",
                (identifier, identifier),
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user"] = user["username"]
            effective = perms.effective_access(DB_PATH, user)
            # Only honor `next` if this user can actually reach it — a
            # deep link redirected here pre-login (e.g. ?next=/stations)
            # would otherwise 403 the instant they land back on it.
            next_url = request.args.get("next")
            top_level_urls = {url_for(k): k for k, _, _ in perms.MENU_TREE if effective.get(k)}
            if next_url in top_level_urls:
                return redirect(next_url)
            landing_key = perms.default_landing_key(effective)
            return redirect(url_for(landing_key) if landing_key else url_for("login"))
        error = "Incorrect username/email or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/")
@require_menu("dashboard")
def dashboard():
    ensure_data()
    station_id = current_station_id()
    # No range in the URL defaults to "today" — the KPI cards read as a
    # daily ops view unless the user deliberately widens the window.
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    summary = data.get_summary(DB_PATH, date_from=date_from, date_to=date_to, station_id=station_id)
    report = tracker.daily_report(db_path=DB_PATH, date_from=date_from, date_to=date_to)
    runs_trend = data.get_runs_trend(DB_PATH, station_id=station_id)
    trend_delta = None
    if len(runs_trend) >= 2:
        trend_delta = runs_trend[-1]["runs"] - runs_trend[-2]["runs"]
    return render_template(
        "dashboard.html",
        active="dashboard",
        summary=summary,
        runs_per_hub=data.get_runs_per_hub(DB_PATH, date_from=date_from, date_to=date_to, station_id=station_id),
        report=report,
        outcome_mix=data.summarize_outcomes(summary),
        product_mix=data.summarize_by_product(report),
        runs_trend=runs_trend,
        trend_delta=trend_delta,
    )


@app.route("/fleet-map")
@require_menu("fleet_map")
def fleet_map():
    ensure_data()
    station_id = current_station_id()
    return render_template(
        "fleet_map.html",
        active="fleet_map",
        hubs=data.get_hubs(DB_PATH, station_id=station_id),
        fleet=data.get_fleet_status(DB_PATH, station_id=station_id),
    )


@app.route("/runs")
@require_menu("runs")
def runs():
    ensure_data()
    station_id = current_station_id()
    effective = perms.effective_access(DB_PATH, current_user())
    filters = {
        "hub": request.args.getlist("hub"),
        "product": request.args.getlist("product"),
        "status": request.args.getlist("status"),
        "farmer": request.args.getlist("farmer"),
        "date_from": request.args.get("date_from") or "",
        "date_to": request.args.get("date_to") or "",
    }
    all_rows = data.get_runs_filtered(
        DB_PATH,
        hubs=filters["hub"],
        products=filters["product"],
        statuses=filters["status"],
        farmers=filters["farmer"],
        date_from=filters["date_from"] or None,
        date_to=filters["date_to"] or None,
        station_id=station_id,
    )

    per_page = request.args.get("per_page", type=int, default=50)
    if per_page not in (50, 100, 150):
        per_page = 50
    total = len(all_rows)
    total_pages = max(1, -(-total // per_page))  # ceil division
    page = min(max(request.args.get("page", type=int, default=1), 1), total_pages)
    start = (page - 1) * per_page
    rows = all_rows[start:start + per_page]

    # Comparison charts only make sense once 2+ boxes are checked on a
    # dimension — a single selection is just a filter, not a comparison.
    compare_product = data.compare_by_product(
        DB_PATH, filters["product"], date_from=filters["date_from"] or None,
        date_to=filters["date_to"] or None, station_id=station_id,
    ) if len(filters["product"]) >= 2 else []
    compare_farmer = data.compare_by_farmer(
        DB_PATH, filters["farmer"], date_from=filters["date_from"] or None,
        date_to=filters["date_to"] or None, station_id=station_id,
    ) if len(filters["farmer"]) >= 2 else []

    return render_template(
        "runs.html",
        active="runs",
        rows=rows,
        filters=filters,
        options=data.get_filter_options(DB_PATH),
        can_log=perms.has_access(effective, "runs.log"),
        can_export=perms.has_access(effective, "runs.export"),
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        compare_product=compare_product,
        compare_farmer=compare_farmer,
    )


@app.route("/runs/new", methods=["GET", "POST"])
@require_menu("runs.log")
def runs_new():
    ensure_data()
    station_id = current_station_id()
    vehicles = admin.list_vehicles(DB_PATH, station_id=station_id)
    farmers = data.get_farmers(DB_PATH, station_id=station_id)
    error = None

    if request.method == "POST":
        vehicle_id = request.form.get("vehicle_id", type=int)
        vehicle = admin.get_vehicle(DB_PATH, vehicle_id) if vehicle_id else None
        farmer_ids = request.form.getlist("farmer_id")
        quantities = request.form.getlist("quantity")

        # Non-admins may only log against a vehicle at their own station.
        if not vehicle or (station_id and vehicle["home_hub_id"] != station_id):
            error = "Choose a valid vehicle for your station."
        elif not farmer_ids or not any(q.strip() for q in quantities):
            error = "Add at least one farmer with a quantity collected."
        else:
            run_id = tracker.start_run(
                vehicle_id=vehicle["id"], hub_id=vehicle["home_hub_id"], db_path=DB_PATH
            )
            farmers_by_id = {f["id"]: f for f in farmers}
            logged_any = False
            for farmer_id, qty in zip(farmer_ids, quantities):
                if not farmer_id or not qty.strip():
                    continue
                farmer = farmers_by_id.get(int(farmer_id))
                if not farmer:
                    continue
                unit = "litres" if farmer["value_chain"] == "dairy" else "kg"
                tracker.log_collection(
                    run_id, farmer["id"], farmer["value_chain"], float(qty), unit, db_path=DB_PATH
                )
                logged_any = True
            if logged_any:
                flash("Collection run logged — mark it delivered from Collection Runs once it reaches the station.", "success")
                return redirect(url_for("runs"))
            error = "Add at least one farmer with a quantity collected."

    return render_template(
        "runs_new.html", active="runs", vehicles=vehicles, farmers=farmers, error=error
    )


@app.route("/runs/<int:run_id>/complete", methods=["POST"])
@require_menu("runs.log")
def runs_complete(run_id):
    ensure_data()
    station_id = current_station_id()
    with connect(DB_PATH) as conn:
        run = conn.execute("SELECT * FROM collection_runs WHERE id = ?", (run_id,)).fetchone()
    if not run or (station_id and run["hub_id"] != station_id):
        abort(404)
    status = tracker.complete_run(run_id, db_path=DB_PATH)
    flash(f"Run #{run_id} marked {status.replace('_', ' ')}.", "success")
    return redirect(url_for("runs"))


@app.route("/runs/export.csv")
@require_menu("runs.export")
def export_runs_csv():
    ensure_data()
    rows = data.get_runs_filtered(
        DB_PATH,
        hubs=request.args.getlist("hub"),
        products=request.args.getlist("product"),
        statuses=request.args.getlist("status"),
        farmers=request.args.getlist("farmer"),
        date_from=request.args.get("date_from") or None,
        date_to=request.args.get("date_to") or None,
        station_id=current_station_id(),
    )
    buf = io.StringIO()
    fieldnames = ["id", "run_date", "vehicle", "hub", "products", "farmers", "item_count",
                  "start_time", "delivery_time", "status"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=collection_runs.csv"},
    )


# ---------- Farmers ("which client brought what") ----------

@app.route("/farmers")
@require_menu("farmers")
def farmers():
    ensure_data()
    station_id = current_station_id()
    effective = perms.effective_access(DB_PATH, current_user())
    filters = {
        "date_from": request.args.get("date_from") or "",
        "date_to": request.args.get("date_to") or "",
        "value_chain": request.args.get("value_chain") or "",
    }
    rows = data.get_farmer_report(
        DB_PATH,
        station_id=station_id,
        date_from=filters["date_from"] or None,
        date_to=filters["date_to"] or None,
        value_chain=filters["value_chain"] or None,
    )
    return render_template(
        "farmers.html",
        active="farmers",
        rows=rows,
        filters=filters,
        options=data.get_filter_options(DB_PATH),
        can_export=perms.has_access(effective, "farmers.export"),
    )


@app.route("/farmers/export.csv")
@require_menu("farmers.export")
def export_farmers_csv():
    ensure_data()
    rows = data.get_farmer_report(
        DB_PATH,
        station_id=current_station_id(),
        date_from=request.args.get("date_from") or None,
        date_to=request.args.get("date_to") or None,
        value_chain=request.args.get("value_chain") or None,
    )
    buf = io.StringIO()
    fieldnames = ["id", "name", "station", "value_chain", "unit",
                  "total_quantity", "collections", "last_collection"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=farmer_report.csv"},
    )


@app.route("/api/fleet")
@require_menu("fleet_map")
def api_fleet():
    ensure_data()
    return jsonify(data.get_fleet_status(DB_PATH, station_id=current_station_id()))


@app.route("/simulate", methods=["POST"])
@login_required
def simulate():
    """Append one more simulated day on top of the existing history, for
    the demo button. Admin-only — it affects every station's data, not
    just the current user's. Deliberately does NOT wipe the database:
    hubs/farmers/vehicles/users and every previously-simulated day stay
    exactly as they were; only a new day's runs are added, with their
    own randomized numbers."""
    if current_user()["role"] != "admin":
        abort(403)
    ensure_data()
    new_date = simulate_next_day(DB_PATH)
    flash(f"Simulated a new day of collections for {new_date}.", "success")
    return redirect(request.referrer or url_for("dashboard"))


# ---------- Stations ----------

@app.route("/stations", methods=["GET", "POST"])
@require_menu("stations")
def stations():
    ensure_data()
    error = None
    if request.method == "POST":
        if not perms.has_access(perms.effective_access(DB_PATH, current_user()), "stations.manage"):
            abort(403)
        name = request.form.get("name", "").strip()
        county = request.form.get("county", "").strip()
        lat = request.form.get("latitude", type=float)
        lon = request.form.get("longitude", type=float)
        if not name or not county:
            error = "Station name and county are required."
        else:
            admin.create_station(DB_PATH, name, county, lat, lon)
            flash(f"Station \"{name}\" added.", "success")
            return redirect(url_for("stations"))
    return render_template(
        "stations.html", active="stations", stations=admin.list_stations(DB_PATH), error=error
    )


@app.route("/stations/<int:station_id>/edit", methods=["GET", "POST"])
@require_menu("stations.manage")
def station_edit(station_id):
    ensure_data()
    station = admin.get_station(DB_PATH, station_id)
    if not station:
        abort(404)
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        county = request.form.get("county", "").strip()
        lat = request.form.get("latitude", type=float)
        lon = request.form.get("longitude", type=float)
        if not name or not county:
            error = "Station name and county are required."
        else:
            admin.update_station(DB_PATH, station_id, name, county, lat, lon)
            flash(f"Station \"{name}\" updated.", "success")
            return redirect(url_for("stations"))
    return render_template("station_edit.html", active="stations", station=station, error=error)


# ---------- Fleet management ----------

@app.route("/fleet", methods=["GET", "POST"])
@require_menu("fleet")
def fleet():
    ensure_data()
    error = None
    if request.method == "POST":
        if not perms.has_access(perms.effective_access(DB_PATH, current_user()), "fleet.manage"):
            abort(403)
        plate = request.form.get("plate_or_tag", "").strip()
        device_id = request.form.get("traccar_device_id", "").strip()
        station_id = request.form.get("home_hub_id", type=int)
        if not plate or not device_id or not station_id:
            error = "Plate/tag, Traccar device ID, and station are all required."
        else:
            admin.create_vehicle(DB_PATH, plate, device_id, station_id)
            flash(f"Vehicle \"{plate}\" added.", "success")
            return redirect(url_for("fleet"))
    return render_template(
        "fleet.html", active="fleet_manage",
        vehicles=admin.list_vehicles(DB_PATH),
        station_options=admin.list_stations(DB_PATH),
        error=error,
    )


@app.route("/fleet/<int:vehicle_id>/edit", methods=["GET", "POST"])
@require_menu("fleet.manage")
def fleet_edit(vehicle_id):
    ensure_data()
    vehicle = admin.get_vehicle(DB_PATH, vehicle_id)
    if not vehicle:
        abort(404)
    error = None
    if request.method == "POST":
        plate = request.form.get("plate_or_tag", "").strip()
        device_id = request.form.get("traccar_device_id", "").strip()
        station_id = request.form.get("home_hub_id", type=int)
        if not plate or not device_id or not station_id:
            error = "Plate/tag, Traccar device ID, and station are all required."
        else:
            admin.update_vehicle(DB_PATH, vehicle_id, plate, device_id, station_id)
            flash(f"Vehicle \"{plate}\" updated — now mapped to its new station.", "success")
            return redirect(url_for("fleet"))
    return render_template(
        "fleet_edit.html", active="fleet_manage", vehicle=vehicle,
        station_options=admin.list_stations(DB_PATH), error=error,
    )


# ---------- Users ----------

@app.route("/users", methods=["GET", "POST"])
@require_menu("users")
def users():
    ensure_data()
    error = None
    if request.method == "POST":
        if not perms.has_access(perms.effective_access(DB_PATH, current_user()), "users.manage"):
            abort(403)
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "field_staff")
        station_id = request.form.get("station_id", type=int)
        if not username or not password:
            error = "Username and password are required."
        elif role != "admin" and not station_id:
            error = "Station leads and field staff must be assigned a station."
        elif admin.username_or_email_taken(DB_PATH, username, email):
            error = "That username or email is already taken."
        else:
            admin.create_user(DB_PATH, username, email, password, role,
                               None if role == "admin" else station_id)
            flash(f"User \"{username}\" created.", "success")
            return redirect(url_for("users"))
    return render_template(
        "users.html", active="users",
        users=admin.list_users(DB_PATH),
        station_options=admin.list_stations(DB_PATH),
        error=error,
    )


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@require_menu("users.manage")
def user_edit(user_id):
    ensure_data()
    target = admin.get_user(DB_PATH, user_id)
    if not target:
        abort(404)
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        role = request.form.get("role", target["role"])
        station_id = request.form.get("station_id", type=int)
        new_password = request.form.get("password", "").strip()
        if role != "admin" and not station_id:
            error = "Station leads and field staff must be assigned a station."
        elif email and admin.username_or_email_taken(DB_PATH, target["username"], email, exclude_user_id=user_id):
            error = "That email is already taken by another user."
        else:
            admin.update_user(DB_PATH, user_id, email, role,
                               None if role == "admin" else station_id,
                               new_password or None)
            flash(f"User \"{target['username']}\" updated.", "success")
            return redirect(url_for("users"))
    return render_template(
        "user_edit.html", active="users", target=target,
        station_options=admin.list_stations(DB_PATH), error=error,
    )


# ---------- ERPNext integration ----------

@app.route("/integration")
@require_menu("integration")
def integration():
    ensure_data()
    client, is_live = erp.build_client()
    return render_template(
        "integration.html",
        active="integration",
        erp_summary=client.get_summary(),
        erp_fleet=client.get_fleet_status(),
        erp_live=is_live,
        erpnext_url=os.environ.get("ERPNEXT_URL", erp.DEFAULT_URL),
    )


# ---------- Settings / Access control ----------

@app.route("/settings")
@require_menu("settings")
def settings():
    return redirect(url_for("settings_access"))


@app.route("/settings/access", methods=["GET", "POST"])
@require_menu("settings.access")
def settings_access():
    ensure_data()
    all_users = admin.list_users(DB_PATH)
    if not all_users:
        abort(404)
    selected_id = request.values.get("user_id", type=int) or all_users[0]["id"]
    target = admin.get_user(DB_PATH, selected_id)
    if not target:
        abort(404)

    if request.method == "POST":
        checked = set(request.form.getlist("menu_key"))
        perms.set_overrides(DB_PATH, selected_id, checked, target["role"])
        flash(f"Access updated for \"{target['username']}\".", "success")
        return redirect(url_for("settings_access", user_id=selected_id))

    effective = perms.effective_access(DB_PATH, target)
    return render_template(
        "settings_access.html", active="settings",
        all_users=all_users, target=target,
        menu_tree=perms.MENU_TREE, effective=effective,
        role_defaults=perms.ROLE_DEFAULTS.get(target["role"], set()),
    )


if __name__ == "__main__":
    ensure_data()
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, port=port)
