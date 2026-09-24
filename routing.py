"""Routing engine for Ranger Rescue GPS.

Pure Python (standard library only) so it is easy to test and reason about.

Edge cost = time + risk_weight * risk

    risk_weight = 1      the "time + risk" rule from the Entelect practice challenge
    risk_weight = 0      fastest route, risk ignored
    risk_weight = large  safest route, time only breaks ties

Multi-stop missions ("collect these stations in any order, then reach the
incident") are solved by:
  1. running Dijkstra from the start and from every station,
  2. choosing the cheapest visiting order (brute force for small missions,
     Held-Karp dynamic programming for larger ones),
  3. stitching the winning legs into one continuous route.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import permutations
from typing import Iterable, Sequence

INF = float("inf")
BRUTE_FORCE_MAX_STOPS = 8  # 8! = 40,320 orderings, still instant
MAX_STOPS = 12  # Held-Karp is O(n^2 * 2^n); 12 stops is fine in pure Python


class RoutingError(Exception):
    """Base class for routing problems."""


class UnknownNodeError(RoutingError):
    """A requested node does not exist in the reserve."""


class NoRouteError(RoutingError):
    """No open trails connect the requested points."""


@dataclass(frozen=True)
class Trail:
    a: str
    b: str
    time: float
    risk: float = 0.0
    open: bool = True


@dataclass(frozen=True)
class Leg:
    start: str
    end: str
    path: tuple[str, ...]
    time: float
    risk: float
    cost: float


@dataclass(frozen=True)
class MissionPlan:
    order: tuple[str, ...]  # start, stations in visiting order, end
    route: tuple[str, ...]  # complete node-by-node route
    legs: tuple[Leg, ...]
    time: float
    risk: float
    cost: float
    method: str


class Graph:
    """Undirected trail network. Closed trails are ignored when routing."""

    def __init__(self, nodes: Iterable[str], trails: Iterable[Trail]) -> None:
        self._adj: dict[str, dict[str, Trail]] = {n: {} for n in nodes}
        for t in trails:
            if t.time < 0 or t.risk < 0:
                raise ValueError(f"Trail {t.a}-{t.b} has a negative time or risk")
            self.require(t.a)
            self.require(t.b)
            if not t.open:
                continue
            if t.b in self._adj[t.a]:
                raise ValueError(f"Duplicate open trail {t.a}-{t.b}")
            self._adj[t.a][t.b] = t
            self._adj[t.b][t.a] = t

    @property
    def nodes(self) -> list[str]:
        return list(self._adj)

    def require(self, node: str) -> None:
        if node not in self._adj:
            raise UnknownNodeError(f"Unknown node: {node!r}")

    def neighbours(self, node: str) -> dict[str, Trail]:
        return self._adj[node]


def _check_weight(risk_weight: float) -> None:
    if risk_weight < 0:
        raise ValueError("risk_weight must be >= 0")


def dijkstra(
    graph: Graph, source: str, risk_weight: float = 1.0
) -> tuple[dict[str, float], dict[str, str]]:
    """Cheapest cost from source to every node, plus predecessor links."""
    _check_weight(risk_weight)
    graph.require(source)
    dist = {n: INF for n in graph.nodes}
    prev: dict[str, str] = {}
    dist[source] = 0.0
    heap: list[tuple[float, str]] = [(0.0, source)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue  # stale heap entry
        for v, trail in graph.neighbours(u).items():
            nd = d + trail.time + risk_weight * trail.risk
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    return dist, prev


def _rebuild(prev: dict[str, str], source: str, target: str) -> list[str]:
    path = [target]
    while path[-1] != source:
        path.append(prev[path[-1]])
    return path[::-1]


def _measure(graph: Graph, path: Sequence[str], risk_weight: float) -> Leg:
    time = risk = 0.0
    for a, b in zip(path, path[1:]):
        trail = graph.neighbours(a)[b]
        time += trail.time
        risk += trail.risk
    return Leg(path[0], path[-1], tuple(path), time, risk, time + risk_weight * risk)


def shortest_path(
    graph: Graph, start: str, end: str, risk_weight: float = 1.0
) -> Leg:
    dist, prev = dijkstra(graph, start, risk_weight)
    graph.require(end)
    if dist[end] == INF:
        raise NoRouteError(f"No open route from {start} to {end}")
    return _measure(graph, _rebuild(prev, start, end), risk_weight)


def _best_order_brute_force(n, d_start, d, d_end):
    best_cost, best_perm = INF, None
    for perm in permutations(range(n)):
        cost = d_start[perm[0]] + d_end[perm[-1]]
        cost += sum(d[a][b] for a, b in zip(perm, perm[1:]))
        if cost < best_cost:
            best_cost, best_perm = cost, perm
    return best_cost, list(best_perm)


def _best_order_held_karp(n, d_start, d, d_end):
    """dp[mask][j] = cheapest way to visit the stops in mask, ending at stop j."""
    full = (1 << n) - 1
    dp = [[INF] * n for _ in range(1 << n)]
    parent = [[-1] * n for _ in range(1 << n)]
    for j in range(n):
        dp[1 << j][j] = d_start[j]
    for mask in range(1, 1 << n):
        for j in range(n):
            base = dp[mask][j]
            if not mask & (1 << j) or base == INF:
                continue
            for k in range(n):
                if mask & (1 << k):
                    continue
                nxt = mask | (1 << k)
                cost = base + d[j][k]
                if cost < dp[nxt][k]:
                    dp[nxt][k] = cost
                    parent[nxt][k] = j
    best_cost, last = min((dp[full][j] + d_end[j], j) for j in range(n))
    order, mask, j = [], full, last
    while j != -1:
        order.append(j)
        prev_j = parent[mask][j]
        mask ^= 1 << j
        j = prev_j
    order.reverse()
    return best_cost, order


def plan_mission(
    graph: Graph,
    start: str,
    end: str,
    stops: Sequence[str],
    risk_weight: float = 1.0,
    method: str = "auto",
) -> MissionPlan:
    """Visit every stop (any order), starting at `start` and finishing at `end`.

    Duplicate stops are merged; stops equal to start or end are already
    satisfied and dropped.
    """
    if method not in ("auto", "brute_force", "held_karp"):
        raise ValueError(f"Unknown method: {method!r}")
    _check_weight(risk_weight)
    stops = [s for s in dict.fromkeys(stops) if s not in (start, end)]
    if len(stops) > MAX_STOPS:
        raise ValueError(f"A mission can have at most {MAX_STOPS} stations")
    for node in (start, end, *stops):
        graph.require(node)

    if not stops:
        leg = shortest_path(graph, start, end, risk_weight)
        return MissionPlan((start, end), leg.path, (leg,), leg.time, leg.risk, leg.cost, "direct")

    trees = {n: dijkstra(graph, n, risk_weight) for n in (start, *stops)}
    n = len(stops)
    d_start = [trees[start][0][s] for s in stops]
    d_end = [trees[s][0][end] for s in stops]
    d = [[trees[a][0][b] for b in stops] for a in stops]
    if INF in d_start or INF in d_end or any(INF in row for row in d):
        raise NoRouteError("Some stations cannot be reached with the currently open trails")

    if method == "auto":
        method = "brute_force" if n <= BRUTE_FORCE_MAX_STOPS else "held_karp"
    solver = _best_order_brute_force if method == "brute_force" else _best_order_held_karp
    _, idx_order = solver(n, d_start, d, d_end)

    sequence = [start, *(stops[i] for i in idx_order), end]
    legs = []
    for a, b in zip(sequence, sequence[1:]):
        legs.append(_measure(graph, _rebuild(trees[a][1], a, b), risk_weight))

    route = list(legs[0].path)
    for leg in legs[1:]:
        route.extend(leg.path[1:])  # drop the duplicated join node
    return MissionPlan(
        order=tuple(sequence),
        route=tuple(route),
        legs=tuple(legs),
        time=sum(l.time for l in legs),
        risk=sum(l.risk for l in legs),
        cost=sum(l.cost for l in legs),
        method=method,
    )
