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

    # ---- map coordinates and migrations ---------------------------------

    def test_seeded_nodes_have_unique_coordinates_in_range(self):
        for reserve in ("small-reserve", "great-savannah"):
            nodes = db.get_nodes(self.conn, reserve)
            points = [(n["x"], n["y"]) for n in nodes]
            self.assertNotIn(None, [c for p in points for c in p])
            self.assertTrue(all(0 <= x <= 100 and 0 <= y <= 100 for x, y in points))
            self.assertEqual(len(points), len(set(points)), "two nodes share a position")

    def test_known_coordinates(self):
        nodes = {n["name"]: n for n in db.get_nodes(self.conn, "great-savannah")}
        self.assertEqual((nodes["B"]["x"], nodes["B"]["y"]), (65, 95))
        self.assertEqual((nodes["S3"]["x"], nodes["S3"]["y"]), (5, 32))

    def test_move_node(self):
        node = db.update_node_position(self.conn, "small-reserve", "C", 10.5, 60)
        self.assertEqual((node["x"], node["y"]), (10.5, 60))
        self.assertIsNone(db.update_node_position(self.conn, "small-reserve", "ZZ", 1, 1))

    def test_node_position_out_of_range_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            db.update_node_position(self.conn, "small-reserve", "C", 150, 10)

    def test_migration_upgrades_a_version_0_database(self):
        path = os.path.join(self.tmp.name, "legacy.db")
        legacy = db.connect(path)
        legacy.executescript(db.SCHEMA)  # the original schema: nodes have no x / y
        legacy.execute("INSERT INTO reserves VALUES ('small-reserve', 'Old', NULL)")
        legacy.executemany(
            "INSERT INTO nodes (reserve_id, name, kind, label) VALUES ('small-reserve', ?, ?, ?)",
            [("A", "base", "Ranger base"), ("B", "incident", "Stuck rhino")],
        )
        legacy.execute(
            "INSERT INTO trails (reserve_id, node_a, node_b, time_min, risk) "
            "VALUES ('small-reserve', 'A', 'B', 7, 1)"
        )
        legacy.commit()
        self.assertEqual(legacy.execute("PRAGMA user_version").fetchone()[0], 0)

        db.init_db(legacy)

        self.assertEqual(legacy.execute("PRAGMA user_version").fetchone()[0], 1)
        nodes = {n["name"]: n for n in db.get_nodes(legacy, "small-reserve")}
        self.assertEqual((nodes["A"]["x"], nodes["A"]["y"]), (44, 5))
        self.assertEqual(len(db.get_trails(legacy, "small-reserve")), 1)  # data kept
        self.assertEqual(len(db.list_reserves(legacy)), 1)  # not re-seeded
        db.init_db(legacy)  # running again changes nothing
        self.assertEqual(legacy.execute("PRAGMA user_version").fetchone()[0], 1)
        legacy.close()

    def test_fresh_database_is_at_latest_version(self):
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], db.MIGRATIONS[-1][0])


if __name__ == "__main__":
    unittest.main()
