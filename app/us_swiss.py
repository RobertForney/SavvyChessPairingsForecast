"""US Chess procedural Swiss forecasting.

This module follows ordered score brackets and natural split-half pairings.
It deliberately keeps required opponent repairs separate from optional color
switches: a rating difference between eventual opponents is never a switch
limit. Tournament-director discretion still prevents a unique universal result.
"""
from __future__ import annotations

import heapq
import itertools
import time
from copy import deepcopy


class SearchStopped(Exception):
    pass


class _Planner:
    def __init__(self, tournament, rules, players, started, allow_leader_series=False):
        from .pairing import PLAYED_RESULTS
        self.tournament, self.rules = tournament, rules
        self.completed = tournament["completedRounds"]
        self.players = {p["id"]: p for p in players}
        self.started = started
        self.deadline = started + rules["searchMillis"] / 1000
        self.nodes = 0
        self.stopped = False
        self.quota_reached = False
        self.allow_leader_series = allow_leader_series
        self.edge_cache = {}
        self.has_reentries = any(p.get("reentryOf") for p in tournament["players"])
        self.history = {
            p["id"]: {r["round"]: (r.get("color") if r["result"] in PLAYED_RESULTS and r.get("played", True) else None)
                       for r in p.get("results", []) if r["round"] <= self.completed}
            for p in players}
        self.colors = {identifier: [c for _, c in sorted(history.items()) if c]
                       for identifier, history in self.history.items()}
        self.opponents = {p["id"]: {r["opponentId"] for r in p.get("results", [])
                                    if r["round"] <= self.completed and r["result"] in PLAYED_RESULTS
                                    and r.get("played", True) and r.get("opponentId")}
                          for p in players}
        self.prohibited = {frozenset(pair) for pair in tournament.get("prohibitedPairs", [])}
        self.forced_colors = {}
        self.forced = []
        self.forced_partner = {}
        for pair in tournament.get("forcedPairs", []):
            if isinstance(pair, dict):
                a, b = pair["whiteId"], pair["blackId"]
                self.forced_colors[frozenset((a, b))] = (a, b)
            else:
                a, b = pair
            self.forced.append((a, b))
            self.forced_partner[a], self.forced_partner[b] = b, a

    def tick(self):
        self.nodes += 1
        if self.nodes > self.rules["maxNodes"] or time.perf_counter() >= self.deadline:
            self.stopped = True
            raise SearchStopped

    def rating(self, identifier):
        p = self.players[identifier]
        return p.get("pairingRating", p.get("rating", 0))

    def number(self, identifier):
        p = self.players[identifier]
        number = p.get("pairingNumber", p.get("seed"))
        if number is not None:
            return (0, int(number))
        return (1, int(identifier)) if str(identifier).isdecimal() else (2, str(identifier))

    def key(self, identifier):
        p = self.players[identifier]
        if self.rules.get("pairingOrder", "rating") == "pairing-number":
            return (-p["pairingScore"], self.number(identifier), -self.rating(identifier), str(identifier))
        return (-p["pairingScore"], -self.rating(identifier), self.number(identifier), str(identifier))

    def due(self, identifier):
        colors = self.colors[identifier]
        balance = colors.count("W") - colors.count("B")
        if balance:
            return ("B" if balance > 0 else "W"), balance
        return (("B" if colors[-1] == "W" else "W") if colors else None), 0

    def _priority_orientation(self, a, b):
        """29E4 conflict allocation; compare actual rounds, retaining unplayed gaps."""
        da, ba = self.due(a)
        db, bb = self.due(b)
        if da and db and da != db:
            return ((a, b) if da == "W" else (b, a)), "Both players receive their due colors (29E3–4)"
        if da and not db:
            return ((a, b) if da == "W" else (b, a)), "Only one player has a due color (29E3)"
        if db and not da:
            return ((b, a) if db == "W" else (a, b)), "Only one player has a due color (29E3)"
        if da:
            if abs(ba) != abs(bb):
                winner = a if abs(ba) > abs(bb) else b
                note = "Greater color imbalance receives priority (29E4.1–2)"
            else:
                winner = None
                note = "Higher-ranked player receives due color for identical histories (29E4.5)"
                for rnd in range(self.completed, 0, -1):
                    ca, cb = self.history[a].get(rnd), self.history[b].get(rnd)
                    if ca == cb:
                        continue
                    # With an unplayed gap, the player with a played color in
                    # this round receives its opposite. This is not a compressed
                    # color-string comparison.
                    chosen = a if ca else b
                    previous = ca or cb
                    orientation = (chosen, b if chosen == a else a) if previous == "B" else (b if chosen == a else a, chosen)
                    return orientation, f"Latest different played-color round {rnd} decides priority (29E4.3–4)"
                if winner is None:
                    winner = min((a, b), key=self.key)
            other = b if winner == a else a
            return ((winner, other) if da == "W" else (other, winner)), note
        higher = min((a, b), key=self.key)
        other = b if higher == a else a
        desired = self.rules["firstRoundColor"]
        return ((higher, other) if desired == "W" else (other, higher)), "No played color preference; initial-color convention applies"

    def _quality(self, identifier, color):
        colors = self.colors[identifier]
        due, balance = self.due(identifier)
        after = balance + (1 if color == "W" else -1)
        streak = 1
        for previous in reversed(colors):
            if previous != color:
                break
            streak += 1
        necessary_equalization = abs(after) < abs(balance)
        # An equalizing third color is permitted by 29E5f. Other series are
        # diagnosed, and the bracket's color-repair phase tries to remove them.
        series_limit = self.rules["maxConsecutiveColors"] if self.rules["strictColors"] and self.rules.get("colorPolicy", "us-chess") == "strict" else 2
        series = int(streak > series_limit and not necessary_equalization)
        major = int(abs(after) >= 2)
        equalization = int(due is not None and color != due and balance != 0)
        alternation = int(due is not None and color != due and balance == 0)
        return (series, major, equalization, alternation), abs(after), streak

    def edge(self, a, b):
        pair = frozenset((a, b))
        if pair in self.edge_cache:
            return self.edge_cache[pair]
        if a == b or pair in self.prohibited:
            self.edge_cache[pair] = None
            return None
        if (a in self.forced_partner and self.forced_partner[a] != b) or (b in self.forced_partner and self.forced_partner[b] != a):
            self.edge_cache[pair] = None
            return None
        if self.rules["avoidRematches"] and (b in self.opponents[a] or a in self.opponents[b]):
            self.edge_cache[pair] = None
            return None
        if self.rules["avoidRematches"] and self.has_reentries:
            from .pairing import reentry_encounter
            if reentry_encounter(self.tournament, a, b, allow_both_reset=True):
                self.edge_cache[pair] = None
                return None
        pa, pb = self.players[a], self.players[b]
        team_policy = self.rules.get("teamPolicy", "prefer" if self.rules["avoidSameTeam"] else "off")
        if team_policy == "forbid" and pa.get("team") and pa.get("team") == pb.get("team"):
            self.edge_cache[pair] = None
            return None
        orientation, note = self._priority_orientation(a, b)
        forced = self.forced_colors.get(pair)
        if forced:
            orientation, note = forced, "Director-specified colors"
        options = [orientation] if forced else [orientation, tuple(reversed(orientation))]
        valid = []
        hard_colors = self.rules["strictColors"] and self.rules.get("colorPolicy", "us-chess") == "strict"
        leader_exception = self.allow_leader_series and pa.get("prizeContender", False) and pb.get("prizeContender", False)
        for order, (white, black) in enumerate(options):
            wq, wb, ws = self._quality(white, "W")
            bq, bb, bs = self._quality(black, "B")
            if hard_colors and (max(wb, bb) > self.rules["maxColorImbalance"] or (max(ws, bs) > self.rules["maxConsecutiveColors"] and not leader_exception)):
                continue
            quality = tuple(x + y for x, y in zip(wq, bq))
            # Standard US policy uses the specific color-entitlement procedure,
            # not an additive optimizer that silently reverses that priority.
            valid.append((order, quality, white, black))
        if not valid:
            result = None
        else:
            order, quality, white, black = min(valid)
            team = int(team_policy == "prefer" and pa.get("team") and pa.get("team") == pb.get("team"))
            result = {"white": white, "black": black, "quality": quality,
                      "team": team, "note": note if not order else "Custom hard color limits override ordinary color entitlement",
                      "lastRoundException": bool(leader_exception and quality[0])}
            if result["lastRoundException"]:
                result["note"] += "; explicit last-round prize-contender series exception (29E5f1)"
        self.edge_cache[pair] = result
        return result

    def canonical(self, pairs):
        return tuple(sorted((tuple(sorted(pair, key=self.key)) for pair in pairs), key=lambda pair: (self.key(pair[0]), self.key(pair[1]))))

    def signature(self, pairs):
        return tuple(sorted(tuple(sorted(pair)) for pair in pairs))

    def quality(self, pairs):
        result = [0, 0, 0, 0, 0]
        for a, b in pairs:
            edge = self.edge(a, b)
            if edge is None:
                return None
            for i, value in enumerate(edge["quality"]):
                result[i] += value
            result[4] += edge["team"]
        return tuple(result)

    def _component_switches(self, original, candidate):
        """Evaluate disjoint final two-board exchanges against the original.

        No accumulation of individually cheap intermediate moves can hide an
        over-limit final switch. Pure larger opposite-half cycles use a
        conservative maximum final displacement on either equivalent side.
        Larger mixed interchanges/float cycles remain unsupported.
        """
        old_partner = {x: y for a, b in original for x, y in ((a, b), (b, a))}
        new_partner = {x: y for a, b in candidate for x, y in ((a, b), (b, a))}
        changed = {x for x in old_partner if old_partner[x] != new_partner[x]}
        switches = []
        while changed:
            start = min(changed, key=self.key)
            component, todo = set(), [start]
            while todo:
                x = todo.pop()
                if x in component:
                    continue
                component.add(x)
                todo.extend((old_partner[x], new_partner[x]))
            changed -= component
            boards = [pair for pair in original if pair[0] in component]
            if len(component) > 4:
                left = {a for a, _ in boards}
                right = {b for _, b in boards}
                if (len({self.players[x]["pairingScore"] for x in component}) != 1
                        or any(new_partner[x] not in right for x in left)
                        or any(new_partner[x] not in left for x in right)):
                    return None
                def displacement(x, y):
                    return abs(self.rating(x) - self.rating(y)) if self.rating(x) and self.rating(y) else 0
                upper = max(displacement(x, old_partner[new_partner[x]]) for x in left)
                lower = max(displacement(x, old_partner[new_partner[x]]) for x in right)
                switches.append(("shift transposition", min(upper, lower)))
                continue
            if len(component) != 4:
                return None
            (a, b), (c, d) = boards
            scores = {x: self.players[x]["pairingScore"] for x in component}
            if len(set(scores.values())) > 1:
                # Across an odd-player board the two equivalent swaps can
                # straddle a score boundary (the published 29E7 example 5).
                counts = {score: sum(value == score for value in scores.values()) for score in set(scores.values())}
                triple = next((score for score, count in counts.items() if count == 3), None)
                if triple is not None:
                    outside = next(x for x in component if scores[x] != triple)
                    old_odd, new_odd = old_partner[outside], new_partner[outside]
                    order = sorted([x for x in old_partner if self.players[x]["pairingScore"] == triple], key=self.key)
                    lo, hi = sorted((order.index(old_odd), order.index(new_odd)))
                    intermediate = order[lo + 1:hi]
                    if any(self.edge(x, outside) and self.edge(old_odd, old_partner[x]) for x in intermediate):
                        # The next-lowest odd player is the first alternative;
                        # do not jump over a viable intermediate odd player just
                        # because an equivalent far-side rating gap is smaller.
                        return None
                    other_old, other_new = old_partner[new_odd], old_partner[old_odd]
                    alternatives = ((old_odd, new_odd), (other_old, other_new))
                    cost = min(abs(self.rating(x) - self.rating(y)) if self.rating(x) and self.rating(y) else 0 for x, y in alternatives)
                    switches.append(("transposition", cost))
                    continue
            # In a transposition each new board still joins opposite halves of
            # these two original boards. The equivalent upper/lower swaps must
            # both be considered when calculating the smaller rating change.
            if new_partner[a] == d:
                alternatives = ((a, c), (b, d))
                cost = min(abs(self.rating(x) - self.rating(y)) if self.rating(x) and self.rating(y) else 0 for x, y in alternatives)
                kind = "transposition"
            else:
                # A–B/C–D becomes A–C/B–D through the boundary interchange B↔C.
                cost = abs(self.rating(b) - self.rating(c)) if self.rating(b) and self.rating(c) else 0
                kind = "interchange"
            switches.append((kind, cost))
        return switches

    def _neighbors(self, pairs, only_board=None, color_only=False):
        for i in range(len(pairs)):
            if only_board is not None and i != only_board:
                continue
            for j in range(i + 1, len(pairs)) if only_board is None else range(len(pairs)):
                if i == j:
                    continue
                a, b = pairs[i]
                c, d = pairs[j]
                lo, hi = min(i, j), max(i, j)
                section = pairs[lo:hi + 1]
                shift = (color_only and self.rules.get("usTranspositionStyle", "shift") == "shift"
                         and hi - lo > 1
                         and len({self.players[x]["pairingScore"] for pair in section for x in pair}) == 1)
                if shift:
                    upper = [pair[0] for pair in section]
                    lower = [pair[1] for pair in section]
                    for rotated in ([lower[-1]] + lower[:-1], lower[1:] + [lower[0]]):
                        self.tick()
                        changed = list(pairs)
                        changed[lo:hi + 1] = list(zip(upper, rotated))
                        yield self.canonical(changed)
                else:
                    self.tick()
                    changed = list(pairs)
                    changed[i], changed[j] = (a, d), (c, b)
                    yield self.canonical(changed)
                # A boundary interchange stays a two-board operation in both
                # styles; shift only changes optional transposition generation.
                self.tick()
                changed = list(pairs)
                changed[i], changed[j] = (a, c), (b, d)
                yield self.canonical(changed)

    def mandatory_plans(self, identifiers):
        if not identifiers:
            return [((), [])]
        ordered = sorted(identifiers, key=self.key)
        half = len(ordered) // 2
        natural = tuple(zip(ordered[:half], ordered[half:]))
        if all(self.edge(a, b) is not None for a, b in natural):
            return [(natural, [])]
        # Repair the first prohibited board, retaining a complete matching in
        # every search state. Unbounded rating changes are allowed when needed
        # for no-repeat/director restrictions rather than mere color preference.
        queue, serial, visited = [], itertools.count(), set()
        heapq.heappush(queue, (0, 0, next(serial), natural))
        results = []
        while queue and not results:
            self.tick()
            _, _, _, pairs = heapq.heappop(queue)
            signature = self.signature(pairs)
            if signature in visited:
                continue
            visited.add(signature)
            bad = [i for i, (a, b) in enumerate(pairs) if self.edge(a, b) is None]
            if not bad:
                results.append((pairs, ["Required opponent/restriction repair precedes optional color switches (27A1, 29C2)"]))
                continue
            for candidate in self._neighbors(pairs, bad[0]):
                key = self.signature(candidate)
                if key in visited:
                    continue
                invalid = sum(self.edge(a, b) is None for a, b in candidate)
                switches = self._component_switches(natural, candidate)
                distance = sum(cost for _, cost in switches) if switches is not None else sum(abs(self.rating(a) - self.rating(b)) for a, b in candidate)
                heapq.heappush(queue, (invalid, distance, next(serial), candidate))
        return results

    def color_plans(self, original, preserve_brackets=False):
        initial_quality = self.quality(original)
        self.last_color_candidate = (original, [], initial_quality)
        if not original or initial_quality == (0, 0, 0, 0, 0):
            return [(original, [], initial_quality)]
        frontier = [(original, [], initial_quality)]
        final = []
        visited = {self.signature(original)}
        width = max(2, self.rules["candidateCount"])
        pattern = lambda pairs: sorted(tuple(sorted((self.players[a]["pairingScore"], self.players[b]["pairingScore"]), reverse=True)) for a, b in pairs)
        original_pattern = pattern(original)
        while frontier:
            next_frontier = []
            for current, notes, quality in frontier:
                improvements = []
                top_down = self.rules.get("usColorMethod", "look-ahead") == "top-down"
                for candidate in self._neighbors(current, color_only=True):
                    if preserve_brackets and pattern(candidate) != original_pattern:
                        continue
                    signature = self.signature(candidate)
                    if signature in visited:
                        continue
                    visited.add(signature)
                    new_quality = self.quality(candidate)
                    if new_quality is None or new_quality >= quality:
                        continue
                    switches = self._component_switches(original, candidate)
                    if switches is None:
                        continue
                    major_improvement = new_quality[1:3] < initial_quality[1:3]
                    limit = self.rules.get("usColorEqualizationLimit", 200) if major_improvement else self.rules.get("usColorAlternationLimit", 80)
                    if self.rules.get("usEqualizationPriority", False) and major_improvement:
                        limit = float("inf")
                    if any(cost > limit for _, cost in switches):
                        continue
                    preference = 0 if all(kind in {"transposition", "shift transposition"} and cost <= self.rules.get("usColorAlternationLimit", 80) for kind, cost in switches) else 1
                    cost = sum(cost for _, cost in switches)
                    description = "; ".join(f"{kind.capitalize()} of {value:g} rating points" for kind, value in switches)
                    candidate_pairs = {frozenset(pair) for pair in candidate}
                    first_changed_conflict = min((index for index, pair in enumerate(current)
                                                  if frozenset(pair) not in candidate_pairs and any(self.edge(*pair)["quality"])), default=len(current))
                    improvements.append((new_quality, preference, cost, signature, candidate, [f"{description}; compared with the pre-color natural pairing (29E5)"], first_changed_conflict))
                if not improvements:
                    final.append((current, notes, quality))
                else:
                    if top_down:
                        # An unrepairable early board does not end the pass:
                        # continue to the earliest later board that can improve.
                        first_repairable = min(row[6] for row in improvements)
                        improvements = [row for row in improvements if row[6] == first_repairable]
                    improvements.sort(key=lambda row: row[:4])
                    best_quality = improvements[0][0]
                    best_preference = improvements[0][1]
                    best_cost = improvements[0][2]
                    # Alternatives are equal-priority director choices, rather
                    # than arbitrary weaker matchings dressed as probabilities.
                    for row in improvements:
                        if row[:3] != (best_quality, best_preference, best_cost):
                            break
                        next_frontier.append((row[4], row[5], row[0]))
                        if row[0] < self.last_color_candidate[2]:
                            self.last_color_candidate = (row[4], row[5], row[0])
            next_frontier.sort(key=lambda row: (row[2], self.signature(row[0])))
            frontier = next_frontier[:width]
        final.sort(key=lambda row: (row[2], self.signature(row[0])))
        return final[:width] or [(original, [], initial_quality)]

    def incoming_choices(self, pending, residents, target_outgoing):
        """Pair higher-bracket odd players before the resident split."""
        def walk(index, available, pairs, skipped, notes):
            self.tick()
            if len(skipped) > target_outgoing:
                return
            if index == len(pending):
                yield available, pairs, skipped, notes
                return
            a = pending[index]
            choices = [b for b in sorted(available, key=self.key) if self.edge(a, b)]
            for b in choices:
                yield from walk(index + 1, [x for x in available if x != b], pairs + [(a, b)], skipped,
                                notes + [f"Higher-score odd player paired into the closest available lower bracket (29D); opponent {b}"])
            yield from walk(index + 1, available, pairs, skipped + [a], notes)
        yield from walk(0, list(residents), [], [], [])

    def local_options(self, residents, pending, outgoing_count):
        for remaining, incoming_pairs, skipped, incoming_notes in self.incoming_choices(pending, residents, outgoing_count):
            drop_count = outgoing_count - len(skipped)
            if not 0 <= drop_count <= len(remaining) or (len(remaining) - drop_count) % 2:
                continue
            # Lowest rated *rated* player is the ordinary odd player. Unrated
            # players are considered only after rated candidates are exhausted.
            ordinary = sorted(remaining, key=lambda x: (int(not self.rating(x)), self.rating(x), tuple(-v if isinstance(v, int) else v for v in self.number(x))))
            for dropped in itertools.combinations(ordinary, drop_count):
                self.tick()
                kept = [x for x in remaining if x not in dropped]
                for baseline, mandatory_notes in self.mandatory_plans(kept):
                    outgoing = sorted(skipped + list(dropped), key=self.key)
                    notes = incoming_notes + mandatory_notes
                    if dropped:
                        notes.append("Lowest eligible rated odd player tried first; backtracking protects completion of lower groups (29D1)")
                    yield incoming_pairs + list(baseline), outgoing, notes, self.quality(baseline)

    def solve(self, groups, index=0, pending=()):
        if index >= len(groups):
            if not pending:
                yield [], [], []
            return
        self.tick()
        residents = groups[index]
        total = len(residents) + len(pending)
        for outgoing_count in range(total % 2, total + 1, 2):
            if index == len(groups) - 1 and outgoing_count:
                continue
            found = False
            for pairs, outgoing, notes, quality in self.local_options(residents, list(pending), outgoing_count):
                for tail_pairs, tail_notes, path in self.solve(groups, index + 1, outgoing):
                    found = True
                    yield pairs + tail_pairs, notes + tail_notes, [{"pairingScore": self.players[residents[0]]["pairingScore"], "outgoing": outgoing_count, "quality": list(quality)}] + path
            if found:
                return


