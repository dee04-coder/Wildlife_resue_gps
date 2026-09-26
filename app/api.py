"""Ranger Rescue GPS: HTTP API factory (create_app builds the FastAPI app)."""
import hmac
import mimetypes
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db, routing
from .schemas import (
    GraphOut,
    LegOut,
    MissionOut,
    MissionRequest,
    MissionSummary,
    NodeOut,
    NodeUpdate,
    ReserveOut,
    RouteRequest,
    TrailOut,
    TrailUpdate,
)


# Some Windows installs map .js to text/plain, which browsers refuse to run.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# The map page has no inline scripts or styles, so a strict policy works.
FRONTEND_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


def _leg_dict(leg: routing.Leg) -> dict:
    return {
        "start": leg.start,
        "end": leg.end,
        "path": list(leg.path),
        "time": leg.time,
        "risk": leg.risk,
        "cost": leg.cost,
    }


def create_app(db_path: Optional[str] = None, admin_key: Optional[str] = None) -> FastAPI:
    db_path = db_path or os.environ.get("RANGER_DB", "ranger.db")
    if admin_key is None:
        admin_key = os.environ.get("RANGER_ADMIN_KEY")

    boot = db.connect(db_path)
    db.init_db(boot)
    boot.close()

    app = FastAPI(
        title="Ranger Rescue GPS API",
        version="0.1.0",
        description=(
            "Risk-aware trail routing and multi-stop mission planning for wildlife rangers. "
            "Scenario and sample graphs adapted from the Entelect Hackathons practice "
            "challenge 'The Ranger's Rescue Route'."
        ),
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/map"):
            response.headers["Content-Security-Policy"] = FRONTEND_CSP
        return response

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = db.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def require_admin(x_api_key: Optional[str] = Header(default=None)) -> None:
        if not admin_key:
            raise HTTPException(503, "Admin endpoints are disabled: RANGER_ADMIN_KEY is not set")
        if x_api_key is None or not hmac.compare_digest(
            x_api_key.encode(), admin_key.encode()
        ):
            raise HTTPException(401, "Invalid or missing API key")

    def reserve_or_404(conn: sqlite3.Connection, reserve_id: str) -> dict:
        reserve = db.get_reserve(conn, reserve_id)
        if reserve is None:
            raise HTTPException(404, f"Reserve {reserve_id!r} not found")
        return reserve

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/reserves", response_model=list[ReserveOut])
    def list_reserves(conn=Depends(get_conn)):
        return db.list_reserves(conn)

    @app.get("/reserves/{reserve_id}/graph", response_model=GraphOut)
    def get_graph(reserve_id: str, conn=Depends(get_conn)):
        reserve = reserve_or_404(conn, reserve_id)
        return {
            "reserve": reserve,
            "nodes": db.get_nodes(conn, reserve_id),
            "trails": db.get_trails(conn, reserve_id),
        }

    @app.post("/reserves/{reserve_id}/route", response_model=LegOut)
    def plan_route(reserve_id: str, body: RouteRequest, conn=Depends(get_conn)):
        reserve_or_404(conn, reserve_id)
        graph = db.load_graph(conn, reserve_id)
        try:
            leg = routing.shortest_path(graph, body.start, body.end, body.risk_weight)
        except routing.UnknownNodeError as exc:
            raise HTTPException(422, str(exc)) from exc
        except routing.NoRouteError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _leg_dict(leg)

    @app.post("/reserves/{reserve_id}/missions", response_model=MissionOut, status_code=201)
    def plan_mission(reserve_id: str, body: MissionRequest, conn=Depends(get_conn)):
        reserve_or_404(conn, reserve_id)
        start = body.start
        if start is None:
            bases = db.node_names_of_kind(conn, reserve_id, "base")
            start = bases[0] if bases else None
        incident = body.incident
        if incident is None:
            incidents = db.node_names_of_kind(conn, reserve_id, "incident")
            incident = incidents[0] if incidents else None
        if start is None or incident is None:
            raise HTTPException(422, "This reserve has no default base/incident; provide both")
        stations = (
            body.stations
            if body.stations is not None
            else db.node_names_of_kind(conn, reserve_id, "station")
        )

        graph = db.load_graph(conn, reserve_id)
        try:
            plan = routing.plan_mission(graph, start, incident, stations, body.risk_weight)
        except routing.UnknownNodeError as exc:
            raise HTTPException(422, str(exc)) from exc
        except routing.NoRouteError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        mission_id = db.save_mission(
            conn, reserve_id, start, incident, list(stations), body.risk_weight, plan
        )
        return {
            "id": mission_id,
            "reserve_id": reserve_id,
            "risk_weight": body.risk_weight,
            "visiting_order": list(plan.order),
            "route": list(plan.route),
            "legs": [_leg_dict(leg) for leg in plan.legs],
            "total_time": plan.time,
            "total_risk": plan.risk,
            "total_cost": plan.cost,
            "method": plan.method,
        }

    @app.get("/reserves/{reserve_id}/missions", response_model=list[MissionSummary])
    def list_missions(reserve_id: str, conn=Depends(get_conn)):
        reserve_or_404(conn, reserve_id)
        return db.list_missions(conn, reserve_id)

    @app.patch(
        "/reserves/{reserve_id}/nodes/{node_name}",
        response_model=NodeOut,
        dependencies=[Depends(require_admin)],
    )
    def update_node(reserve_id: str, node_name: str, body: NodeUpdate, conn=Depends(get_conn)):
        reserve_or_404(conn, reserve_id)
        node = db.update_node_position(conn, reserve_id, node_name, body.x, body.y)
        if node is None:
            raise HTTPException(404, f"Node {node_name!r} not found in reserve {reserve_id!r}")
        return node

    @app.patch(
        "/reserves/{reserve_id}/trails/{trail_id}",
        response_model=TrailOut,
        dependencies=[Depends(require_admin)],
    )
    def update_trail(
        reserve_id: str, trail_id: int, body: TrailUpdate, conn=Depends(get_conn)
    ):
        reserve_or_404(conn, reserve_id)
        changes = {
            k: v
            for k, v in (("risk", body.risk), ("status", body.status), ("note", body.note))
            if v is not None
        }
        trail = db.update_trail(conn, reserve_id, trail_id, changes)
        if trail is None:
            raise HTTPException(404, f"Trail {trail_id} not found in reserve {reserve_id!r}")
        return trail

    if FRONTEND_DIR.is_dir():
        app.mount("/map", StaticFiles(directory=FRONTEND_DIR, html=True), name="map")

        @app.get("/", include_in_schema=False)
        def root():
            return RedirectResponse("/map/")

    return app

