"""Pydantic models (request validation and response shapes)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ReserveOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None


class NodeOut(BaseModel):
    name: str
    kind: str
    label: Optional[str] = None


class TrailOut(BaseModel):
    id: int
    node_a: str
    node_b: str
    time_min: float
    risk: float
    status: str
    note: Optional[str] = None


class GraphOut(BaseModel):
    reserve: ReserveOut
    nodes: list[NodeOut]
    trails: list[TrailOut]


class RouteRequest(BaseModel):
    start: str
    end: str
    risk_weight: float = Field(
        1.0, ge=0, le=1000,
        description="0 = fastest, 1 = time + risk, large = safest",
    )


class LegOut(BaseModel):
    start: str
    end: str
    path: list[str]
    time: float
    risk: float
    cost: float


class MissionRequest(BaseModel):
    start: Optional[str] = Field(None, description="Defaults to the reserve's base node")
    incident: Optional[str] = Field(None, description="Defaults to the reserve's incident node")
    stations: Optional[list[str]] = Field(
        None, description="Stations to visit in any order. Defaults to every station in the reserve."
    )
    risk_weight: float = Field(1.0, ge=0, le=1000)


class MissionOut(BaseModel):
    id: int
    reserve_id: str
    risk_weight: float
    visiting_order: list[str]
    route: list[str]
    legs: list[LegOut]
    total_time: float
    total_risk: float
    total_cost: float
    method: str


class MissionSummary(BaseModel):
    id: int
    reserve_id: str
    created_at: str
    start: str
    incident: str
    stations: list[str]
    risk_weight: float
    visiting_order: list[str]
    route: list[str]
    total_time: float
    total_risk: float
    total_cost: float
    method: str


class TrailUpdate(BaseModel):
    risk: Optional[float] = Field(None, ge=0, le=5)
    status: Optional[Literal["open", "closed"]] = None
    note: Optional[str] = Field(None, max_length=200)

    @model_validator(mode="after")
    def at_least_one_field(self):
        if self.risk is None and self.status is None and self.note is None:
            raise ValueError("Provide at least one of: risk, status, note")
        return self
