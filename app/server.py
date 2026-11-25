"""Local, dependency-free web application. Run with python -m app.server."""
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import hashlib
import secrets
import time
import html
import threading
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPException
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .demo import demo_tournament
from .config import ServerConfig, initialize_access_key
from .importers import load_tournament, parse_bytes, parse_text
from .pairing import DEFAULT_RULES, forecast, normalize_rules, engine_status
from .sources import CATALOG, MAX_BYTES, fetch_source
from .validation import validate_tournament
from .sample_data import SAMPLE_PATH, SAMPLE_FILENAME, sample_tournament, sample_metadata
from .follow import new_follow, update_follow, follow_status, workspace_forecast

ROOT = Path(__file__).resolve().parent.parent
WORKBOOK = ROOT / "Louisiana_State_Championship_2026_pairings_no_rematches.xlsx"


def is_managed(tournament):
    return isinstance(tournament.get("managed"), dict) and tournament["managed"].get("enabled") is True


def issued_view(record):
    return {**deepcopy(record.get("snapshot", {})), **deepcopy(record), "round": record.get("number", record.get("round"))}


def workspace_view(tournament, rules):
    """Managed assignments are snapshots; only an explicit draft runs an engine."""
    if not is_managed(tournament):
        return workspace_forecast(tournament, rules)
    managed = tournament["managed"]
    draft = managed.get("draft")
    if draft:
        return deepcopy(draft["forecast"])
    issued = next((rnd for rnd in reversed(managed.get("rounds", [])) if rnd["status"] == "issued"), None)
    issued = issued_view(issued) if issued else None
    return {"round": issued["round"] if issued else tournament["completedRounds"] + 1,
            "status": "issued" if issued else "complete" if tournament["completedRounds"] >= tournament["rounds"] else "blocked",
            "blockingReason": "managed", "engine": issued.get("engine", "Managed tournament") if issued else "Managed tournament",
            "pairings": deepcopy(issued.get("pairings", [])) if issued else [], "byes": deepcopy(issued.get("byes", [])) if issued else [],
            "bye": None, "requestedByes": deepcopy(issued.get("requestedByes", [])) if issued else [], "candidates": [],
            "issues": ["Use Run tournament to review, issue and complete rounds."], "warnings": [], "rules": deepcopy(rules),
            "stats": {"nodes": 0, "elapsedMs": 0}}


def managed_round(tournament, query):
    rounds = tournament["managed"].get("rounds", [])
    selected = parse_qs(query).get("round", [str(rounds[-1]["number"]) if rounds else ""])[0]
    if not selected.isdecimal():
        raise ValueError("Issue a round before exporting its pairings.")
    for record in rounds:
        if record["number"] == int(selected):
            return issued_view(record)
    raise ValueError("Select an issued or completed round.")


def pairing_csv(record):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    def safe(value):
        value = str(value)
        return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
    writer.writerow(["round", "board", "white_id", "white", "black_id", "black", "result", "status"])
    for board in record["pairings"]:
        writer.writerow([safe(value) for value in [record["round"], board["board"], board["white"]["id"], board["white"]["name"],
                         board["black"]["id"], board["black"]["name"], record.get("results", {}).get(str(board["board"]), ""), record["status"]]])
    for label, awards in (("BYE", record.get("byes", [])), ("REQUESTED BYE", record.get("requestedByes", []))):
        for award in awards:
            writer.writerow([safe(value) for value in [record["round"], label, award["player"]["id"], award["player"]["name"], "", "", award["points"], record["status"]]])
    return stream.getvalue().encode("utf-8-sig")


