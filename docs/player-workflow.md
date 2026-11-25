# Following a tournament as a player

The primary workflow starts with the event a player is joining. Historical replay and the Louisiana sources are optional examples for checking behavior.

## Before the roster is published

Open **Follow** and enter the tournament name. Section, your name/member ID and the published results URL can be added when known. Save the event with no players and no opponents. The app does not create placeholder opponents or treat the absence of published pairings as a data error.

Leave unannounced details at **Not known yet**. This includes the total rounds, pairing procedure, initial color draw, acceleration, original pairing order and schedule merge. A published standings rank is not an original pairing number. Confirming that order requires actual original numbers. Different schedules require an explicit decision about whether they pair separately, share a pool or merge at a particular round.

The acceleration choices are unknown, none, native FIDE Baku, or manual virtual points. Baku requires a confirmed event length and the FIDE Dutch procedure. Manual points are configured in Players & results; selecting no acceleration cannot silently retain conflicting virtual points.

US Chess Swiss and FIDE Dutch Swiss have forecasting engines. An event with another format can be followed, but it requires an appropriate engine before the app can claim valid predictions for that format.

## As results appear

Save the published standings URL, then choose **Check published standings**. Supported structured exports and tables can be interpreted directly. If the page only supplies its data through JavaScript or an authenticated API, use a downloadable export or pasted supported table. Generic file import remains format dependent; the app reports unsupported layouts instead of guessing results.

Select the section being followed before checking a source containing several sections. Preview the roster and result changes, including any corrections to previously recorded results, then choose **Apply reviewed standings**. A preview does not change the workspace. An expired preview or a preview made before settings/results changed must be fetched and reviewed again.

The merge preserves confirmed event setup, identifies players using stable federation identities or unambiguous names, and retains earlier evidence absent from a partial update. Missing players do not become withdrawals. Final ranking does not replace the original pairing order. Standings totals alone do not establish opponents, results or played colors.

The optional **Check saved URL every two minutes** control runs only while Follow is open and the backend is reachable. It prepares a review and stops replacing checks while a preview awaits a decision. It never applies standings automatically. A background check preserves unsaved form text and focus.

## Find your next opponent

When your entry appears, select yourself in the roster. **Follow** and **Forecast** can then show your opponent in the current candidate, with the evidence and assumptions used to generate it. This is a prediction, not an official pairing assignment.

Confirm known byes, withdrawals and event details as announcements become available. If the director's exact setup is still unknown, any supported forecast hypothesis remains labelled. Current results can establish a next-round forecast without the next round's opponents being known.

Unfinished games, missing opponent history or missing played colors can prevent a reliable complete forecast. The app shows what is missing. It does not turn aggregate scores or a later published pairing into the evidence used for an earlier forecast.

## Connection and storage

New checks, edits, imports and forecasts use the pairing backend. An installed mobile web app can show a previously saved view without a connection, clearly marked read only. Such a view may be stale. Source checks are not queued for later submission.

The current server holds one active tournament workspace. Starting another event replaces that active workspace; export the existing event first if it needs to be retained. Browser and device setup is described in [platform support](platforms.md).

## Verification

`node tests/test_follow_ui.js` exercises unknown setup, event creation without opponents, escaped source text, source checks that do not apply results, reviewed application, correction notices, optional polling, offline blocking and stale-forecast suppression. Backend identity/merge and pairing-procedure tests check the corresponding data boundaries.
