"""Exercise the official validation source through its actual HTTP boundary."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from app.server import Store, make_handler


class OfficialServerTests(unittest.TestCase):
    def test_official_snapshot_and_replay_do_not_replace_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "workspace.json")
            before = store.state()
            handler = make_handler(store)
            handler.log_message = lambda *args: None
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()

            def request(path, body=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                try:
                    connection.request("POST" if body is not None else "GET", path,
                                       json.dumps(body) if body is not None else None,
                                       {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(response.status, 200, result)
                    return result
                finally:
                    connection.close()

            try:
                source = request("/api/official")
                self.assertEqual(source["metadata"]["workbookComparison"]["matchedPlayers"], 51)
                self.assertEqual(source["metadata"]["completedRounds"], 7)
                self.assertFalse(source["metadata"]["live"])
                replay = request("/api/simulation", {"source": "official", "throughRound": 1,
                                                    "rules": {"profile": "us-chess"}})
                self.assertEqual(replay["forecast"]["status"], "ok")
                self.assertEqual(replay["comparison"]["opponentMatches"], 17)
                self.assertEqual(replay["comparison"]["opponentCompared"], 25)
                self.assertTrue(all(r["round"] <= 1 for p in replay["tournament"]["players"] for r in p["results"]))
                self.assertTrue(all("reportedScore" not in p for p in replay["tournament"]["players"]))
                self.assertEqual(replay["sourceSummary"]["provenance"]["source"],
                                 "https://ratings.uschess.org/event/202609070433")
                self.assertEqual(store.state(), before)
                self.assertFalse(store.path.exists(), "A replay must not persist or replace the workspace")
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)
