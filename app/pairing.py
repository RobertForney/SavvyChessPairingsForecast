"""Common Swiss inputs, history validation and explicit procedure dispatch."""

from __future__ import annotations

import math
import time
import hashlib
import json
from datetime import datetime, timezone
from copy import deepcopy


DEFAULT_RULES = {
    "profile": "us-chess", "avoidRematches": True, "strictColors": True,
    "maxColorImbalance": 2, "maxConsecutiveColors": 2,
    "avoidSameTeam": False, "firstRoundColor": "W", "byePoints": 1,
    "allowRepeatBye": False, "searchMillis": 1200, "maxNodes": 100000,
    "candidateCount": 3,
    "pairingOrder": "rating", "colorPolicy": "us-chess",
    "usColorMethod": "look-ahead", "usColorAlternationLimit": 80,
    "usTranspositionStyle": "shift",
    "usColorEqualizationLimit": 200, "usEqualizationPriority": False,
    "allowLastRoundExceptions": False, "teamPolicy": "off",
}
POINTS = {"W": 1, "D": .5, "L": 0, "BYE": 1, "HBYE": .5,
          "ZERO": 0, "FORFEIT_WIN": 1, "FORFEIT_LOSS": 0, "UNPLAYED": 0,
          "ADJOURNED": .5}
PLAYED_RESULTS = {"W", "D", "L", "ADJOURNED"}
COMPLEMENTS = {"W": "L", "D": "D", "L": "W",
               "FORFEIT_WIN": "FORFEIT_LOSS", "FORFEIT_LOSS": "FORFEIT_WIN", "ADJOURNED": "ADJOURNED"}


def normalize_rules(raw=None):
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("Pairing rules must be an object.")
    rules = {**DEFAULT_RULES, **(raw or {})}
    if raw and "colorPolicy" not in raw and any(k in raw for k in ("strictColors", "maxColorImbalance", "maxConsecutiveColors")):
        rules["colorPolicy"] = "strict"
    if raw and "teamPolicy" not in raw and raw.get("avoidSameTeam"):
        rules["teamPolicy"] = "prefer"
    if rules["profile"] not in {"us-chess", "fide-constraints", "fide-dutch"}:
        raise ValueError("Unknown rule profile.")
    for key in ("avoidRematches", "strictColors", "avoidSameTeam", "allowRepeatBye", "usEqualizationPriority", "allowLastRoundExceptions"):
        if not isinstance(rules[key], bool):
            raise ValueError(f"{key} must be true or false.")
    for key, low, high in (("maxColorImbalance", 1, 6), ("maxConsecutiveColors", 1, 6),
                          ("searchMillis", 50, 5000), ("maxNodes", 100, 500000),
                          ("candidateCount", 1, 5), ("usColorAlternationLimit", 0, 1000),
                          ("usColorEqualizationLimit", 0, 1000)):
        value = rules[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} must be a number.")
        rules[key] = max(low, min(high, int(value)))
    if rules["firstRoundColor"] not in {"W", "B"}:
        raise ValueError("First-round color must be W or B.")
    if isinstance(rules["byePoints"], bool) or rules["byePoints"] not in {0, .5, 1}:
        raise ValueError("Bye points must be 0, 0.5 or 1.")
    for key, allowed in (("pairingOrder", {"rating", "pairing-number"}),
                         ("colorPolicy", {"us-chess", "strict"}),
                         ("usColorMethod", {"look-ahead", "top-down"}),
                         ("usTranspositionStyle", {"direct-swap", "shift"}),
                         ("teamPolicy", {"off", "prefer", "forbid"})):
        if rules[key] not in allowed:
            raise ValueError(f"Unknown {key} value.")
    rules["avoidSameTeam"] = rules["teamPolicy"] != "off"
    return {key: rules[key] for key in DEFAULT_RULES}


def player_score(player, through_round=None):
    return float(player.get("initialScore", 0)) + sum(
        float(r.get("points", POINTS.get(r["result"], 0)))
        for r in player.get("results", [])
        if through_round is None or r["round"] <= through_round)


def pairing_rating(player):
    """An explicitly assigned pairing rating is independent of published rating."""
    return player.get("pairingRating") if player.get("pairingRating") is not None else player.get("rating", 0)


