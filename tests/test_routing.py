import random
import unittest

from app import routing
from app.routing import Graph, Trail
from app.seed_data import GREAT_SAVANNAH, SMALL_RESERVE


def build(reserve, closed=()):
    """Graph from a seed reserve. `closed` is a collection of {a, b} pairs to close."""
    nodes = [n[0] for n in reserve["nodes"]]
    trails = [
        Trail(a, b, t, r, open=frozenset((a, b)) not in {frozenset(c) for c in closed})
        for a, b, t, r in reserve["trails"]
    ]
    return Graph(nodes, trails)


class ShortestPathTests(unittest.TestCase):
    def test_small_reserve_benchmark(self):
        leg = routing.shortest_path(build(SMALL_RESERVE), "A", "B")
        self.assertEqual(leg.path, ("A", "D", "E", "B"))
        self.assertAlmostEqual(leg.cost, 9)

    def test_start_equals_end(self):
        leg = routing.shortest_path(build(SMALL_RESERVE), "A", "A")
        self.assertEqual(leg.path, ("A",))
        self.assertEqual(leg.cost, 0)

    def test_unknown_node(self):
        with self.assertRaises(routing.UnknownNodeError):
            routing.shortest_path(build(SMALL_RESERVE), "A", "ZZZ")

    def test_negative_risk_weight_rejected(self):
        with self.assertRaises(ValueError):
            routing.shortest_path(build(SMALL_RESERVE), "A", "B", risk_weight=-1)

    def test_closed_trails_are_avoided(self):
        graph = build(SMALL_RESERVE, closed=[("D", "E")])
        leg = routing.shortest_path(graph, "A", "B")
        self.assertNotIn(("D", "E"), list(zip(leg.path, leg.path[1:])))
        self.assertGreater(leg.cost, 9)

    def test_disconnected_raises_no_route(self):
        graph = build(SMALL_RESERVE, closed=[("B", "E"), ("B", "F")])
        with self.assertRaises(routing.NoRouteError):
            routing.shortest_path(graph, "A", "B")


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.graph = build(GREAT_SAVANNAH)
        self.stations = ["S1", "S2", "S3", "S4"]

    def plan(self, **kw):
        return routing.plan_mission(self.graph, "A", "B", self.stations, **kw)

    def test_savannah_benchmark(self):
        plan = self.plan()
        self.assertAlmostEqual(plan.cost, 60)
        self.assertEqual(plan.order, ("A", "S3", "S1", "S2", "S4", "B"))
        self.assertEqual(
            plan.route,
            ("A", "P1", "S3", "P2", "P3", "S1", "P9", "P8", "S2", "P10", "S4", "P5", "B"),
        )

    def test_route_is_continuous_and_visits_every_station(self):
        plan = self.plan()
        for a, b in zip(plan.route, plan.route[1:]):
            self.assertIn(b, self.graph.neighbours(a))
        for station in self.stations:
            self.assertIn(station, plan.route)

    def test_totals_match_legs(self):
        plan = self.plan()
        self.assertAlmostEqual(plan.time + plan.risk, plan.cost)
        self.assertAlmostEqual(sum(l.cost for l in plan.legs), plan.cost)

    def test_alphabetical_order_is_worse(self):
        seq = ["A", "S1", "S2", "S3", "S4", "B"]
        cost = sum(
            routing.shortest_path(self.graph, a, b).cost for a, b in zip(seq, seq[1:])
        )
        self.assertGreater(cost, self.plan().cost)

    def test_brute_force_and_held_karp_agree(self):
        bf = self.plan(method="brute_force")
        hk = self.plan(method="held_karp")
        self.assertAlmostEqual(bf.cost, hk.cost)

    def test_methods_agree_on_random_graphs(self):
        rng = random.Random(42)
        for _ in range(20):
            names = [f"N{i}" for i in range(12)]
            trails = [Trail(names[i], names[i + 1], rng.randint(1, 9), rng.randint(0, 5))
                      for i in range(11)]  # a spanning path keeps it connected
            for _ in range(12):
                a, b = rng.sample(names, 2)
                if not any({t.a, t.b} == {a, b} for t in trails):
                    trails.append(Trail(a, b, rng.randint(1, 9), rng.randint(0, 5)))
            graph = Graph(names, trails)
            stops = rng.sample(names[1:-1], 6)
            bf = routing.plan_mission(graph, names[0], names[-1], stops, method="brute_force")
            hk = routing.plan_mission(graph, names[0], names[-1], stops, method="held_karp")
            self.assertAlmostEqual(bf.cost, hk.cost)

    def test_fastest_vs_balanced_tradeoff(self):
        fastest = self.plan(risk_weight=0)
        balanced = self.plan(risk_weight=1)
        self.assertLessEqual(fastest.time, balanced.time + 1e-9)
        self.assertLessEqual(balanced.time + balanced.risk, fastest.time + fastest.risk + 1e-9)

    def test_safest_minimises_risk(self):
        safest = self.plan(risk_weight=1000)
        for w in (0, 1):
            self.assertLessEqual(safest.risk, self.plan(risk_weight=w).risk + 1e-9)

    def test_duplicates_and_endpoints_are_ignored(self):
        plan = routing.plan_mission(
            self.graph, "A", "B", ["S1", "S1", "S2", "S3", "S4", "A", "B"]
        )
        self.assertAlmostEqual(plan.cost, 60)

    def test_no_stops_is_direct_route(self):
        plan = routing.plan_mission(self.graph, "A", "B", [])
        self.assertEqual(plan.method, "direct")
        self.assertEqual(plan.route, routing.shortest_path(self.graph, "A", "B").path)

    def test_closed_trail_changes_the_plan(self):
        graph = build(GREAT_SAVANNAH, closed=[("A", "P1")])
        plan = routing.plan_mission(graph, "A", "B", self.stations)
        self.assertNotIn("P1", plan.route[:2])
        self.assertGreater(plan.cost, 60)

    def test_unreachable_station_raises(self):
        graph = build(GREAT_SAVANNAH, closed=[("S3", "P1"), ("S3", "P2")])
        with self.assertRaises(routing.NoRouteError):
            routing.plan_mission(graph, "A", "B", self.stations)

    def test_too_many_stops(self):
        names = [f"N{i}" for i in range(20)]
        trails = [Trail(names[i], names[i + 1], 1) for i in range(19)]
        with self.assertRaises(ValueError):
            routing.plan_mission(Graph(names, trails), "N0", "N19", names[1:14])


if __name__ == "__main__":
    unittest.main()
