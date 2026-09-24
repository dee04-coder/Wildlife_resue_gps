import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient

    from app.api import create_app

    HAVE_API_DEPS = True
except ImportError:  # fastapi / httpx not installed
    HAVE_API_DEPS = False

KEY = "test-admin-key"


@unittest.skipUnless(HAVE_API_DEPS, "fastapi and httpx are required (pip install -r requirements-dev.txt)")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "api.db")
        self.client = TestClient(create_app(self.path, admin_key=KEY))

    def tearDown(self):
        self.tmp.cleanup()

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_list_reserves_and_graph(self):
        ids = [r["id"] for r in self.client.get("/reserves").json()]
        self.assertIn("great-savannah", ids)
        graph = self.client.get("/reserves/great-savannah/graph").json()
        self.assertEqual(len(graph["nodes"]), 18)
        self.assertEqual(len(graph["trails"]), 20)

    def test_route_small_reserve(self):
        r = self.client.post("/reserves/small-reserve/route", json={"start": "A", "end": "B"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["path"], ["A", "D", "E", "B"])
        self.assertAlmostEqual(body["cost"], 9)

    def test_mission_defaults_to_all_stations(self):
        r = self.client.post("/reserves/great-savannah/missions", json={})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertAlmostEqual(body["total_cost"], 60)
        self.assertEqual(body["visiting_order"], ["A", "S3", "S1", "S2", "S4", "B"])
        self.assertEqual(len(body["legs"]), 5)
        history = self.client.get("/reserves/great-savannah/missions").json()
        self.assertEqual(history[0]["id"], body["id"])

    def test_error_codes(self):
        self.assertEqual(self.client.get("/reserves/nope/graph").status_code, 404)
        r = self.client.post("/reserves/small-reserve/route", json={"start": "A", "end": "ZZ"})
        self.assertEqual(r.status_code, 422)
        r = self.client.post(
            "/reserves/small-reserve/route", json={"start": "A", "end": "B", "risk_weight": -1}
        )
        self.assertEqual(r.status_code, 422)

    def test_trail_update_requires_api_key(self):
        url = "/reserves/small-reserve/trails/1"
        self.assertEqual(self.client.patch(url, json={"status": "closed"}).status_code, 401)
        bad = self.client.patch(url, json={"status": "closed"}, headers={"X-API-Key": "wrong"})
        self.assertEqual(bad.status_code, 401)

    def test_closing_trails_changes_routes(self):
        trails = self.client.get("/reserves/small-reserve/graph").json()["trails"]
        de = next(t for t in trails if (t["node_a"], t["node_b"]) == ("D", "E"))
        r = self.client.patch(
            f"/reserves/small-reserve/trails/{de['id']}",
            json={"status": "closed", "note": "Bridge washed out"},
            headers={"X-API-Key": KEY},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "closed")
        route = self.client.post("/reserves/small-reserve/route", json={"start": "A", "end": "B"}).json()
        self.assertGreater(route["cost"], 9)

    def test_trail_update_validation(self):
        r = self.client.patch(
            "/reserves/small-reserve/trails/1", json={}, headers={"X-API-Key": KEY}
        )
        self.assertEqual(r.status_code, 422)
        r = self.client.patch(
            "/reserves/small-reserve/trails/1", json={"risk": 9}, headers={"X-API-Key": KEY}
        )
        self.assertEqual(r.status_code, 422)

    def test_admin_disabled_without_key(self):
        client = TestClient(create_app(os.path.join(self.tmp.name, "nokey.db"), admin_key=""))
        r = client.patch(
            "/reserves/small-reserve/trails/1", json={"status": "closed"}, headers={"X-API-Key": "x"}
        )
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()