def pairing_score(player, pairing_round):
    """Fictitious acceleration points never change actual standings."""
    return player_score(player, pairing_round - 1) + sum(v["points"] for v in player.get("virtualPoints", []) if v["round"] == pairing_round)


def starting_order_key(player, mode="rating"):
    identifier = str(player["id"])
    identity = (0, int(identifier)) if identifier.isdecimal() else (1, identifier)
    number = player.get("pairingNumber", player.get("seed"))
    number_key = (0, number) if number is not None else (1, 0)
    if mode == "pairing-number":
        return (number_key, -pairing_rating(player), identity)
    return (-pairing_rating(player), number_key, identity)


def rank_key(player):
    return (-player.get("pairingScore", player["score"]), *starting_order_key(player))


def restrictions(tournament):
    prohibited = {frozenset(map(str, pair)) for pair in tournament.get("prohibitedPairs", [])}
    forced, orientations = {}, {}
    for pair in tournament.get("forcedPairs", []):
        if isinstance(pair, dict):
            a, b = pair["whiteId"], pair["blackId"]
            orientations[frozenset((a, b))] = (a, b)
        else:
            a, b = pair
        forced[a], forced[b] = b, a
    return prohibited, forced, orientations


def entry_lineage(tournament, identifier):
    by_id = {p["id"]: p for p in tournament["players"]}
    lineage = []
    while identifier in by_id and identifier not in lineage:
        lineage.append(identifier)
        identifier = by_id[identifier].get("reentryOf")
    return lineage


def reentry_encounter(tournament, a, b, allow_both_reset=False):
    """US 28S1–3: one reset does not erase the unchanged opponent's history."""
    lineage_a, lineage_b = entry_lineage(tournament, a), set(entry_lineage(tournament, b))
    if set(lineage_a) & lineage_b:
        return True
    by_id = {p["id"]: p for p in tournament["players"]}
    for entry in lineage_a:
        for record in by_id[entry].get("results", []):
            if record["round"] > tournament["completedRounds"] or record["result"] not in PLAYED_RESULTS or not record.get("played", True):
                continue
            opponent = record.get("opponentId")
            if opponent in lineage_b:
                if allow_both_reset and entry != a and opponent != b:
                    continue
                return True
    return False