def forecast_us(tournament, rules):
    from .pairing import audit_history, pairing_score, player_score

    started = time.perf_counter()
    completed, rnd = tournament["completedRounds"], tournament["completedRounds"] + 1
    issues, warnings = audit_history(tournament, rules)
    def active(p):
        return p.get("active", True) and p.get("entryRound", 1) <= rnd <= p.get("withdrawnAfterRound", tournament["rounds"])
    all_players = [dict(deepcopy(p), score=player_score(p, completed), pairingScore=pairing_score(p, rnd)) for p in tournament["players"] if active(p)]
    requested = [{"player": p, "points": b["points"]} for p in all_players for b in p.get("scheduledByes", []) if b["round"] == rnd]
    excused = {b["player"]["id"] for b in requested}
    players = [p for p in all_players if p["id"] not in excused]
    result = {"round": rnd, "status": "blocked", "blockingReason": "history", "pairings": [], "bye": None,
              "requestedByes": requested, "candidates": [], "issues": issues, "warnings": warnings,
              "engine": "Savvy US Chess procedural forecast", "rules": rules,
              "engineVersion": "us-swiss-procedural-3",
              "ruleEdition": "US Chess 2026-01-01",
              "scope": "Ordered US Chess Swiss score brackets with natural pairings, required opponent repairs and bounded color switches. Pure shift cycles use conservative final-displacement limits; complex mixed interchange/float cycles, director discretion and merged-schedule reconstruction may produce different official pairings.",
              "objectiveLabels": ["Bye order", "Cross-score pairs", "Pairing score gap", "Color series exceptions", "Color imbalance cases", "Equalization misses", "Alternation misses"],
              "objective": None,
              "stats": {"nodes": 0, "elapsedMs": 0, "exhaustive": False, "activePlayers": len(players), "scoreGroups": len({p["pairingScore"] for p in players}), "searchSpace": "US procedural score-bracket candidates"}}
    if not players and not requested:
        issues.append("No active players are available.")
    if issues:
        result["stats"]["elapsedMs"] = round((time.perf_counter() - started) * 1000)
        return result
    planner = _Planner(tournament, rules, players, started)
    if rules["allowRepeatBye"]:
        warnings.append("Custom controls permit a repeat pairing-allocated bye.")
    if not rules["avoidRematches"]:
        warnings.append("Custom controls permit repeated played opponents.")
    if not rules["strictColors"]:
        warnings.append("Custom hard color limits are disabled; ordinary US Chess color priorities still apply.")
    if rules["strictColors"] and rules.get("colorPolicy", "us-chess") == "strict":
        warnings.append(f"Custom strict color policy: maximum imbalance {rules['maxColorImbalance']} and same-color streak {rules['maxConsecutiveColors']}. These hard limits can prohibit pairings allowed by ordinary US Chess preferences.")
    if rules.get("usEqualizationPriority", False):
        warnings.append("US Chess variation 29E5h selected: improving color equalization takes priority over the rating-switch limits.")
    elif (rules.get("usColorAlternationLimit", 80), rules.get("usColorEqualizationLimit", 200)) != (80, 200):
        warnings.append("Custom color-switch limits replace the standard US Chess 80/200-point limits.")
    if rules.get("usColorMethod", "look-ahead") == "top-down":
        warnings.append("US Chess top-down color-adjustment variation 29E6b selected instead of look-ahead.")
    if rules.get("usTranspositionStyle", "shift") == "direct-swap":
        warnings.append("Direct-swap transpositions selected consistently (US Chess 29E7 example 2); intervening boards are left in place.")
    if rules.get("pairingOrder", "rating") == "pairing-number":
        warnings.append("Explicit pairing-number order replaces ordinary US Chess rating order within score groups.")
    forced_ids = {x for pair in planner.forced for x in pair}
    for a, b in planner.forced:
        if a not in planner.players or b not in planner.players:
            issues.append("A forced pairing names a player who is inactive, absent or taking a requested bye.")
    if issues:
        result["blockingReason"] = "constraints"
        return result

    def previous_full_award(p):
        return any(r["result"] in {"BYE", "FORFEIT_WIN"} or (not r.get("played", r["result"] in {"W", "D", "L", "ADJOURNED"}) and r.get("points", {"HBYE": .5, "W": 1}.get(r["result"], 0)) >= 1)
                   for r in p.get("results", []) if r["round"] <= completed)

    def half_bye(p):
        return any(r["result"] == "HBYE" for r in p.get("results", []) if r["round"] <= completed) or any(b["points"] == .5 for b in p.get("scheduledByes", []))

    def eligible(p, pool):
        if rules["allowRepeatBye"]:
            return True
        if previous_full_award(p) or (completed == 0 and p.get("lateEntry", False)):
            return False
        if half_bye(p):
            group = [q for q in pool if q["pairingScore"] == p["pairingScore"] and q["id"] != p["id"]]
            return all(previous_full_award(q) for q in group)
        return True

    free = [p for p in players if p["id"] not in forced_ids]
    byes = [None]
    if len(free) % 2:
        byes = sorted([p for p in free if eligible(p, players)], key=lambda p: (
            p["pairingScore"], int(not planner.rating(p["id"])),
            int(not p.get("hasPriorRatedGames", False)) if not planner.rating(p["id"]) else 0,
            planner.rating(p["id"]), planner.number(p["id"])))
        if not byes:
            issues.append("No eligible pairing-bye recipient under US Chess 28L3–4. Review earlier awards and requested byes.")
            result["blockingReason"] = "constraints"
            return result
    found, seen = [], set()
    def collect():
        if any(planner.edge(a, b) is None for a, b in planner.forced):
            return
        try:
            for bye_index, bye in enumerate(byes):
                groups_by_score = {}
                for p in sorted(free, key=lambda p: planner.key(p["id"])):
                    if bye is None or p["id"] != bye["id"]:
                        groups_by_score.setdefault(p["pairingScore"], []).append(p["id"])
                groups = list(groups_by_score.values())
                for pairs, notes, path in planner.solve(groups):
                    pairs = list(planner.forced) + pairs
                    try:
                        variants = planner.color_plans(pairs, preserve_brackets=True)
                    except SearchStopped:
                        # The full natural bracket round already exists. Keep
                        # its best valid color refinement when optimization ends.
                        variants = [planner.last_color_candidate]
                    for colored, color_notes, quality in variants:
                        signature = (bye["id"] if bye else None, planner.signature(colored))
                        if signature in seen:
                            continue
                        seen.add(signature)
                        crossing = sum(planner.players[a]["pairingScore"] != planner.players[b]["pairingScore"] for a, b in colored)
                        gap = sum(abs(planner.players[a]["pairingScore"] - planner.players[b]["pairingScore"]) for a, b in colored)
                        objective = [bye_index, crossing, gap, *quality[:4]]
                        found.append((colored, bye, objective, list(dict.fromkeys(notes + color_notes)), path))
                        if len(found) >= rules["candidateCount"]:
                            planner.quota_reached = True
                            return
                    if planner.stopped:
                        return
                if found:
                    # A higher-rated recipient is considered only if the ordinary
                    # recipient cannot complete the round. 28L2a is not silently
                    # enabled as an unannounced forecast assumption.
                    return
        except SearchStopped:
            pass

    collect()
    if (not found and not planner.stopped and rules.get("allowLastRoundExceptions", False)
            and rnd == tournament["rounds"] and rules.get("colorPolicy") == "strict"):
        previous_nodes = planner.nodes
        planner = _Planner(tournament, rules, players, started, allow_leader_series=True)
        planner.nodes = previous_nodes
        collect()
        if found:
            warnings.append("Explicit last-round series exception used only for paired players marked prizeContender (US Chess variation 29E5f1); ordinary strict pairings were tried first.")

    def boards(pairs, notes):
        ordered = sorted(pairs, key=lambda pair: min(planner.key(pair[0]), planner.key(pair[1])))
        output = []
        for number, (a, b) in enumerate(ordered, 1):
            edge = planner.edge(a, b)
            white, black = planner.players[edge["white"]], planner.players[edge["black"]]
            explanation = ["Same pairing-score bracket (27A2, 29C1)" if white["pairingScore"] == black["pairingScore"] else "Odd player paired into a lower score bracket (29D)", edge["note"]]
            if white["pairingScore"] != white["score"] or black["pairingScore"] != black["score"]:
                explanation.append(f"Actual scores {white['score']:g}/{black['score']:g}; pairing scores {white['pairingScore']:g}/{black['pairingScore']:g} include explicit virtual points")
            explanation.append(f"Played color histories: {''.join(planner.history[white['id']].get(r) or 'x' for r in range(1, completed + 1)) or 'none'} / {''.join(planner.history[black['id']].get(r) or 'x' for r in range(1, completed + 1)) or 'none'}")
            explanation.extend("Round procedure: " + note for note in notes if "rating points" in note or "Required opponent" in note)
            output.append({"board": number, "white": white, "black": black,
                           "scoreGap": abs(white["score"] - black["score"]),
                           "pairingScoreGap": abs(white["pairingScore"] - black["pairingScore"]), "explanations": explanation})
        # First-round alternating colors are board-based, after final opponent
        # selection and bye removal, rather than based on original roster parity.
        if completed == 0:
            for index, row in enumerate(output):
                if frozenset((row["white"]["id"], row["black"]["id"])) in planner.forced_colors:
                    continue
                higher = min((row["white"]["id"], row["black"]["id"]), key=planner.key)
                due = rules["firstRoundColor"] if index % 2 == 0 else ("B" if rules["firstRoundColor"] == "W" else "W")
                higher_current = "W" if row["white"]["id"] == higher else "B"
                if due != higher_current:
                    row["white"], row["black"] = row["black"], row["white"]
        return output

    for index, (pairs, bye, objective, notes, path) in enumerate(found):
        candidate = {"id": str(index + 1), "label": "US Chess procedural candidate" if not index else f"Alternative bracket choice {index}",
                     "pairings": boards(pairs, notes), "bye": bye, "byePoints": rules["byePoints"],
                     "objective": objective, "procedure": notes, "bracketPath": path}
        result["candidates"].append(candidate)
    if found:
        best = result["candidates"][0]
        result.update(status="ok", blockingReason=None, pairings=best["pairings"], bye=best["bye"], byePoints=rules["byePoints"], objective=best["objective"], procedure=best["procedure"])
        if best["objective"][3]:
            warnings.append("This candidate contains a same-color series beyond the configured preference. US Chess 29E5f director review is required; strict color policy would prohibit it.")
        if best["bye"] and half_bye(best["bye"]) and not rules["allowRepeatBye"]:
            warnings.append("US Chess 28L4 exception: the bye recipient has a prior/committed half-point bye, and all other players in the score group already have a full-point bye or unplayed win.")
        if planner.stopped:
            warnings.append("The US procedural search reached its budget. A complete candidate was found; further alternatives may exist.")
    else:
        result["blockingReason"] = "search-limit" if planner.stopped else "constraints"
        if any(planner.edge(a, b) is None for a, b in planner.forced):
            issues.append("A forced pairing conflicts with a repeat-opponent, team, prohibited-pair or hard-color restriction.")
        issues.append("US procedural search reached its budget before finding a complete round. Increase the budget or review restrictions."
                      if planner.stopped else "No complete US Chess bracket pairing satisfies the selected restrictions.")
    result["stats"].update(nodes=planner.nodes, elapsedMs=round((time.perf_counter() - started) * 1000),
                           exhaustive=not planner.stopped and not planner.quota_reached,
                           candidateQuotaReached=planner.quota_reached)
    return result