def pairing_sheet(tournament, record):
    esc = lambda value: html.escape(str(value), quote=True)
    labels = {"W": "1–0", "D": "½–½", "L": "0–1", "FORFEIT_WIN": "1–0 (forfeit)", "FORFEIT_LOSS": "0–1 (forfeit)",
              "DOUBLE_FORFEIT": "0–0 (double forfeit)"}
    rows = "".join(f'<tr><td>{esc(board["board"])}</td><td>{esc(board["white"]["name"])}</td><td>{esc(board["white"].get("rating", 0) or "Unrated")}</td>'
                   f'<td>{esc(board["black"]["name"])}</td><td>{esc(board["black"].get("rating", 0) or "Unrated")}</td>'
                   f'<td>{esc(labels.get(record.get("results", {}).get(str(board["board"])), "________"))}</td></tr>' for board in record["pairings"])
    byes = "".join(f'<li>{esc(award["player"]["name"])} — {esc(award["points"])} points ({label})</li>'
                   for label, awards in (("allocated bye", record.get("byes", [])), ("requested bye", record.get("requestedByes", []))) for award in awards)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>{esc(tournament["name"])} — Round {record["round"]}</title><style>'
            'body{font:14px Arial,sans-serif;color:#172f28;margin:32px}h1{font-size:26px;margin-bottom:8px}p{line-height:1.5}'
            'table{border-collapse:collapse;width:100%;margin-top:24px}th,td{padding:12px 9px;border:1px solid #bcc7bf;text-align:left;overflow-wrap:anywhere}'
            'th{background:#eef3ef}tr{break-inside:avoid}footer{margin-top:24px;font-size:11px;color:#536459}'
            '@media print{body{margin:0;color:#000}th{background:#eee}thead{display:table-header-group}@page{size:auto;margin:15mm}}'
            f'</style></head><body><h1>{esc(tournament["name"])}</h1><h2>Round {record["round"]} · {esc(tournament.get("section", ""))}</h2>'
            f'<p>{esc(record["status"].title())} pairings · {esc(record.get("engine", ""))}</p><table><thead><tr><th>Board</th><th>White</th><th>Rating</th>'
            f'<th>Black</th><th>Rating</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table>{"<ul>" + byes + "</ul>" if byes else ""}'
            f'<footer>Issued {esc(record.get("issuedAt", ""))} · Saved local-event pairings. Use your browser’s Print command to print this sheet.</footer></body></html>').encode("utf-8")


class Store:
    def __init__(self, state_path=None):
        self.path = state_path or ROOT / ".local" / "state.json"
        self.lock = threading.RLock()
        self.rules = dict(DEFAULT_RULES)
        self.follow_previews = {}
        self.tournament = None
        saved = None
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self.tournament = validate_tournament(saved["tournament"])
                if is_managed(self.tournament):
                    from .director import validate_managed
                    self.tournament = validate_managed(self.tournament)
                saved_rules = saved["rules"]
                if not isinstance(saved_rules, dict):
                    raise ValueError("Saved pairing rules must be an object.")
                # Version-one saved the old generic engine's default hard limits.
                # Upgrade that unmodified default to the US procedural policy;
                # preserve deliberately customized limits as strict controls.
                if (saved.get("schemaVersion", 1) < 2 and saved_rules.get("profile") == "us-chess"
                        and "colorPolicy" not in saved_rules
                        and saved_rules.get("strictColors", True)
                        and saved_rules.get("maxColorImbalance", 2) == 2
                        and saved_rules.get("maxConsecutiveColors", 2) == 2):
                    saved_rules = {**saved_rules, "colorPolicy": "us-chess"}
                self.rules = normalize_rules(saved_rules)
                if is_managed(self.tournament):
                    from .director import validate_rules
                    validate_rules(self.tournament, self.rules)
            except (ValueError, KeyError, TypeError) as exc:
                kind = "managed event" if isinstance(saved, dict) and isinstance(saved.get("tournament"), dict) and is_managed(saved["tournament"]) else "saved workspace"
                raise ValueError(f"The {kind} could not be restored: {exc}. State file preserved at {self.path}. Restore a valid backup or select another --state-file.") from exc
        if self.tournament is None:
            self.tournament = new_follow({"name": "Your tournament"})
        self.forecast = workspace_view(self.tournament, self.rules)

    def state(self):
        status = follow_status(self.tournament, self.rules) if self.tournament.get("tracking") else None
        if status and self.forecast.get("status") != "ok":
            status["readiness"]["ready"] = False
            status["readiness"]["issues"] = list(dict.fromkeys(status["readiness"]["issues"] + self.forecast.get("issues", [])))
        return deepcopy({"tournament": self.tournament, "rules": self.rules,
                         "forecast": self.forecast, "sources": CATALOG, "engines": engine_status(),
                         "followStatus": status, "directorStatus": self.director_status()})

    def director_status(self):
        if not is_managed(self.tournament):
            return None
        from .director import status
        return status(self.tournament, self.rules)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"schemaVersion": 2, "tournament": self.tournament, "rules": self.rules}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temp, self.path)

    def apply(self, tournament=None, rules=None, *, managed_transition=False, replace_workspace=False):
        # Validate and calculate before committing any state.
        new_tournament = validate_tournament(tournament) if tournament is not None else self.tournament
        if (is_managed(self.tournament) or is_managed(new_tournament)) and not (managed_transition or replace_workspace):
            raise ValueError("This is a managed event. Use Run tournament to change its roster, rules, rounds and results; issued pairings cannot be replaced through a forecast or import.")
        if is_managed(new_tournament):
            from .director import validate_managed
            new_tournament = validate_managed(new_tournament)
        new_rules = normalize_rules(rules) if rules is not None else self.rules
        if is_managed(new_tournament):
            from .director import validate_rules
            validate_rules(new_tournament, new_rules)
        calculated = workspace_view(new_tournament, new_rules)
        if is_managed(new_tournament):
            from .director import status
            status(new_tournament, new_rules)
        previous = self.tournament, self.rules, self.forecast
        self.tournament, self.rules, self.forecast = new_tournament, new_rules, calculated
        try:
            self.save()
        except Exception:
            self.tournament, self.rules, self.forecast = previous
            raise
        return self.state()