def audit_history(tournament, rules):
    """Reject incomplete or contradictory evidence instead of filling it in."""
    issues, warnings = [], list(tournament.get("warnings", []))
    completed = tournament["completedRounds"]
    players = tournament["players"]
    settings = tournament.get("sourceSettings", {})
    for scheme in (tournament.get("scoring", {}), settings):
        if any(key in scheme and scheme[key] != expected for key, expected in (("win", 1), ("draw", .5), ("loss", 0))):
            issues.append("Nonstandard win/draw/loss scoring is unsupported. Review the event scoring before forecasting.")
    acceleration = tournament.get("acceleration")
    manual = acceleration == "manual" or (isinstance(acceleration, dict) and acceleration.get("method") == "manual")
    baku = acceleration == "baku" or (isinstance(acceleration, dict) and acceleration.get("method") == "baku")
    native_baku = baku and rules["profile"] == "fide-dutch"
    if baku and not native_baku:
        issues.append("Automatic Baku acceleration requires the native FIDE Dutch profile. Use explicit manual points for a different procedure.")
    elif (acceleration and not (manual or native_baku)) or (settings.get("acceleration") and settings.get("accelerationConfirmed", True) and not (manual or native_baku)):
        issues.append("Confirmed acceleration is unsupported by the constraint forecast. An engine that implements the event's acceleration is required.")
    system = str(tournament.get("pairingSystem", settings.get("pairingSystem", ""))).lower()
    if any(term in system for term in ("burstein", "round robin", "round-robin", "monrad", "lim system", "team")):
        issues.append(f"Pairing system {system!r} is unsupported by this individual Swiss forecast.")
    by_id = {str(p["id"]): p for p in players}
    if rules["profile"] == "us-chess":
        tied_ratings = {}
        for p in players:
            tied_ratings.setdefault(pairing_rating(p), []).append(p)
        if any(len(group) > 1 and any(p.get("pairingNumber", p.get("seed")) is None for p in group) for group in tied_ratings.values()):
            warnings.append("Equal pairing ratings lack a complete definitive starting order. Stable player IDs break ties in this forecast; the director's original lot/seed order may differ (US Chess 28A).")
    if rules.get("pairingOrder") == "pairing-number" and any(p.get("pairingNumber", p.get("seed")) is None for p in players):
        issues.append("Pairing-number order requires a definitive pairingNumber or seed for every player.")
    _, forced, _ = restrictions(tournament)
    available = {p["id"] for p in players if p.get("active", True)
                 and p.get("entryRound", 1) <= completed + 1 <= p.get("withdrawnAfterRound", tournament["rounds"])
                 and not any(b["round"] == completed + 1 for b in p.get("scheduledByes", []))}
    active_entries = {}
    participating = {p["id"] for p in players if p.get("active", True)
                     and p.get("entryRound", 1) <= completed + 1 <= p.get("withdrawnAfterRound", tournament["rounds"])}
    for identifier in participating:
        root = entry_lineage(tournament, identifier)[-1]
        active_entries.setdefault(root, []).append(identifier)
    for entries in active_entries.values():
        if len(entries) > 1:
            issues.append(f"Several entries for the same reentered player are active: {', '.join(sorted(entries))}. Select the entry carried forward under the event's reentry policy.")
    if any(p.get("reentryOf") for p in players):
        warnings.append("Reentry scores and active-entry selection are taken from the reviewed records. Verify the event's best-score or latest-entry policy (US Chess 28S5); abandoned-entry colors are not inherited.")
    for identifier in forced:
        if identifier not in available:
            issues.append(f"Forced pairing includes unavailable player {identifier}. Review participation and requested byes.")
    histories = {p["id"]: {r["round"]: r for r in p.get("results", [])} for p in players}
    for p in players:
        name = p["name"]
        history = histories[p["id"]]
        if len(history) != len(p.get("results", [])):
            issues.append(f"{name}: duplicate records for a round. Resolve conflicting games or scheduled byes.")
        for scheduled in p.get("scheduledByes", []):
            if scheduled["round"] <= completed:
                actual = history.get(scheduled["round"])
                requested_full_award = bool(actual and scheduled["points"] == 1 and actual["result"] == "FORFEIT_WIN")
                if (not actual or actual["result"] not in {"BYE", "HBYE", "ZERO"} and not requested_full_award
                        or actual.get("opponentId")
                        or float(actual.get("points", POINTS.get(actual["result"], 0))) != scheduled["points"]):
                    issues.append(f"{name}: scheduled bye in round {scheduled['round']} conflicts with the recorded history.")
        for rnd in range(1, completed + 1):
            r = history.get(rnd)
            if r is None:
                if p.get("entryRound", 1) <= rnd <= p.get("withdrawnAfterRound", tournament["rounds"]):
                    issues.append(f"{name}: round {rnd} is missing. Record a game, bye or zero-point absence.")
                continue
            result = r["result"]
            opp_id = r.get("opponentId")
            if opp_id and not p.get("entryRound", 1) <= rnd <= p.get("withdrawnAfterRound", tournament["rounds"]):
                issues.append(f"{name}: round {rnd} has an opponent outside the declared entry/withdrawal interval.")
            if result == "UNPLAYED":
                issues.append(f"{name}: round {rnd} has no final result.")
                continue
            if result in {"BYE", "HBYE", "ZERO"} and opp_id:
                issues.append(f"{name}: round {rnd} bye or absence cannot have an opponent.")
            if result not in PLAYED_RESULTS and r.get("played", False):
                issues.append(f"{name}: round {rnd} bye, absence or forfeit cannot be marked played.")
            if result in PLAYED_RESULTS:
                if not opp_id:
                    issues.append(f"{name}: round {rnd} has a result but no opponent.")
                if r.get("played", True) and r.get("color") not in {"W", "B"}:
                    message = f"{name}: round {rnd} has an unknown played color."
                    issues.append(message)
            elif r.get("color"):
                warnings.append(f"{name}: unplayed round {rnd} color is ignored.")
            if opp_id:
                opponent = by_id.get(opp_id)
                if opponent is None:
                    issues.append(f"{name}: round {rnd} references unknown opponent {opp_id}.")
                elif opp_id == p["id"]:
                    issues.append(f"{name}: round {rnd} is a self-pairing.")
                else:
                    reverse = histories[opp_id].get(rnd)
                    if not reverse or reverse.get("opponentId") != p["id"]:
                        issues.append(f"{name}: round {rnd} opponent record is not reciprocal.")
                    elif result in COMPLEMENTS and reverse["result"] != COMPLEMENTS[result] and not (result == reverse["result"] == "FORFEIT_LOSS"):
                        issues.append(f"{name}: round {rnd} disagrees with the opponent's result.")
                    elif result in PLAYED_RESULTS and bool(r.get("played", True)) != bool(reverse.get("played", True)):
                        issues.append(f"{name}: round {rnd} disagrees with the opponent's played status.")
                    elif result in PLAYED_RESULTS and r.get("played", True) and r.get("color") and reverse.get("color") == r["color"]:
                        issues.append(f"{name}: round {rnd} gives both opponents the same color.")
        pending = [r for r in p.get("results", []) if r["round"] > completed]
        if pending:
            issues.append(f"{name}: results exist after completed round {completed}; finish reviewing the round cutoff.")
        computed = player_score(p, completed)
        reported = p.get("reportedScore")
        if reported is not None and abs(float(reported) - computed) > .0001:
            message = f"{name}: reported score {reported:g} differs from recorded score {computed:g}."
            (warnings if any(r["result"] == "ADJOURNED" for r in p.get("results", [])) else issues).append(message)
        if any(r["result"] == "ADJOURNED" for r in p.get("results", [])):
            warnings.append(f"{name}: adjourned games count as draws for this pairing; the final standings result remains provisional.")
    if completed >= tournament["rounds"]:
        issues.append("All scheduled rounds are complete. Increase the tournament length to forecast another round.")
    if len(players) > 200:
        issues.append("This local forecast supports at most 200 players per section. Split larger events into sections.")
    return list(dict.fromkeys(issues)), list(dict.fromkeys(warnings))


