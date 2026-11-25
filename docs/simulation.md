# Replay the original tournament

`app.replay.replay(tournament, through_round, rules=None)` creates an isolated
historical view and forecasts the next round. It never overwrites the source or
invents later results. The browser can move the cutoff from zero (before the
first round) through the scheduled event length.

The forecast receives only game records through the cutoff. Full-source cached
standings totals, computed scores and snapshot warning summaries do not enter
that input. Scores are recalculated from the visible history. Source totals and
provenance remain available separately under `sourceSummary`, with their scope
identified as the source snapshot. The forecast input hash describes the cutoff
view; the full source has a different audit hash.

Starting ratings, dated entry/withdrawal boundaries and requested byes are
treated as declared setup inputs. The workbook does not supply announcement
timestamps, so that assumption is displayed. An undated final `active=false`
flag is not used to withdraw a player retroactively. Undated next-round director
locks are excluded unless `replayKnownRestrictions=true` explicitly declares
them known for the replay. Prize-contender flags are used only when forecasting
the final round.

## Schedules and merge

The source contains 33 Open 4-Day entries and 18 Open 3-Day entries, merging for
round 2. Round 1 is forecast separately for each schedule. Three requested
half-point byes leave 24 boards. The response labels each board's schedule and
can represent multiple allocated byes through `forecast.byes`.

For a native Dutch calculation before the merge, consecutive schedule-local
initial pairing numbers are derived from the reviewed source seed order. The
original global number remains as context. This is an explicit modeling
assumption: confirm actual local starting order and initial colors before using
the comparison as an official-program reproduction. Starting with round 2,
the whole section is paired together using the source's original roster.

The combined pre-merge candidate uses the first candidate from each schedule.
It does not select combinations retrospectively to fit the observed round. A
blocked schedule blocks the combined round rather than exposing a partial
result as a complete forecast.

## Compare predictions with recorded pairings

The next round's observed opponent pairs are used only after the forecast is
generated. A pending game result can still establish its observed opponents.
Duplicated, contradictory or nonreciprocal pairing evidence is shown but is
excluded from the opponent comparison denominator. Pairings outside a declared
entry or withdrawal interval are also excluded. Administrative zero-point rows
outside those intervals are not counted as missed pairing byes.
Once a pairing outside declared participation is revealed, it also blocks the
following forecast until the contradiction is resolved. Recorded awards without
an opponent can still represent a director-approved late-entry bye.
An official source's unspecified non-playing code, such as US Chess `U`, retains
its zero score without becoming an inferred withdrawal or pairing bye. Such
rows are labelled as unknown availability and excluded from bye agreement.

Opponent matches are counted by unordered board, independent of board number.
The primary color rate compares orientation only on boards whose opponents
matched and whose observed colors are known. Separate per-player color counts
are also available. Unknown colors are missing evidence, not failed predictions.
Rates are `null` when no eligible comparison exists or no valid forecast was
possible. Only the first candidate is scored; later alternatives are not chosen
after seeing the observed round.

These are agreement counts for this source and selected assumptions, not a
calibrated prediction probability or a general claim of engine accuracy.

## What the original evidence can support

Round 1 has complete outcomes and known played colors. A forecast for round 2
can therefore be made using only round 1, without the source's cached final
totals. Round 2 itself contains an unresolved game and a scheduled-bye/game
conflict. Later rounds also have missing rows and unknown colors. Advancing the
cutoff exposes those exact problems and blocks the next forecast. Round 7 has
scheduled declarations but no recorded results; replay does not fill it in.

The response includes per-round coverage, the selected round's integrity,
forecast diagnostics, excluded-comparison reasons, and the untouched source
summary so the first major browser test can demonstrate both working forecasts
and the point at which the sample lacks enough evidence.
