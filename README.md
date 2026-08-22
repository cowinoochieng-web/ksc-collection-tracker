# First-Mile Collection & Fleet Dispatch Tracker

A small working prototype built to understand the operational problem
behind Kisii Smart Community's IT & Systems Developer role, before
applying — rather than just listing tools on a CV.

## What it models

KSC's Songa platform runs solar-charged electric three-wheelers doing
first-mile collection of dairy, banana, and vegetable produce from
smallholder farmers across Kisii, Nyamira, Bomet, and Narok, delivering
to hubs for onward sale, processing, or chilling. This prototype models
that exact workflow:

- **Hubs, farmers, and vehicles** — a small seeded dataset spanning all
  four counties, split across the three value chains.
- **Collection runs** — a vehicle is dispatched from a hub, picks up
  produce from several farmers, and delivers back to a hub.
- **Cold-chain SLA tracking** — dairy is time-sensitive. Each run is
  checked against a maximum collection-to-delivery window
  (`SLA_MINUTES` in `collection_tracker.py`); breaching it flags the
  run `sla_breach`. The thresholds are illustrative placeholders — a
  real deployment would take these from KSC's quality/ops team.
- **Live fleet monitoring via Traccar** — while a run is active, the
  assigned vehicle's GPS position is polled through a real Traccar
  REST API client (`traccar_client.py`, HTTP Basic Auth against
  `/api/devices` and `/api/positions`). A vehicle reporting near-zero
  speed generates an idle alert — useful for spotting a breakdown,
  an overrun battery swap, or a stuck route before it costs a batch
  of milk.
- **Daily reporting** — collected volume aggregated by hub and value
  chain, printed to console and exported to CSV in the same shape
  that would sync to a Google Sheet, Smartsheet, or an ERPNext report
  via API.

## Why it's built this way

This is a standalone prototype, not a live ERPNext customization —
it's meant to prove out the workflow logic and integration shape
quickly, without needing a provisioned ERPNext/Frappe/MariaDB stack
just to demonstrate an idea. Two things were built to map directly
onto how KSC would actually run this:

- **The SQLite schema in `db.py` is plain, portable SQL** (no
  SQLite-specific tricks) — it's written to migrate cleanly onto
  MariaDB/MySQL, or to be re-expressed as ERPNext doctypes
  (`Farmer`, `Hub`, `Vehicle`, `Collection Run`, `Collection Item`)
  with the SLA and idle-alert logic moved into server scripts /
  scheduled jobs.
- **The Traccar client is real, not simulated.** It calls the actual
  documented endpoints and auth method. Because Traccar's public demo
  server requires registering your own account (no shared demo
  credentials exist), it falls back to `MockTraccarClient` — same
  interface, plausible data — when no credentials are configured, so
  the whole pipeline runs end to end out of the box. Point it at a
  real server by setting three environment variables:

  ```bash
  export TRACCAR_URL="https://your-server"
  export TRACCAR_USER="you@example.com"
  export TRACCAR_PASSWORD="yourpassword"
  ```

## Running it

```bash
pip install -r requirements.txt
python main.py
```

This seeds a small dataset, simulates a day of collection runs across
all five vehicles (one deliberately backdated so the SLA-breach path
is visible, not just the happy path), prints a per-hub / per-value-chain
report, and writes `daily_report.csv`.

### Web dashboard

The same data and logic are also exposed as a small Flask app
(`app.py` / `dashboard_data.py` / `templates/`) — the thing to
actually open and click through:

```bash
python app.py
```

Then visit `http://127.0.0.1:5050` and sign in with the demo account
shown on the login page (**recruiter** / **ksc-demo-2026** — see
"Auth" below for why this is fine here). Three sections, in a sidebar:

- **Dashboard** — KPI cards (runs today, SLA breaches, fleet size), a
  runs-per-hub chart, an SLA-outcome donut, and the daily report
  table. A "Simulate new day" button wipes and re-runs the demo so
  the SLA-breach and idle-alert paths are easy to show live.
- **Fleet Map** — a live Leaflet map (OpenStreetMap/CARTO tiles, no
  API key needed) plotting every hub and vehicle, colour-coded by
  idle/moving status, alongside a vehicle list that pans the map on
  click. Polls `/api/fleet` every 8s.
- **Collection Runs** — the full run log, filterable by hub, value
  chain, status, and date, with an **Export CSV** button that
  downloads exactly the filtered view.

**Auth:** one seeded demo account behind a Flask session cookie —
enough to gate the app behind a login screen for a demo, not a real
access-control system. There's no real farmer or financial data
behind it, so building out proper auth (roles, password reset, rate
limiting) would be effort spent proving the wrong skill for this
exercise.

## What I'd build next with real access

- Replace the SQLite layer with ERPNext doctypes and Frappe server
  scripts, so this logic runs inside the actual system of record
  rather than alongside it.
- Push the daily report to Google Sheets via the Sheets API, or into
  a Smartsheet workflow, instead of a local CSV.
- Add a lightweight USSD flow (e.g. via Africa's Talking) for riders
  or hub operators without smartphones to confirm a collection or
  delivery — matching KSC's stated use of USSD alongside cloud apps
  for accessibility.
- Replace the demo login with real auth (role-based access for ops
  staff vs. hub operators vs. admin) tied into whatever identity
  provider KSC already uses.
