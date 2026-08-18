"""
traccar_client.py

A small, real client for the Traccar GPS platform's REST API
(https://www.traccar.org/traccar-api/), used to pull live vehicle
positions for fleet monitoring.

Traccar's API supports HTTP Basic Auth, session cookies, or a bearer
token. This client uses HTTP Basic Auth against /api/devices and
/api/positions, which is the simplest path for a server-side script
like this one.

Because the public Traccar demo server requires you to register your
own account (there are no shared demo credentials), this client falls
back to a small built-in MockTraccarClient when no real credentials
are supplied, so the rest of the system can be exercised end-to-end
without external dependencies. Swap in real credentials (from your own
registered demo-server account, or KSC's production Traccar instance)
by setting TRACCAR_URL, TRACCAR_USER, and TRACCAR_PASSWORD.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import requests


@dataclass
class VehiclePosition:
    device_id: str
    latitude: float
    longitude: float
    speed_knots: float
    fix_time: datetime

    @property
    def is_idle(self) -> bool:
        """Traccar reports speed in knots; treat <1 knot as stationary."""
        return self.speed_knots < 1.0


class TraccarClient:
    """Real REST client for a Traccar server using HTTP Basic Auth."""

    def __init__(self, base_url: str, username: str, password: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password)
        self.timeout = timeout

    def get_devices(self) -> list[dict]:
        resp = requests.get(f"{self.base_url}/api/devices", auth=self.auth, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_position(self, device_id: str) -> Optional[VehiclePosition]:
        """Fetch the latest known position for a single device."""
        resp = requests.get(
            f"{self.base_url}/api/positions",
            params={"deviceId": device_id},
            auth=self.auth,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        positions = resp.json()
        if not positions:
            return None
        p = positions[-1]
        return VehiclePosition(
            device_id=str(device_id),
            latitude=p["latitude"],
            longitude=p["longitude"],
            speed_knots=p.get("speed", 0.0),
            fix_time=datetime.fromisoformat(p["fixTime"].replace("Z", "+00:00")),
        )


class MockTraccarClient:
    """
    Stand-in for TraccarClient with the same interface, used when no
    live server credentials are configured. Generates plausible
    positions around Kisii County so the rest of the pipeline
    (idle detection, SLA checks, reporting) can be demonstrated
    without a live Traccar account.
    """

    # Rough bounding area covering Kisii / Nyamira / Bomet / Narok
    _LAT_RANGE = (-1.15, -0.55)
    _LON_RANGE = (34.65, 35.90)

    def __init__(self, *_, **__):
        self._rng = random.Random(42)  # deterministic for repeatable demos

    def get_devices(self) -> list[dict]:
        return [{"id": f"KSC-EV-{i:03d}", "name": f"Songa Tuk-Tuk {i:03d}"} for i in range(1, 6)]

    def get_position(self, device_id: str) -> Optional[VehiclePosition]:
        lat = self._rng.uniform(*self._LAT_RANGE)
        lon = self._rng.uniform(*self._LON_RANGE)
        # ~20% chance the vehicle looks idle, to exercise the alerting logic
        speed = 0.0 if self._rng.random() < 0.2 else self._rng.uniform(4, 18)
        return VehiclePosition(
            device_id=str(device_id),
            latitude=lat,
            longitude=lon,
            speed_knots=speed,
            fix_time=datetime.now() - timedelta(minutes=self._rng.randint(0, 15)),
        )


def build_client() -> TraccarClient | MockTraccarClient:
    """Use real credentials if present in the environment, else mock."""
    url = os.environ.get("TRACCAR_URL")
    user = os.environ.get("TRACCAR_USER")
    password = os.environ.get("TRACCAR_PASSWORD")
    if url and user and password:
        return TraccarClient(url, user, password)
    return MockTraccarClient()
