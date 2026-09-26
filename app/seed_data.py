"""Demo reserves used to seed the database.

The two trail networks are adapted from the Entelect Hackathons practice
challenge "The Ranger's Rescue Route" (Level 1: small reserve, Level 2: great
savannah). Credit to Entelect for the scenario and graphs.

node tuple:  (name, kind, label, x, y)   kind: base | incident | station | junction
trail tuple: (node_a, node_b, time_minutes, risk 0-5)

Map coordinates: a 0-100 grid, origin at the top-left, y increasing downward
(the same convention as SVG and HTML canvas). Positions are traced from the
challenge diagrams so the map looks like the original.
"""

SMALL_RESERVE = {
    "id": "small-reserve",
    "name": "The Small Reserve",
    "description": "A young rhino is stuck near Point B. Reach it from the ranger base at Point A.",
    "nodes": [
        ("A", "base", "Ranger base", 44, 5),
        ("B", "incident", "Stuck rhino", 95, 95),
        ("C", "junction", None, 5, 52),
        ("D", "junction", None, 44, 52),
        ("E", "junction", None, 95, 52),
        ("F", "junction", None, 31, 95),
    ],
    "trails": [
        ("A", "C", 4, 0), ("A", "D", 2, 0), ("C", "D", 1, 0),
        ("C", "E", 5, 0), ("D", "E", 3, 0), ("D", "F", 6, 0),
        ("E", "F", 2, 0), ("B", "E", 4, 0), ("B", "F", 7, 0),
    ],
}

_SAVANNAH_JUNCTIONS = {
    1: (23, 18), 2: (5, 46), 3: (5, 68), 4: (29, 41), 5: (53, 41), 6: (65, 18),
    7: (77, 41), 8: (41, 77), 9: (41, 59), 10: (53, 59), 11: (95, 41), 12: (95, 63),
}

GREAT_SAVANNAH = {
    "id": "great-savannah",
    "name": "The Great Savannah",
    "description": (
        "An elephant herd is moving toward Point B. Collect medical supplies, a second "
        "ranger, fuel and tracking equipment on the way, in whatever order is cheapest."
    ),
    "nodes": [
        ("A", "base", "Ranger base", 41, 5),
        ("B", "incident", "Elephant herd", 65, 95),
        ("S1", "station", "Medical supplies", 23, 59),
        ("S2", "station", "Second ranger", 71, 59),
        ("S3", "station", "Refuel vehicle", 5, 32),
        ("S4", "station", "Tracking equipment", 41, 41),
        *[(f"P{i}", "junction", None, *_SAVANNAH_JUNCTIONS[i]) for i in range(1, 13)],
    ],
    "trails": [
        ("A", "P1", 4, 0), ("A", "P6", 5, 2), ("P1", "S3", 4, 0), ("S3", "P2", 4, 1),
        ("P2", "P3", 3, 0), ("P3", "S1", 4, 0), ("S1", "P4", 4, 1), ("P4", "S4", 5, 0),
        ("S4", "P5", 4, 0), ("P5", "B", 4, 0), ("P6", "P7", 4, 0), ("P7", "S2", 4, 0),
        ("P7", "P11", 4, 2), ("S2", "P8", 5, 1), ("P8", "P9", 4, 2), ("P9", "S1", 5, 1),
        ("S2", "P10", 5, 2), ("P10", "S4", 7, 0), ("P11", "P12", 5, 1), ("P12", "B", 4, 0),
    ],
}

RESERVES = [SMALL_RESERVE, GREAT_SAVANNAH]
