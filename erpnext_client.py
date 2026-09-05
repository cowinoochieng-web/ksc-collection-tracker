"""
erpnext_client.py

A small client for the ksc_ops ERPNext/Frappe app's whitelisted REST
endpoints (see ksc_ops/api.py in that app, running in WSL2), used to pull
live operational data from the real ERPNext instance for the
/integration page — the "systems integration" bridge between this Flask
demo and the companion ERPNext portfolio piece.

Auth is Frappe's standard API Key/Secret scheme: an
`Authorization: token <key>:<secret>` header against a dedicated,
read-only "KSC API Reader" user — never the Administrator account.

Unlike traccar_client.py's build_client() (which only checks whether
credentials are configured), this build_client() also does a short-
timeout reachability probe against ksc_ops.api.ping. The realistic
failure mode here isn't "no account registered" (as with the public
Traccar demo server) — it's "the reviewer hasn't started WSL2/bench at
all" — so a plain credential check would leave the dashboard hanging on
a connection-refused error instead of falling back cleanly. Falls back
to MockERPNextClient, same interface, so the page always renders.
"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import requests

DEFAULT_URL = "http://ksc.localhost:8000"


def _resolve_localhost_subdomain(base_url: str) -> tuple[str, str | None]:
    """Browsers and curl resolve any `*.localhost` hostname (e.g.
    `ksc.localhost`, used by Frappe's multi-tenant site routing) to
    127.0.0.1 automatically, per RFC 6761 — but Python's `requests`
    (via the OS resolver) does not, so a bare `requests.get("http://
    ksc.localhost...")` raises a NameResolutionError on a machine where
    curl against that exact URL works fine. Rewrite the connection
    target to 127.0.0.1 ourselves and carry the original hostname via
    the Host header instead, which is what Frappe actually reads to
    route the request to the right site."""
    parts = urlsplit(base_url)
    if parts.hostname and parts.hostname.endswith(".localhost"):
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, "", "")), parts.hostname
    return base_url, None


class ERPNextClient:
    """Real client for ksc_ops's whitelisted API methods."""

    def __init__(self, base_url: str, api_key: str, api_secret: str, timeout: int = 5):
        self.display_url = base_url.rstrip("/")
        self.request_url, host_header = _resolve_localhost_subdomain(self.display_url)
        self.headers = {"Authorization": f"token {api_key}:{api_secret}"}
        if host_header:
            self.headers["Host"] = host_header
        self.timeout = timeout

    def _call(self, method_path: str, params: Optional[dict] = None) -> dict | list:
        resp = requests.get(
            f"{self.request_url}/api/method/{method_path}",
            headers=self.headers,
            params={k: v for k, v in (params or {}).items() if v is not None},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]

    def ping(self) -> dict:
        return self._call("ksc_ops.api.ping")

    def get_summary(self, date_from: str | None = None, date_to: str | None = None) -> dict:
        return self._call("ksc_ops.api.get_summary", {"date_from": date_from, "date_to": date_to})

    def get_fleet_status(self) -> list[dict]:
        return self._call("ksc_ops.api.get_fleet_status")


class MockERPNextClient:
    """Stand-in with the same interface, used when ERPNEXT_API_KEY/SECRET
    aren't set, or the real instance isn't reachable (WSL2/bench not
    running) — so a recruiter grading just this repo sees a coherent
    "integration" page instead of a stack trace or a blank screen."""

    def get_summary(self, date_from: str | None = None, date_to: str | None = None) -> dict:
        return {
            "date_from": date_from or "2026-09-05",
            "date_to": date_to or "2026-09-05",
            "total_runs": 7,
            "delivered": 4,
            "sla_breaches": 1,
            "in_progress": 2,
            "vehicle_count": 5,
            "station_count": 4,
            "farmer_count": 22,
        }

    def get_fleet_status(self) -> list[dict]:
        return [
            {"plate": "KSC-EV-001", "home_station": "Ogembo Hub", "driver": "Demo Driver",
             "run_status": "In Progress", "run_date": "2026-09-05",
             "latitude": -0.7123, "longitude": 34.8891},
            {"plate": "KSC-EV-002", "home_station": "Nyamira Hub", "driver": "Demo Driver",
             "run_status": "Delivered", "run_date": "2026-09-05",
             "latitude": -0.5633, "longitude": 34.9358},
        ]


def build_client(timeout: int = 2) -> tuple[ERPNextClient | MockERPNextClient, bool]:
    """Returns (client, is_live). is_live is False whenever the mock is in
    use, for the template to render an honest "not connected" banner
    instead of silently passing off mock data as real."""
    url = os.environ.get("ERPNEXT_URL", DEFAULT_URL)
    key = os.environ.get("ERPNEXT_API_KEY")
    secret = os.environ.get("ERPNEXT_API_SECRET")
    if not (key and secret):
        return MockERPNextClient(), False

    client = ERPNextClient(url, key, secret, timeout=timeout)
    try:
        client.ping()
        return client, True
    except requests.RequestException:
        return MockERPNextClient(), False
