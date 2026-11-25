"""Durable, reviewable Swiss rounds for locally managed events.

Issued assignments are immutable snapshots. Live result entry is kept outside
the completed player history until the whole round is explicitly closed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import secrets

from .pairing import (COMPLEMENTS, DEFAULT_RULES, PLAYED_RESULTS, POINTS,
                      audit_history, forecast, normalize_rules, pairing_rating,
                      pairing_score, player_score, reentry_encounter, starting_order_key)
from .validation import integer, validate_tournament

PROFILES = {"us-chess", "fide-dutch"}
RESULT_CODES = {"W", "D", "L", "FORFEIT_WIN", "FORFEIT_LOSS", "DOUBLE_FORFEIT"}
NOTICE = "Local event management; this app does not submit events for federation rating or certify tournament compliance."
US_LIMITS = "The US Chess procedural engine has documented limits for complex exchange/float cycles and director variations. Review its explanations and limits before issuing a round."


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    # JavaScript has one Number type: JSON.parse/stringify turns 0.0 into 0
    # (and -0 into 0). Preserve numeric equality across a browser backup round
    # trip while retaining the full precision of non-integral IEEE values.
    def canonical(item):
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, dict):
            return {key: canonical(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [canonical(child) for child in item]
        return item
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def pairing_input(tournament):
    clean = deepcopy(tournament)
    clean.pop("managed", None)
    clean.pop("tracking", None)
    return clean


def pairing_input_hash(tournament):
    return _digest(pairing_input(tournament))


def rules_hash(rules):
    return _digest(normalize_rules(rules))


def _draft_digest(draft):
    return _digest({k: v for k, v in draft.items() if k != "snapshotHash"})


def _managed(tournament):
    managed = tournament.get("managed")
    if not isinstance(managed, dict) or managed.get("enabled") is not True:
        raise ValueError("Create or restore a locally managed event first.")
    return managed


def _active_round(tournament):
    return next((r for r in _managed(tournament)["rounds"] if r["status"] == "issued"), None)


def _copy(tournament):
    return validate_managed(tournament)


def _audit(tournament, action, details=None, clear_draft=True):
    managed = _managed(tournament)
    managed["revision"] += 1
    if clear_draft:
        managed["draft"] = None
    managed["audit"].append({"revision": managed["revision"], "at": _now(), "action": action,
                             "details": deepcopy(details or {})})
    return tournament


def _no_active(tournament):
    if _active_round(tournament):
        raise ValueError("Finish or explicitly reopen/revise the issued round before changing the event or roster.")


def _event_rules(body, previous=None):
    if not isinstance(body.get("rules", {}), dict):
        raise ValueError("Pairing rules must be an object.")
    raw = {**(previous or DEFAULT_RULES), **body.get("rules", {})}
    raw["profile"] = body.get("profile", raw.get("profile", "fide-dutch"))
    if raw["profile"] not in PROFILES:
        raise ValueError("Managed rounds support US Chess Swiss or FIDE Dutch Swiss; exploratory custom matching cannot be issued as a standard Swiss round.")
    if "firstRoundColor" in body:
        raw["firstRoundColor"] = body["firstRoundColor"]
    rules = normalize_rules(raw)
    if not rules["avoidRematches"] or rules["allowRepeatBye"]:
        raise ValueError("Managed standard Swiss rounds prohibit repeat played opponents and repeat-bye overrides.")
    return rules


def validate_rules(tournament, rules):
    """Restored event rules cannot rewrite a procedure already used to issue."""
    current = _event_rules({}, normalize_rules(rules))
    mutable = {"searchMillis", "maxNodes", "candidateCount"}
    for round_ in _managed(tournament).get("rounds", []):
        issued = normalize_rules(round_["snapshot"]["rules"])
        if any(current[key] != issued[key] for key in current if key not in mutable):
            raise ValueError("Current rules conflict with an issued round. Pairing procedures and rule variations cannot be changed by restoring a different rules object.")
    return current


def create_event(body):
    if not isinstance(body, dict):
        raise ValueError("Event setup must be an object.")
    if not isinstance(body.get("name"), str):
        raise ValueError("Enter a text event name.")
    name = body["name"].strip()
    if not name:
        raise ValueError("Enter the event name.")
    rules = _event_rules({"profile": "fide-dutch", "rules": {"searchMillis": 5000, "maxNodes": 500000}, **body})
    rounds = integer(body.get("rounds", 5), "Event rounds", 1, 50)
    order = body.get("startingOrder", "rating")
    if order not in {"rating", "manual"}:
        raise ValueError("Initial order must be rating order or explicit manual pairing numbers.")
    acknowledged = body.get("acknowledgeEngineLimits", False)
    if not isinstance(acknowledged, bool):
        raise ValueError("Engine-limit acknowledgement must be true or false.")
    tournament = {"name": name[:240], "section": str(body.get("section", "Open"))[:120],
        "rounds": rounds, "completedRounds": 0, "players": [], "warnings": [],
        "sourceSettings": {"schedulePolicy": "single", "win": 1, "draw": .5, "loss": 0},
        "provenance": {"source": "Locally managed event", "format": "managed-event"},
        "managed": {"enabled": True, "eventId": secrets.token_urlsafe(18), "revision": 0,
                    "createdAt": _now(), "startingOrder": order, "nextPlayerSequence": 1,
                    "acknowledgeEngineLimits": acknowledged, "rounds": [], "draft": None, "audit": []}}
    if body.get("acceleration", "none") not in {"none", "baku"}:
        raise ValueError("Managed event setup currently supports no acceleration or native FIDE Baku.")
    if body.get("acceleration") == "baku":
        if rules["profile"] != "fide-dutch":
            raise ValueError("Automatic FIDE Baku requires the FIDE Dutch procedure.")
        tournament["acceleration"] = "baku"
    _audit(tournament, "event-created", {"profile": rules["profile"], "notice": NOTICE})
    return validate_managed(tournament), rules


def update_event(tournament, rules, body):
    result = _copy(tournament)
    _no_active(result)
    if not isinstance(body, dict):
        raise ValueError("Event setup must be an object.")
    new_rules = _event_rules(body, validate_rules(result, rules))
    if result["managed"]["rounds"]:
        mutable = {"searchMillis", "maxNodes", "candidateCount"}
        if any(new_rules[key] != normalize_rules(rules)[key] for key in new_rules if key not in mutable):
            raise ValueError("The pairing procedure and rule variations are fixed after a round has been issued. Search budgets may be increased without rewriting issued rounds.")
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise ValueError("Enter the event name.")
        result["name"] = str(body["name"]).strip()[:240]
    if "section" in body:
        result["section"] = str(body["section"])[:120]
    if "rounds" in body:
        result["rounds"] = integer(body["rounds"], "Event rounds", max(1, result["completedRounds"]), 50)
    if "startingOrder" in body:
        if result["managed"]["rounds"]:
            raise ValueError("Initial pairing numbers are fixed after the first issued round.")
        if body["startingOrder"] not in {"rating", "manual"}:
            raise ValueError("Choose rating order or manual pairing numbers.")
        result["managed"]["startingOrder"] = body["startingOrder"]
    if "acknowledgeEngineLimits" in body:
        if not isinstance(body["acknowledgeEngineLimits"], bool):
            raise ValueError("Engine-limit acknowledgement must be true or false.")
        result["managed"]["acknowledgeEngineLimits"] = body["acknowledgeEngineLimits"]
    if "acceleration" in body:
        if result["managed"]["rounds"]:
            raise ValueError("Acceleration is fixed after the first issued round.")
        if body["acceleration"] not in {"none", "baku"}:
            raise ValueError("Managed event setup currently supports no acceleration or native FIDE Baku.")
        if body["acceleration"] == "baku":
            result["acceleration"] = "baku"
        else:
            result.pop("acceleration", None)
    _audit(result, "event-updated", {"changedFields": sorted(body)})
    return validate_managed(result), new_rules


def upsert_player(tournament, body):
    result = _copy(tournament)
    _no_active(result)
    if not isinstance(body, dict):
        raise ValueError("Player details must be an object.")
    if "name" in body and (not isinstance(body["name"], str) or not body["name"].strip()):
        raise ValueError("Enter a text player name.")
    identifier = str(body.get("id") or "")
    player = next((p for p in result["players"] if p["id"] == identifier), None)
    if identifier and player is None:
        raise ValueError("That player is not in this event.")
    new = player is None
    if new:
        if len(result["players"]) >= 200:
            raise ValueError("This managed section supports up to 200 players.")
        sequence = result["managed"]["nextPlayerSequence"]
        result["managed"]["nextPlayerSequence"] += 1
        identifier = f"p{sequence}"
        player = {"id": identifier, "name": "", "rating": 0, "active": True, "results": [], "scheduledByes": [], "virtualPoints": []}
        if result["managed"]["rounds"]:
            player["entryRound"] = result["completedRounds"] + 1
            player["pairingNumber"] = max((p.get("pairingNumber", 0) for p in result["players"]), default=0) + 1
        result["players"].append(player)
    allowed = {"name", "rating", "pairingRating", "team", "schedule", "scheduledByes", "active", "withdrawnAfterRound", "entryRound", "usChessId", "fideId"}
    for field in allowed:
        if field in body:
            player[field] = deepcopy(body[field])
    if "pairingNumber" in body:
        if result["managed"]["rounds"] and body["pairingNumber"] != player.get("pairingNumber"):
            raise ValueError("An existing player's initial pairing number cannot change after a round is issued.")
        player["pairingNumber"] = body["pairingNumber"]
    if player.get("active") is False:
        player["withdrawnAfterRound"] = result["completedRounds"]
    elif not new and player.get("withdrawnAfterRound", result["rounds"]) < result["completedRounds"] + 1:
        if not result["managed"]["rounds"] and body.get("active") is True and "withdrawnAfterRound" not in body:
            player.pop("withdrawnAfterRound", None)
        else:
            raise ValueError("A withdrawn entry cannot be silently reactivated. Add a reviewed late entry instead.")
    if not new:
        previous = next(p for p in tournament["players"] if p["id"] == identifier)
        if result["managed"]["rounds"] and player.get("entryRound", 1) != previous.get("entryRound", 1):
            raise ValueError("Historical participation cannot change after pairings have been issued.")
        past_before = [b for b in previous.get("scheduledByes", []) if b["round"] <= result["completedRounds"]]
        past_after = [b for b in player.get("scheduledByes", []) if b["round"] <= result["completedRounds"]]
        if past_before != past_after:
            raise ValueError("Previously completed requested byes cannot be rewritten through roster editing.")
    _audit(result, "player-added" if new else "player-updated", {"playerId": identifier})
    return validate_managed(result)


def remove_player(tournament, identifier):
    result = _copy(tournament)
    _no_active(result)
    if result["managed"]["rounds"]:
        raise ValueError("Keep previously issued player identities. Withdraw the player from future rounds instead.")
    if identifier not in {p["id"] for p in result["players"]}:
        raise ValueError("That player is not in this event.")
    result["players"] = [p for p in result["players"] if p["id"] != identifier]
    _audit(result, "player-removed", {"playerId": identifier})
    return validate_managed(result)


def _number_roster(tournament):
    if tournament["managed"]["rounds"]:
        return
    players = tournament["players"]
    if tournament["managed"]["startingOrder"] == "rating":
        for number, player in enumerate(sorted(players, key=lambda p: (-pairing_rating(p), p["name"].casefold(), p["id"])), 1):
            player["pairingNumber"] = number
    elif sorted(p.get("pairingNumber", 0) for p in players) != list(range(1, len(players) + 1)):
        raise ValueError("Manual initial pairing numbers must contain each number from 1 through the roster size exactly once.")


def _allocated(forecast_result):
    return deepcopy(forecast_result.get("byes") or ([{"player": forecast_result["bye"], "points": forecast_result.get("byePoints", 1)}] if forecast_result.get("bye") else []))


def _quality(tournament, rules, outcome):
    issues, warnings = [], list(outcome.get("warnings", []))
    rnd = tournament["completedRounds"] + 1
    if outcome.get("status") != "ok":
        issues.extend(outcome.get("issues") or ["The engine did not produce a complete round."])
    if outcome.get("round") != rnd:
        issues.append("The draft targets a different round.")
    if rules["profile"] not in PROFILES or outcome.get("rules", {}).get("profile") != rules["profile"]:
        issues.append("The draft does not identify the selected standard Swiss procedure.")
    try:
        if normalize_rules(outcome.get("rules")) != rules:
            issues.append("The engine result was generated with different rule settings.")
    except ValueError:
        issues.append("The engine result does not contain valid normalized rule settings.")
    phases = outcome.get("phaseForecasts") or [{"forecast": outcome}]
    for phase in phases:
        result = phase.get("forecast", phase)
        if result.get("status") != "ok":
            issues.append("Every pairing pool must have a complete engine result.")
        messages = " ".join(result.get("warnings", [])).casefold()
        if result.get("blockingReason") == "search-limit" or "reached its budget" in messages or "reached its execution" in messages:
            issues.append("The engine reached its execution/search budget. Increase the budget and regenerate before issuing.")
        if rules["profile"] == "fide-dutch":
            if not str(result.get("engine", "")).startswith("BBP Pairings") or result.get("stats", {}).get("exhaustive") is not True:
                issues.append("A complete pinned Dutch engine result is required; no heuristic substitute can be issued.")
        elif not str(result.get("engine", "")).startswith("Savvy US Chess"):
            issues.append("The draft engine does not match the selected US Chess procedure.")
        elif result.get("stats", {}).get("exhaustive") is not True and result.get("stats", {}).get("candidateQuotaReached") is not True:
            issues.append("The US search did not finish its procedural search or reach its requested candidate quota. Increase the budget and regenerate before issuing.")
    if rules["profile"] == "us-chess":
        warnings.append(US_LIMITS)
        if not tournament["managed"].get("acknowledgeEngineLimits"):
            issues.append("Review and acknowledge the US Chess engine's documented limits before issuing this procedure.")
    if not rules["avoidRematches"] or rules["allowRepeatBye"]:
        issues.append("Repeat-opponent and repeat-bye overrides cannot be issued in standard managed Swiss mode.")
    eligible = {p["id"]: p for p in tournament["players"] if p.get("active", True)
                and p.get("entryRound", 1) <= rnd <= p.get("withdrawnAfterRound", tournament["rounds"])}
    assigned = []
    boards = outcome.get("pairings", [])
    if not isinstance(boards, list):
        issues.append("Engine boards must be a list.")
        boards = []
    board_numbers = []
    prohibited = {frozenset(pair) for pair in tournament.get("prohibitedPairs", [])}
    scheduled = {p["id"]: b["points"] for p in eligible.values() for b in p.get("scheduledByes", []) if b["round"] == rnd}
    policy = tournament.get("sourceSettings", {}).get("schedulePolicy")
    merge_round = tournament.get("sourceSettings", {}).get("mergeRound")
    if policy in {None, ""}: policy = "merge" if merge_round is not None else "unknown"
    separate = policy == "separate" or policy == "merge" and isinstance(merge_round, int) and not isinstance(merge_round, bool) and rnd < merge_round
    labels = {identifier: str(p.get("schedule") or "").strip() if separate else "single" for identifier, p in eligible.items()}
    if policy not in {"unknown", "single", "separate", "merge"} or policy == "unknown" and len({str(p.get("schedule") or "").strip() for p in eligible.values()}) > 1:
        issues.append("The pairing-pool policy is not confirmed.")
    if policy == "merge" and (isinstance(merge_round, bool) or not isinstance(merge_round, int) or not 1 <= merge_round <= tournament["rounds"] + 1):
        issues.append("The schedule merge boundary is invalid.")
    orientations = {}
    pool_boards = {}
    forced_colors = {frozenset((pair["whiteId"], pair["blackId"])) for pair in tournament.get("forcedPairs", []) if isinstance(pair, dict)}
    for board in boards:
        try:
            a, b = board["white"]["id"], board["black"]["id"]
            number = board["board"]
        except (TypeError, KeyError):
            issues.append("An engine board is missing a player or board number.")
            continue
        board_numbers.append(number)
        if isinstance(number, bool) or not isinstance(number, int):
            issues.append("Board numbers must be whole-number labels, not booleans or text.")
        assigned.extend((a, b))
        orientations[frozenset((a, b))] = (a, b)
        if a == b or a not in eligible or b not in eligible:
            issues.append("An issued board names an ineligible player or a self-pairing.")
        elif a in scheduled or b in scheduled:
            issues.append("A requested-bye player cannot also receive an opponent.")
        elif labels[a] != labels[b]:
            issues.append("An engine board crosses pairing pools before their declared merge.")
        elif reentry_encounter(tournament, a, b, allow_both_reset=rules["profile"] == "us-chess"):
            issues.append("An engine board repeats a played opponent.")
        if frozenset((a, b)) in prohibited:
            issues.append("An engine board violates a prohibited opponent restriction.")
        if rules["teamPolicy"] == "forbid" and a in eligible and b in eligible and eligible[a].get("team") and eligible[a].get("team") == eligible[b].get("team"):
            issues.append("An engine board violates a prohibited same-team pairing.")
        if a in eligible and b in eligible:
            pool = labels[a]
            local_index = pool_boards.get(pool, 0)
            pool_boards[pool] = local_index + 1
            if rnd == 1 and rules["profile"] == "us-chess" and frozenset((a, b)) not in forced_colors:
                higher = min((a, b), key=lambda identifier: (-pairing_score(eligible[identifier], rnd), *starting_order_key(eligible[identifier], rules["pairingOrder"])))
                due = rules["firstRoundColor"] if local_index % 2 == 0 else ("B" if rules["firstRoundColor"] == "W" else "W")
                if ("W" if a == higher else "B") != due:
                    issues.append("A first-round board reverses the confirmed US initial-color draw and board alternation.")
            final_top = rnd == tournament["rounds"] and any(player_score(eligible[i], rnd - 1) > (rnd - 1) / 2 for i in (a, b))
            us_series_exception = (rules["allowLastRoundExceptions"] and rnd == tournament["rounds"]
                                   and all(eligible[i].get("prizeContender") for i in (a, b)))
            for identifier, color in ((a, "W"), (b, "B")):
                history = [r["color"] for r in sorted(eligible[identifier].get("results", []), key=lambda r: r["round"])
                           if r["round"] < rnd and r["result"] in PLAYED_RESULTS and r.get("played", True) and r.get("color")]
                balance = abs(history.count("W") - history.count("B") + (1 if color == "W" else -1))
                streak = 1
                for previous in reversed(history):
                    if previous != color: break
                    streak += 1
                if rules["profile"] == "fide-dutch" and (balance > 2 or streak > 2) and not final_top:
                    issues.append("A Dutch board violates an absolute color limit outside the final-round topscorer exception.")
                elif rules["profile"] == "us-chess" and rules["strictColors"] and rules["colorPolicy"] == "strict":
                    if balance > rules["maxColorImbalance"] or streak > rules["maxConsecutiveColors"] and not us_series_exception:
                        issues.append("An engine board violates the selected hard color limits.")
                elif rules["profile"] == "us-chess" and (balance > 2 or streak > 2):
                    warnings.append(f"{eligible[identifier]['name']}: this assignment needs review of the US color procedure (imbalance {balance}, consecutive-color series {streak}); ordinary US preferences are not absolute custom color limits.")
    for forced in tournament.get("forcedPairs", []):
        ordered = isinstance(forced, dict)
        a, b = (forced["whiteId"], forced["blackId"]) if ordered else forced
        actual = orientations.get(frozenset((a, b)))
        if not actual or ordered and actual != (a, b):
            issues.append("The engine result does not satisfy a required pairing or color lock.")
        if rules["profile"] == "fide-dutch":
            issues.append("The native Dutch adapter does not support required pair/color overrides; remove them before issuing its prescribed result.")
    if board_numbers != list(range(1, len(boards) + 1)):
        issues.append("Board numbers must be consecutive and unique.")
    allocated = _allocated(outcome)
    bye_counts = {}
    for bye in allocated:
        try:
            identifier, points = bye["player"]["id"], bye["points"]
        except (TypeError, KeyError):
            issues.append("An allocated bye is incomplete.")
            continue
        assigned.append(identifier)
        if identifier in labels:
            bye_counts[labels[identifier]] = bye_counts.get(labels[identifier], 0) + 1
        if identifier not in eligible or identifier in scheduled or points != rules["byePoints"]:
            issues.append("An allocated bye has an ineligible player or an incorrect award.")
        elif any(r["round"] <= tournament["completedRounds"] and (r["result"] in {"BYE", "FORFEIT_WIN"}
                 or not r.get("played", r["result"] in PLAYED_RESULTS) and r.get("points", POINTS[r["result"]]) >= 1)
                 for r in eligible[identifier].get("results", [])):
            issues.append("The allocated bye recipient already received a disqualifying bye or unplayed win.")
    pool_sizes = {}
    for identifier, label in labels.items():
        if identifier not in scheduled: pool_sizes[label] = pool_sizes.get(label, 0) + 1
    if any(bye_counts.get(label, 0) != count % 2 for label, count in pool_sizes.items()) or set(bye_counts) - set(pool_sizes):
        issues.append("Allocated-bye counts do not match the parity of each pairing pool after requested byes.")
    requested = outcome.get("requestedByes", [])
    requested_map = {}
    for bye in requested:
        try:
            identifier, points = bye["player"]["id"], bye["points"]
        except (TypeError, KeyError):
            issues.append("A requested bye is incomplete.")
            continue
        assigned.append(identifier)
        requested_map[identifier] = points
    if requested_map != scheduled:
        issues.append("The draft's requested byes differ from the configured requests.")
    if len(assigned) != len(set(assigned)) or set(assigned) != set(eligible):
        issues.append("Every eligible player must appear exactly once, on a board or an awarded/requested bye.")
    if not eligible:
        issues.append("No participating players are available for this round.")
    history_issues, _ = audit_history(pairing_input(tournament), rules)
    issues.extend(history_issues)
    return {"canIssue": not issues, "issues": list(dict.fromkeys(issues)), "warnings": list(dict.fromkeys(warnings))}


def generate_draft(tournament, rules):
    result = _copy(tournament)
    _no_active(result)
    rules = validate_rules(result, rules)
    if result["completedRounds"] >= result["rounds"]:
        raise ValueError("All scheduled rounds are complete.")
    if len(result["players"]) < 2:
        raise ValueError("Add at least two players before generating a round.")
    _number_roster(result)
    result = validate_managed(result)
    outcome = forecast(pairing_input(result), rules)
    outcome["byes"] = _allocated(outcome)
    draft = {"id": secrets.token_urlsafe(18), "round": result["completedRounds"] + 1, "createdAt": _now(),
             "inputHash": pairing_input_hash(result), "rulesHash": rules_hash(rules),
             "forecast": deepcopy(outcome), "quality": _quality(result, rules, outcome)}
    draft["snapshotHash"] = _draft_digest(draft)
    result["managed"]["draft"] = draft
    _audit(result, "draft-generated", {"draftId": draft["id"], "round": draft["round"], "canIssue": draft["quality"]["canIssue"]}, clear_draft=False)
    return validate_managed(result)


def issue_round(tournament, rules, draft_id):
    result = _copy(tournament)
    _no_active(result)
    rules = validate_rules(result, rules)
    draft = result["managed"].get("draft")
    if not draft or draft.get("id") != draft_id:
        raise ValueError("Select the current generated draft before issuing a round.")
    if draft["snapshotHash"] != _draft_digest(draft):
        raise ValueError("The generated draft changed. Generate a fresh draft before issuing.")
    if draft["inputHash"] != pairing_input_hash(result) or draft["rulesHash"] != rules_hash(rules):
        raise ValueError("The roster, results or rules changed after generation. Generate a fresh draft before issuing.")
    quality = _quality(result, rules, draft["forecast"])
    if not quality["canIssue"]:
        raise ValueError("The round cannot be issued: " + " ".join(quality["issues"]))
    outcome = draft["forecast"]
    snapshot = {"pairings": deepcopy(outcome["pairings"]), "byes": _allocated(outcome),
        "requestedByes": deepcopy(outcome.get("requestedByes", [])), "rules": deepcopy(rules),
        "engine": outcome.get("engine"), "engineVersion": outcome.get("engineVersion"), "ruleEdition": outcome.get("ruleEdition"),
        "engineDetails": deepcopy(outcome.get("engineDetails", {})), "scope": outcome.get("scope"),
        "inputHash": draft["inputHash"], "inputSnapshot": pairing_input(result), "rulesHash": draft["rulesHash"], "generatedAt": outcome.get("generatedAt"),
        "warnings": quality["warnings"], "scheduleContext": deepcopy(outcome.get("scheduleContext", {}))}
    issued = {"number": draft["round"], "status": "issued", "issuedAt": _now(), "snapshot": snapshot,
              "snapshotHash": _digest(snapshot), "results": {}, "resultRevision": 0, "revisions": []}
    result["managed"]["rounds"].append(issued)
    _audit(result, "round-issued", {"round": issued["number"], "draftId": draft_id, "snapshotHash": issued["snapshotHash"]})
    return validate_managed(result)


def record_results(tournament, round_number, results, expected_revision=None):
    event = _copy(tournament)
    round_number = integer(round_number, "Round number", 1, event["rounds"])
    if expected_revision is not None:
        expected_revision = integer(expected_revision, "Expected event revision", 0, 10000000)
    if expected_revision is not None and expected_revision != event["managed"]["revision"]:
        raise ValueError("Results changed in another view. Reload the current round before saving.")
    active = _active_round(event)
    if not active or active["number"] != round_number:
        raise ValueError("Enter results only for the currently issued round.")
    if not isinstance(results, list):
        raise ValueError("Results must be a list of board/result entries.")
    board_numbers = {b["board"] for b in active["snapshot"]["pairings"]}
    changes, seen = {}, set()
    for entry in results:
        if not isinstance(entry, dict):
            raise ValueError("Each result needs a board number and a result from White's perspective.")
        board = integer(entry.get("board"), "Board number", 1, 200)
        code = entry.get("result")
        if board not in board_numbers or board in seen or code not in RESULT_CODES | {None, ""}:
            raise ValueError("Use each issued board once with W, D, L, FORFEIT_WIN, FORFEIT_LOSS, DOUBLE_FORFEIT or a blank pending result.")
        seen.add(board)
        changes[str(board)] = code or None
    before = deepcopy(active["results"])
    for board, code in changes.items():
        if code:
            active["results"][board] = code
        else:
            active["results"].pop(board, None)
    active["resultRevision"] += 1
    active["revisions"].append({"at": _now(), "action": "results-saved", "before": before, "after": deepcopy(active["results"])})
    _audit(event, "results-saved", {"round": round_number, "boards": sorted(seen)}, clear_draft=False)
    return validate_managed(event)


def _round_records(round_):
    records = {}
    number = round_["number"]
    for board in round_["snapshot"]["pairings"]:
        code = round_["results"].get(str(board["board"]))
        if code not in RESULT_CODES:
            raise ValueError("Every board needs a final result before closing the round.")
        played = code in {"W", "D", "L"}
        white, black = board["white"]["id"], board["black"]["id"]
        white_code = "FORFEIT_LOSS" if code == "DOUBLE_FORFEIT" else code
        black_code = "FORFEIT_LOSS" if code == "DOUBLE_FORFEIT" else COMPLEMENTS[code]
        records[white] = {"round": number, "opponentId": black, "result": white_code, "color": "W" if played else None, "played": played}
        records[black] = {"round": number, "opponentId": white, "result": black_code, "color": "B" if played else None, "played": played}
    for awarded in round_["snapshot"]["byes"]:
        records[awarded["player"]["id"]] = {"round": number, "opponentId": None, "result": "BYE", "points": awarded["points"], "color": None, "played": False}
    for requested in round_["snapshot"]["requestedByes"]:
        points = requested["points"]
        records[requested["player"]["id"]] = {"round": number, "opponentId": None,
            "result": {0: "ZERO", .5: "HBYE", 1: "FORFEIT_WIN"}[points], "points": points, "color": None, "played": False}
    return records


def close_round(tournament, rules, round_number):
    result = _copy(tournament)
    round_number = integer(round_number, "Round number", 1, result["rounds"])
    rules = validate_rules(result, rules)
    active = _active_round(result)
    if not active or active["number"] != round_number or round_number != result["completedRounds"] + 1:
        raise ValueError("Close only the next currently issued round.")
    records = _round_records(active)
    for player in result["players"]:
        if any(r["round"] == round_number for r in player["results"]):
            raise ValueError("A round result already exists in completed history; reopen it explicitly before making changes.")
        if player["id"] in records:
            player["results"].append(records[player["id"]])
    active["status"], active["closedAt"] = "closed", _now()
    result["completedRounds"] = round_number
    _audit(result, "round-closed", {"round": round_number, "snapshotHash": active["snapshotHash"]})
    validated = validate_managed(result)
    # Do not run a next-round engine at closure. History checks catch reciprocal
    # result/color errors while an event-complete message is expected at the end.
    history_issues, _ = audit_history(pairing_input(validated), normalize_rules(rules))
    history_issues = [issue for issue in history_issues if not issue.startswith("All scheduled rounds are complete")]
    if history_issues:
        raise ValueError("Round history needs review: " + " ".join(history_issues))
    return validated


def reopen_latest(tournament, rules, reason):
    result = _copy(tournament)
    validate_rules(result, rules)
    _no_active(result)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Enter a reason for reopening the latest completed round.")
    if result["managed"].get("draft"):
        raise ValueError("Discard the next-round draft before reopening a completed round.")
    rounds = result["managed"]["rounds"]
    if not rounds or rounds[-1]["status"] != "closed":
        raise ValueError("There is no completed round to reopen.")
    latest = rounds[-1]
    for player in result["players"]:
        player["results"] = [r for r in player["results"] if r["round"] != latest["number"]]
    latest["revisions"].append({"at": _now(), "action": "round-reopened", "reason": reason.strip()[:1000],
                                "previousClosedAt": latest.get("closedAt"), "results": deepcopy(latest["results"])})
    latest["status"] = "issued"
    latest.pop("closedAt", None)
    result["completedRounds"] -= 1
    _audit(result, "round-reopened", {"round": latest["number"], "reason": reason.strip()[:1000]})
    return validate_managed(result)


def discard_draft(tournament):
    result = _copy(tournament)
    if not result["managed"].get("draft"):
        raise ValueError("There is no generated draft to discard.")
    _audit(result, "draft-discarded")
    return validate_managed(result)


def restore_event(tournament, rules):
    """A restored branch has a fresh identity so older browser writes stay stale."""
    result = _copy(tournament)
    normalized = validate_rules(result, rules)
    source_id, source_revision = result["managed"]["eventId"], result["managed"]["revision"]
    result["managed"]["eventId"] = secrets.token_urlsafe(18)
    _audit(result, "event-restored", {"sourceEventId": source_id, "sourceRevision": source_revision})
    return validate_managed(result), normalized


def validate_managed(tournament):
    """Validate restored state and ensure closed history matches issued rounds."""
    result = validate_tournament(tournament)
    managed = _managed(result)
    if not isinstance(managed.get("eventId"), str) or not managed["eventId"]:
        raise ValueError("Managed event identity is missing.")
    integer(managed.get("revision"), "Managed revision", 0, 10000000)
    integer(managed.get("nextPlayerSequence"), "Next player identity", 1, 10000000)
    if managed.get("startingOrder") not in {"rating", "manual"} or not isinstance(managed.get("acknowledgeEngineLimits"), bool):
        raise ValueError("Managed event setup is incomplete.")
    if not isinstance(managed.get("audit"), list) or len(managed["audit"]) > 100000:
        raise ValueError("Managed event audit must be a bounded list.")
    if managed["audit"]:
        revisions = [entry.get("revision") for entry in managed["audit"] if isinstance(entry, dict)]
        if revisions != list(range(1, managed["revision"] + 1)):
            raise ValueError("Managed audit revisions are not consecutive.")
    elif managed["revision"]:
        raise ValueError("Managed event revision has no audit history.")
    rounds = managed.get("rounds")
    if not isinstance(rounds, list) or len(rounds) > result["rounds"]:
        raise ValueError("Issued rounds must be a bounded list within the event length.")
    ids = {p["id"] for p in result["players"]}
    expected = {identifier: [] for identifier in ids}
    closed = 0
    for index, round_ in enumerate(rounds, 1):
        if not isinstance(round_, dict) or round_.get("number") != index or round_.get("status") not in {"issued", "closed"}:
            raise ValueError("Issued rounds need consecutive round numbers and a valid status.")
        integer(round_["number"], "Issued round number", 1, result["rounds"])
        if round_["status"] == "issued" and index != len(rounds):
            raise ValueError("Only the latest issued round may be in progress.")
        snapshot = round_.get("snapshot")
        if not isinstance(snapshot, dict) or round_.get("snapshotHash") != _digest(snapshot):
            raise ValueError("An issued pairing snapshot changed or its checksum is missing.")
        if not isinstance(snapshot.get("inputSnapshot"), dict) or snapshot.get("inputHash") != _digest(snapshot["inputSnapshot"]):
            raise ValueError("Issued pairing inputs do not match their stored fingerprint.")
        if snapshot["inputSnapshot"].get("completedRounds") != index - 1:
            raise ValueError("Issued pairing inputs do not describe the preceding completed-round boundary.")
        if snapshot.get("rules", {}).get("profile") not in PROFILES or snapshot.get("rulesHash") != rules_hash(snapshot["rules"]):
            raise ValueError("Issued rules do not match their stored fingerprint.")
        if not isinstance(round_.get("results"), dict) or not isinstance(round_.get("revisions"), list):
            raise ValueError("Issued results and revisions are malformed.")
        board_numbers = []
        seen = []
        for board in snapshot.get("pairings", []):
            try:
                a, b, number = board["white"]["id"], board["black"]["id"], board["board"]
            except (KeyError, TypeError) as exc:
                raise ValueError("An issued board is malformed.") from exc
            if a == b or a not in ids or b not in ids:
                raise ValueError("An issued board references an invalid player.")
            seen.extend((a, b)); board_numbers.append(number)
            integer(number, "Issued board number", 1, 200)
        if board_numbers != list(range(1, len(board_numbers) + 1)):
            raise ValueError("Issued board numbers are not consecutive and unique.")
        for field in ("byes", "requestedByes"):
            if not isinstance(snapshot.get(field), list):
                raise ValueError("Issued bye assignments are malformed.")
            for bye in snapshot[field]:
                if not isinstance(bye, dict) or not isinstance(bye.get("player"), dict) or bye["player"].get("id") not in ids or bye.get("points") not in {0, .5, 1}:
                    raise ValueError("An issued bye assignment is invalid.")
                seen.append(bye["player"]["id"])
        if len(seen) != len(set(seen)):
            raise ValueError("A player occurs more than once in an issued round.")
        if any(key not in {str(n) for n in board_numbers} or code not in RESULT_CODES for key, code in round_["results"].items()):
            raise ValueError("A result does not belong to an issued board or has an invalid code.")
        integer(round_.get("resultRevision"), "Result revision", 0, 10000000)
        result_history, result_revision = {}, 0
        for revision in round_["revisions"]:
            if not isinstance(revision, dict) or revision.get("action") not in {"results-saved", "round-reopened"}:
                raise ValueError("A round result audit entry is malformed.")
            if revision["action"] == "results-saved":
                if revision.get("before") != result_history or not isinstance(revision.get("after"), dict):
                    raise ValueError("A round result revision does not follow its preceding saved results.")
                result_history = deepcopy(revision["after"])
                result_revision += 1
            elif not str(revision.get("reason", "")).strip() or revision.get("results") != result_history:
                raise ValueError("A reopened round must retain its preceding results and an audit reason.")
        if result_revision != round_["resultRevision"] or result_history != round_["results"]:
            raise ValueError("Saved results disagree with the round's result revision history.")
        if round_["status"] == "closed":
            closed += 1
            for identifier, record in _round_records(round_).items():
                expected[identifier].append(record)
    if result["completedRounds"] != closed:
        raise ValueError("Completed-round count disagrees with closed issued rounds.")
    if any(sorted(p["results"], key=lambda r: r["round"]) != expected[p["id"]] for p in result["players"]):
        raise ValueError("Completed player history does not match the closed issued rounds and their saved results.")
    draft = managed.get("draft")
    if draft is not None:
        if not isinstance(draft, dict) or draft.get("snapshotHash") != _draft_digest(draft):
            raise ValueError("The generated draft changed or its checksum is missing.")
        if any(r["status"] == "issued" for r in rounds):
            raise ValueError("A new draft cannot coexist with an in-progress issued round.")
    if rounds:
        history_issues, _ = audit_history(pairing_input(result), normalize_rules(rounds[-1]["snapshot"]["rules"]))
        history_issues = [issue for issue in history_issues if not issue.startswith("All scheduled rounds are complete")]
        if history_issues:
            raise ValueError("Managed completed history is inconsistent: " + " ".join(history_issues))
    return result


def status(tournament, rules):
    event = _copy(tournament)
    rules = validate_rules(event, rules)
    managed, active = event["managed"], _active_round(event)
    draft = deepcopy(managed.get("draft"))
    issues, warnings = [], []
    if draft:
        draft["quality"] = _quality(event, rules, draft["forecast"])
        if draft["inputHash"] != pairing_input_hash(event) or draft["rulesHash"] != rules_hash(rules):
            draft["quality"]["canIssue"] = False
            draft["quality"]["issues"].append("The draft is stale. Regenerate it with current inputs and rules.")
        issues.extend(draft["quality"]["issues"])
        warnings.extend(draft["quality"]["warnings"])
    if len(event["players"]) < 2:
        issues.append("Add at least two players to generate the first round.")
    if rules["profile"] == "us-chess": warnings.append(US_LIMITS)
    standings = sorted(({"id": p["id"], "name": p["name"], "rating": pairing_rating(p), "score": player_score(p, event["completedRounds"]),
                         "active": p.get("active", True), "pairingNumber": p.get("pairingNumber")} for p in event["players"]),
                       key=lambda p: (-p["score"], p["name"].casefold(), p["id"]))
    previous_score, rank = None, 0
    for index, player in enumerate(standings, 1):
        if player["score"] != previous_score: rank = index
        player["rank"], previous_score = rank, player["score"]
    unresolved = [b["board"] for b in active["snapshot"]["pairings"] if str(b["board"]) not in active["results"]] if active else []
    complete = event["completedRounds"] >= event["rounds"]
    return {"enabled": True, "eventId": managed["eventId"], "revision": managed["revision"],
        "stage": "complete" if complete else "in-progress" if active else "draft" if draft else "setup" if not managed["rounds"] else "ready",
        "activeRound": deepcopy(active), "draft": draft, "unresolvedBoards": unresolved,
        "canGenerate": not active and not complete and len(event["players"]) >= 2,
        "canIssue": bool(draft and draft["quality"]["canIssue"] and not active), "canClose": bool(active and not unresolved),
        "canReopen": bool(managed["rounds"] and not active and not draft), "standings": standings,
        "issues": list(dict.fromkeys(issues)), "warnings": list(dict.fromkeys(warnings)),
        "engineLimits": US_LIMITS if rules["profile"] == "us-chess" else "Pinned BBP Dutch engine; unsupported inputs or unavailable native execution block issuance.",
        "notice": NOTICE, "standingsNote": "Ranked by recorded points; equal scores share rank. No tie-break system has been applied."}
