"""A separate synthetic fixture, never confused with the source event."""
from datetime import datetime, timezone


def demo_tournament():
    names = ["Avery Morgan", "Jordan Chen", "Riley Patel", "Cameron Brooks", "Taylor Nguyen", "Alex Rivera", "Casey Williams", "Jamie Shah", "Drew Bennett", "Quinn Park", "Morgan Ellis", "Sam Cooper"]
    players = [{"id": str(i + 1), "name": name, "rating": 2240 - i * 65, "active": True, "results": [], "team": ""} for i, name in enumerate(names)]
    rounds = [
        [(1, 7, "W"), (8, 2, "L"), (3, 9, "D"), (10, 4, "L"), (5, 11, "W"), (12, 6, "L")],
        [(2, 1, "D"), (4, 5, "W"), (6, 3, "D"), (7, 10, "W"), (9, 8, "L"), (11, 12, "D")],
        [(1, 4, "D"), (5, 2, "L"), (3, 8, "W"), (6, 7, "W"), (12, 9, "L"), (10, 11, "W")],
    ]
    reverse = {"W": "L", "L": "W", "D": "D"}
    for rnd, matches in enumerate(rounds, 1):
        for white, black, outcome in matches:
            players[white - 1]["results"].append({"round": rnd, "opponentId": str(black), "color": "W", "result": outcome, "played": True})
            players[black - 1]["results"].append({"round": rnd, "opponentId": str(white), "color": "B", "result": reverse[outcome], "played": True})
    return {"name": "Savvy Invitational · complete demo", "section": "Open · synthetic data", "rounds": 7, "completedRounds": 3,
            "players": players, "warnings": [], "provenance": {"source": "Built-in synthetic demonstration (not the Louisiana event)", "format": "JSON", "importedAt": datetime.now(timezone.utc).isoformat()}}