def make_handler(store, config=None):
    configuration = config or ServerConfig()
    class Handler(BaseHTTPRequestHandler):
        server_version = "SavvyPairings/0.3"
        timeout = 15

        def send_data(self, data, content_type="application/json; charset=utf-8", status=200, filename=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Savvy-Auth-Required", "true" if configuration.access_key else "false")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def json(self, value, status=200):
            self.send_data(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), status=status)

        def director_action(self, path, body):
            from . import director
            with store.lock:
                action = path.removeprefix("/api/director/")
                if is_managed(store.tournament):
                    expected = body.get("expectedRevision")
                    if isinstance(expected, bool) or not isinstance(expected, int):
                        raise ValueError("Reload Run tournament before making changes; the current event revision is required.")
                    if expected != store.tournament["managed"]["revision"] or body.get("expectedEventId") != store.tournament["managed"]["eventId"]:
                        self.json({"error": "The event changed in another action or tab. Reload Run tournament before applying this change.", "code": "STALE_REVISION"}, 409)
                        return
                if action == "new":
                    tournament, rules = director.create_event(body)
                elif action == "restore":
                    if not isinstance(body.get("rules"), dict):
                        raise ValueError("The managed-event backup must include its saved rules.")
                    tournament = validate_tournament(body.get("tournament"))
                    if not is_managed(tournament):
                        raise ValueError("Choose a managed-event JSON backup exported from Run tournament.")
                    tournament = director.validate_managed(tournament)
                    rules = normalize_rules(body.get("rules"))
                    tournament, rules = director.restore_event(tournament, rules)
                else:
                    if not is_managed(store.tournament):
                        raise ValueError("Create or restore an event in Run tournament first.")
                    expected = body.get("expectedRevision")
                    tournament, rules = store.tournament, store.rules
                    if action == "settings":
                        tournament, rules = director.update_event(tournament, rules, body)
                    elif action == "players":
                        tournament = director.upsert_player(tournament, body)
                    elif action == "players/remove":
                        tournament = director.remove_player(tournament, body.get("id"))
                    elif action == "roster":
                        text = body.get("text")
                        if not isinstance(text, str) or not text.strip():
                            raise ValueError("Paste a CSV roster with name,rating,team headers.")
                        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
                        if not reader.fieldnames:
                            raise ValueError("Use name,rating,team roster headers.")
                        reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]
                        if "name" not in reader.fieldnames or any(name not in {"name", "rating", "team"} for name in reader.fieldnames):
                            raise ValueError("Roster columns are name,rating,team. Enter results in the issued round, not in roster import.")
                        count = 0
                        for row in reader:
                            if not any(row.values()):
                                continue
                            raw_rating = (row.get("rating") or "0").strip()
                            if not raw_rating.isdecimal():
                                raise ValueError("Roster ratings must be whole numbers; use 0 for unrated.")
                            tournament = director.upsert_player(tournament, {"name": (row.get("name") or "").strip(), "rating": int(raw_rating), "team": (row.get("team") or "").strip()})
                            count += 1
                            if count > 200:
                                raise ValueError("A section supports at most 200 players.")
                        if not count:
                            raise ValueError("Add at least one named player to the roster.")
                    elif action == "draft":
                        tournament = director.generate_draft(tournament, rules)
                    elif action == "draft/discard":
                        tournament = director.discard_draft(tournament)
                    elif action == "issue":
                        tournament = director.issue_round(tournament, rules, body.get("draftId"))
                    elif action == "results":
                        if isinstance(expected, bool) or not isinstance(expected, int):
                            raise ValueError("Reload the round before saving results; an event revision is required.")
                        tournament = director.record_results(tournament, body.get("round"), body.get("results"), expected_revision=expected)
                    elif action == "close":
                        tournament = director.close_round(tournament, rules, body.get("round"))
                    elif action == "reopen":
                        tournament = director.reopen_latest(tournament, rules, body.get("reason"))
                    else:
                        self.json({"error": "Unknown tournament management action."}, 404)
                        return
                self.json(store.apply(tournament, rules, managed_transition=True))

        def trusted_request(self):
            host = self.headers.get("Host", "")
            allowed = configuration.hosts(self.server.server_port)
            if host not in allowed:
                self.json({"error": "This hostname is not configured for this tournament workspace."}, 403)
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in configuration.origins(self.server.server_port):
                self.json({"error": "Cross-origin requests are not allowed."}, 403)
                return False
            return True

        def authorized_api(self, path):
            if path == "/api/health" or not path.startswith("/api/") or configuration.authorized(self.headers.get("Authorization")):
                return True
            self.json({"error": "Enter the tournament access key to connect to this workspace.", "code": "AUTH_REQUIRED"}, 401)
            return False

        def do_GET(self):
            if not self.trusted_request():
                return
            parsed = urlsplit(self.path)
            if not self.authorized_api(parsed.path):
                return
            if parsed.path == "/api/health":
                self.json({"ok": True, "app": "Savvy Pairings", "version": "0.3", "authRequired": configuration.access_key is not None,
                           "platform": "web-pwa", "forecastRequiresConnection": True})
            elif parsed.path == "/api/sample":
                self.json({"metadata": sample_metadata(), "tournament": sample_tournament()})
            elif parsed.path == "/api/official":
                from .official_data import official_metadata, official_tournament
                self.json({"metadata": official_metadata(), "tournament": official_tournament()})
            elif parsed.path == "/api/sample/source":
                self.send_data(SAMPLE_PATH.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=SAMPLE_FILENAME)
            elif parsed.path == "/api/state":
                with store.lock:
                    self.json(store.state())
            elif parsed.path == "/api/follow/status":
                with store.lock:
                    self.json(store.state()["followStatus"] or follow_status(store.tournament, store.rules))
            elif parsed.path == "/api/director/status":
                with store.lock:
                    self.json(store.director_status() or {"enabled": False})
            elif parsed.path in {"/api/director/export", "/api/director/print"}:
                with store.lock:
                    tournament, rules = deepcopy(store.tournament), deepcopy(store.rules)
                if not is_managed(tournament):
                    self.json({"error": "Create a managed event before exporting it."}, 409)
                    return
                try:
                    format_ = parse_qs(parsed.query).get("format", ["csv"])[0]
                    if parsed.path == "/api/director/export" and format_ == "json":
                        backup = {"schemaVersion": 1, "kind": "savvy-managed-event", "exportedAt": datetime.now(timezone.utc).isoformat(), "tournament": tournament, "rules": rules}
                        self.send_data(json.dumps(backup, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"), filename="savvy-local-event.json")
                    else:
                        record = managed_round(tournament, parsed.query)
                        if parsed.path == "/api/director/print":
                            self.send_data(pairing_sheet(tournament, record), "text/html; charset=utf-8", filename=f"savvy-round-{record['round']}.html")
                        elif format_ == "csv":
                            self.send_data(pairing_csv(record), "text/csv; charset=utf-8", filename=f"savvy-round-{record['round']}.csv")
                        else:
                            raise ValueError("Choose CSV pairings or a JSON event backup.")
                except ValueError as exc:
                    self.json({"error": str(exc)}, 400)
            elif parsed.path == "/api/export":
                with store.lock:
                    state = store.state()
                fmt = parse_qs(parsed.query).get("format", ["json"])[0]
                if fmt == "json":
                    # Tournament is at the root so exported JSON can be reimported.
                    data = dict(state["tournament"], savedRules=state["rules"], savedForecast=state["forecast"])
                    self.send_data(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"), filename="savvy-tournament.json")
                elif fmt == "csv":
                    if state["forecast"]["status"] != "ok":
                        self.json({"error": "No valid pairing candidate is available to export."}, 409)
                        return
                    try:
                        candidate_index = int(parse_qs(parsed.query).get("candidate", ["0"])[0])
                        candidate = state["forecast"]["candidates"][candidate_index]
                        if candidate_index < 0:
                            raise IndexError
                    except (ValueError, IndexError):
                        self.json({"error": "Select a valid candidate index before exporting."}, 400)
                        return
                    output = io.StringIO(newline="")
                    writer = csv.writer(output)
                    writer.writerow(["round", "board", "white_id", "white", "white_rating", "white_score", "black_id", "black", "black_rating", "black_score", "notes"])
                    def safe(value):
                        value = str(value)
                        return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
                    for pair in candidate["pairings"]:
                        w, b = pair["white"], pair["black"]
                        writer.writerow([safe(v) for v in [state["forecast"]["round"], pair["board"], w["id"], w["name"], w["rating"], w["score"], b["id"], b["name"], b["rating"], b["score"], "; ".join(pair["explanations"])]])
                    byes = candidate.get("byes") or ([{"player": candidate["bye"], "points": state["rules"]["byePoints"]}] if candidate.get("bye") else [])
                    for awarded in byes:
                        bye = awarded["player"]
                        writer.writerow([safe(v) for v in [state["forecast"]["round"], "BYE", bye["id"], bye["name"], bye["rating"], bye["score"], "", "", "", "", f"Pairing-allocated bye: {awarded['points']:g} points"]])
                    for requested in state["forecast"].get("requestedByes", []):
                        player = requested["player"]
                        writer.writerow([safe(v) for v in [state["forecast"]["round"], "REQUESTED BYE", player["id"], player["name"], player["rating"], player["score"], "", "", "", "", f"Requested bye: {requested['points']:g} points"]])
                    self.send_data(output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", filename="savvy-pairing-forecast.csv")
                else:
                    self.json({"error": "Export format must be json or csv."}, 400)
            elif parsed.path.startswith("/api/"):
                self.json({"error": "Unknown API route."}, 404)
            else:
                static_paths = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/styles.css": "styles.css",
                                "/simulation.js": "simulation.js", "/follow.js": "follow.js", "/director.js": "director.js", "/pwa.js": "pwa.js", "/sw.js": "sw.js", "/offline.html": "offline.html",
                                "/manifest.webmanifest": "manifest.webmanifest"}
                for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png", "icon.svg"):
                    static_paths["/icons/" + name] = "icons/" + name
                relative = static_paths.get(parsed.path)
                if not relative or not (ROOT / "web" / relative).is_file():
                    self.json({"error": "Not found."}, 404)
                    return
                path = ROOT / "web" / relative
                content_type = "application/manifest+json" if path.suffix == ".webmanifest" else ("application/javascript" if path.suffix == ".js" else mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                if not content_type.startswith("image/"):
                    content_type += "; charset=utf-8"
                self.send_data(path.read_bytes(), content_type)

        def do_PUT(self):
            self.do_POST()

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > MAX_BYTES:
                    self.json({"error": "Import limit is 8 MB."}, 413)
                    return
                raw = self.rfile.read(length)
                # Consume the bounded local request body before returning an
                # origin rejection. Closing with unread bytes can cause TCP RST
                # on Windows and prevent clients from receiving the 403 body.
                if not self.trusted_request():
                    return
                path = urlsplit(self.path).path
                if not self.authorized_api(path):
                    return
                body = {} if path == "/api/import" else json.loads(raw or b"{}", parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite numbers are not allowed.")))
                if not isinstance(body, dict):
                    raise ValueError("Request body must be an object.")
                if path.startswith("/api/director/"):
                    self.director_action(path, body)
                elif path in {"/api/import", "/api/import/text", "/api/import/url"}:
                    extra = {}
                    if path == "/api/import":
                        tournament = parse_bytes(raw, unquote(self.headers.get("X-Filename", "results.csv")))
                        extra["sourceSha256"] = hashlib.sha256(raw).hexdigest()
                    elif path == "/api/import/text":
                        if not isinstance(body.get("text"), str):
                            raise ValueError("Paste results text first.")
                        tournament = parse_text(body["text"], body.get("filename", "results.csv"))
                    else:
                        data, source_url, content_type, cached = fetch_source(body.get("url"))
                        suffix = Path(urlsplit(source_url).path).suffix.lower()
                        filename = "results" + (suffix if suffix in {".xlsx", ".json", ".csv", ".trf", ".txt", ".html", ".htm"} else ".html" if "html" in content_type else ".txt")
                        tournament = parse_bytes(data, filename)
                        tournament["provenance"] = {**tournament.get("provenance", {}), "source": source_url, "requestedUrl": body["url"], "importedAt": datetime.now(timezone.utc).isoformat(), "cached": cached}
                        extra["cached"] = cached
                    tournament = validate_tournament(tournament)
                    self.json({"tournament": tournament, "summary": {"players": len(tournament["players"]), "completedRounds": tournament["completedRounds"], "warnings": tournament["warnings"], **extra}})
                elif path == "/api/simulation":
                    from .replay import replay
                    source = body.get("source", "sample")
                    if source not in {"sample", "official", "workspace", "upload"}:
                        raise ValueError("Choose the original sample, the official validation snapshot, a workspace snapshot or an uploaded workbook.")
                    if source == "sample":
                        tournament = sample_tournament()
                    elif source == "official":
                        from .official_data import official_tournament
                        tournament = official_tournament()
                    elif body.get("tournament") is not None:
                        tournament = validate_tournament(body["tournament"])
                    else:
                        with store.lock:
                            tournament = deepcopy(store.tournament)
                    rules = normalize_rules(body.get("rules", store.rules))
                    self.json(replay(tournament, body.get("throughRound", 0), rules))
                elif path == "/api/tournament":
                    with store.lock:
                        self.json(store.apply(body.get("tournament", body)))
                elif path in {"/api/follow/new", "/api/follow/settings"}:
                    with store.lock:
                        if path.endswith("/new") and is_managed(store.tournament):
                            current = store.tournament["managed"]
                            if body.get("expectedRevision") != current["revision"] or body.get("expectedEventId") != current["eventId"]:
                                self.json({"error": "A managed event is active. Reload the workspace and export its record before starting another event.", "code": "STALE_EVENT"}, 409)
                                return
                        tournament = new_follow(body) if path.endswith("/new") else update_follow(store.tournament, body)
                        rules = dict(DEFAULT_RULES) if path.endswith("/new") else dict(store.rules)
                        tracking = tournament["tracking"]
                        if tracking["profile"] in {"us-chess", "fide-dutch"}:
                            rules["profile"] = tracking["profile"]
                        if tracking.get("firstRoundColor"):
                            rules["firstRoundColor"] = tracking["firstRoundColor"]
                        self.json(store.apply(tournament, rules, replace_workspace=path.endswith("/new")))
                elif path == "/api/follow/preview":
                    from .feeds import fetch_feed, parse_feed_bytes, preview_feed
                    source_url = body.get("url")
                    if source_url:
                        fetched = fetch_feed(source_url, section=body.get("section"))
                        incoming, source_url = fetched["tournament"], fetched["sourceUrl"]
                    elif isinstance(body.get("text"), str):
                        incoming = parse_feed_bytes(body["text"].encode("utf-8"), body.get("filename", "standings.csv"), section=body.get("section"))
                    elif isinstance(body.get("tournament"), dict):
                        incoming = body["tournament"]
                    else:
                        raise ValueError("Provide a standings URL, pasted standings, or an imported tournament preview.")
                    with store.lock:
                        preview = preview_feed(store.tournament, incoming, source_url=source_url)
                        preview_id = secrets.token_urlsafe(18)
                        now = time.monotonic()
                        store.follow_previews = {key: value for key, value in store.follow_previews.items() if now - value[0] < 900}
                        if len(store.follow_previews) >= 10:
                            store.follow_previews.pop(next(iter(store.follow_previews)))
                        store.follow_previews[preview_id] = (now, deepcopy(preview))
                        self.json({**preview, "previewId": preview_id})
                elif path == "/api/follow/apply":
                    from .feeds import snapshot_hash
                    with store.lock:
                        pending = store.follow_previews.get(body.get("previewId"))
                        if not pending or time.monotonic() - pending[0] > 900:
                            self.json({"error": "This standings preview expired. Refresh the source before applying."}, 409)
                            return
                        preview = pending[1]
                        if snapshot_hash(store.tournament) != preview["baseHash"]:
                            self.json({"error": "Tournament settings or results changed after this preview. Refresh and review the source again."}, 409)
                            return
                        tournament = deepcopy(preview["tournament"])
                        tracking = tournament.setdefault("tracking", {"enabled": True})
                        tracking["lastAppliedAt"] = datetime.now(timezone.utc).isoformat()
                        refreshed_source = tournament.get("provenance", {}).get("lastStandingsRefresh", {}).get("source", "")
                        if refreshed_source.startswith("https://"):
                            tracking["sourceUrl"] = refreshed_source
                        self.json(store.apply(tournament))
                        store.follow_previews.pop(body["previewId"], None)
                elif path == "/api/forecast":
                    with store.lock:
                        tournament = deepcopy(body.get("tournament") or store.tournament)
                        if tournament.get("tracking", {}).get("profile") == "unknown" and isinstance(body.get("rules"), dict):
                            tournament["tracking"]["assumedProfile"] = normalize_rules(body["rules"])["profile"]
                        self.json(store.apply(tournament, body.get("rules", store.rules))["forecast"])
                elif path == "/api/demo":
                    with store.lock:
                        self.json(store.apply(demo_tournament(), DEFAULT_RULES))
                elif path == "/api/reset":
                    with store.lock:
                        self.json(store.apply(load_tournament(WORKBOOK), DEFAULT_RULES))
                else:
                    self.json({"error": "Unknown API route."}, 404)
            except (ValueError, KeyError, TypeError, UnicodeError, OverflowError) as exc:
                self.json({"error": str(exc)}, 400)
            except (OSError, HTTPException) as exc:
                self.json({"error": f"Could not complete the operation: {exc}"}, 502)
            except Exception as exc:
                self.log_error("Unexpected operation failure: %s", type(exc).__name__)
                self.json({"error": "The input could not be processed. Check its format and try a supported export."}, 500)

        def log_message(self, fmt, *args):
            print(f"[{self.log_date_time_string()}] {fmt % args}")

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Run Savvy Pairings locally.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1", help="Loopback by default; non-loopback requires an HTTPS public origin and access key")
    parser.add_argument("--public-origin", help="Explicit external HTTPS origin when served behind an HTTPS reverse proxy")
    parser.add_argument("--access-key-file", type=Path, help="File containing a private tournament access key")
    parser.add_argument("--init-access-key", nargs="?", const=str(ROOT / ".local" / "access-key"), type=Path, help="Create an access-key file without printing its secret")
    parser.add_argument("--state-file", type=Path, help="Separate workspace state file (useful for isolated browser tests)")
    args = parser.parse_args()
    if args.init_access_key:
        path = initialize_access_key(args.init_access_key)
        print(f"Tournament access key is available in {path}. The secret was not printed.")
        return
    try:
        access_key = args.access_key_file.read_text(encoding="utf-8").strip() if args.access_key_file else os.environ.get("SAVVY_PAIRINGS_ACCESS_KEY")
        configuration = ServerConfig(args.public_origin, access_key)
        configuration.validate_bind(args.host)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    store = Store(args.state_file)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store, configuration))
    print(f"Savvy Pairings is ready at {configuration.public_origin or f'http://127.0.0.1:{args.port}'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