def _colors(player, completed):
    return [r["color"] for r in sorted(player.get("results", []), key=lambda r: r["round"])
            if r["round"] <= completed and r["result"] in PLAYED_RESULTS
            and r.get("played", True) and r.get("color") in {"W", "B"}]


def _color_cost(colors, color, rules):
    balance = colors.count("W") - colors.count("B")
    after = balance + (1 if color == "W" else -1)
    streak = 0
    for previous in reversed(colors):
        if previous != color:
            break
        streak += 1
    if rules["strictColors"] and (abs(after) > rules["maxColorImbalance"]
                                  or streak + 1 > rules["maxConsecutiveColors"]):
        return None
    # Equalizing takes precedence over alternation within the forecast heuristic.
    return abs(after) * 4 + (1 if colors and colors[-1] == color else 0)


def _forecast_constraints(tournament, raw_rules=None):
    started = time.perf_counter()
    rules = normalize_rules(raw_rules)
    completed = tournament["completedRounds"]
    issues, warnings = audit_history(tournament, rules)
    def participates_next(p):
        return p.get("active", True) and p.get("entryRound", 1) <= completed + 1 <= p.get("withdrawnAfterRound", tournament["rounds"])

    requested_byes = [{"player": dict(deepcopy(p), score=player_score(p, completed)), "points": b["points"]}
                      for p in tournament["players"] if participates_next(p)
                      for b in p.get("scheduledByes", []) if b["round"] == completed + 1]
    excused = {b["player"]["id"] for b in requested_byes}
    players = [dict(deepcopy(p), score=player_score(p, completed), pairingScore=pairing_score(p, completed + 1))
               for p in tournament["players"] if participates_next(p) and p["id"] not in excused]
    players.sort(key=lambda p: (-p["pairingScore"], *starting_order_key(p, rules["pairingOrder"])))
    base = {
        "round": completed + 1, "status": "blocked", "blockingReason": "history", "pairings": [], "bye": None,
        "candidates": [], "issues": issues, "warnings": warnings,
        "requestedByes": requested_byes,
        "engine": "Savvy custom constraint forecast", "rules": rules,
        "ruleEdition": "Custom constraint matching v1",
        "scope": "Custom matching under selected restrictions; use the US Chess or native FIDE Dutch profile for the corresponding procedure.",
        "objectiveLabels": ["Bye priority", "Total score gap", "Cross-score pairs", "Repeated floats", "Same-team pairs", "Color preference cost", "Split-half displacement"],
        "stats": {"nodes": 0, "elapsedMs": 0, "exhaustive": False,
                  "activePlayers": len(players), "scoreGroups": len({p["pairingScore"] for p in players})},
        "objective": None,
    }
    if not players and not requested_byes:
        issues.append("No active players are available.")
    if issues:
        base["stats"]["elapsedMs"] = round((time.perf_counter() - started) * 1000)
        return base
    if not rules["strictColors"]:
        warnings.append("Hard color limits are disabled. Color legality cannot be guaranteed.")
    if not rules["avoidRematches"]:
        warnings.append("Repeat opponents are allowed by the selected custom controls.")
    if rules["allowRepeatBye"]:
        warnings.append("Repeat pairing-allocated byes are allowed by the selected custom controls.")
    if rules["profile"] == "fide-constraints":
        warnings.append("Custom matching checks selected restrictions; it does not implement the US Chess or FIDE Dutch pairing procedure.")
    n = len(players)
    prohibited, forced, locked_colors = restrictions(tournament)
    has_reentries = any(p.get("reentryOf") for p in tournament["players"])
    colors = [_colors(p, completed) for p in players]
    # Unplayed forfeits do not create a played-opponent restriction.
    opponents = [{r["opponentId"] for r in p.get("results", [])
                  if r["round"] <= completed and r["result"] in PLAYED_RESULTS and r.get("played", True) and r.get("opponentId")}
                 for p in players]
    by_id = {p["id"]: p for p in tournament["players"]}
    last_float = []
    for p in players:
        last = next((r for r in p.get("results", []) if r["round"] == completed), None)
        direction = 0
        if last and last.get("opponentId") in by_id and last["result"] in PLAYED_RESULTS and last.get("played", True):
            opp = by_id[last["opponentId"]]
            delta = pairing_score(p, completed) - pairing_score(opp, completed)
            direction = (delta > 0) - (delta < 0)
        last_float.append(direction)
    groups = {}
    for i, p in enumerate(players):
        groups.setdefault(p["pairingScore"], []).append(i)
    split_targets = {}
    for indices in groups.values():
        half = (len(indices) + 1) // 2
        for position, i in enumerate(indices):
            split_targets[i] = indices[position + half] if position + half < len(indices) else indices[max(0, position - half)]
    edges = {}
    for i in range(n):
        for j in range(i + 1, n):
            a, b = players[i], players[j]
            pair_key = frozenset((a["id"], b["id"]))
            if pair_key in prohibited or (a["id"] in forced and forced[a["id"]] != b["id"]) or (b["id"] in forced and forced[b["id"]] != a["id"]):
                continue
            if rules["teamPolicy"] == "forbid" and a.get("team") and a.get("team") == b.get("team"):
                continue
            if rules["avoidRematches"] and (b["id"] in opponents[i] or a["id"] in opponents[j]):
                continue
            if has_reentries and rules["avoidRematches"] and reentry_encounter(tournament, a["id"], b["id"]):
                continue
            orientations = []
            for white, black in ((i, j), (j, i)):
                if pair_key in locked_colors and locked_colors[pair_key] != (players[white]["id"], players[black]["id"]):
                    continue
                wc = _color_cost(colors[white], "W", rules)
                bc = _color_cost(colors[black], "B", rules)
                if wc is not None and bc is not None:
                    # A first-round top seed gets the configured initial color;
                    # alternate the upper-half players' color down the boards.
                    initial_penalty = 0
                    if completed == 0:
                        desired = rules["firstRoundColor"] if i % 2 == 0 else ("B" if rules["firstRoundColor"] == "W" else "W")
                        initial_penalty = int(("W" if white == i else "B") != desired)
                    orientations.append((wc + bc + initial_penalty, white, black))
            if not orientations:
                continue
            color_cost, white, black = min(orientations)
            gap = abs(a["pairingScore"] - b["pairingScore"])
            direction = (a["pairingScore"] > b["pairingScore"]) - (a["pairingScore"] < b["pairingScore"])
            repeat_float = int(direction != 0 and last_float[i] == direction) + int(direction != 0 and last_float[j] == -direction)
            team = int(rules["avoidSameTeam"] and bool(a.get("team")) and a.get("team") == b.get("team"))
            displacement = abs(j - split_targets[i]) + abs(i - split_targets[j])
            cost = (gap, int(gap > 0), repeat_float, team, color_cost, displacement)
            edges[i, j] = (cost, white, black)

    def edge(i, j):
        return edges.get((min(i, j), max(i, j)))

    def bye_eligible(p):
        if p["id"] in forced:
            return False
        if rules["allowRepeatBye"]:
            return True
        committed_half_bye = rules["profile"] == "us-chess" and any(b["points"] == .5 for b in p.get("scheduledByes", []))
        return not committed_half_bye and not any(r["result"] in {"BYE", "FORFEIT_WIN"}
                       or (not r.get("played", r["result"] in PLAYED_RESULTS) and float(r.get("points", POINTS[r["result"]])) >= 1)
                       or (rules["profile"] == "us-chess" and r["result"] == "HBYE")
                       for r in p.get("results", []))

    bye_choices = [None]
    if n % 2:
        eligible = [i for i in range(n) if bye_eligible(players[i])]
        # US 28L2 prefers a rated recipient within the lowest eligible score group.
        bye_choices = sorted(eligible, key=lambda i: (
            players[i]["pairingScore"],
            int(pairing_rating(players[i]) == 0) if rules["profile"] == "us-chess" else 0,
            pairing_rating(players[i]), -i))
        if not bye_choices:
            base["blockingReason"] = "constraints"
            issues.append("No player is eligible for the pairing-allocated bye under the selected controls.")
            return base
    retained = []
    seen = set()
    nodes = 0
    stopped = False
    deadline = started + rules["searchMillis"] / 1000

    def visit(remaining, pairs, total, bye):
        nonlocal nodes, stopped
        nodes += 1
        if nodes > rules["maxNodes"] or time.perf_counter() >= deadline:
            stopped = True
            return
        if len(retained) >= rules["candidateCount"] and total > retained[-1][0]:
            return
        if not remaining:
            signature = (bye, tuple(sorted((min(w, b), max(w, b)) for w, b in pairs)))
            if signature not in seen:
                seen.add(signature)
                retained.append((total, list(pairs), bye))
                retained.sort(key=lambda c: (c[0], c[1]))
                del retained[rules["candidateCount"]:]
            return
        # Most-constrained player first avoids greedy dead ends.
        choices = []
        for i in remaining:
            possible = [j for j in remaining if i != j and edge(i, j)]
            if not possible:
                return
            choices.append((len(possible), i, possible))
        _, i, possible = min(choices, key=lambda c: (c[0], c[1]))
        possible.sort(key=lambda j: (edge(i, j)[0], j))
        for j in possible:
            cost, white, black = edge(i, j)
            summed = (total[0],) + tuple(a + b for a, b in zip(total[1:], cost))
            visit(remaining - {i, j}, pairs + [(white, black)], summed, bye)
            if stopped:
                return

    for priority, bye in enumerate(bye_choices):
        visit(set(range(n)) - ({bye} if bye is not None else set()), [], (priority, 0, 0, 0, 0, 0, 0), bye)
        if stopped:
            break

    def boards(pairs):
        ordered = sorted(pairs, key=lambda pair: (min(pair), max(pair)))
        output = []
        for board, (w, b) in enumerate(ordered, 1):
            a, z = players[w], players[b]
            gap = abs(a["pairingScore"] - z["pairingScore"])
            explanation = ["Same score group" if gap == 0 else f"Float between score groups ({gap:g}-point gap)"]
            explanation.append("No previous played encounter" if z["id"] not in opponents[w] else "Repeat encounter permitted by custom controls")
            explanation.append(f"Played color history: {''.join(colors[w]) or 'none'} / {''.join(colors[b]) or 'none'}")
            if a["pairingScore"] != a["score"] or z["pairingScore"] != z["score"]:
                explanation.append("Explicit virtual pairing points are included; actual standings scores are unchanged")
            if a["id"] in forced:
                explanation.append("Director-locked pairing")
            if rules["avoidSameTeam"] and a.get("team") and a.get("team") == z.get("team"):
                explanation.append("Same-team pairing remains in this candidate")
            output.append({"board": board, "white": a, "black": z, "scoreGap": gap, "explanations": explanation})
        return output

    for index, (objective, pairs, bye) in enumerate(retained):
        base["candidates"].append({"id": str(index + 1), "label": "Best found" if index == 0 else f"Alternative {index}",
                                   "pairings": boards(pairs), "bye": players[bye] if bye is not None else None,
                                   "byePoints": rules["byePoints"], "objective": list(objective)})
    if retained:
        best = base["candidates"][0]
        base.update(status="ok", blockingReason=None, pairings=best["pairings"], bye=best["bye"],
                    byePoints=rules["byePoints"], objective=best["objective"])
        if stopped:
            warnings.append("Search limit reached. These are the best candidates found; global optimality is not established.")
    else:
        base["blockingReason"] = "search-limit" if stopped else "constraints"
        issues.append("Search limit reached before finding a complete pairing. Increase the search budget or review controls."
                      if stopped else "No complete pairing satisfies the selected hard restrictions. Review history, colors and bye eligibility.")
    base["stats"].update(nodes=nodes, elapsedMs=round((time.perf_counter() - started) * 1000), exhaustive=not stopped)
    return base


