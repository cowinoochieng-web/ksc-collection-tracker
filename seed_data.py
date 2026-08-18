"""
seed_data.py

Populates the database with a small, realistic slice of KSC's actual
footprint: hubs across their four counties, a handful of dairy/banana/
vegetable farmers, and a few Songa e-mobility vehicles. Numbers are
illustrative (a workable demo subset), not real KSC records.
"""

from db import connect

HUBS = [
    ("Ogembo Hub", "Kisii"),
    ("Keroka Hub", "Kisii"),
    ("Nyamira Hub", "Nyamira"),
    ("Bomet Hub", "Bomet"),
    ("Narok Hub", "Narok"),
]

FARMERS = [
    # (name, phone, hub_index, value_chain)
    ("Jane Nyaboke", "+254700111001", 0, "dairy"),
    ("Peter Ondieki", "+254700111002", 0, "dairy"),
    ("Grace Kemunto", "+254700111003", 1, "dairy"),
    ("Samuel Nyakundi", "+254700111004", 1, "banana"),
    ("Alice Moraa", "+254700111005", 2, "dairy"),
    ("John Mochama", "+254700111006", 2, "vegetable"),
    ("Mercy Chepkoech", "+254700111007", 3, "dairy"),
    ("David Kiptoo", "+254700111008", 3, "vegetable"),
    ("Esther Naisula", "+254700111009", 4, "banana"),
    ("James Sironka", "+254700111010", 4, "dairy"),
]

VEHICLES = [
    # (plate_or_tag, traccar_device_id, home_hub_index)
    ("KSC-EV-001", "KSC-EV-001", 0),
    ("KSC-EV-002", "KSC-EV-002", 1),
    ("KSC-EV-003", "KSC-EV-003", 2),
    ("KSC-EV-004", "KSC-EV-004", 3),
    ("KSC-EV-005", "KSC-EV-005", 4),
]


def seed(db_path: str = "ksc_demo.db"):
    with connect(db_path) as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM hubs")
        if cur.fetchone()[0] > 0:
            print("Database already seeded — skipping.")
            return

        hub_ids = []
        for name, county in HUBS:
            cur.execute("INSERT INTO hubs (name, county) VALUES (?, ?)", (name, county))
            hub_ids.append(cur.lastrowid)

        for name, phone, hub_idx, value_chain in FARMERS:
            cur.execute(
                "INSERT INTO farmers (name, phone, hub_id, value_chain) VALUES (?, ?, ?, ?)",
                (name, phone, hub_ids[hub_idx], value_chain),
            )

        for plate, device_id, hub_idx in VEHICLES:
            cur.execute(
                "INSERT INTO vehicles (plate_or_tag, traccar_device_id, home_hub_id) VALUES (?, ?, ?)",
                (plate, device_id, hub_ids[hub_idx]),
            )

        print(f"Seeded {len(HUBS)} hubs, {len(FARMERS)} farmers, {len(VEHICLES)} vehicles.")


if __name__ == "__main__":
    from db import init_db

    init_db()
    seed()
