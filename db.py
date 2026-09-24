"""SQLite persistence layer (standard library only).

All SQL is parameterised. The only dynamic SQL is the UPDATE column list in
update_trail(), which is built from a fixed allow-list, never from user input.
Swapping in PostgreSQL later only means replacing this module.
"""
from __future__ import annotations

import json
import sqlite3

from .routing import Graph, MissionPlan, Trail
from .seed_data import RESERVES

SCHEMA = """
CREATE TABLE IF NOT EXISTS reserves (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    reserve_id TEXT NOT NULL REFERENCES reserves(id),
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('base', 'incident', 'station', 'junction')),
    label      TEXT,
    PRIMARY KEY (reserve_id, name)
);

CREATE TABLE IF NOT EXISTS trails (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    reserve_id TEXT NOT NULL REFERENCES reserves(id),
    node_a     TEXT NOT NULL,
    node_b     TEXT NOT NULL,
    time_min   REAL NOT NULL CHECK (time_min >= 0),
    risk       REAL NOT NULL DEFAULT 0 CHECK (risk >= 0 AND risk <= 5),
    status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    note       TEXT,
    CHECK (node_a < node_b),
    UNIQUE (reserve_id, node_a, node_b),
    FOREIGN KEY (reserve_id, node_a) REFERENCES nodes(reserve_id, name),
    FOREIGN KEY (reserve_id, node_b) REFERENCES nodes(reserve_id, name)
);

CREATE TABLE IF NOT EXISTS missions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    reserve_id    TEXT NOT NULL REFERENCES reserves(id),
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    start_node    TEXT NOT NULL,
    incident_node TEXT NOT NULL,
    stops_json    TEXT NOT NULL,
    risk_weight   REAL NOT NULL,
    order_json    TEXT NOT NULL,
    route_json    TEXT NOT NULL,
    total_time    REAL NOT NULL,
    total_risk    REAL NOT NULL,
    total_cost    REAL NOT NULL,
    method        TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM reserves").fetchone()[0] == 0:
        _seed(conn)
    conn.commit()


def _seed(conn: sqlite3.Connection) -> None:
    for r in RESERVES:
        conn.execute(
            "INSERT INTO reserves (id, name, description) VALUES (?, ?, ?)",
            (r["id"], r["name"], r["description"]),
        )
        conn.executemany(
            "INSERT INTO nodes (reserve_id, name, kind, label) VALUES (?, ?, ?, ?)",
            [(r["id"], name, kind, label) for name, kind, label in r["nodes"]],
        )
        conn.executemany(
            "INSERT INTO trails (reserve_id, node_a, node_b, time_min, risk) VALUES (?, ?, ?, ?, ?)",
            [(r["id"], *sorted((a, b)), t, risk) for a, b, t, risk in r["trails"]],
        )


# ---- reads ---------------------------------------------------------------

def list_reserves(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, name, description FROM reserves ORDER BY id")]


def get_reserve(conn: sqlite3.Connection, reserve_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, name, description FROM reserves WHERE id = ?", (reserve_id,)
    ).fetchone()
    return dict(row) if row else None


def get_nodes(conn: sqlite3.Connection, reserve_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT name, kind, label FROM nodes WHERE reserve_id = ? ORDER BY rowid", (reserve_id,)
    )
    return [dict(r) for r in rows]


def get_trails(conn: sqlite3.Connection, reserve_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, node_a, node_b, time_min, risk, status, note FROM trails "
        "WHERE reserve_id = ? ORDER BY id",
        (reserve_id,),
    )
    return [dict(r) for r in rows]


def node_names_of_kind(conn: sqlite3.Connection, reserve_id: str, kind: str) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM nodes WHERE reserve_id = ? AND kind = ? ORDER BY rowid",
        (reserve_id, kind),
    )
    return [r["name"] for r in rows]


def load_graph(conn: sqlite3.Connection, reserve_id: str) -> Graph:
    """Build a routing Graph from the current trail statuses and risk values."""
    nodes = [n["name"] for n in get_nodes(conn, reserve_id)]
    trails = [
        Trail(t["node_a"], t["node_b"], t["time_min"], t["risk"], t["status"] == "open")
        for t in get_trails(conn, reserve_id)
    ]
    return Graph(nodes, trails)


# ---- writes --------------------------------------------------------------

def update_trail(
    conn: sqlite3.Connection, reserve_id: str, trail_id: int, changes: dict
) -> dict | None:
    """Update risk / status / note on a trail. Returns the trail, or None if not found."""
    exists = conn.execute(
        "SELECT 1 FROM trails WHERE id = ? AND reserve_id = ?", (trail_id, reserve_id)
    ).fetchone()
    if exists is None:
        return None
    fields = {k: v for k, v in changes.items() if k in {"risk", "status", "note"}}
    if fields:
        assignments = ", ".join(f"{column} = ?" for column in fields)
        conn.execute(
            f"UPDATE trails SET {assignments} WHERE id = ?", (*fields.values(), trail_id)
        )
        conn.commit()
    row = conn.execute(
        "SELECT id, node_a, node_b, time_min, risk, status, note FROM trails WHERE id = ?",
        (trail_id,),
    ).fetchone()
    return dict(row)


def save_mission(
    conn: sqlite3.Connection,
    reserve_id: str,
    start: str,
    incident: str,
    stations: list[str],
    risk_weight: float,
    plan: MissionPlan,
) -> int:
    cur = conn.execute(
        "INSERT INTO missions (reserve_id, start_node, incident_node, stops_json, risk_weight, "
        "order_json, route_json, total_time, total_risk, total_cost, method) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            reserve_id, start, incident, json.dumps(stations), risk_weight,
            json.dumps(plan.order), json.dumps(plan.route),
            plan.time, plan.risk, plan.cost, plan.method,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_missions(conn: sqlite3.Connection, reserve_id: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM missions WHERE reserve_id = ? ORDER BY id DESC LIMIT ?",
        (reserve_id, limit),
    )
    missions = []
    for r in rows:
        missions.append(
            {
                "id": r["id"],
                "reserve_id": r["reserve_id"],
                "created_at": r["created_at"],
                "start": r["start_node"],
                "incident": r["incident_node"],
                "stations": json.loads(r["stops_json"]),
                "risk_weight": r["risk_weight"],
                "visiting_order": json.loads(r["order_json"]),
                "route": json.loads(r["route_json"]),
                "total_time": r["total_time"],
                "total_risk": r["total_risk"],
                "total_cost": r["total_cost"],
                "method": r["method"],
            }
        )
    return missions
