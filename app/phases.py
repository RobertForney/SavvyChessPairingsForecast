"""Shared schedule orchestration for live and historical Swiss forecasts.

Pool engines receive only their own roster before an announced merge. The
callback never calls this orchestrator, so partitioning cannot recurse.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _stamp(result, tournament, rules):
    result["inputHash"], result["rulesHash"] = _digest(tournament), _digest(rules)
    result["generatedAt"] = datetime.now(timezone.utc).isoformat()
    return result


def _context(mode, policy, merge_round, schedules):
    return {"mode": mode, "policy": policy, "mergeRound": merge_round, "schedules": schedules}


def _blocked(tournament, rules, context, issues, reason="settings", warnings=None):
    rnd = tournament["completedRounds"] + 1
    active = [p for p in tournament["players"] if p.get("active", True)
              and p.get("entryRound", 1) <= rnd <= p.get("withdrawnAfterRound", tournament["rounds"])]
    return _stamp({"round": rnd, "status": "blocked", "blockingReason": reason,
                   "pairings": [], "bye": None, "byes": [], "requestedByes": [], "candidates": [],
                   "issues": list(dict.fromkeys(issues)), "warnings": list(dict.fromkeys(warnings or tournament.get("warnings", []))),
                   "rules": rules, "engine": "Schedule configuration", "engineVersion": "schedule-orchestration/1",
                   "scope": "No combined pairing is issued until every announced pairing pool can be forecast consistently.",
                   "ruleEdition": "FIDE 2026-02-01" if rules["profile"] == "fide-dutch" else "US Chess 2026-01-01" if rules["profile"] == "us-chess" else None,
                   "objective": None, "objectiveLabels": [], "phaseForecasts": [], "scheduleContext": context,
                   "stats": {"nodes": 0, "elapsedMs": 0, "exhaustive": False, "activePlayers": len(active), "scoreGroups": 0, "phases": 0}},
                  tournament, rules)


def _forced_ids(pair):
    return (pair["whiteId"], pair["blackId"]) if isinstance(pair, dict) else tuple(pair)


def forecast_phases(tournament, rules, pool_forecast):
    """Apply explicit schedule policy, then call an individual-pool engine.

    ``single`` treats schedule labels as metadata; ``separate`` keeps pools
    separate throughout the event; ``merge`` separates them until mergeRound.
    A legacy explicit mergeRound also declares merge policy. Multiple named
    schedules without a policy are ambiguous and block instead of crossing.
    """
    settings = tournament.get("sourceSettings", {})
    policy = settings.get("schedulePolicy")
    merge_round = settings.get("mergeRound")
    rnd = tournament["completedRounds"] + 1
    labels = {p["id"]: str(p.get("schedule") or "").strip() for p in tournament["players"]}
    named = sorted(set(labels.values()) - {""})
    schedules = sorted(set(labels.values()))
    display_schedules = [name or "Unspecified schedule" for name in schedules]
    if policy in {None, ""}:
        policy = "merge" if merge_round is not None else "unknown"
    context = _context("unconfirmed", policy, merge_round, display_schedules)
    if policy not in {"unknown", "single", "separate", "merge"}:
        return _blocked(tournament, rules, context, ["Choose whether schedules form one pairing pool, stay separate, or merge at an announced round."])
    if policy == "unknown" and len(schedules) > 1 and named:
        return _blocked(tournament, rules, context, ["Multiple schedules have no confirmed pairing-pool policy. Set schedulePolicy to single or separate, or declare mergeRound."])
    if policy == "merge" and (isinstance(merge_round, bool) or not isinstance(merge_round, int)
                              or not 1 <= merge_round <= tournament["rounds"] + 1):
        return _blocked(tournament, rules, context, ["A merging event needs a whole mergeRound from 1 through the round after the event."])
    separate = policy == "separate" or policy == "merge" and rnd < merge_round
    if separate and "" in schedules and (named or settings.get("schedulePolicy") in {"separate", "merge"}):
        return _blocked(tournament, rules, context, ["Assign every entry to its announced schedule before forecasting separate pairing pools."])
    if not separate or len(schedules) < 2:
        result = pool_forecast(tournament, rules)
        mode = "merged" if policy == "merge" and rnd >= merge_round else "single"
        result["scheduleContext"] = _context(mode, policy, merge_round, display_schedules)
        return result

    context = _context("separate", policy, merge_round, display_schedules)
    by_id = {p["id"]: p for p in tournament["players"]}
    cross_locks = [pair for pair in tournament.get("forcedPairs", [])
                   if any(i not in labels for i in _forced_ids(pair))
                   or len({labels[i] for i in _forced_ids(pair)}) > 1]
    cross_entries = [p for p in tournament["players"] if p.get("reentryOf")
                     and (p["reentryOf"] not in labels or labels[p["reentryOf"]] != labels[p["id"]])]
    if cross_locks or cross_entries:
        return _blocked(tournament, rules, context,
                        ["A locked pairing or reentry link spans unmerged schedules. Review the announced pairing pools and entry links."])
    cross_history = [p for p in tournament["players"] for r in p.get("results", [])
                     if r["round"] <= tournament["completedRounds"] and r.get("opponentId") in by_id
                     and labels[p["id"]] != labels[r["opponentId"]]]
    if cross_history:
        return _blocked(tournament, rules, context,
                        ["Recorded opponents span schedules that are declared separate at this cutoff. Review schedule assignments and merge timing."], "history")

    # Check the entire historical roster, including pools with no active entry.
    # Local starting-number completeness is checked again inside each pool.
    from .pairing import audit_history, starting_order_key
    from .validation import validate_tournament
    issues, global_warnings = audit_history(tournament, {**rules, "pairingOrder": "rating"})
    if issues:
        return _blocked(tournament, rules, context, issues, "history", global_warnings)
    phases = []
    numbering_notes = []
    for schedule in schedules:
        players = [deepcopy(p) for p in tournament["players"] if labels[p["id"]] == schedule]
        if not any(p.get("active", True) and p.get("entryRound", 1) <= rnd <= p.get("withdrawnAfterRound", tournament["rounds"]) for p in players):
            continue
        identifiers = {p["id"] for p in players}
        local_numbers = [p.get("schedulePairingNumber") for p in players]
        if any(n is not None for n in local_numbers):
            if (any(isinstance(n, bool) or not isinstance(n, int) for n in local_numbers)
                    or set(local_numbers) != set(range(1, len(players) + 1))):
                return _blocked(tournament, rules, context,
                                [f"Schedule {schedule!r} needs complete consecutive schedulePairingNumber values from 1 through its roster size."])
            ordered = sorted(players, key=lambda p: p["schedulePairingNumber"])
            numbering_notes.append(f"{schedule}: explicit schedule-local starting pairing numbers are used.")
        else:
            mode = "pairing-number" if all(p.get("pairingNumber", p.get("seed")) is not None for p in players) else "rating"
            ordered = sorted(players, key=lambda p: starting_order_key(p, mode))
            numbering_notes.append(f"{schedule}: schedule-local initial pairing numbers are derived from the reviewed starting order; confirm the director's actual local order and initial colors.")
        for number, p in enumerate(ordered, 1):
            p["sourcePairingNumber"] = p.get("pairingNumber", p.get("seed"))
            p["pairingNumber"] = p.get("schedulePairingNumber") or number
        phase = deepcopy(tournament)
        phase["section"] = f"{tournament.get('section', 'Open')} / {schedule}"
        phase["players"] = ordered
        phase["prohibitedPairs"] = [pair for pair in tournament.get("prohibitedPairs", []) if set(pair) <= identifiers]
        phase["forcedPairs"] = [pair for pair in tournament.get("forcedPairs", []) if set(_forced_ids(pair)) <= identifiers]
        phases.append({"schedule": schedule, "forecast": pool_forecast(validate_tournament(phase), rules)})
    if not phases:
        result = pool_forecast(tournament, rules)
        result["scheduleContext"] = context
        return result

    issues = [f"{p['schedule']}: {issue}" for p in phases for issue in p["forecast"]["issues"]]
    warnings = ["Each announced schedule is forecast as an independent pairing pool according to the declared pool policy."]
    warnings += numbering_notes
    warnings += [f"{p['schedule']}: {warning}" for p in phases for warning in p["forecast"]["warnings"]]
    pairings, byes, requested = [], [], []
    for phase in phases:
        outcome, schedule = phase["forecast"], phase["schedule"]
        for pair in outcome["pairings"]:
            pairings.append({**deepcopy(pair), "board": len(pairings) + 1, "schedule": schedule})
        if outcome.get("bye"):
            byes.append({"player": deepcopy(outcome["bye"]), "points": outcome.get("byePoints", rules["byePoints"]), "schedule": schedule})
        requested.extend({**deepcopy(bye), "schedule": schedule} for bye in outcome.get("requestedByes", []))
    blocked = [p["forecast"] for p in phases if p["forecast"]["status"] != "ok"]
    ok = not blocked
    reason = next((kind for kind in ("history", "settings", "engine", "search-limit", "constraints")
                   if any(f.get("blockingReason") == kind for f in blocked)), "constraints")
    base = deepcopy(phases[0]["forecast"])
    base.update(status="ok" if ok else "blocked", blockingReason=None if ok else reason,
                engine="Schedule forecast · " + phases[0]["forecast"]["engine"],
                scope="Schedules are forecast independently; the first candidate from each pool forms the combined forecast. No probability or global rank is assigned to combinations of alternatives.",
                issues=issues, warnings=list(dict.fromkeys(warnings)), requestedByes=requested,
                phaseForecasts=phases, scheduleContext=context, pairings=pairings if ok else [], byes=byes if ok else [],
                bye=byes[0]["player"] if ok and len(byes) == 1 else None,
                objective=None, objectiveLabels=[], candidates=[])
    if "procedure" in base:
        base["procedure"] = [f"{p['schedule']}: {step}" for p in phases for step in p["forecast"].get("procedure", [])]
    if "engineDetails" in base:
        base["engineDetails"].pop("inputSha256", None)
        base["engineDetails"].pop("diagnostic", None)
        base["engineDetails"]["phaseInputs"] = [{"schedule": p["schedule"], "inputSha256": p["forecast"].get("engineDetails", {}).get("inputSha256")} for p in phases]
    base["stats"] = {"nodes": sum(p["forecast"]["stats"].get("nodes", 0) for p in phases),
                     "elapsedMs": sum(p["forecast"]["stats"].get("elapsedMs", 0) for p in phases),
                     "exhaustive": all(p["forecast"]["stats"].get("exhaustive", False) for p in phases),
                     "activePlayers": sum(p["forecast"]["stats"].get("activePlayers", 0) for p in phases),
                     "scoreGroups": sum(p["forecast"]["stats"].get("scoreGroups", 0) for p in phases), "phases": len(phases)}
    if ok:
        base["candidates"] = [{"id": "1", "label": "Combined schedule forecast", "pairings": deepcopy(pairings),
                               "bye": deepcopy(base["bye"]), "byes": deepcopy(byes), "byePoints": rules["byePoints"], "objective": None}]
    return _stamp(base, tournament, rules)
