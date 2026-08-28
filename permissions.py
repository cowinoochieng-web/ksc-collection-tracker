"""
permissions.py

The system's menu registry and role-based access control. Every page in
the app maps to a menu_key here; a user can see/do it if that key is
"granted" for them. Grant = role default, unless an explicit per-user
override exists in the `permissions` table (Settings > Access Control),
which always wins.

Three roles, matching how KSC's actual field operation is structured:
- admin: full system access, every station.
- station_lead: runs their own station's dashboard/map/collection log.
- field_staff: only logs collections at their station — no dashboards,
  no reports. ("mostly reports to be visible to admins/leads")

Station scoping (which station's *data* a non-admin sees) is separate
from menu access (which *pages* they see) — see dashboard_data.py's
station_id filters and app.py's current_station_id().
"""

from __future__ import annotations

from db import connect

# (key, label, [(child_key, child_label), ...])
MENU_TREE = [
    ("dashboard", "Dashboard", []),
    ("fleet_map", "Fleet Map", []),
    ("runs", "Collection Runs", [
        ("runs.view", "View & filter runs"),
        ("runs.log", "Log new collection"),
        ("runs.export", "Export CSV"),
    ]),
    ("farmers", "Farmers", [
        ("farmers.export", "Export CSV"),
    ]),
    ("fleet", "Fleet Management", [
        ("fleet.manage", "Add / edit vehicles"),
    ]),
    ("stations", "Stations", [
        ("stations.manage", "Add / edit stations"),
    ]),
    ("users", "Users", [
        ("users.manage", "Add / edit / transfer users"),
    ]),
    ("settings", "Settings", [
        ("settings.access", "Edit access control"),
    ]),
]


def all_keys() -> list[str]:
    keys = []
    for top_key, _, children in MENU_TREE:
        keys.append(top_key)
        keys.extend(c[0] for c in children)
    return keys


ROLE_DEFAULTS = {
    "admin": set(all_keys()),
    "station_lead": {
        "dashboard", "fleet_map",
        "runs", "runs.view", "runs.log", "runs.export",
        "farmers", "farmers.export",
        "fleet",
    },
    "field_staff": {
        "runs", "runs.log",
    },
}


def get_overrides(db_path: str, user_id: int) -> dict[str, bool]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT menu_key, allowed FROM permissions WHERE user_id = ?", (user_id,)
        ).fetchall()
    return {r["menu_key"]: bool(r["allowed"]) for r in rows}


def effective_access(db_path: str, user_row) -> dict[str, bool]:
    """The final allowed/denied state for every menu key, for this user."""
    defaults = ROLE_DEFAULTS.get(user_row["role"], set())
    overrides = get_overrides(db_path, user_row["id"])
    result = {key: (key in defaults) for key in all_keys()}
    result.update(overrides)
    return result


def has_access(effective: dict[str, bool], key: str) -> bool:
    return effective.get(key, False)


def default_landing_key(effective: dict[str, bool]) -> str | None:
    """The first menu (in MENU_TREE order) this user can actually reach —
    used to send them somewhere real right after login instead of
    hardcoding the Dashboard, which field staff can't see."""
    for top_key, _, children in MENU_TREE:
        if effective.get(top_key, False):
            return top_key
        for child_key, _ in children:
            if effective.get(child_key, False):
                return top_key
    return None


def visible_top_level(effective: dict[str, bool]) -> list[tuple[str, str, list]]:
    """Menu entries to render in the sidebar: a top-level item shows if
    it (or any of its children) is granted."""
    visible = []
    for top_key, label, children in MENU_TREE:
        child_visible = [c for c in children if effective.get(c[0], False)]
        if effective.get(top_key, False) or child_visible:
            visible.append((top_key, label, child_visible))
    return visible


def set_overrides(db_path: str, user_id: int, checked_keys: set[str], role: str) -> None:
    """Save the Settings > Access Control form: only stores a row where
    the checked state differs from the role default, so a fresh user
    (or one reset to defaults) has no override rows at all."""
    defaults = ROLE_DEFAULTS.get(role, set())
    with connect(db_path) as conn:
        conn.execute("DELETE FROM permissions WHERE user_id = ?", (user_id,))
        for key in all_keys():
            checked = key in checked_keys
            if checked != (key in defaults):
                conn.execute(
                    "INSERT INTO permissions (user_id, menu_key, allowed) VALUES (?, ?, ?)",
                    (user_id, key, 1 if checked else 0),
                )
