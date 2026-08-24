# First-Mile Collection & Fleet Dispatch Tracker

A small working prototype built to understand the operational problem
behind Kisii Smart Community and Songa Mobility.

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

Then visit `http://127.0.0.1:5050` and sign in with one of three demo
accounts (password for all: **ksc-demo-2026**), each showing a
different slice of the system:

| Username        | Role          | Scope                        |
|------------------|---------------|-------------------------------|
| `recruiter`      | Admin         | Every station, every menu     |
| `ogembo.lead`    | Station lead  | Ogembo Hub only                |
| `ogembo.staff`   | Field staff   | Ogembo Hub, log-only           |

Sign-in accepts either the username or its `@ksc-demo.local` email.

A sun/moon icon in the top right of every page toggles dark mode —
useful for field staff logging collections in the evening. It's
theme-variable-driven (`static/style.css`), applies instantly with no
page reload, remembers your choice in `localStorage`, and defaults to
your OS's light/dark preference on first visit.

**Pages** (sidebar shown depends on the signed-in user's access — see
"Access control" below):

- **Dashboard** — KPI cards, a runs-per-hub chart, and an SLA-outcome
  donut with an explicit colour+shape+count key underneath (not just
  colour — usable if you can't distinguish red from green). Scoped to
  one station for non-admins. Admin-only "Simulate new day" button
  wipes and re-simulates so the SLA-breach / idle-alert paths are easy
  to show live.
- **Fleet Map** — a live Leaflet map plotting every hub and vehicle,
  colour-coded by idle/moving status, with a Google-Maps-style layer
  switcher (top right) between **Streets**, **Satellite** (Esri World
  Imagery), **Terrain** (OpenTopoMap), and **Dark**, plus a labels
  overlay for a satellite/hybrid view — all free, keyless tile
  sources. Polls `/api/fleet` every 8s.
- **Collection Runs** — the full run log, filterable and CSV-exportable,
  plus a **Log new collection** form for entering a real run (pick a
  vehicle, add one or more farmer + quantity lines) and a **Mark
  delivered** action that closes it out and runs the SLA check —
  a genuine data-entry path, not just the auto-simulated demo day.
- **Fleet Management** — add vehicles and reassign an existing one to
  a different station (what actually re-maps a vehicle on the Fleet
  Map).
- **Stations** — register a new station or edit an existing one's
  name, county, and map coordinates.
- **Users** — create staff accounts, assign a role and station, and
  transfer a user to a different station by editing them.
- **Settings → Access Control** — every menu and sub-menu in the
  system as checkboxes, per user. A checked box grants that user
  access regardless of their role's default; an orange note shows
  wherever a user's access has been changed from their role default.

**Access control** (`permissions.py`): three roles — **admin** (
everything), **station_lead** (their station's dashboard/map/runs),
**field_staff** (collection logging only, no dashboards or reports —
matching how KSC's actual field/lead/admin split works). Role grants
are defaults; Settings → Access Control overrides them per user. This
is enforced server-side on every route (a 403, not just a hidden nav
link) and non-admins are additionally scoped to their own station's
data everywhere — the fleet map, dashboard, and run log all filter to
`station_id` under the hood.

**Auth:** three seeded demo accounts behind a Flask session cookie.
The access-control *shape* above is real and enforced, but there's no
password reset, rate limiting, or audit log — this is a portfolio demo
with no real farmer or financial data behind it, so that layer would
be effort spent proving the wrong skill for this exercise.

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
- Tie the role/permission model already in `permissions.py` to a real
  identity provider (SSO), and add password reset, rate limiting, and
  an audit log of who changed what access for whom.
