# Local tournament verification

The Run tournament workflow was tested in the browser on November 20, 2026 (America/Chicago), using an independent five-player **Neighborhood Chess Night** fixture. It used the real pinned BBP Pairings 6.0.0 Dutch engine, three rounds and an isolated `.local/director-final-browser-test.json` workspace. No Louisiana fixture, published later opponents or source-workbook predictions supplied its pairings.

1. Created an empty event with the default Dutch procedure and imported a five-player CSV roster.
2. Generated and reviewed round one before explicitly issuing it. Avery–Casey and Devon–Blair played; Emery received the allocated bye.
3. Closed the round, reopened it with a correction reason, changed Devon–Blair from a draw to a White win and closed it again. The issued assignments stayed fixed.
4. Generated round two from that corrected history. Emery–Avery and Casey–Devon were paired, with Blair receiving the bye.
5. Exported the event through the browser, selected the downloaded JSON file, reviewed it and restored it successfully. The server retained the issued round and correction history, recorded the restore under a fresh event identity and cleared the unissued draft. Regeneration returned the same round-two assignments.
6. Issued round two, recorded a Black win on board one and a double forfeit on board two, then closed it. Both forfeiting players received unplayed losses without played colors.
7. Generated round three with Avery–Devon and Blair–Emery, and Casey receiving the bye. There were no repeat played opponents or repeated allocated byes. Issued the round, saved two draws and closed it.
8. Restarted the backend and reloaded the browser. All three rounds, final points, correction and restore audit entries remained intact. The completed event offered no fourth-round draft and kept completed players distinct from withdrawals. Browser console checks returned no warnings or errors.

The final points were Avery 2.5, Blair 1.5, Devon 1.5, Emery 1.5 and Casey 1. Equal scores shared a rank. Total points were eight: the double-forfeit board awarded neither player a point. No tiebreak or prize order was inferred.

An earlier browser pass also checked partial-result saves across reload, rejection of incomplete closure, an ordinary single-player forfeit, and protection of managed rounds from the generic Players editor. Browser testing exposed two defects before completion: Python/browser numeric serialization broke backup checksums, and a requested full-point bye could block round closure. Both were fixed and reproduced in automated regressions; checksum validation remains enforced.

At 375 and 305 CSS pixels, the interface had no page-level horizontal overflow. The six visible navigation targets measured approximately 57×60 and 46×60 pixels, respectively. The replay lab remains accessible through Rules on small screens. These are responsive browser checks; physical Apple/Android installation remains separate platform testing.

All **309 Python tests** passed with the native Dutch dependency available. Director, Follow, workspace and replay JavaScript suites passed, together with **22 service-worker checks**. The 48 director-related Python tests include adversarial issuance checks, persistence failures, stale identities/revisions, browser-style JSON numeric conversion, requested zero/half/full-point byes, and double forfeits followed by valid US and native Dutch rounds.

Browser proof images are ignored local artifacts: `.local/local-tournament-pairings.jpg` shows fixed issued boards, and `.local/local-tournament-manager.jpg` shows the persisted final standings. The owned preview server was stopped after verification. The original XLSX remained unchanged (SHA-256 `c5a5369a246aaccba4332054f132b31f042069ce9535021acde78a1185c03b2d`).

This evidence checks the implemented workflow and strict issuance boundary. It does not certify the application or establish universal agreement with every director-permitted US pairing. See [pairing quality and engine scope](pairing-quality.md) and [the director workflow](run-tournament.md).