def engine_status():
    """Inspect local engine availability; never download while forecasting."""
    try:
        from .dutch import availability
    except ModuleNotFoundError as exc:
        if exc.name != "app.dutch":
            raise
        return {"dutch": {"available": False, "version": None, "ruleEdition": "FIDE 2026-02-01",
                           "reason": "The pinned Dutch engine is not installed. Run python scripts/setup_dutch_engine.py."}}
    return {"dutch": availability()}


def _unavailable_dutch(tournament, rules):
    issues, warnings = audit_history(tournament, rules)
    history_blocked = bool(issues)
    if not history_blocked:
        issues.append(engine_status()["dutch"]["reason"])
    active = [p for p in tournament["players"] if p.get("active", True)]
    return {"round": tournament["completedRounds"] + 1, "status": "blocked",
            "blockingReason": "history" if history_blocked else "engine",
            "pairings": [], "bye": None, "requestedByes": [], "candidates": [], "issues": issues,
            "warnings": warnings, "rules": rules, "engine": "BBP Pairings Dutch (unavailable)",
            "scope": "The Dutch profile never substitutes the custom matching algorithm for the official procedure.",
            "ruleEdition": "FIDE 2026-02-01", "objective": None, "objectiveLabels": [],
            "stats": {"nodes": 0, "elapsedMs": 0, "exhaustive": False, "activePlayers": len(active), "scoreGroups": len({pairing_score(p, tournament['completedRounds'] + 1) for p in active})}}


