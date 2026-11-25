'use strict';

// A managed event owns issued rounds; drafts and result entry never rewrite them.
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const copy = value => JSON.parse(JSON.stringify(value));
  const profiles = { 'fide-dutch': 'FIDE Dutch Swiss · pinned engine', 'us-chess': 'US Chess Swiss · procedural engine' };
  const results = { '': 'Not entered', W: '1–0 · White wins', D: '½–½ · Draw', L: '0–1 · Black wins', FORFEIT_WIN: '1–0 · White wins by forfeit', FORFEIT_LOSS: '0–1 · Black wins by forfeit', DOUBLE_FORFEIT: '0–0 · Both players forfeit' };
  const state = { loading: false, error: '', newOpen: false, newDraft: null, setupDraft: null, setupKey: '', playerDraft: null, resultDraft: null, resultKey: '', resultsDirty: false, bulkText: '', reopenReason: '', status: null, statusKey: '', statusPending: false, restorePreview: null, restoreFilename: '', restorePending: false };
  let hooks;
  const app = () => hooks?.getAppState() || {};
  const tournament = () => app().tournament || {};
  const managed = () => tournament().managed || {};
  const enabled = () => managed().enabled === true;
  const offline = () => Boolean(app().offline);
  const busy = () => state.loading || offline();
  const rounds = () => managed().rounds || [];
  const numberOf = round => Number(round?.number ?? round?.round ?? 0);
  const snapshot = round => round?.snapshot || round || {};
  const activeRound = () => rounds().find(round => round.status === 'issued');
  const historyStarted = () => rounds().length > 0;
  const status = () => app().directorStatus || state.status || {};
  const redraw = () => hooks?.renderApp();
  const opts = (values, selected) => Object.entries(values).map(([value, label]) => `<option value="${esc(value)}"${String(selected ?? '') === value ? ' selected' : ''}>${esc(label)}</option>`).join('');
  const disabled = condition => condition ? ' disabled' : '';
  const date = value => {
    if (!value) return 'Not recorded';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? 'Not recorded' : parsed.toLocaleString();
  };
  const points = value => Number.isFinite(Number(value)) ? String(Number(value)) : '—';
  const revision = () => Number(managed().revision || 0);

  function key() { return JSON.stringify([managed().eventId, managed().revision, tournament().completedRounds, tournament().players?.length]); }

  async function operate(work) {
    if (!hooks || busy()) return;
    state.loading = true;
    state.error = '';
    redraw();
    try { await work(); }
    catch (error) {
      state.error = error.message || 'This tournament action could not be completed.';
      hooks.toast?.(state.error, true);
    } finally { state.loading = false; redraw(); }
  }

  async function change(path, body, method = 'POST', message = '') {
    if (enabled() || !['/api/director/new', '/api/director/restore'].includes(path)) body = { ...body, expectedRevision: revision(), expectedEventId: managed().eventId };
    const data = await hooks.request(path, { method, body });
    state.status = null;
    state.statusKey = '';
    hooks.acceptState(data);
    if (message) hooks.toast?.(message);
    return data;
  }

  function defaultSetup() {
    return { name: '', section: 'Open', rounds: '5', profile: 'fide-dutch', firstRoundColor: 'W', acknowledgeEngineLimits: false };
  }

  function setupValues(create) {
    if (create) return state.newDraft || defaultSetup();
    const currentKey = `${managed().eventId || ''}:${managed().revision || 0}`;
    if (state.setupDraft && state.setupKey === currentKey) return state.setupDraft;
    state.setupKey = currentKey;
    state.setupDraft = { name: tournament().name || '', section: tournament().section || '', rounds: String(tournament().rounds || 5),
      profile: app().rules?.profile || 'fide-dutch', firstRoundColor: app().rules?.firstRoundColor || 'W',
      acknowledgeEngineLimits: managed().acknowledgeEngineLimits === true || managed().settings?.acknowledgeEngineLimits === true };
    return state.setupDraft;
  }

  function readSetup(form) {
    const values = new FormData(form);
    const existing = form.id === 'director-settings';
    return { name: String(values.get('name') || '').trim(), section: String(values.get('section') || '').trim(),
      rounds: Number(values.get('rounds')), profile: String(values.get('profile') || (existing ? app().rules?.profile : null) || 'fide-dutch'),
      firstRoundColor: String(values.get('firstRoundColor') || (existing ? app().rules?.firstRoundColor : null) || 'W'), acknowledgeEngineLimits: values.has('acknowledgeEngineLimits') };
  }

  function setupForm(create = false) {
    const values = setupValues(create), prefix = create ? 'director-new' : 'director-settings';
    const locked = busy() || (!create && Boolean(activeRound()));
    return `<form id="${prefix}" data-director-form="${create ? 'new' : 'settings'}" class="panel-body">
      <div class="simulation-controls">
        <div class="field"><label for="${prefix}-name">Event name</label><input id="${prefix}-name" name="name" maxlength="240" value="${esc(values.name)}" required${disabled(locked)}></div>
        <div class="field"><label for="${prefix}-section">Section</label><input id="${prefix}-section" name="section" maxlength="120" value="${esc(values.section)}"${disabled(locked)}></div>
        <div class="field"><label for="${prefix}-rounds">Number of rounds</label><input id="${prefix}-rounds" name="rounds" type="number" min="1" max="50" value="${esc(values.rounds)}" required${disabled(locked)}></div>
        <div class="field"><label for="${prefix}-profile">Pairing procedure</label><select id="${prefix}-profile" name="profile"${disabled(locked || (!create && historyStarted()))}>${opts(profiles, values.profile)}</select></div>
        <div class="field"><label for="${prefix}-color">Top seed’s first-round color</label><select id="${prefix}-color" name="firstRoundColor"${disabled(locked || (!create && historyStarted()))}>${opts({ W: 'White', B: 'Black' }, values.firstRoundColor)}</select></div>
      </div>
      <p class="field-help">The starting order is established before the first round and retained afterward. FIDE Dutch needs the configured native engine. Missing engines or unsupported input block pairing instead of switching procedures.</p>
      <label class="toggle-inline separated-panel"><input name="acknowledgeEngineLimits" type="checkbox"${values.acknowledgeEngineLimits ? ' checked' : ''}${disabled(locked)}> I have reviewed the US Chess engine’s documented limits and will review its pairings before issuing them.</label>
      <p class="field-help">The US Chess engine implements ordered Swiss procedures with documented edge-case limits. This acknowledgement does not certify an event or relax issue-time checks.</p>
      ${create ? '<p class="notice separated-panel">Creating this event replaces the active workspace. Export the current workspace first if you need to retain it. This feature assists local events and does not submit rating reports to a federation.</p>' : activeRound() ? '<p class="notice separated-panel">An issued round is in progress. Its settings and pairings stay fixed while results are entered.</p>' : ''}
      <div class="action-row"><button class="primary" type="submit"${disabled(locked)}>${create ? 'Create local tournament' : 'Save event settings'}</button>${create && enabled() ? '<button type="button" class="secondary" data-director-action="cancel-new">Cancel</button>' : ''}</div>
    </form>`;
  }

  function roster() {
    const players = tournament().players || [], started = historyStarted(), current = activeRound();
    const draft = state.playerDraft || { name: '', rating: '', team: '', active: true, nextBye: '' };
    const eventComplete = Number(tournament().completedRounds || 0) >= Number(tournament().rounds);
    const locked = busy() || Boolean(current) || eventComplete, identityLocked = started && Boolean(draft.id);
    const nextRound = Number(tournament().completedRounds || 0) + 1;
    const availability = player => {
      const through = Number(player.withdrawnAfterRound ?? tournament().rounds);
      if (player.active === false || through < Math.min(nextRound, Number(tournament().rounds))) return 'Withdrawn';
      if (eventComplete) return 'Event complete';
      return Number(player.entryRound || 1) > nextRound ? 'Not yet entered' : 'Active';
    };
    const rows = players.map(player => `<tr><td>${esc(player.pairingNumber ?? player.seed ?? '—')}</td><td><strong>${esc(player.name)}</strong>${player.team ? `<span class="player-sub">${esc(player.team)}</span>` : ''}</td><td>${esc(player.pairingRating ?? player.rating ?? 0)}</td><td>${availability(player)}${!eventComplete && (player.scheduledByes || []).some(bye => Number(bye.round) === nextRound) ? '<span class="player-sub">Requested bye next round</span>' : ''}</td><td><button class="secondary small" data-director-action="edit-player" data-player-id="${esc(player.id)}"${disabled(locked)}>Edit ${esc(player.name)}</button>${!started ? ` <button class="text-button" data-director-action="remove-player" data-player-id="${esc(player.id)}"${disabled(locked)}>Remove</button>` : ''}</td></tr>`).join('');
    return `<section class="panel separated-panel"><div class="panel-header"><div><h2>Players and availability</h2><p>${players.length} registered · manage requested byes and withdrawals before generating the next draft.</p></div></div>
      ${current ? '<div class="panel-body"><p>Availability is locked for the issued round. Record an appropriate result or forfeit for an absent player, then update future availability after the round closes.</p></div>' : ''}
      ${eventComplete ? '<div class="panel-body"><p>The scheduled event is complete. Extend the number of rounds in event settings before adding players or changing future availability.</p></div>' : ''}
      <form id="director-player" class="panel-body"><h3>${draft.id ? `Edit ${esc(draft.name)}` : 'Add a player'}</h3><div class="simulation-controls extra-top-space">
        <div class="field"><label for="director-player-name">Player name</label><input id="director-player-name" name="name" value="${esc(draft.name)}" maxlength="160" required${disabled(locked || identityLocked)}></div>
        <div class="field"><label for="director-player-rating">Pairing rating</label><input id="director-player-rating" name="rating" type="number" min="0" max="4000" value="${esc(draft.rating)}" placeholder="0 for unrated"${disabled(locked || identityLocked)}></div>
        <div class="field"><label for="director-player-team">Team or club</label><input id="director-player-team" name="team" value="${esc(draft.team)}" maxlength="120"${disabled(locked || identityLocked)}></div>
        ${draft.id ? `<div class="field"><label for="director-player-bye">Requested bye in round ${nextRound}</label><select id="director-player-bye" name="nextBye"${disabled(locked || nextRound > Number(tournament().rounds))}>${opts({ '': 'No requested bye', '0': 'Zero-point absence', '0.5': 'Half-point bye', '1': 'One-point bye' }, draft.nextBye)}</select></div>` : ''}
      </div>${draft.id ? `<label class="toggle-inline separated-panel"><input name="active" type="checkbox"${draft.active !== false ? ' checked' : ''}${disabled(locked)}> Participates in future rounds</label>` : ''}
      ${identityLocked ? '<p class="field-help">Issued history fixes identity, rating and starting order. Future availability and requested byes can be updated between rounds.</p>' : ''}${draft.id && draft.active === false && started ? '<p class="field-help">A withdrawn entry cannot be silently reactivated. Add a reviewed late entry if the player returns; earlier issued history remains attached to the original entry.</p>' : ''}
      <div class="action-row"><button class="secondary" type="submit"${disabled(locked)}>${draft.id ? 'Save player availability' : 'Add player'}</button>${draft.id ? '<button class="text-button" type="button" data-director-action="cancel-player">Cancel editing</button>' : ''}</div></form>
      ${!started ? `<details class="panel-body"><summary>Add several players from CSV</summary><form id="director-roster"><div class="field extra-top-space"><label for="director-roster-text">Player roster CSV</label><textarea id="director-roster-text" name="text" rows="5" placeholder="name,rating,team" required${disabled(locked)}>${esc(state.bulkText)}</textarea><p class="field-help">Use a name column and optional rating and team columns. Review the resulting roster before generating a round.</p></div><button class="secondary" type="submit"${disabled(locked)}>Add CSV roster</button></form></details>` : ''}
      ${players.length ? `<div class="table-scroll"><table><caption class="hidden">Registered players and next-round availability</caption><thead><tr><th>Number</th><th>Player</th><th>Rating</th><th>Availability</th><th>Actions</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="panel-body"><p>No players registered. Add the field before generating the first round.</p></div>'}
    </section>`;
  }

  function pairingsTable(source, caption) {
    return `<div class="table-scroll"><table class="pairings-table"><caption class="hidden">${esc(caption)}</caption><thead><tr><th>Board</th><th>White</th><th>Black</th></tr></thead><tbody>${(source.pairings || []).map(board => `<tr><td>${esc(board.board)}</td><td><span class="player-name">${esc(board.white?.name)}</span><span class="player-sub">${esc(board.white?.rating ?? 0)} · ${points(board.white?.score ?? 0)} points</span></td><td><span class="player-name">${esc(board.black?.name)}</span><span class="player-sub">${esc(board.black?.rating ?? 0)} · ${points(board.black?.score ?? 0)} points</span></td></tr>`).join('')}</tbody></table></div>`;
  }

  function byes(source) {
    const allocated = source.byes || (source.bye ? [{ player: source.bye, points: app().rules?.byePoints ?? 1 }] : []);
    return [...allocated.map(bye => ({ ...bye, label: 'Pairing bye' })), ...(source.requestedByes || []).map(bye => ({ ...bye, label: 'Requested bye' }))].map(bye => `<div class="bye-row"><span><strong>${esc(bye.label)}</strong> · ${esc(bye.player?.name)}</span><span>${points(bye.points)} points</span></div>`).join('');
  }

  function draftPanel() {
    if (activeRound()) return '';
    if (Number(tournament().completedRounds || 0) >= Number(tournament().rounds)) return '<section class="notice success separated-panel"><strong>All scheduled rounds are complete.</strong><p>Review the final points standings and export the event record. Shared scores keep a shared rank; no unannounced tiebreak is invented.</p></section>';
    const draft = status().draft || managed().draft;
    const forecast = draft?.forecast || draft;
    const quality = draft?.quality || {};
    const issues = [...new Set([...(quality.issues || []), ...(forecast?.issues || [])])];
    const warnings = [...new Set([...(quality.warnings || []), ...(forecast?.warnings || [])])];
    const canGenerate = status().canGenerate !== false && (tournament().players?.length || 0) >= 2;
    const canIssue = Boolean(draft?.id && (quality.canIssue ?? forecast?.status === 'ok') && status().canIssue !== false);
    return `<section class="panel separated-panel"><div class="panel-header"><div><h2>Prepare round ${Number(tournament().completedRounds || 0) + 1}</h2><p>Generate a complete draft, inspect it, then issue the round deliberately.</p></div><button class="primary" data-director-action="draft"${disabled(busy() || !canGenerate)}>Generate pairing draft</button></div>
      ${draft ? `<div class="panel-body"><div class="notice ${canIssue ? '' : 'danger'}"><strong>${canIssue ? 'Draft ready for director review' : 'Draft cannot be issued'}</strong><p>${esc(forecast?.engine || 'Pairing engine')} · ${esc(forecast?.ruleEdition || '')}</p>${issues.length ? `<ul>${issues.map(issue => `<li>${esc(issue)}</li>`).join('')}</ul>` : ''}<p>A draft is not an announced round. Issuing rechecks the current roster, rules, completeness and input fingerprint.</p></div></div>${pairingsTable(forecast || {}, 'Proposed pairing draft')}${byes(forecast || {})}${warnings.length ? `<details class="panel-body"><summary>Quality notes (${warnings.length})</summary><ul class="issue-list">${warnings.map(warning => `<li>${esc(warning)}</li>`).join('')}</ul></details>` : ''}<div class="panel-body"><div class="action-row"><button class="primary" data-director-action="issue"${disabled(busy() || !canIssue)}>Issue round ${esc(draft.round || Number(tournament().completedRounds || 0) + 1)}</button><button class="secondary" data-director-action="discard-draft"${disabled(busy())}>Discard this draft</button></div><p class="field-help">Issued pairings are saved as a fixed snapshot. In-progress results will be recorded separately.</p></div>` : '<div class="panel-body"><p>The app will check opponent history, colors, eligibility and bye assignments before a draft can be issued. Incomplete or truncated calculations cannot be issued.</p></div>'}
    </section>`;
  }

  function resultValues(round) {
    const currentKey = `${managed().eventId}:${numberOf(round)}:${round.resultRevision ?? managed().revision ?? 0}`;
    if (state.resultDraft && state.resultKey === currentKey) return state.resultDraft;
    state.resultKey = currentKey;
    state.resultDraft = copy(round.results || {});
    state.resultsDirty = false;
    return state.resultDraft;
  }

  function links(round) {
    return `<div class="action-row"><button class="secondary" data-director-action="download" data-format="csv" data-round="${numberOf(round)}"${disabled(busy())}>Download round CSV</button><button class="secondary" data-director-action="download" data-format="print" data-round="${numberOf(round)}"${disabled(busy())}>Download printable sheet</button></div>`;
  }

  function restorePanel() {
    const preview = state.restorePreview, event = preview?.tournament;
    return `<details class="panel separated-panel"${preview || state.restorePending ? ' open' : ''}><summary class="panel-body">Restore a saved local-event backup</summary><div class="panel-body"><div class="field"><label for="director-restore-file">Local-event JSON backup</label><input id="director-restore-file" type="file" accept=".json,application/json"${disabled(busy())}><p class="field-help">Choose an exported managed-event record. Selecting it prepares a review only; it does not replace this workspace.</p></div>${event ? `<div class="notice separated-panel"><strong>Review backup: ${esc(event.name)}</strong><p>${esc(state.restoreFilename)} · ${event.players?.length || 0} players · ${Number(event.completedRounds || 0)} completed rounds · ${event.managed?.rounds?.length || 0} issued round snapshots.</p><p>Restoring replaces the active workspace, including any in-progress local event. Export its record first if you need to retain it. The server validates the saved round history before accepting this backup.</p><div class="action-row"><button class="secondary" data-director-action="restore"${disabled(busy())}>Restore this reviewed event</button><button class="text-button" data-director-action="discard-restore"${disabled(busy())}>Discard backup preview</button></div></div>` : ''}</div></details>`;
  }

  function resultsPanel() {
    const round = activeRound();
    if (!round) return '';
    const source = snapshot(round), values = resultValues(round), boards = source.pairings || [];
    const entered = boards.filter(board => Boolean(round.results?.[String(board.board)])).length;
    const complete = entered === boards.length;
    return `<section class="panel separated-panel"><div class="panel-header"><div><h2>Round ${numberOf(round)} · issued</h2><p>${entered} of ${boards.length} board results saved · issued ${esc(date(round.issuedAt))}</p></div><span class="pill">Pairings fixed</span></div>
      <form id="director-results"><div class="table-scroll"><table class="pairings-table"><caption class="hidden">Enter results from White's perspective</caption><thead><tr><th>Board</th><th>White</th><th>Black</th><th>Result</th></tr></thead><tbody>${boards.map(board => `<tr><td>${esc(board.board)}</td><td>${esc(board.white?.name)}</td><td>${esc(board.black?.name)}</td><td><div class="field"><label class="hidden" for="director-result-${esc(board.board)}">Board ${esc(board.board)} result</label><select id="director-result-${esc(board.board)}" name="board-${esc(board.board)}" data-director-board="${esc(board.board)}"${disabled(busy())}>${opts(results, values[String(board.board)] || '')}</select></div></td></tr>`).join('')}</tbody></table></div>
      ${byes(source)}<div class="panel-body"><p class="field-help">Results are entered from White’s perspective. A forfeit is unplayed and does not create a played-color history. Byes retain their issued awards.</p>${state.resultsDirty ? '<p class="notice separated-panel">Unsaved board results. Save them before closing the round.</p>' : ''}<div class="action-row"><button class="primary" type="submit"${disabled(busy())}>Save board results</button><button class="secondary" type="button" data-director-action="close"${disabled(busy() || !complete || state.resultsDirty || status().canClose === false)}>Close round ${numberOf(round)}</button></div>${links(round)}<p class="field-help">Closing commits all saved results together and makes the next round available. Unentered games are never converted to draws or losses.</p></div></form>
    </section>`;
  }

  function standings() {
    const values = status().standings || (tournament().players || []).map(player => ({ ...player, score: (player.results || []).filter(result => result.round <= Number(tournament().completedRounds || 0)).reduce((sum, result) => sum + Number(result.points ?? ({ W: 1, D: .5, L: 0, BYE: 1, HBYE: .5, ZERO: 0, FORFEIT_WIN: 1, FORFEIT_LOSS: 0 }[result.result] || 0)), Number(player.initialScore || 0)) })).sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
    let previousScore, rank = 0;
    return `<section class="panel separated-panel"><div class="panel-header"><div><h2>Points standings</h2><p>Through ${Number(tournament().completedRounds || 0)} completed rounds. In-progress scores remain separate until the round closes.</p></div></div><div class="table-scroll"><table><thead><tr><th>Rank</th><th>Player</th><th>Points</th><th>Rating</th></tr></thead><tbody>${values.map((player, index) => { if (previousScore !== Number(player.score)) rank = index + 1; previousScore = Number(player.score); return `<tr><td>${esc(player.rank ?? rank)}</td><td>${esc(player.name)}</td><td>${points(player.score)}</td><td>${esc(player.rating ?? 0)}</td></tr>`; }).join('')}</tbody></table></div><div class="panel-footer">Equal points share a rank. No federation rating submission or unannounced tiebreak calculation is performed.</div></section>`;
  }

  function history() {
    const closed = rounds().filter(round => round.status === 'closed');
    if (!closed.length) return '';
    const latest = closed[closed.length - 1];
    const mayReopen = !activeRound() && !managed().draft && status().canReopen !== false;
    return `<section class="panel separated-panel"><div class="panel-header"><div><h2>Issued round history</h2><p>Snapshots retain the procedure, rules and input hashes used when each round was issued.</p></div></div>${closed.map(round => { const source = snapshot(round); return `<details class="panel-body"><summary>Round ${numberOf(round)} · completed ${esc(date(round.closedAt))}</summary><p class="field-help">${esc(source.engine || '')} · ${esc(source.ruleEdition || '')}</p>${pairingsTable(source, `Issued round ${numberOf(round)}`)}${byes(source)}<ul class="issue-list">${(source.pairings || []).map(board => `<li>Board ${esc(board.board)}: ${esc(results[round.results?.[String(board.board)]] || 'Not entered')}</li>`).join('')}</ul><p class="source-attribution">Input: ${esc(source.inputHash || round.inputHash || '')}<br>Rules: ${esc(source.rulesHash || round.rulesHash || '')}</p>${links(round)}</details>`; }).join('')}
      ${mayReopen ? `<details class="panel-body"><summary>Correct the most recently completed round</summary><form id="director-reopen"><div class="field extra-top-space"><label for="director-reopen-reason">Reason for reopening round ${numberOf(latest)}</label><input id="director-reopen-reason" name="reason" value="${esc(state.reopenReason)}" maxlength="500" required${disabled(busy())}><p class="field-help">Reopening preserves its issued pairings and audit history. Correct its board results and close it again before generating a later round.</p></div><button class="secondary" type="submit"${disabled(busy())}>Reopen round ${numberOf(latest)}</button></form></details>` : '<div class="panel-body"><p class="field-help">The latest completed round can be reopened with a reason only before a later round or draft is created.</p></div>'}
    </section>`;
  }

  function render() {
    const detail = status();
    const qualityNotes = [...new Set([...(detail.issues || []), ...(Array.isArray(detail.engineLimits) ? detail.engineLimits : detail.engineLimits ? [detail.engineLimits] : [])])];
    return `<div id="director-workspace" aria-busy="${state.loading}"><div class="page-heading"><div><p class="eyebrow">LOCAL TOURNAMENT DIRECTOR</p><h1>Run your tournament</h1><p>Create reviewed Swiss pairings, issue fixed rounds and record results for your local event.</p></div>${enabled() ? '<button class="secondary" data-director-action="new">Create another event</button>' : ''}</div>
      <div class="notice source-notice"><strong>Local event management · professional pairing procedures</strong><p>Rules, eligibility and issue-time checks remain explicit. This feature does not submit tournaments or rating reports to US Chess or FIDE.</p></div>
      ${offline() ? '<div class="notice danger source-notice"><strong>Saved event · read only</strong><p>Reconnect before changing the roster, issuing pairings or recording results. No actions are queued.</p></div>' : ''}
      ${state.error ? `<div class="notice danger source-notice" role="alert"><strong>${esc(state.error)}</strong>${enabled() ? `<p>If another action or tab changed this event, reload the saved event before retrying. Reloading discards unsaved board selections and roster edits in this view.</p><div class="action-row"><button class="secondary" data-director-action="reload"${disabled(busy())}>Reload saved event</button></div>` : ''}</div>` : ''}
      ${restorePanel()}
      ${!enabled() || state.newOpen ? `<section class="panel"><div class="panel-header"><h2>Create a local Swiss tournament</h2></div>${setupForm(true)}</section>` : `<section class="panel"><div class="panel-header"><div><h2>${esc(tournament().name)}</h2><p>${esc(tournament().section || 'Single section')} · ${esc(profiles[app().rules?.profile] || app().rules?.profile || '')} · ${Number(tournament().completedRounds || 0)} / ${Number(tournament().rounds || 0)} rounds complete</p></div><button class="secondary" data-director-action="download" data-format="json"${disabled(busy())}>Export event record</button></div><details><summary class="panel-body">Event settings and engine scope</summary>${setupForm(false)}</details></section>
      ${qualityNotes.length ? `<details class="notice source-notice separated-panel"><summary>Pairing readiness and engine scope</summary><ul>${qualityNotes.map(item => `<li>${esc(item)}</li>`).join('')}</ul></details>` : ''}
      ${resultsPanel()}${draftPanel()}${roster()}${standings()}${history()}`}
    </div>`;
  }

  function bind() {
    const root = document.querySelector('#director-workspace');
    if (!root) return;
    root.addEventListener('input', event => {
      const target = event.target;
      if (target.id === 'director-roster-text') state.bulkText = target.value;
      if (target.id === 'director-reopen-reason') state.reopenReason = target.value;
      if (target.closest?.('#director-new')) state.newDraft = readSetup(target.closest('#director-new'));
      if (target.closest?.('#director-settings')) state.setupDraft = readSetup(target.closest('#director-settings'));
      if (target.closest?.('#director-player')) state.playerDraft = readPlayer(target.closest('#director-player'));
    });
    root.addEventListener('change', event => {
      const target = event.target;
      if (busy()) return;
      if (target.id === 'director-restore-file') {
        const file = target.files?.[0];
        if (!file) return;
        state.restorePending = true;
        void operate(async () => {
          try {
            state.restorePreview = null; state.restoreFilename = '';
            if (file.size > 8 * 1024 * 1024) throw new Error('Choose a backup smaller than 8 MB.');
            let backup;
            try { backup = JSON.parse(await file.text()); } catch { throw new Error('This file is not a valid JSON event backup.'); }
            if (backup?.kind !== 'savvy-managed-event' || !backup.tournament?.managed?.enabled || !backup.rules || typeof backup.rules !== 'object') throw new Error('Choose a managed-event backup exported from Run tournament.');
            state.restorePreview = backup; state.restoreFilename = file.name;
          } finally { state.restorePending = false; }
        });
        return;
      }
      if (target.hasAttribute?.('data-director-board')) {
        const round = activeRound();
        if (!round) return;
        resultValues(round)[target.dataset.directorBoard] = target.value || null;
        state.resultsDirty = true;
        redraw();
        return;
      }
      if (target.closest?.('#director-new')) state.newDraft = readSetup(target.closest('#director-new'));
      if (target.closest?.('#director-settings')) state.setupDraft = readSetup(target.closest('#director-settings'));
      if (target.closest?.('#director-player')) state.playerDraft = readPlayer(target.closest('#director-player'));
    });
    root.addEventListener('submit', event => {
      event.preventDefault();
      const form = event.target;
      if (busy() || !form.reportValidity()) return;
      if (form.id === 'director-new' || form.id === 'director-settings') {
        const create = form.id === 'director-new', body = readSetup(form);
        // Disabled immutable fields are absent from FormData after play starts.
        if (!create && historyStarted()) { body.profile = app().rules.profile; body.firstRoundColor = app().rules.firstRoundColor; }
        if (create) state.newDraft = body; else state.setupDraft = body;
        void operate(async () => {
          await change(create ? '/api/director/new' : '/api/director/settings', body, create ? 'POST' : 'PUT', create ? 'Local event created. Add the players before generating a round.' : 'Event settings saved.');
          state.newOpen = false; state.newDraft = null; state.setupDraft = null; state.playerDraft = null; state.resultDraft = null; state.resultsDirty = false;
        });
      }
      if (form.id === 'director-player') {
        const draft = readPlayer(form), body = { name: draft.name, rating: draft.rating === '' ? 0 : Number(draft.rating), team: draft.team };
        state.playerDraft = draft;
        if (draft.id) {
          body.id = draft.id; body.active = draft.active;
          const player = tournament().players.find(item => String(item.id) === String(draft.id));
          const nextRound = Number(tournament().completedRounds || 0) + 1;
          body.scheduledByes = (player?.scheduledByes || []).filter(bye => Number(bye.round) !== nextRound);
          if (draft.nextBye !== '' && nextRound <= Number(tournament().rounds)) body.scheduledByes.push({ round: nextRound, points: Number(draft.nextBye) });
          if (historyStarted()) { delete body.name; delete body.rating; delete body.team; }
        }
        void operate(async () => { await change('/api/director/players', body, 'POST', draft.id ? 'Future availability saved.' : 'Player added.'); state.playerDraft = null; });
      }
      if (form.id === 'director-roster') {
        state.bulkText = String(new FormData(form).get('text') || '');
        void operate(async () => { await change('/api/director/roster', { text: state.bulkText }, 'POST', 'Roster added. Review the field before generating pairings.'); state.bulkText = ''; });
      }
      if (form.id === 'director-results') {
        const round = activeRound();
        if (!round) return;
        const values = resultValues(round);
        const body = { round: numberOf(round), expectedRevision: revision(), results: (snapshot(round).pairings || []).map(board => ({ board: Number(board.board), result: values[String(board.board)] || null })) };
        void operate(async () => { await change('/api/director/results', body, 'POST', 'Board results saved. Issued pairings are unchanged.'); state.resultDraft = null; state.resultsDirty = false; });
      }
      if (form.id === 'director-reopen') {
        state.reopenReason = String(new FormData(form).get('reason') || '').trim();
        void operate(async () => { await change('/api/director/reopen', { reason: state.reopenReason }, 'POST', 'Latest round reopened for a recorded correction.'); state.reopenReason = ''; state.resultDraft = null; });
      }
    });
    root.addEventListener('click', event => {
      const button = event.target.closest('[data-director-action]');
      if (!button || button.disabled || busy()) return;
      const action = button.dataset.directorAction;
      if (action === 'reload') void operate(async () => {
        const data = await hooks.request('/api/state');
        state.status = null; state.statusKey = ''; state.setupDraft = null; state.playerDraft = null; state.resultDraft = null; state.resultsDirty = false;
        hooks.acceptState(data);
        hooks.toast?.('Latest saved event loaded. Review it before retrying.');
      });
      if (action === 'new') { state.newOpen = true; state.error = ''; redraw(); }
      if (action === 'cancel-new') { state.newOpen = false; redraw(); }
      if (action === 'cancel-player') { state.playerDraft = null; redraw(); }
      if (action === 'discard-restore') { state.restorePreview = null; state.restoreFilename = ''; redraw(); }
      if (action === 'restore' && state.restorePreview) void operate(async () => {
        await change('/api/director/restore', { tournament: state.restorePreview.tournament, rules: state.restorePreview.rules }, 'POST', 'Saved local event restored.');
        state.restorePreview = null; state.restoreFilename = ''; state.playerDraft = null; state.setupDraft = null; state.resultDraft = null; state.resultsDirty = false; state.newOpen = false;
      });
      if (action === 'edit-player') {
        const player = tournament().players.find(item => String(item.id) === button.dataset.playerId);
        if (!player) return;
        const nextRound = Number(tournament().completedRounds || 0) + 1;
        const bye = (player.scheduledByes || []).find(item => Number(item.round) === nextRound);
        state.playerDraft = { id: player.id, name: player.name, rating: String(player.rating || 0), team: player.team || '', active: player.active !== false, nextBye: bye ? String(bye.points) : '' };
        redraw();
      }
      if (action === 'remove-player') void operate(() => change('/api/director/players/remove', { id: button.dataset.playerId }, 'POST', 'Player removed from the unissued roster.'));
      if (action === 'draft') void operate(() => change('/api/director/draft', {}, 'POST', 'Pairing draft calculated. Review it before issuing.'));
      if (action === 'discard-draft') void operate(() => change('/api/director/draft/discard', {}, 'POST', 'Unissued draft discarded. Issued history is unchanged.'));
      if (action === 'issue') {
        const draft = managed().draft || status().draft;
        if (draft?.id) void operate(() => change('/api/director/issue', { draftId: draft.id }, 'POST', 'Round issued. Its pairing snapshot is fixed.'));
      }
      if (action === 'close') {
        const round = activeRound();
        if (round && !state.resultsDirty) void operate(async () => { await change('/api/director/close', { round: numberOf(round) }, 'POST', 'Round closed. Scores and history committed together.'); state.resultDraft = null; });
      }
      if (action === 'download') {
        const round = button.dataset.round ? Number(button.dataset.round) : null, format = button.dataset.format;
        const path = format === 'print' ? `/api/director/print?round=${round}` : `/api/director/export?format=${format}${round ? `&round=${round}` : ''}`;
        void operate(() => hooks.download(path, format === 'print' ? `round-${round}-pairings.html` : format === 'json' ? 'local-tournament.json' : `round-${round}-pairings.csv`));
      }
    });
  }

  function readPlayer(form) {
    const values = new FormData(form), previous = state.playerDraft || {};
    return { id: previous.id, name: values.get('name') == null ? previous.name || '' : String(values.get('name') || '').trim(),
      rating: values.get('rating') == null ? previous.rating ?? '' : String(values.get('rating') || ''),
      team: values.get('team') == null ? previous.team || '' : String(values.get('team') || '').trim(),
      active: previous.id ? values.has('active') : true, nextBye: String(values.get('nextBye') || '') };
  }

  async function ensureLoaded() {
    if (!hooks || app().view !== 'director' || !enabled() || offline() || state.statusPending || app().directorStatus) return;
    const current = key();
    if (state.statusKey === current) return;
    state.statusKey = current; state.statusPending = true;
    try { state.status = await hooks.request('/api/director/status'); }
    catch (error) { state.error = error.message || 'Managed-event status could not be loaded.'; }
    finally { state.statusPending = false; redraw(); }
  }

  window.SavvyDirector = { configure(options) { hooks = options; }, render, bind, ensureLoaded };
})();
