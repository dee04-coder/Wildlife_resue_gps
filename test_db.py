import os
import sqlite3
import tempfile
import unittest

from app import db, routing


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "test.db"))
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_seeded_reserves(self):
        ids = [r["id"] for r in db.list_reserves(self.conn)]
        self.assertEqual(ids, ["great-savannah", "small-reserve"])

    def test_init_is_idempotent(self):
        db.init_db(self.conn)
        self.assertEqual(len(db.list_reserves(self.conn)), 2)
        self.assertEqual(len(db.get_trails(self.conn, "small-reserve")), 9)

    def test_graph_from_db_matches_benchmarks(self):
        small = db.load_graph(self.conn, "small-reserve")
        self.assertAlmostEqual(routing.shortest_path(small, "A", "B").cost, 9)
        savannah = db.load_graph(self.conn, "great-savannah")
        stations = db.node_names_of_kind(self.conn, "great-savannah", "station")
        self.assertEqual(stations, ["S1", "S2", "S3", "S4"])
        self.assertAlmostEqual(routing.plan_mission(savannah, "A", "B", stations).cost, 60)

    def test_closing_a_trail_reroutes(self):
        trails = db.get_trails(self.conn, "small-reserve")
        de = next(t for t in trails if (t["node_a"], t["node_b"]) == ("D", "E"))
        updated = db.update_trail(self.conn, "small-reserve", de["id"], {"status": "closed"})
        self.assertEqual(updated["status"], "closed")
        graph = db.load_graph(self.conn, "small-reserve")
        self.assertGreater(routing.shortest_path(graph, "A", "B").cost, 9)

    def test_raising_risk_changes_balanced_route_only(self):
        trails = db.get_trails(self.conn, "small-reserve")
        de = next(t for t in trails if (t["node_a"], t["node_b"]) == ("D", "E"))
        db.update_trail(self.conn, "small-reserve", de["id"], {"risk": 5, "note": "Flooded crossing"})
        graph = db.load_graph(self.conn, "small-reserve")
        fastest = routing.shortest_path(graph, "A", "B", risk_weight=0)
        balanced = routing.shortest_path(graph, "A", "B", risk_weight=1)
        self.assertEqual(fastest.path, ("A", "D", "E", "B"))
        self.assertNotEqual(balanced.path, fastest.path)

    def test_update_unknown_trail(self):
        self.assertIsNone(db.update_trail(self.conn, "small-reserve", 9999, {"risk": 1}))

    def test_update_ignores_unexpected_columns(self):
        trail = db.get_trails(self.conn, "small-reserve")[0]
        db.update_trail(self.conn, "small-reserve", trail["id"], {"time_min": 999, "risk": 2})
        after = db.get_trails(self.conn, "small-reserve")[0]
        self.assertEqual(after["time_min"], trail["time_min"])
        self.assertEqual(after["risk"], 2)

    def test_database_rejects_invalid_values(self):
        trail = db.get_trails(self.conn, "small-reserve")[0]
        with self.assertRaises(sqlite3.IntegrityError):
            db.update_trail(self.conn, "small-reserve", trail["id"], {"risk": 99})

    def test_mission_log(self):
        graph = db.load_graph(self.conn, "great-savannah")
        stations = ["S1", "S2", "S3", "S4"]
        plan = routing.plan_mission(graph, "A", "B", stations)
        mission_id = db.save_mission(self.conn, "great-savannah", "A", "B", stations, 1.0, plan)
        missions = db.list_missions(self.conn, "great-savannah")
        self.assertEqual(missions[0]["id"], mission_id)
        self.assertEqual(missions[0]["visiting_order"], list(plan.order))
        self.assertAlmostEqual(missions[0]["total_cost"], 60)
        self.assertEqual(db.list_missions(self.conn, "small-reserve"), [])


if __name__ == "__main__":
    unittest.main()