def _forecast_pool(tournament, raw_rules=None):
    """Dispatch one pairing pool; schedule orchestration must not recurse here."""
    rules = normalize_rules(raw_rules)
    if rules["profile"] == "us-chess":
        from .us_swiss import forecast_us
        result = forecast_us(tournament, rules)
        result.setdefault("engineVersion", "savvy-us-swiss/2")
    elif rules["profile"] == "fide-dutch":
        try:
            from .dutch import forecast_dutch
        except ModuleNotFoundError as exc:
            if exc.name != "app.dutch":
                raise
            result = _unavailable_dutch(tournament, rules)
        else:
            result = forecast_dutch(tournament, rules)
    else:
        result = _forecast_constraints(tournament, rules)
        result.setdefault("engineVersion", "savvy-constraints/1")
        result["scope"] = "Custom constraint matching with an additive lexicographic objective. This mode is not the US Chess procedural engine or the FIDE Dutch engine."
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    result["inputHash"] = hashlib.sha256(canonical(tournament)).hexdigest()
    result["rulesHash"] = hashlib.sha256(canonical(rules)).hexdigest()
    result["generatedAt"] = datetime.now(timezone.utc).isoformat()
    return result


def forecast(tournament, raw_rules=None):
    """Forecast announced pairing pools consistently in live and replay use."""
    from .phases import forecast_phases
    return forecast_phases(tournament, normalize_rules(raw_rules), _forecast_pool)
