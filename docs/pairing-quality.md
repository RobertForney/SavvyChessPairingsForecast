# Pairing quality for managed local events

Local or casual event status changes the administrative scope, not the expected care in the pairings. Run tournament uses the same reviewed Swiss procedures as the forecasting workspace, with an additional issuance boundary and a durable round lifecycle. It does not submit federation reports or claim certification.

## Procedure and scope

The FIDE Dutch profile uses the pinned BBP Pairings build and its native bracket, color, float and bye calculation. The application verifies that executable and checks the exchanged inputs and returned assignments. An absent, modified, timed-out or rejected native calculation cannot silently become an approximate pairing.

The US Chess profile uses the application's ordered Swiss procedure and published-example regressions. Its documented limits around larger mixed exchange cycles and director discretion remain relevant. The director must explicitly acknowledge that scope before issuing a US Chess round. Acknowledgement does not relax any issuance invariant. See [the US procedure](us-chess-algorithm.md) and [native Dutch integration](dutch-engine.md).

The custom constraint optimizer is an exploratory forecasting feature and is not a managed-event issuance procedure. Unsupported systems, inputs or relaxations must be resolved or clearly rejected. A failed search must not invent an opponent, omit a player, or relax an entered restriction to make a round appear complete.

## Draft and issuance checks

Before a round is issued, its reviewed draft must still match the current tournament inputs and normalized rules. Its saved content must also be intact: input hashes alone do not detect a changed assignment inside a stored draft. Issuance independently checks assignments and their coverage, including eligible players, requested absences, allocated byes, unique boards, self-pairings, prohibited/repeated opponents and applicable color constraints.

Engine identity and rule profile belong to the saved round. A complete candidate found after a search budget expires is insufficient for managed issuance. Reaching a configured candidate quota is different from exhausting the search budget; diagnostics must preserve that distinction. Separate schedules must each produce a valid complete result before their combined round can be issued.

Issuing the draft saves the actual assignments, their input snapshot and engine provenance. An issued round does not change merely because the current forecast, a player's details, or another application's standings snapshot changes. Restored event rules must agree with the issued procedure and variations; increasing a search budget cannot become a way to change the underlying procedure.

## Results and corrections

Pending results belong to the issued round. They do not enter completed player history or the next-round pairing input. Closing a round requires every played board to have a supported result, writes reciprocal records, and awards reviewed byes exactly once. Forfeits remain unplayed and do not create played color history.

Only the latest eligible closed round can be reopened, with an explicit correction reason and revision history. Reopening preserves the issued assignments; correcting a result is distinct from replacing the original pairings. Earlier corrections need a controlled rollback of dependent rounds rather than silently recalculating an announced event.

The server must protect this lifecycle from the older generic tournament editors, feed application and forecast endpoints. Mutations identify both the event and its observed revision so a stale tab cannot edit a replacement event that happens to have the same revision number. File persistence must either save the entire transition or retain the previous in-memory and on-disk state. Public API and saved-state validation are part of the quality boundary, not just the Run tournament screen.

## Verification evidence

The engine tests include published US numerical examples, independently constructed color/bye/reentry cases, and native Dutch fixtures. Managed-event tests add adversarial draft changes, stale input/rule hashes, coverage and bye failures, pending-result isolation, incomplete close rejection, reciprocal history and reopen behavior. HTTP tests also cover bypass attempts, persistence failures, backups after browser JSON number serialization, and full-point requested byes through round closure and later bye eligibility.

Passing these checks supports the implemented scope. It does not establish universal agreement with every director-permitted US pairing or replace broader event comparison and operational testing.
