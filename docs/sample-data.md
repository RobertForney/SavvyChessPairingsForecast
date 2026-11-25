# Original tournament sample

The browser simulation reads the original
`Louisiana_State_Championship_2026_pairings_no_rematches.xlsx` directly. It is not a
CSV conversion or a synthetic replacement. `app/sample_data.py` exposes an isolated
validated tournament snapshot and content-hashed evidence metadata. Neither helper
recalculates formulas, repairs results or writes to the workbook.

The source contains 13 worksheets, 51 players and seven scheduled rounds. Actual
history reaches round 6, but only round 1 is completely entered. The normalized
history contains 222 player records derived from 105 entered game/bye rows plus
explicit scheduled-bye evidence. There are 15 scheduled-bye declarations, including
two for round 7; no round-7 game results are fabricated.

## Schedules and first-round evidence

The roster has 33 Open 4-Day and 18 Open 3-Day entries. The source's merge round is
2. Every observed round-1 game stays within its original schedule: 16 games in the
4-Day schedule and eight in the 3-Day schedule. The remaining three players have
declared half-point byes: Etienne #23, Slaton #35 and FAID #51. All 48 played
round-1 color assignments are known. A first-round replay must respect these
separate schedules before forecasting the merged second round.

## Incomplete and conflicting evidence

- Tagorda #25 has both a game and a scheduled half-point bye in round 2, and both a
  game and a scheduled zero-point bye in round 3.
- Beavers #13 has both a game and a scheduled half-point bye in round 5.
- Round 2 has one unresolved game. Round 3 has one unresolved game and three players
  with no record. Rounds 4–6 each lack records for 28 players.
- Fifty-five recorded pairings lack confirmed colors; 54 of them have completed
  results. These remain unknown rather than being inferred from column order.

Cached Standings totals are preserved as source `reportedScore` values. They are
snapshot totals, not scores known at every earlier round. Replay forecasts must
derive their scores from the revealed history and must not feed later results,
cached totals or future conflict warnings into an earlier prediction.

The metadata distinguishes source worksheet rows, mirrored player records,
scheduled declarations and game pairings. Counts for these different units must
not be added as if they represented independent games. Missing rows do not imply
withdrawals, losses or zero-point byes. The source's acceleration setting is an
unverified modeling assumption and is not enabled automatically.
