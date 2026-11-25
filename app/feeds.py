"""Reviewable standings refreshes independent of a tournament or publisher layout.

Published row numbers resolve that snapshot's opponent references only. They
never replace the application's stable identities or the director's seeds.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from .importers import ImportError, normalize_tournament, parse_bytes
from .pairing import POINTS, PLAYED_RESULTS
from .sources import MAX_BYTES, fetch_source
from .validation import validate_tournament


def snapshot_hash(tournament):
    """An optimistic-concurrency fingerprint of the current reviewed workspace."""
    return hashlib.sha256(json.dumps(tournament, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _name(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _header(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.title = ""
        self.heading = ""
        self._title = False
        self._heading = None
        self._table = None
        self._row = None
        self._cell = None
        self._depth = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if self._skip:
            if tag not in {"br", "img", "hr", "input", "meta", "link", "wbr"}:
                self._skip += 1
            return
        hidden = tag in {"script", "style", "noscript"} or "hidden" in values or values.get("aria-hidden") == "true"
        hidden |= bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", values.get("style", ""), re.I))
        if hidden:
            self._skip = 1
            return
        if tag == "title": self._title = True
        if tag in {"h1", "h2", "h3", "caption"}: self._heading = []
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._table = {"label": self.heading, "rows": []}
        if self._depth != 1:
            return
        if tag == "tr": self._row = []
        if tag in {"td", "th"}: self._cell = {"parts": [], "links": []}
        if tag == "a" and self._cell is not None and values.get("href"):
            self._cell["links"].append(values["href"])

    def handle_endtag(self, tag):
        if self._skip:
            self._skip -= 1
            return
        if tag == "title": self._title = False
        if tag in {"h1", "h2", "h3", "caption"} and self._heading is not None:
            self.heading = " ".join(self._heading).strip()
            self._heading = None
            if tag == "caption" and self._table is not None:
                self._table["label"] = self.heading
        if self._depth == 1:
            if tag in {"td", "th"} and self._cell is not None:
                self._cell["text"] = " ".join(self._cell.pop("parts")).strip()
                if self._row is not None: self._row.append(self._cell)
                self._cell = None
            if tag == "tr" and self._row is not None:
                self._table["rows"].append(self._row)
                self._row = None
        if tag == "table":
            if self._depth == 1 and self._table is not None:
                self.tables.append(self._table)
                self._table = None
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        if self._skip: return
        if self._title: self.title += data
        if self._heading is not None and data.strip(): self._heading.append(data.strip())
        if self._cell is not None and data.strip(): self._cell["parts"].append(data.strip())


_ALIASES = {
    "name": {"name", "player", "playername", "participant"},
    "id": {"id", "no", "nr", "number", "rank", "pair", "pairno", "pairingno", "startno"},
    "rating": {"rating", "elo", "rtg", "r", "prerating", "preeventrating"},
    "score": {"score", "pts", "points", "total"},
    "usChessId": {"uschessid", "uscfid", "uscf", "memberid"},
    "fideId": {"fideid", "fide"},
    "pairingNumber": {"pairingnumber", "initialpairingnumber", "startingnumber", "tpn", "seed"},
    "section": {"section", "group", "category", "eventsection"},
}


def _columns(row):
    headers = [_header(cell["text"]) for cell in row]
    fields = {field: next((i for i, value in enumerate(headers) if value in aliases), None)
              for field, aliases in _ALIASES.items()}
    # An empty "#" header is common, but an unnamed column is not assumed a rank.
    for i, cell in enumerate(row):
        if cell["text"].strip() == "#": fields["id"] = i
    rounds = [(int(match.group(1)), i) for i, value in enumerate(headers)
              if (match := re.fullmatch(r"(?:r|rd|round)(\d+)", value))]
    return fields, rounds


def _round(text, rnd):
    token = " ".join(str(text).strip().split())
    if not token or token in {"-", "--", "—"}: return None
    upper = token.upper()
    special = {"B": "BYE", "BYE": "BYE", "H": "HBYE", "HBYE": "HBYE",
               "U": "ZERO", "ZERO": "ZERO", "UNPLAYED": "UNPLAYED"}
    if upper in special:
        result = special[upper]
        return {"round": rnd, "opponentId": None, "result": result, "played": False,
                "color": None, "sourceCode": token, **({"availabilityUnknown": True} if upper == "U" else {})}
    if "⚪" in token and "⚫" in token:
        raise ImportError(f"Conflicting colors in round {rnd} token {token!r}.")
    color = "W" if "⚪" in token else "B" if "⚫" in token else None
    cleaned = token.replace("⚪", "").replace("⚫", "").replace("\ufe0f", "").strip()
    match = re.fullmatch(r"([WDLXF])\s*(\d+)(?:\s*\(?([wb])\)?)?", cleaned, re.I)
    if match:
        code, opponent, suffix = match.groups()
        result = {"X": "FORFEIT_WIN", "F": "FORFEIT_LOSS"}.get(code.upper(), code.upper())
        if suffix:
            if color and color != suffix.upper(): raise ImportError(f"Conflicting colors in round {rnd} token {token!r}.")
            color = suffix.upper()
    else:
        match = re.fullmatch(r"(\d+)\s*([wb])\s*(1(?:\.0)?|0(?:\.0)?|(?:0?\.5)|½|=|\+|-)", cleaned, re.I)
        if match:
            opponent, suffix, code = match.groups()
            color = suffix.upper()
            result = {"1": "W", "1.0": "W", "0": "L", "0.0": "L", ".5": "D", "0.5": "D", "½": "D", "=": "D", "+": "FORFEIT_WIN", "-": "FORFEIT_LOSS"}[code]
        elif cleaned in {"1", "1.0", "0", "0.0", "0.5", ".5", "½", "="}:
            opponent = None
            result = "W" if cleaned in {"1", "1.0"} else "L" if cleaned in {"0", "0.0"} else "D"
        else:
            raise ImportError(f"Unrecognized round {rnd} token {token!r}. Supply a CSV/TRF export with opponent and color history.")
    played = result in PLAYED_RESULTS
    return {"round": rnd, "opponentId": opponent, "result": result, "played": played,
            "color": color if played else None, "sourceCode": token}


def _tabular(rows, filename, title="", section=None, source_url=None):
    header_index = None
    for i, row in enumerate(rows[:15]):
        fields, rounds = _columns(row)
        if fields["name"] is not None:
            header_index = i
            break
    if header_index is None:
        raise ImportError("No recognizable player/name header was found in this standings table.")
    section_values = {}
    if fields["section"] is not None:
        for row in rows[header_index + 1:]:
            if fields["name"] >= len(row) or not row[fields["name"]]["text"].strip(): continue
            if _header(row[fields["name"]]["text"]) in _ALIASES["name"]: continue
            value = row[fields["section"]]["text"].strip() if fields["section"] < len(row) else ""
            if not value:
                raise ImportError("A named player has no section in the published section column. Review the section before import.")
            section_values[_name(value)] = value
        if section and _name(section) not in section_values:
            raise ImportError("The selected section was not found. Available sections: " + "; ".join(section_values.values()))
        if not section and len(section_values) > 1:
            raise ImportError("Choose one section before importing. Available sections: " + "; ".join(section_values.values()))
        if not section and section_values:
            section = next(iter(section_values.values()))
    players = []
    for index, row in enumerate(rows[header_index + 1:], 1):
        def get(field):
            column = fields[field]
            return row[column]["text"].strip() if column is not None and column < len(row) else ""
        name = get("name")
        if not name or _header(name) in _ALIASES["name"]: continue
        if len(row) <= fields["name"]: continue
        if fields["section"] is not None and section and _name(get("section")) != _name(section): continue
        identifier = get("id") or str(index)
        if not re.fullmatch(r"\S+", identifier):
            # State/flag decorations after a table row number are not identities.
            first = identifier.split()[0]
            if first.isdecimal(): identifier = first
            else: raise ImportError("A standings row number has an ambiguous layout.")
        player = {"id": identifier, "name": name, "rating": 0, "results": [], "sourceId": identifier}
        starting_number = get("pairingNumber")
        if starting_number:
            if not starting_number.isdecimal() or int(starting_number) < 1:
                raise ImportError("An explicit initial pairing-number column must contain positive whole numbers.")
            player["pairingNumber"] = int(starting_number)
        for field in ("usChessId", "fideId"):
            value = get(field)
            if value:
                if not value.isdecimal(): raise ImportError(f"The {field} column must contain a numeric membership ID.")
                player[field] = value
        name_cell = row[fields["name"]]
        for link in name_cell.get("links", []):
            host = urlsplit(link).hostname or urlsplit(source_url or "").hostname
            if host == "ratings.uschess.org" and (member := re.search(r"/player/(\d{8})(?:[/?#]|$)", link)):
                player["usChessId"] = member.group(1)
                if member.group(1) in name:
                    player["name"] = name.split(member.group(1), 1)[0].strip()
                    if value := re.search(r"/\s*R\s+(\d+|----)", name):
                        player["rating"] = 0 if value.group(1) == "----" else int(value.group(1))
        rating = get("rating")
        if rating:
            if rating not in {"-", "----", "UR", "Unrated"}:
                if not rating.isdecimal(): raise ImportError(f"Unrecognized rating {rating!r}; map a numeric pre-event rating column.")
                player["rating"] = int(rating)
        score = get("score").replace("½", ".5")
        if score:
            try: player["reportedScore"] = float(score)
            except ValueError as exc: raise ImportError("The points column contains an unrecognized value.") from exc
        for rnd, column in rounds:
            if column < len(row) and (record := _round(row[column]["text"], rnd)):
                player["results"].append(record)
        players.append(player)
    if not players:
        raise ImportError("No player rows were found beneath the standings headers.")
    warnings = ["Published row numbers identify this snapshot only; they are not original pairing numbers."]
    if not rounds:
        warnings.append("This standings source supplies no round history. Totals do not establish opponents, results or played colors.")
    return normalize_tournament({"name": title or Path(filename).stem, "section": section or "Open", "rounds": 50,
                                 "players": players, "warnings": warnings,
                                 "feed": {"identityScope": "snapshot-rows", "roundsKnown": False,
                                          "historyColumns": [rnd for rnd, _ in rounds]}}, filename, "Standings table")


def parse_feed_rows(rows, filename="standings", section=None):
    """Map a worksheet or delimited table through the same explicit headers."""
    if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
        raise ImportError("Standings rows must be a list of cell lists.")
    cells = [[{"text": str(value) if value is not None else "", "links": []} for value in row] for row in rows]
    return _tabular(cells, filename, section=section)


def parse_feed_bytes(data, filename="standings.html", section=None, source_url=None):
    """Parse supported exports or one explicitly recognizable static table."""
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ImportError("Provide a standings file of at most 8 MB.")
    text = data.decode("utf-8-sig", errors="replace")
    html = Path(filename).suffix.lower() in {".html", ".htm"} or bool(re.search(r"<(?:html|table|!doctype)\b", text[:1000], re.I))
    if html:
        parser = _Tables()
        parser.feed(text)
        candidates = []
        for i, table in enumerate(parser.tables, 1):
            if any(_columns(row)[0]["name"] is not None for row in table["rows"][:15]):
                candidates.append((str(i), table))
        if section:
            matching = [(i, table) for i, table in candidates if section == i or _name(section) == _name(table["label"])]
            if matching: candidates = matching
            elif len(candidates) > 1:
                raise ImportError("The selected section was not found. Available tables: " + "; ".join(i + ": " + (t["label"] or "unnamed") for i, t in candidates))
        if len(candidates) == 1:
            _, table = candidates[0]
            # A heading may be an event title rather than a section name. An
            # explicit Section column remains authoritative when supplied.
            has_section_column = any(_columns(row)[0]["section"] is not None for row in table["rows"][:15])
            selected_section = section if has_section_column else table["label"] or section
            result = _tabular(table["rows"], filename, parser.title.strip(), selected_section, source_url)
            result.setdefault("feed", {})["sourceTableLabel"] = table["label"] or None
            result["feed"]["requestedSection"] = section
            if section and table["label"] and _name(section) != _name(table["label"]):
                result["warnings"].append(f"The source table is labelled {table['label']!r}; you requested section {section!r}. Confirm its section in the preview before applying.")
            return result
        if len(candidates) > 1:
            raise ImportError("Choose one section before importing. Available tables: " + "; ".join(i + ": " + (t["label"] or "unnamed") for i, t in candidates))
        try:
            return parse_bytes(data, filename)
        except ValueError as exc:
            raise ImportError("This page does not expose a supported standings table. JavaScript-only ratings pages need a publisher CSV/TRF/JSON export or a saved rendered table; no game history was inferred.") from exc
    if Path(filename).suffix.lower() in {".csv", ".tsv"}:
        try: dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error: dialect = csv.excel_tab if Path(filename).suffix.lower() == ".tsv" else csv.excel
        rows = [[{"text": value, "links": []} for value in row] for row in csv.reader(io.StringIO(text), dialect)]
        return _tabular(rows, filename, section=section, source_url=source_url)
    return parse_bytes(data, filename)


def fetch_feed(url, section=None):
    """User-initiated fetch; TLS, address pinning and redirect limits stay shared."""
    data, resolved, content_type, cached = fetch_source(url, allow_public=True, max_age=30)
    suffix = Path(urlsplit(resolved).path).suffix.lower()
    filename = "standings" + (suffix if suffix in {".xlsx", ".json", ".csv", ".tsv", ".trf", ".txt", ".html", ".htm"} else ".html" if "html" in content_type else ".json" if "json" in content_type else ".txt")
    tournament = parse_feed_bytes(data, filename, section=section, source_url=resolved)
    return {"tournament": tournament, "sourceUrl": resolved, "contentType": content_type, "cached": cached}


def preview_feed(current, incoming, source_url=None):
    """Merge a publisher snapshot for explicit review without writing any state."""
    if not isinstance(current, dict) or not isinstance(incoming, dict):
        raise ValueError("Current workspace and incoming standings must be tournament objects.")
    source = validate_tournament(incoming)
    merged = deepcopy(current)
    players = deepcopy(current.get("players", []))
    by_id = {str(p["id"]): p for p in players}
    members = {field: defaultdict(list) for field in ("usChessId", "fideId")}
    names = defaultdict(list)
    for player in players:
        names[_name(player["name"])].append(player)
        for field, entries in members.items():
            if player.get(field): entries[str(player[field])].append(player)
    summary = {"addedPlayers": 0, "updatedPlayers": 0, "addedResults": 0, "updatedResults": 0,
               "unchangedResults": 0, "preservedRecords": 0, "identityMatches": [], "corrections": [], "conflicts": [],
               "missingColors": [], "missingHistory": [], "setupDifferences": [], "warnings": list(source.get("warnings", [])), "previousCompletedRounds": int(current.get("completedRounds", 0))}
    mapping, claimed, seen_members = {}, set(), set()
    for original in source["players"]:
        matches = []
        for field, entries in members.items():
            if not original.get(field): continue
            value = str(original[field])
            if (field, value) in seen_members: raise ValueError("Incoming standings contain a duplicate membership ID. Review the source identities.")
            seen_members.add((field, value))
            matches += [(p, field) for p in entries.get(value, [])]
        unique = {p["id"] for p, method in matches}
        if len(unique) > 1 or len(matches) > 1 and len({method for p, method in matches}) == 1:
            raise ValueError(f"Conflicting membership identities for {original['name']}. Resolve the mapping before refresh.")
        if matches:
            player, method = matches[0]
            if any(player.get(field) and original.get(field) and str(player[field]) != str(original[field]) for field in members):
                raise ValueError(f"Conflicting membership identities for {original['name']}; review the mapping before applying standings.")
        else:
            same_name = names.get(_name(original["name"]), [])
            if len(same_name) > 1:
                raise ValueError(f"Ambiguous name {original['name']!r}; a stable membership ID or reviewed identity mapping is required.")
            if same_name:
                player, method = same_name[0], "unique-name"
                if any(player.get(field) and original.get(field) and str(player[field]) != str(original[field]) for field in members):
                    raise ValueError(f"The membership ID changed for {original['name']}; review identity before applying standings.")
            else:
                number = 1
                while "p" + str(number) in by_id: number += 1
                identifier = "p" + str(number)
                player = {"id": identifier, "name": original["name"], "rating": original.get("rating", 0),
                          "ratingKnown": bool(original.get("rating", 0)), "active": True, "results": []}
                for field in members:
                    if original.get(field): player[field] = original[field]
                players.append(player)
                by_id[identifier] = player
                names[_name(player["name"])].append(player)
                for field, entries in members.items():
                    if player.get(field): entries[str(player[field])].append(player)
                summary["addedPlayers"] += 1
                method = "new-player"
        if player["id"] in claimed:
            raise ValueError(f"Multiple incoming rows match {player['name']}; review identities before applying standings.")
        claimed.add(player["id"])
        mapping[original["id"]] = player["id"]
        for field in members:
            if original.get(field) and not player.get(field): player[field] = original[field]
        summary["identityMatches"].append({"incomingId": original["id"], "playerId": player["id"], "name": player["name"], "method": method})
    for original in source["players"]:
        player = by_id[mapping[original["id"]]]
        for field in ("pairingNumber", "seed", "pairingRating", "schedule", "team", "entryRound", "withdrawnAfterRound"):
            if original.get(field) is None: continue
            if player.get(field) is None or player.get(field) == "":
                player[field] = deepcopy(original[field])
            elif player[field] != original[field]:
                summary["setupDifferences"].append({"playerId": player["id"], "name": player["name"], "field": field,
                                                    "reviewed": player[field], "published": original[field]})
        round_numbers = [r["round"] for r in original.get("results", [])]
        if len(round_numbers) != len(set(round_numbers)):
            raise ValueError(f"The source supplies duplicate records for {original['name']} in one round. Resolve that conflict before refreshing.")
        if not player.get("rating") and player.get("ratingKnown") is not True and original.get("rating", 0):
            player["rating"], player["ratingKnown"] = original["rating"], True
        elif original.get("rating", 0) and player.get("rating") != original["rating"]:
            summary["setupDifferences"].append({"playerId": player["id"], "name": player["name"], "field": "rating",
                                                "reviewed": player.get("rating"), "published": original["rating"]})
        history = defaultdict(list)
        for record in player.get("results", []): history[record["round"]].append(record)
        changed = False
        for raw in original.get("results", []):
            record = deepcopy(raw)
            if record.get("opponentId"):
                record["opponentId"] = mapping.get(str(record["opponentId"]), "unresolved-feed:" + str(record["opponentId"]))
            previous = history.get(record["round"], [])
            if len(previous) == 1:
                prior = previous[0]
                same_opponent = prior.get("opponentId") == record.get("opponentId")
                if record["result"] == "UNPLAYED" and prior["result"] != "UNPLAYED" and (not record.get("opponentId") or same_opponent):
                    summary["preservedRecords"] += 1
                    summary["warnings"].append(f"{player['name']}, round {record['round']}: the publisher's pending row did not erase a reviewed final result.")
                    continue
                if same_opponent and prior.get("played", prior["result"] in PLAYED_RESULTS) == record.get("played", record["result"] in PLAYED_RESULTS) and not record.get("color") and prior.get("color"):
                    record["color"] = prior["color"]
                compared = lambda r: {key: r.get(key, r.get("result") in PLAYED_RESULTS if key == "played" else POINTS[r["result"]] if key == "points" else None) for key in ("round", "result", "opponentId", "color", "played", "points")}
                if compared(prior) == compared(record):
                    summary["unchangedResults"] += 1
                    continue
            if previous:
                correction = {"playerId": player["id"], "name": player["name"], "round": record["round"], "previous": deepcopy(previous), "incoming": deepcopy(record)}
                summary["corrections"].append(correction)
                summary["updatedResults"] += 1
                if len(previous) > 1 or record["result"] == "UNPLAYED" or str(record.get("opponentId") or "").startswith("unresolved-feed:"):
                    summary["conflicts"].append({**correction, "message": "A published correction conflicts with reviewed history; resolve it before relying on a forecast."})
            else:
                summary["addedResults"] += 1
            history[record["round"]] = [record]
            changed = True
        player["results"] = sorted((record for records in history.values() for record in records), key=lambda r: r["round"])
        if original.get("reportedScore") is not None:
            player["sourceReportedScore"] = original["reportedScore"]
            player["sourceReportedRound"] = source.get("latestRound", 0)
        if changed: summary["updatedPlayers"] += 1
    merged["players"] = players
    if not merged.get("rounds"):
        merged["rounds"] = 50
    observed = max((r["round"] for p in players for r in p.get("results", [])), default=0)
    if observed > merged["rounds"]:
        raise ValueError("Published rounds exceed the configured event length. Review tournament setup before applying this source.")
    probe = deepcopy(merged)
    probe.pop("completedRounds", None)
    probe["latestRound"] = observed
    normalized = normalize_tournament(probe, "reviewed-standings.json", "Merged standings preview")
    inferred = normalized["completedRounds"]
    committed = summary["previousCompletedRounds"]
    merged["completedRounds"] = max(committed, inferred)
    merged["latestRound"] = observed
    if inferred < committed:
        summary["conflicts"].append({"message": "Published corrections leave a previously reviewed round incomplete. The committed cutoff is retained and forecasting must wait for reconciliation.", "previousCompletedRounds": committed, "completeHistoryRounds": inferred})
    for player in players:
        computed = sum(float(r.get("points", POINTS[r["result"]])) for r in player.get("results", []) if r["round"] <= merged["completedRounds"]) + float(player.get("initialScore", 0))
        if player.get("reportedScore") is not None:
            # Published totals become scoped evidence. They must not silently
            # substitute for newly received game history or create stale gates.
            player.setdefault("sourceReportedScore", player["reportedScore"])
            player.pop("reportedScore", None)
        for rnd in range(1, observed + 1):
            if rnd < player.get("entryRound", 1) or rnd > player.get("withdrawnAfterRound", merged["rounds"]): continue
            records = [r for r in player.get("results", []) if r["round"] == rnd]
            if not records:
                summary["missingHistory"].append({"playerId": player["id"], "name": player["name"], "round": rnd})
            for record in records:
                if record["result"] in PLAYED_RESULTS and record.get("played", True) and not record.get("color"):
                    summary["missingColors"].append({"playerId": player["id"], "name": player["name"], "round": rnd})
        if player.get("sourceReportedScore") is not None and player["sourceReportedScore"] != computed:
            summary["warnings"].append(f"{player['name']}: source total {player['sourceReportedScore']:g} is retained as published evidence; reviewed-history score is {computed:g}.")
    omitted = [p["name"] for p in players if p["id"] not in claimed]
    if omitted: summary["warnings"].append(f"{len(omitted)} existing players are absent from this snapshot and remain in the event; absence does not establish withdrawal.")
    if summary["setupDifferences"]: summary["warnings"].append("Some published setup fields differ from reviewed setup. Existing ratings, starting order and participation declarations were retained for review.")
    if summary["missingColors"]: summary["warnings"].append("Published history omits played colors. Those colors remain unknown; a color-sensitive forecast needs additional evidence.")
    if not any(p.get("results") for p in source["players"]): summary["warnings"].append("Only a roster/standings total was published. No opponents or game results were inferred from points.")
    summary["proposedCompletedRounds"] = merged["completedRounds"]
    summary["observedRound"] = observed
    summary["rosterOnly"] = not any(p.get("results") for p in source["players"])
    summary["preservedPlayers"] = len(omitted)
    previous_refresh = current.get("provenance", {}).get("lastStandingsRefresh", {})
    prior_feed_warnings = set(previous_refresh.get("warningMessages", []))
    if "warningMessages" not in previous_refresh:
        # Early development snapshots lacked warning attribution. Migrate only
        # exact messages this module emitted, and only after their cause clears.
        if any(p.get("results") for p in source["players"]):
            prior_feed_warnings.update({
                "Only a roster/standings total was published. No opponents or game results were inferred from points.",
                "This standings source supplies no round history. Totals do not establish opponents, results or played colors.",
            })
        if not summary["missingColors"]:
            prior_feed_warnings.add("Published history omits played colors. Those colors remain unknown; a color-sensitive forecast needs additional evidence.")
        for player in players:
            computed = sum(float(r.get("points", POINTS[r["result"]])) for r in player.get("results", [])
                           if r["round"] <= merged["completedRounds"]) + float(player.get("initialScore", 0))
            if player.get("sourceReportedScore") != computed:
                continue
            pattern = re.escape(player["name"]) + r": source total \d+(?:\.\d+)? is retained as published evidence; reviewed-history score is \d+(?:\.\d+)?\."
            prior_feed_warnings.update(warning for warning in current.get("warnings", []) if re.fullmatch(pattern, warning))
    retained_warnings = [warning for warning in current.get("warnings", []) if warning not in prior_feed_warnings]
    current_feed_warnings = list(dict.fromkeys(summary["warnings"]))
    merged["warnings"] = list(dict.fromkeys([*retained_warnings, *current_feed_warnings]))[:500]
    merged.setdefault("provenance", {})["lastStandingsRefresh"] = {"source": source_url or source.get("provenance", {}).get("source", "Reviewed standings"),
                "checkedAt": datetime.now(timezone.utc).isoformat(), "incomingHash": snapshot_hash(source), "reviewRequired": True,
                "warningMessages": current_feed_warnings}
    return {"tournament": validate_tournament(merged), "summary": summary, "baseHash": snapshot_hash(current), "requiresReview": True}
