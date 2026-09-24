# Ranger Rescue GPS (backend)

Risk-aware trail routing and multi-stop mission planning for wildlife rangers.

A ranger gets an incident (a stuck rhino, a migrating elephant herd). The API plans the
best route through the reserve's trail network, including the supply stations that must be
collected on the way (medical supplies, second ranger, fuel, tracking equipment), and
re-plans when trails are closed or become more dangerous.

> Scenario and sample trail networks are adapted from the Entelect Hackathons practice
> challenge *The Ranger's Rescue Route*. This project is an independent application built
> around that idea; it is not an official Entelect submission.

## Features

- **Dijkstra routing** over a weighted, undirected trail graph
- **Risk-adjusted cost:** `cost = time + risk_weight * risk`
  (`0` = fastest, `1` = time + risk, large = safest)
- **Multi-stop mission planning:** finds the cheapest order to visit every station, then
  stitches the legs into one continuous route
  (brute force up to 8 stations, Held-Karp dynamic programming up to 12)
- **Live trail updates:** close a trail or change its risk; the next plan reflects it
- **Mission log** of every plan with time, risk and cost totals
- **SQLite storage** with constraints (valid risk range, no duplicate trails, foreign keys)
- **Protected admin endpoint** (API key, constant-time comparison; disabled unless configured)
- Two demo reserves seeded on first run

## Quick start

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

export RANGER_ADMIN_KEY=change-me  # enables PATCH /trails (Windows: set RANGER_ADMIN_KEY=change-me)
uvicorn app.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

## Try it

Plan the Great Savannah mission (base A → four stations in the best order → elephant herd B):

```bash
curl -X POST http://127.0.0.1:8000/reserves/great-savannah/missions \
     -H "Content-Type: application/json" -d '{}'
```

Visiting order returned: `A → S3 → S1 → S2 → S4 → B`, total cost 60
(time 53 + risk 7). Visiting the stations in numeric order costs 91.

Change the priority to "safest":

```bash
curl -X POST http://127.0.0.1:8000/reserves/great-savannah/missions \
     -H "Content-Type: application/json" -d '{"risk_weight": 1000}'
```

Close a trail (admin):

```bash
curl -X PATCH http://127.0.0.1:8000/reserves/small-reserve/trails/5 \
     -H "X-API-Key: change-me" -H "Content-Type: application/json" \
     -d '{"status": "closed", "note": "Bridge washed out"}'
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| GET | `/reserves` | List reserves |
| GET | `/reserves/{id}/graph` | Nodes and trails (for drawing the map) |
| POST | `/reserves/{id}/route` | Shortest path between two nodes |
| POST | `/reserves/{id}/missions` | Plan and log a multi-stop mission |
| GET | `/reserves/{id}/missions` | Mission history |
| PATCH | `/reserves/{id}/trails/{trail_id}` | Change risk / open-closed status (needs `X-API-Key`) |

Status codes: `404` unknown reserve or trail, `422` invalid input or unknown node,
`409` no open route, `401` bad API key, `503` admin endpoints not configured.

## Project layout

```
app/
  routing.py     Dijkstra, mission planning (no third-party dependencies)
  db.py          SQLite schema, seeding, queries
  seed_data.py   Demo reserves
  schemas.py     Request/response models
  api.py         FastAPI app factory
  main.py        uvicorn entry point
tests/           unit tests for routing and database, plus API tests
```

## Tests

```bash
python -m unittest -v      # or: pytest
```

The routing and database tests need only the standard library. The API tests need
`fastapi` and `httpx` and are skipped automatically if they are missing.

The tests use the challenge graphs as benchmarks (Small Reserve cost 9, Great Savannah
cost 60) and cross-check the brute-force and Held-Karp solvers against each other on
random graphs.

## Design notes

- **Why Dijkstra:** all costs are non-negative, so it is optimal, and one run per station
  gives a small cost matrix for the ordering step.
- **Why the ordering step is separate:** picking the visiting order is a travelling-salesman
  style problem. Exhaustive search is simplest and exact for small missions; Held-Karp
  brings the cost from O(n!) to O(n² · 2ⁿ) for larger ones.
- **Routing has no dependencies** so it can be tested and reused without a web framework.
- **Security:** parameterised SQL only; the single dynamic UPDATE uses a fixed column
  allow-list; input ranges are validated in the API and again by database CHECK constraints.

## Roadmap

- Front-end map (SVG or Leaflet) drawing the route and trail risk
- Per-ranger accounts with roles instead of a single admin key
- Node coordinates and real GPS positions
- Time-of-day risk (e.g. predators at dusk) and vehicle-type restrictions
- Docker image and deployed demo
