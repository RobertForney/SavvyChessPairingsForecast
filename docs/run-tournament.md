# Run a local tournament

**Run tournament** manages one local Swiss section using the same general pairing engines as the forecasting workspace. It adds a durable round lifecycle: prepare a draft, review it, issue fixed pairings, save board results, then close the round. It assists user-run local events and does not submit tournaments, rating reports or membership records to US Chess or FIDE.

The event's pairing quality is not relaxed because it is casual. A complete engine result, current input fingerprint and independent assignment checks are required before issuance. Unsupported input, missing engines and incomplete or truncated calculations block the round. An explanatory warning is not permission to issue an invalid assignment.

## Create the event and roster

Open **Run tournament** and enter the event name, section, round count and procedure. Choose the top seed's initial color according to your announced color draw. The default procedure is the pinned FIDE Dutch engine, which must be available on the pairing backend. The current packaged native engine runs on Windows x64; a phone or Mac can use the same reachable backend through the web app.

The US Chess profile uses the application's ordered procedural engine with its documented edge-case limits. Read its scope and explicitly acknowledge those limits before issuing a US Chess round. That acknowledgement does not weaken the independent checks or certify a tournament. Custom exploratory matching is not offered as a standard managed-event pairing procedure.

Add players individually or paste a CSV roster:

```csv
name,rating,team
Ada,1900,North Club
Ben,1800,South Club
Cara,1700,North Club
Dave,1600,South Club
```

Use zero for an unrated player. Review names and assigned pairing ratings before the first round. Initial pairing numbers are established from the reviewed starting order and retained once play starts. Standings rank never becomes a new pairing number between rounds. The interface supports one active event; creating or restoring another event replaces that server workspace, so export its event record first if it needs to be retained.

Before the first issued round, roster entries can be edited or removed. After issued history exists, identity, pairing rating and original order are protected; future availability and next-round requested byes are updated between rounds. An issued round's roster is fixed. If a player is absent after issue, record the actual result or an appropriate unplayed forfeit rather than removing the player from that round.

## Generate, inspect and issue

Choose **Generate pairing draft**. Review every board, requested and allocated bye, engine identity and quality note. Draft generation does not announce the round or create game results.

Choose **Issue round** only after reviewing a usable draft. The server rechecks its roster, rules, coverage, eligibility and fingerprints. Each participating player must occur exactly once as a player or bye recipient. A stale or incomplete draft cannot be issued. Repeated issue clicks cannot create a second copy of a round.

Issuance saves a snapshot of the boards, colors, bye awards, selected rules, engine identity, actual pairing input and normalized input/rules hashes. Later result entry cannot recalculate those boards. A managed event cannot be replaced by a routine standings refresh or edited through the generic forecast workspace.

Use **Download round CSV** or **Download printable sheet** to distribute or print the issued boards. Downloads use the same authenticated workspace connection as the application. The printable HTML file is self-contained; open it in a browser and use the browser's print command. These are local event documents, not federation submission files.

## Enter results and close the round

The board results screen presents the issued White and Black players. Its result choices are from White's perspective:

| Choice | Meaning |
| --- | --- |
| Not entered | The game remains unresolved |
| 1–0 · White wins | Played White win |
| ½–½ · Draw | Played draw |
| 0–1 · Black wins | Played Black win |
| 1–0 · White wins by forfeit | Unplayed White win |
| 0–1 · Black wins by forfeit | Unplayed Black win |
| 0–0 · Both players forfeit | Both players receive an unplayed zero-point loss |

Choose **Save board results** to save partial or complete results. These are in-progress results; the closed-round standings do not advance yet. Unentered games are never silently converted to draws or losses. Byes retain the point awards stored in the issued snapshot. Unplayed forfeits do not contribute to played color history.

After every board has a saved result, choose **Close round**. The server commits both players' reciprocal histories and the issued bye awards together, then makes the next round available. The next pairing calculation uses this completed history. Points standings show shared ranks for equal points; this version does not invent a tiebreak system or prize allocation.

## Correct a completed round

Only the latest completed round can be reopened, and only before a later round is issued. If an unissued later draft exists, discard that draft first. Enter a reason in **Correct the most recently completed round**, then reopen it. The event retains the issued pairing snapshot, previous saved results and the correction reason in its audit history.

Correct the board results, save them, and close the round again before generating the next round. Reopening does not silently repair or revise already announced later rounds.

## Save and restore

**Export event record** downloads a JSON backup containing the managed event and its rules. It includes issued snapshots, saved results, closed histories and revisions. Keep explicit backups in addition to the server's local saved workspace.

Restoring preserves the issued snapshots and results, clears an unissued draft, and records the restore under a new event identity. Previously open tabs must reload before they can change the restored event. Saved rules must match the immutable rules of all issued rounds. Invalid saved state is preserved and reported rather than silently replaced with an empty workspace.

To restore, select that JSON file under **Restore a saved local-event backup**. Selecting a file prepares a review with its event name, roster and issued-round counts. **Restore this reviewed event** explicitly replaces the active workspace after server-side validation. A malformed or inconsistent backup is rejected. Changes made from a stale browser view are rejected rather than overwriting a newer managed revision.

An offline saved view is read only. Creating events, editing rosters, generating or issuing pairings, saving results and restoring backups require the backend; these actions are not queued for later execution.

## Current scope

This feature shares the application's existing US Chess and FIDE Dutch Swiss engine scope and limits. It does not add round robin, knockout or team-event pairing engines, automatic federation submissions, arbitrary board overrides, a full tiebreak/prize system, or multi-event hosting. Engine-specific limitations remain visible. See [the US Chess procedure](us-chess-algorithm.md), [the pinned Dutch dependency](dutch-engine.md) and [platform support](platforms.md).

`node tests/test_director_ui.js` exercises independent creation, escaped roster text, draft review without automatic issuance, fixed issued snapshots, separate result saves and round closure, reasoned reopening, authenticated printable downloads, reviewed backup restoration, revision tokens and offline write blocking.
