'use strict';

// Replay has its own source and rules. Reviewing history never changes the workspace.
(() => {
  const CACHE_KEY = 'savvy-chess-replay-v1';
  const SAMPLE_FILENAME = 'Louisiana_State_Championship_2026_pairings_no_rematches.xlsx';
  const profiles = { 'us-chess': 'US Chess · ordered Swiss', 'fide-dutch': 'FIDE Dutch · pinned engine', 'fide-constraints': 'Custom constraint forecast' };
  const copy = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const list = values => [...new Set((values || []).filter(Boolean))];
  let hooks;
  const state = { initialized: false, loading: false, sequence: 0, source: 'sample', sourceTournament: null, metadata: null, rules: null, throughRound: 0, result: null, error: '', capturedAt: null, restored: false };

  const app = () => hooks?.getAppState() || {};
  const offline = () => Boolean(app().offline);
  const disabled = () => state.loading || offline();
  const redraw = () => hooks?.renderApp();
  const label = () => state.source === 'sample' ? 'Original workbook' : state.source === 'official' ? 'Official validation snapshot' : state.source === 'workspace' ? 'Workspace snapshot' : 'Uploaded workbook';
  const totalRounds = () => Number(state.sourceTournament?.rounds || state.result?.sourceSummary?.rounds || state.metadata?.rounds || 7);
  const number = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString() : '—';
  const points = value => Number.isFinite(Number(value)) ? String(Number(value)) : '—';
  const rate = value => typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1).replace(/\.0$/, '')}%` : '—';
  const names = ids => (ids || []).map(id => {
    const player = state.sourceTournament?.players?.find(p => String(p.id) === String(id)) || state.result?.tournament?.players?.find(p => String(p.id) === String(id));
    return player ? `${player.name} (#${id})` : `#${id}`;
  }).join(', ');

  function remember() {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ version: 1, source: state.source, metadata: state.metadata, rules: state.rules, throughRound: state.throughRound, result: state.result, capturedAt: state.capturedAt }));
    } catch (_) { /* A full device storage must not interrupt a replay. */ }
  }

  function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
      if (saved?.version !== 1 || !saved.result?.forecast || !saved.result?.comparison) return false;
      state.source = ['sample', 'official', 'workspace', 'upload'].includes(saved.source) ? saved.source : 'sample';
      state.metadata = saved.metadata;
      state.rules = saved.rules;
      state.result = saved.result;
      state.throughRound = saved.result.throughRound;
      state.capturedAt = saved.capturedAt;
      state.restored = true;
      return true;
    } catch (_) { return false; }
  }

  async function operate(work) {
    if (!hooks || disabled()) return;
    const sequence = ++state.sequence;
    state.loading = true;
    state.error = '';
    redraw();
    try {
      await work(sequence);
    } catch (error) {
      if (sequence === state.sequence) {
        state.error = error.message || 'The replay could not be loaded.';
        hooks.toast?.(state.error, true);
      }
    } finally {
      if (sequence === state.sequence) {
        state.loading = false;
        redraw();
      }
    }
  }

  async function predict(throughRound, sequence) {
    const body = { source: state.source, throughRound, rules: copy(state.rules || app().rules || {}) };
    if (!['sample', 'official'].includes(state.source)) body.tournament = copy(state.sourceTournament);
    let result;
    try {
      result = await hooks.request('/api/simulation', { method: 'POST', body });
    } catch (error) {
      // Keep a failed rule experiment from relabelling the previous comparison.
      if (sequence === state.sequence && state.result?.forecast?.rules) state.rules = copy(state.result.forecast.rules);
      throw error;
    }
    if (sequence !== state.sequence) return;
    if (!result?.forecast || !result?.comparison) throw new Error('The server returned an incomplete replay response.');
    state.result = result;
    state.throughRound = result.throughRound;
    state.capturedAt = new Date().toISOString();
    state.restored = false;
    remember();
  }

  async function loadSource(source) {
    return operate(async sequence => {
      state.source = source;
      state.result = null;
      state.metadata = null;
      state.sourceTournament = null;
      state.throughRound = 0;
      state.restored = false;
      if (source === 'sample' || source === 'official') {
        const response = await hooks.request(source === 'official' ? '/api/official' : '/api/sample');
        if (sequence !== state.sequence) return;
        state.metadata = response.metadata;
        state.sourceTournament = copy(response.tournament);
      } else if (source === 'workspace') {
        const tournament = app().tournament;
        if (!tournament?.players?.length) throw new Error('Load a tournament in the workspace before taking a replay snapshot.');
        state.sourceTournament = copy(tournament);
        state.metadata = { name: tournament.name, sourceKind: 'workspace-snapshot', players: tournament.players.length, rounds: tournament.rounds };
      } else {
        redraw();
        return;
      }
      await predict(0, sequence);
    });
  }

  async function upload(file) {
    if (!file) return;
    return operate(async sequence => {
      if (!/\.xlsx$/i.test(file.name)) throw new Error('Choose an Excel workbook (.xlsx) for this replay.');
      if (file.size > 8 * 1024 * 1024) throw new Error('Choose a workbook smaller than 8 MB.');
      const bytes = await file.arrayBuffer();
      const response = await hooks.request('/api/import', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name) }, body: bytes });
      if (sequence !== state.sequence) return;
      if (!response.tournament?.players?.length) throw new Error('No tournament roster was found in this workbook.');
      state.source = 'upload';
      state.sourceTournament = copy(response.tournament);
      state.metadata = { filename: file.name, sourceKind: 'uploaded-xlsx', sha256: response.summary?.sourceSha256, bytes: file.size, name: response.tournament.name, players: response.tournament.players.length, rounds: response.tournament.rounds };
      state.result = null;
      state.throughRound = 0;
      await predict(0, sequence);
    });
  }

  function options(values, selected) {
    return Object.entries(values).map(([value, text]) => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(text)}</option>`).join('');
  }

  function controls() {
    const locked = disabled() ? ' disabled' : '';
    const controlsLocked = disabled() || state.restored ? ' disabled' : '';
    const result = state.result;
    const cutoff = result?.throughRound ?? state.throughRound;
    const rounds = totalRounds();
    return `<section class="panel" aria-label="Replay controls">
      <div class="panel-header"><div><h2>Replay the recorded tournament</h2><p>Reveal one round of results, then forecast the following round.</p></div><span class="pill neutral">${esc(label())}</span></div>
      <div class="panel-body">
        <div class="simulation-controls">
          <div class="field"><label for="sim-source">Tournament source</label><select id="sim-source"${locked}>${options({ sample: 'Original sample workbook', official: 'Official US Chess validation snapshot', workspace: 'Snapshot of current workspace', upload: 'Upload another workbook' }, state.source)}</select></div>
          <div class="field"><label for="sim-profile">Pairing procedure</label><select id="sim-profile"${controlsLocked}>${options(profiles, state.rules?.profile || 'us-chess')}</select></div>
          <div class="field"><label for="sim-color">Initial color for top pairing number</label><select id="sim-color"${controlsLocked}>${options({ W: 'White', B: 'Black' }, state.rules?.firstRoundColor || 'W')}</select></div>
        </div>
        ${state.source === 'upload' ? `<div class="field"><label for="sim-file">Workbook to preview</label><input type="file" id="sim-file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"${locked}><p class="field-help">Imported into this replay only. Excel workbook, up to 8 MB.</p></div>` : ''}
        <div class="simulation-summary"><span class="pill${result?.forecast?.status === 'blocked' ? ' warning' : ''}">${cutoff === 0 ? 'Before round 1' : `Results through round ${number(cutoff)}`}</span><span class="small muted">${esc(profiles[state.rules?.profile] || profiles['us-chess'])} · initial ${state.rules?.firstRoundColor === 'B' ? 'Black' : 'White'}</span></div>
        <div class="action-row">
          <button class="secondary" data-sim-action="reset"${controlsLocked || !result || cutoff === 0 ? ' disabled' : ''}>Reset to start</button>
          <button class="secondary" data-sim-action="previous"${controlsLocked || !result || cutoff === 0 ? ' disabled' : ''}>Previous round</button>
          <button class="primary" data-sim-action="next"${controlsLocked || !result || cutoff >= rounds ? ' disabled' : ''}>${cutoff >= rounds ? 'End of recorded event' : `Reveal round ${cutoff + 1} results`}</button>
          <button class="text-button" data-sim-action="copy-rules"${controlsLocked}>Copy active rule controls</button>
          <button class="text-button" data-sim-action="reload"${locked}>Reload original sample</button>
        </div>
        <div class="simulation-rounds" role="group" aria-label="Choose results cutoff"><button class="${cutoff === 0 ? 'primary' : 'secondary'}" data-sim-round="0" aria-pressed="${cutoff === 0}"${controlsLocked || !result ? ' disabled' : ''}>Start</button>${Array.from({ length: rounds }, (_, i) => `<button class="${cutoff === i + 1 ? 'primary' : 'secondary'}" data-sim-round="${i + 1}" aria-pressed="${cutoff === i + 1}"${controlsLocked || !result ? ' disabled' : ''}>R${i + 1}</button>`).join('')}</div>
        <p class="field-help">Only results at or before the selected cutoff enter the forecast. Declared entries, ratings, schedules and requested byes are treated as known setup. The other rule controls are copied when the replay starts; use “Copy active rule controls” after changing them in the workspace.</p>
      </div>
    </section>`;
  }

  function provenance() {
    const metadata = state.metadata || {};
    const summary = state.result?.sourceSummary || {};
    const sample = state.source === 'sample';
    const warnings = list([...(summary.warnings || []), ...(summary.assumptions || [])]);
    const schedules = Object.entries(metadata.schedules || {}).map(([schedule, count]) => `${schedule}: ${count}`).join(' · ');
    return `<details class="notice source-notice separated-panel"><summary><div><strong>${esc(metadata.name || summary.name || label())}</strong><span>${number(metadata.players || summary.players)} players · ${number(metadata.rounds || summary.rounds)} rounds · ${sample ? 'Original XLSX evidence' : esc(label())}${metadata.completedRounds != null ? ` · complete through R${metadata.completedRounds}` : ''}</span></div></summary>
      <p>${esc(metadata.filename || summary.provenance?.source || 'Frozen tournament snapshot')}</p>
      ${state.source === 'official' ? `<p><a href="https://ratings.uschess.org/event/202609070433" target="_blank" rel="noreferrer">Official US Chess crosstable</a>${metadata.capturedAt ? ` · snapshot captured ${esc(metadata.capturedAt)}` : ''}. This is a saved validation source, not a live refresh.</p><p>Official results and colors are combined with the workbook’s declared pairing setup where membership IDs match. Actual pairing ratings, order, availability announcements, and director choices still require confirmation. Final ranking is never used as the starting pairing number.</p>` : ''}
      ${schedules ? `<p>${esc(schedules)}${metadata.mergeRound ? ` · merged from round ${metadata.mergeRound}` : ''}</p>` : ''}
      ${metadata.sha256 ? `<p class="source-attribution">${state.source === 'official' ? 'Source snapshot' : 'Workbook'} SHA-256: <code>${esc(metadata.sha256)}</code> · ${number(metadata.bytes)} bytes${metadata.worksheetCount != null ? ` · ${number(metadata.worksheetCount)} worksheets` : ''}</p>` : ''}
      <p>Whole-source standings totals are excluded from replay scoring. A blank result or missing color remains unknown.</p>
      ${warnings.length ? `<div class="source-issue-scroll"><ul>${warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
      ${sample ? `<p><button class="text-button" data-sim-action="download"${offline() ? ' disabled' : ''}>Download original workbook</button></p>` : ''}
    </details>`;
  }

  function metric(title, matches, compared, value, detail) {
    return `<div class="stat-card"><div class="stat-top">${esc(title)}</div><div class="stat-value">${rate(value)}</div><div class="stat-detail">${number(matches || 0)} / ${number(compared || 0)} ${esc(detail)}</div></div>`;
  }

  function coverage() {
    const rows = state.result?.coverage || [];
    if (!rows.length) return '';
    return `<section class="panel separated-panel"><div class="panel-header"><div><h2>Source evidence by round</h2><p>Coverage describes the workbook, independently of the chosen replay cutoff.</p></div></div><div class="table-scroll"><table><caption class="hidden">Recorded results and unresolved evidence for each round</caption><thead><tr><th>Round</th><th>Player records</th><th>Missing results</th><th>Unresolved players</th><th>Unknown colors</th><th>Conflicting players</th><th>Evidence</th></tr></thead><tbody>${rows.map(row => `<tr><th scope="row">R${row.round}</th><td>${number(row.recordedPlayers)} / ${number(row.expectedPlayers)}</td><td>${number(row.missingPlayers?.length || 0)}</td><td>${number(row.unresolvedPlayers?.length || 0)}</td><td>${number(row.unknownColorPlayers?.length || 0)}</td><td>${number(row.duplicatePlayers?.length || 0)}</td><td><span class="pill ${row.complete ? '' : row.status === 'not-entered' ? 'neutral' : 'warning'}">${row.complete ? 'Complete' : row.status === 'not-entered' ? 'Not entered' : 'Partial'}</span></td></tr>`).join('')}</tbody></table></div><div class="panel-footer">Counts identify players; a conflicting player can have more than one result row. Unknown colors affect color history even when a result is recorded.</div></section>`;
  }

  function comparison() {
    const result = state.result;
    const forecast = result.forecast;
    const comparison = result.comparison;
    const blocked = forecast.status !== 'ok';
    const opponents = new Map();
    for (const board of forecast.pairings || []) {
      opponents.set(String(board.white.id), { other: board.black, color: 'W' });
      opponents.set(String(board.black.id), { other: board.white, color: 'B' });
    }
    const predictedFor = player => {
      const entry = opponents.get(String(player.id));
      return entry ? `${entry.color === 'W' ? 'White' : 'Black'} vs ${entry.other.name} (#${entry.other.id})` : 'No forecast pairing';
    };
    const rows = (comparison.boards || []).map(board => {
      const status = board.conflicted ? 'Conflicting evidence' : board.invalidAssignment ? 'Invalid assignment' : !board.reciprocal ? 'Non-reciprocal evidence' : board.opponentMatch == null ? 'Not compared' : board.opponentMatch ? 'Opponent matched' : 'Different opponents';
      const colorText = !board.colorsKnown ? 'Colors unknown' : board.colorMatch == null ? 'Not compared' : board.colorMatch ? 'Colors matched' : 'Colors differ';
      const white = board.whiteId === board.player1.id ? board.player1 : board.whiteId === board.player2.id ? board.player2 : null;
      return `<tr><td><span class="player-name">${esc(board.player1.name)} <small class="muted">#${esc(board.player1.id)}</small></span><span class="player-sub">vs ${esc(board.player2.name)} (#${esc(board.player2.id)})${white ? ` · ${esc(white.name)} White` : ' · colors unknown'}</span></td><td>${esc(predictedFor(board.player1))}<span class="player-sub">${esc(predictedFor(board.player2))}</span></td><td><span class="pill ${board.opponentMatch === true ? '' : 'neutral'}">${esc(status)}</span><span class="player-sub">${esc(colorText)}</span></td></tr>`;
    }).join('');
    const notes = list(comparison.notes || []);
    return `<section class="separated-panel" aria-label="Observed comparison">
      <div class="page-heading"><div><h2>Round ${result.targetRound} forecast vs observed pairings</h2><p>${blocked ? 'Forecast unavailable at this cutoff; recorded evidence is shown for review.' : 'Agreement with recorded pairings for the first forecast candidate.'}</p></div></div>
      <div class="stats-grid">${metric('Observed opponent agreement', comparison.opponentMatches, comparison.opponentCompared, comparison.opponentRate, 'comparable boards')}${metric('Color agreement on matched boards', comparison.colorMatches, comparison.colorCompared, comparison.colorRate, 'matched boards with known colors')}${metric('Player color agreement', comparison.playerColorMatches, comparison.playerColorCompared, comparison.playerColorRate, 'players with comparable colors')}${metric('Recorded bye agreement', comparison.byeMatches, comparison.byeCompared, comparison.byeRate, 'recorded byes')}</div>
      <div class="notice"><strong>Agreement is a historical comparison, not a probability of the next pairing.</strong><p>${number(comparison.observedBoards)} observed boards; ${number(comparison.comparableBoards)} have unambiguous reciprocal evidence. ${number(comparison.conflictingBoards)} conflicting, ${number(comparison.nonReciprocalBoards)} non-reciprocal, ${number(comparison.missingColorBoards)} with unknown colors. Excluded evidence does not enter an agreement denominator.</p></div>
      ${rows ? `<div class="panel separated-panel"><div class="panel-header"><h3>Observed boards and forecast opponents</h3></div><div class="table-scroll"><table><thead><tr><th>Observed pairing</th><th>Forecast at cutoff</th><th>Comparison</th></tr></thead><tbody>${rows}</tbody></table></div></div>` : '<div class="notice separated-panel">No recorded opponent pairings are available for this target round.</div>'}
      ${comparison.observedByes?.length ? `<div class="panel separated-panel"><div class="panel-body"><h3>Observed byes and non-playing results</h3><ul class="issue-list">${comparison.observedByes.map(bye => `<li>${esc(bye.name)} (#${esc(bye.playerId)}) · ${bye.evidenceStatus === 'availability-unknown' ? 'U · unavailable/unspecified' : esc(bye.result)} · ${points(bye.points)} points · ${bye.match == null ? 'not compared' : bye.match ? 'forecast matched' : 'forecast differs'}</li>`).join('')}</ul></div></div>` : ''}
      ${notes.length ? `<details class="notice separated-panel"><summary>Comparison methodology and evidence limits</summary><ul>${notes.map(note => `<li>${esc(note)}</li>`).join('')}</ul>${comparison.unobservedPlayers?.length ? `<p>No opponent or bye evidence: ${esc(names(comparison.unobservedPlayers))}.</p>` : ''}</details>` : ''}
    </section>`;
  }

  function predictions() {
    const result = state.result;
    const forecast = result.forecast;
    if (result.throughRound >= totalRounds()) {
      return `<section class="panel separated-panel"><div class="panel-header"><h2>Event replay complete</h2><span class="pill neutral">${number(totalRounds())} rounds revealed</span></div><div class="panel-body"><p>There is no following round to pair. Review the source coverage below for any unresolved results, or return to an earlier cutoff to compare a forecast.</p></div></section>`;
    }
    const context = forecast.scheduleContext || {};
    const blocked = forecast.status !== 'ok';
    const issues = list([...(forecast.issues || []), ...(result.roundStatus?.issues || [])]);
    const warnings = list(forecast.warnings || []);
    const phaseText = context.mode === 'separate' ? `Schedules are paired separately until round ${context.mergeRound}.` : context.mode === 'merged' ? `Schedules are merged from round ${context.mergeRound}.` : 'Single pairing pool.';
    const player = p => `<span class="player-name">${esc(p.name)}</span><span class="player-sub">#${esc(p.id)} · ${number(p.rating || 0)} · ${points(p.score)} points${p.pairingScore != null && p.pairingScore !== p.score ? ` · pairs on ${points(p.pairingScore)}` : ''}</span>`;
    return `<section class="panel separated-panel"><div class="panel-header"><div><h2>${blocked ? 'Forecast stopped for review' : `Forecast for round ${result.targetRound}`}</h2><p>${esc(phaseText)} ${esc(forecast.engine || '')}</p></div><span class="pill ${blocked ? 'warning' : ''}">${blocked ? 'Blocked' : `${number(forecast.pairings?.length || 0)} boards`}</span></div>
      ${blocked ? `<div class="panel-body"><div class="notice danger"><strong>${result.throughRound >= totalRounds() ? 'The event has no following round.' : 'The available history does not support a reliable next-round forecast.'}</strong><p>${esc(forecast.blockingReason ? `Reason: ${forecast.blockingReason}.` : 'Review the recorded evidence and selected engine settings.')} No missing game result or color has been invented.</p>${issues.length ? `<div class="source-issue-scroll"><ul>${issues.map(issue => `<li>${esc(issue)}</li>`).join('')}</ul></div>` : ''}</div></div>` : `<div class="table-scroll"><table class="pairings-table"><thead><tr><th>Board</th><th>White</th><th>Black</th>${context.mode === 'separate' ? '<th>Schedule</th>' : ''}</tr></thead><tbody>${(forecast.pairings || []).map(board => `<tr><td>${number(board.board)}</td><td class="player-cell">${player(board.white)}</td><td class="player-cell">${player(board.black)}</td>${context.mode === 'separate' ? `<td>${esc(board.schedule || '')}</td>` : ''}</tr>`).join('')}</tbody></table></div>`}
      ${(forecast.requestedByes || []).map(bye => `<div class="bye-row"><strong>Requested bye · ${esc(bye.player.name)} (#${esc(bye.player.id)})</strong><span>${points(bye.points)} points${bye.schedule ? ` · ${esc(bye.schedule)}` : ''}</span></div>`).join('')}
      ${(forecast.byes || (forecast.bye ? [{ player: forecast.bye, points: state.rules?.byePoints }] : [])).map(bye => `<div class="bye-row"><strong>Pairing bye · ${esc(bye.player.name)} (#${esc(bye.player.id)})</strong><span>${points(bye.points)} points${bye.schedule ? ` · ${esc(bye.schedule)}` : ''}</span></div>`).join('')}
      ${warnings.length ? `<details class="panel-body details-issues"><summary>Forecast qualifications (${warnings.length})</summary><ul class="issue-list">${warnings.map(warning => `<li>${esc(warning)}</li>`).join('')}</ul></details>` : ''}
      <div class="panel-footer">${esc(forecast.scope || 'Prediction depends on the director’s actual procedure and event settings.')} ${esc(forecast.ruleEdition || '')}</div>
    </section>`;
  }

  function render() {
    const sample = state.source === 'sample';
    const unverifiedAcceleration = sample || state.sourceTournament?.sourceSettings?.accelerationConfirmed === false || state.result?.tournament?.sourceSettings?.accelerationConfirmed === false;
    let captured = '';
    if (state.capturedAt) {
      const date = new Date(state.capturedAt);
      captured = Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
    }
    return `<div id="simulation-workspace" aria-busy="${state.loading}"><div class="page-heading"><div><p class="eyebrow">HISTORICAL TOURNAMENT REPLAY</p><h1>Test the next round</h1><p>Use recorded results to test a forecast at each round boundary.</p></div></div>
      ${offline() || state.restored ? `<div class="notice source-notice"><strong>Read-only saved replay${captured ? ` · captured ${esc(captured)}` : ''}</strong><p>${offline() ? 'Reconnect to load source files or calculate another forecast.' : 'This saved response has not been recalculated. Reload a source to run a fresh replay.'} ${state.result ? 'The displayed results and agreement are from that saved response.' : 'No saved replay is available on this device.'}</p></div>` : ''}
      ${unverifiedAcceleration ? '<div class="notice source-notice"><strong>Workbook acceleration is unverified.</strong><p>The source workbook does not confirm whether acceleration was used. This replay does not infer it from the observed pairings. The selected procedure and initial color are assumptions; agreement can change when the actual director settings are known.</p></div>' : ''}
      ${state.source === 'official' && state.metadata?.workbookComparison ? `<div class="notice source-notice"><strong>Official evidence is separate from the workbook.</strong><p>${number(state.metadata.workbookComparison.matchedPlayers)} membership IDs matched. Compared with the workbook: ${number(state.metadata.workbookComparison.ratingsChanged)} pre-event ratings differ, ${number(state.metadata.workbookComparison.resultsChanged)} round records differ, ${number(state.metadata.workbookComparison.colorsChanged)} colors are supplied or changed, and ${number(state.metadata.workbookComparison.officialOnlyRecords)} round records are added. Original files and workspace results are unchanged.</p><p>Future bye requests and withdrawal announcements are unknown. Observed byes do not become advance declarations in this replay.</p></div>` : ''}
      ${controls()}
      ${state.error ? `<div class="notice danger separated-panel" role="alert">${esc(state.error)} Use “Reload original sample” to retry.</div>` : ''}
      ${state.loading ? '<div class="notice separated-panel" role="status">Loading source evidence and calculating the next round…</div>' : ''}
      ${state.result ? `${provenance()}${predictions()}${state.result.throughRound < totalRounds() ? comparison() : ''}${coverage()}` : !state.loading ? `<div class="notice separated-panel">${state.source === 'upload' ? 'Choose a workbook to begin a separate replay.' : 'Load the original sample to begin.'}</div>` : ''}
    </div>`;
  }

  function bind() {
    if (!hooks) return;
    const root = document.querySelector('#simulation-workspace');
    if (!root) return;
    root.addEventListener('change', event => {
      const target = event.target;
      if (disabled()) return;
      if (target.id === 'sim-source') loadSource(target.value);
      if (target.id === 'sim-file') upload(target.files?.[0]);
      if (target.id === 'sim-profile' || target.id === 'sim-color') {
        state.rules = { ...(state.rules || app().rules || {}), [target.id === 'sim-profile' ? 'profile' : 'firstRoundColor']: target.value };
        if (state.result && state.sourceTournament && !state.restored) operate(sequence => predict(state.throughRound, sequence));
      }
    });
    root.addEventListener('click', event => {
      const button = event.target.closest('[data-sim-action], [data-sim-round]');
      if (!button || button.disabled || disabled()) return;
      if (button.hasAttribute('data-sim-round')) {
        if (!state.sourceTournament || state.restored) return;
        operate(sequence => predict(Number(button.dataset.simRound), sequence));
        return;
      }
      const action = button.dataset.simAction;
      if (action === 'reload') { state.rules = copy(app().rules || {}); loadSource('sample'); }
      if (action === 'copy-rules') {
        state.rules = copy(app().rules || {});
        if (state.result && state.sourceTournament && !state.restored) operate(sequence => predict(state.throughRound, sequence));
        else redraw();
      }
      if (action === 'reset' || action === 'previous' || action === 'next') {
        if (!state.sourceTournament || state.restored) return;
        const round = action === 'reset' ? 0 : state.throughRound + (action === 'next' ? 1 : -1);
        operate(sequence => predict(round, sequence));
      }
      if (action === 'download') Promise.resolve(hooks.download?.('/api/sample/source', SAMPLE_FILENAME)).catch(error => hooks.toast?.(error.message, true));
    });
  }

  function configure(configuration) {
    if (!configuration || typeof configuration.request !== 'function' || typeof configuration.getAppState !== 'function' || typeof configuration.renderApp !== 'function') throw new Error('Simulation requires request, getAppState and renderApp callbacks.');
    hooks = configuration;
  }

  function ensureLoaded() {
    if (!hooks || state.initialized) return;
    state.initialized = true;
    state.rules = copy(app().rules || {});
    if (offline()) { restore(); redraw(); return; }
    return loadSource('sample');
  }

  window.SavvySimulation = { configure, render, bind, ensureLoaded };
})();
