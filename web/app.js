'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const clone = value => JSON.parse(JSON.stringify(value));
const defaults = { profile: 'us-chess', avoidRematches: true, strictColors: true, maxColorImbalance: 2, maxConsecutiveColors: 2, avoidSameTeam: false, firstRoundColor: 'W', byePoints: 1, allowRepeatBye: false, searchMillis: 1500, maxNodes: 100000, candidateCount: 3, pairingOrder: 'rating', colorPolicy: 'us-chess', usColorMethod: 'look-ahead', usTranspositionStyle: 'shift', usColorAlternationLimit: 80, usColorEqualizationLimit: 200, usEqualizationPriority: false, allowLastRoundExceptions: false, teamPolicy: 'off' };
const state = { tournament: null, rules: { ...defaults }, forecast: null, sources: [], engines: {}, followStatus: null, directorStatus: null, view: 'follow', candidate: 0, stale: false, busy: false, importPreview: null, playerSearch: '', offline: false, cachedAt: null, authRequired: false };
const main = $('#main');
const SNAPSHOT_KEY = 'savvy.workspace.snapshot.v1';
const ACCESS_KEY = 'savvy.workspace.access-key';
let toastTimer;
let memoryAccessKey = '';

function backendStatus(available) {
  document.dispatchEvent(new CustomEvent('savvy:backend', { detail: { available } }));
}

function accessKey() {
  try { return sessionStorage.getItem(ACCESS_KEY) || memoryAccessKey; } catch { return memoryAccessKey; }
}

async function fetchResponse(path, options = {}) {
  if (state.offline && !['GET','HEAD'].includes((options.method || 'GET').toUpperCase())) {
    throw new Error('This is a cached read-only view. Reconnect before editing or generating a new forecast.');
  }
  const headers = { ...options.headers };
  if (accessKey()) headers.Authorization = `Bearer ${accessKey()}`;
  let body = options.body;
  if (body !== undefined && !(body instanceof ArrayBuffer) && !(body instanceof Blob) && typeof body !== 'string') {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, { ...options, body, headers, cache: 'no-store' });
  } catch (error) {
    state.offline = true;
    backendStatus(false);
    applyReadOnly();
    throw new Error('The tournament server is unavailable. A saved view can be reviewed, but new pairings require a connection.');
  }
  state.authRequired = response.headers.get('X-Savvy-Auth-Required') === 'true';
  // A source publisher can fail while this server remains healthy. Only a
  // gateway/worker response without our server header means lost connectivity.
  const unavailable = response.status >= 502 && !response.headers.has('X-Savvy-Auth-Required');
  backendStatus(!unavailable);
  if (unavailable) {
    state.offline = true;
    applyReadOnly();
  }
  return response;
}

async function api(path, options = {}) {
  if (path === '/api/follow/new' && state.tournament?.managed?.enabled) {
    options = { ...options, body: { ...options.body, expectedRevision: state.tournament.managed.revision, expectedEventId: state.tournament.managed.eventId } };
  }
  const response = await fetchResponse(path, options);
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('json') ? await response.json() : { error: await response.text() };
  if (!response.ok) {
    const error = new Error(data.error || data.message || `Request failed (${response.status}).`);
    error.status = response.status;
    error.code = data.code;
    throw error;
  }
  return data;
}

async function download(path, filename = 'savvy-export.json') {
  const response = await fetchResponse(path);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || `Download failed (${response.status}).`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const supplied = disposition.match(/filename="([\w. -]+)"/);
  const link = document.createElement('a');
  const url = URL.createObjectURL(blob);
  link.href = url;
  link.download = supplied?.[1] || filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

function applyReadOnly() {
  if (!state.offline) { $('#workspace-offline-notice')?.remove(); return; }
  if (state.tournament && !$('#workspace-offline-notice')) {
    main.insertAdjacentHTML('afterbegin', `<div id="workspace-offline-notice" class="notice danger source-notice"><strong>Saved view · connection required</strong><p>These results may be out of date. Edits and new forecasts are disabled until this workspace reconnects.</p><button class="secondary" data-action="reconnect" data-offline-allowed>Reconnect to server</button></div>`);
  }
  $$('input, select, textarea, button', main).forEach(control => {
    if (!control.hasAttribute('data-offline-allowed')) control.disabled = true;
  });
}

function toast(message, isError = false) {
  const element = $('#toast');
  element.textContent = message;
  element.className = `toast visible${isError ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.className = 'toast'; }, isError ? 8500 : 4500);
}

async function runAction(action, message) {
  if (state.busy) return;
  state.busy = true;
  $$('button[data-action]').forEach(button => { button.disabled = true; });
  try {
    await action();
    if (message) toast(message);
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.busy = false;
    $$('button[data-action]').forEach(button => { button.disabled = false; });
    applyReadOnly();
  }
}

function cacheWorkspace() {
  const data = { tournament: state.tournament, rules: state.rules, forecast: state.forecast, sources: state.sources, engines: state.engines, followStatus: state.followStatus, directorStatus: state.directorStatus };
  try { localStorage.setItem(SNAPSHOT_KEY, JSON.stringify({ version: 1, savedAt: new Date().toISOString(), data })); } catch { /* Storage limits must not block online work. */ }
}

function acceptState(data, cached = false, cachedAt = null) {
  if (!data.tournament) throw new Error('The server did not return tournament data.');
  state.tournament = data.tournament;
  state.rules = { ...defaults, ...(data.rules || state.rules) };
  state.forecast = data.forecast || null;
  state.sources = data.sources || state.sources;
  state.engines = data.engines || state.engines;
  state.followStatus = data.followStatus || null;
  state.directorStatus = data.directorStatus || null;
  state.candidate = 0;
  state.stale = false;
  state.playerSearch = '';
  state.offline = cached;
  state.cachedAt = cachedAt;
  if (!cached) cacheWorkspace();
  render();
}

function score(player) {
  if (typeof player.score === 'number') return player.score;
  return (player.results || []).filter(result => result.round <= Number(state.tournament?.completedRounds || 0)).reduce((sum, result) => sum + (typeof result.points === 'number' ? result.points : ({ W: 1, D: .5, L: 0, ADJOURNED: .5, BYE: 1, HBYE: .5, ZERO: 0, FORFEIT_WIN: 1, FORFEIT_LOSS: 0 }[result.result] || 0)), Number(player.initialScore || 0));
}

function bakuAcceleration(tournament = state.tournament) {
  const acceleration = tournament?.acceleration;
  return (typeof acceleration === 'string' ? acceleration : acceleration?.method) === 'baku';
}

function setAutomaticAcceleration(tournament, selection) {
  if (selection === 'baku') {
    if (!bakuAcceleration(tournament)) tournament.acceleration = 'baku';
  } else if (bakuAcceleration(tournament)) delete tournament.acceleration;
}

function pairingScore(player) {
  if (typeof player.pairingScore === 'number') return player.pairingScore;
  if (bakuAcceleration()) return score(player);
  const round = Number(state.tournament?.completedRounds || 0) + 1;
  return score(player) + (player.virtualPoints || []).filter(entry => Number(entry.round) === round).reduce((sum, entry) => sum + Number(entry.points || 0), 0);
}

function pairingRating(player) { return player.pairingRating ?? player.rating ?? 0; }

function playerPairingMeta(player) {
  const actual = score(player);
  const virtual = pairingScore(player);
  const number = player.pairingNumber ?? player.seed;
  return `<span class="player-sub">#${escapeHtml(player.id)}${number != null ? ` · seed ${escapeHtml(number)}` : ''} &nbsp; ${escapeHtml(pairingRating(player) || 'Unrated')} &nbsp; <span class="score-chip">${points(actual)} pts</span>${virtual !== actual ? `<span class="pairing-score">Pairs on ${points(virtual)}</span>` : ''}</span>`;
}

function engineScope() {
  const exact = state.rules.profile === 'fide-dutch';
  const custom = state.rules.profile === 'fide-constraints';
  const label = exact ? 'FIDE Dutch engine result' : custom ? 'Custom constraint forecast' : 'US Chess Swiss forecast';
  const explanation = (!state.stale && state.forecast?.scope) || (exact ? 'Uses the pinned BBP Pairings Dutch engine for supported input. Engine availability and unsupported event settings are checked before pairing.' : custom ? 'A bounded optimizer explores your selected constraints. It does not implement a complete federation pairing procedure.' : 'Uses score brackets, natural split pairings, rating-limited color switches and explicit event variations. Tournament director choices can still change the official result.');
  const baku = bakuAcceleration() || (!state.stale && state.forecast?.engineDetails?.acceleration === 'baku');
  const accelerationContext = baku ? `<p>${exact ? 'FIDE Baku acceleration is computed by the native engine. Recorded scores remain unchanged; the score-group card does not estimate native Baku bracket adjustments.' : 'Automatic FIDE Baku acceleration requires the FIDE Dutch procedure. Select that procedure explicitly; no profile is switched automatically.'} Baku cannot be combined with nonzero manual virtual points.</p>` : '';
  return `<div class="notice"><strong>${label}</strong><p>${escapeHtml(explanation)} Candidate order is not a probability. <a href="#rulebook">Read the scope</a>.</p>${accelerationContext}${state.forecast?.ruleEdition ? `<p class="engine-edition">${escapeHtml(state.forecast.ruleEdition)}</p>` : ''}</div>`;
}

function forecastTiming(forecast) {
  if (!forecast?.stats) return 'Awaiting complete input';
  const elapsed = Math.round(forecast.stats.elapsedMs || 0);
  return forecast.rules?.profile === 'fide-dutch' || /^BBP\b/i.test(forecast.engine || '') ? `Native engine · ${elapsed} ms` : `${Number(forecast.stats.nodes || 0).toLocaleString()} search nodes · ${elapsed} ms`;
}

function points(value) {
  const number = Number(value || 0);
  return Number.isInteger(number) ? String(number) : number === .5 ? '½' : String(number).replace('.5', '½');
}

function eligibleForRound(player, round = Number(state.tournament?.completedRounds || 0) + 1) {
  return player.active !== false && Number(player.entryRound || 1) <= round && round <= Number(player.withdrawnAfterRound ?? state.tournament?.rounds ?? 0);
}

function availabilityLabel(player) {
  const round = Number(state.tournament.completedRounds || 0) + 1;
  if (player.active === false) return 'Inactive';
  if (round > Number(state.tournament.rounds)) return 'Event complete';
  if (round < Number(player.entryRound || 1)) return 'Not yet entered';
  if (round > Number(player.withdrawnAfterRound ?? state.tournament.rounds)) return 'Withdrawn';
  return 'Active';
}

function allIssues() {
  return [...new Set([...(state.tournament?.warnings || []), ...(state.forecast?.issues || [])])];
}

function sourceNotice() {
  const issues = allIssues();
  if (!issues.length) return '';
  const title = state.forecast?.blockingReason === 'engine' ? 'Review the requested engine' : ['constraints','search-limit'].includes(state.forecast?.blockingReason) ? 'Review the forecast settings' : 'Review the source before trusting a forecast';
  return `<details class="notice source-notice" ${state.forecast?.status !== 'ok' ? 'open' : ''}><summary><div><strong>${title}</strong><span>${issues.length} source or pairing issue${issues.length === 1 ? '' : 's'} need attention.</span></div></summary><div class="source-issue-scroll"><ul>${issues.map(issue => `<li>${escapeHtml(issue)}</li>`).join('')}</ul></div></details>`;
}

function heading(eyebrow, title, description, actions = '') {
  return `<div class="page-heading"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p>${description}</p></div>${actions ? `<div class="heading-actions">${actions}</div>` : ''}</div>`;
}

function exportPairingsButton() {
  if (state.forecast?.status !== 'ok' || state.stale || state.offline) return '<button id="export-pairings" class="secondary" disabled aria-disabled="true" title="Generate a valid forecast under the current rules before exporting">↓ Export pairings</button>';
  return `<a id="export-pairings" class="secondary" href="/api/export?format=csv&amp;candidate=${state.candidate}" download>↓ Export pairings</a>`;
}

function render() {
  if (!state.tournament) return;
  $('#event-label').textContent = state.tournament.name || 'Untitled tournament';
  $$('nav a[data-view]').forEach(link => {
    link.classList.toggle('active', link.dataset.view === state.view);
    if (link.dataset.view === state.view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  const views = { director: () => window.SavvyDirector?.render() || heading('RUN A TOURNAMENT', 'Your local event', 'Create an event, issue pairings and record results.'), follow: () => window.SavvyFollow?.render() || heading('FOLLOW A TOURNAMENT', 'Your tournament', 'Add an event and its published standings.'), forecast: renderForecast, players: renderPlayers, import: renderImport, rulebook: renderRulebook,
    simulation: () => window.SavvySimulation?.render() || heading('TOURNAMENT SIMULATION', 'Opening simulation', 'Loading the original workbook replay.') };
  main.innerHTML = (views[state.view] || renderForecast)();
  bindView();
  window.SavvyFollow?.onViewChange?.(state.view);
  window.SavvyDirector?.onViewChange?.(state.view);
  if (state.view === 'director') {
    window.SavvyDirector?.bind();
    window.SavvyDirector?.ensureLoaded();
  }
  if (state.view === 'follow') {
    window.SavvyFollow?.bind();
    window.SavvyFollow?.ensureLoaded();
  }
  if (state.view === 'simulation') {
    window.SavvySimulation?.bind();
    window.SavvySimulation?.ensureLoaded();
  }
  applyReadOnly();
}

function renderForecast() {
  if (state.tournament.managed?.enabled) return managedWorkspaceNotice();
  if (!state.tournament.players?.length) return `${heading('LIVE PAIRING FORECAST', 'Waiting for the field', 'Start following an event before its roster or opponents are published.')}<section class="panel"><div class="panel-body"><p>Add standings when they become available. No players or opponents are invented.</p><a class="primary" href="#follow">Set up your tournament</a></div></section>`;
  const tournament = state.tournament;
  const forecast = state.forecast;
  const players = tournament.players || [];
  const active = players.filter(player => eligibleForRound(player));
  const round = forecast?.round || Number(tournament.completedRounds || 0) + 1;
  const requestedByes = active.filter(player => (player.scheduledByes || []).some(bye => Number(bye.round) === round));
  const available = active.filter(player => !requestedByes.includes(player));
  const baku = bakuAcceleration(tournament) || forecast?.engineDetails?.acceleration === 'baku';
  const scoreGroups = new Set(available.map(player => baku ? score(player) : pairingScore(player))).size;
  const issues = allIssues();
  const isDemo = tournament.provenance?.format === 'demo' || /synthetic|demo/i.test(tournament.provenance?.source || '');
  return `${heading('ONE ROUND AHEAD', 'Next-round forecast', `${escapeHtml(tournament.section || 'Tournament')} · ${escapeHtml(tournament.name || 'Untitled tournament')}`, `${exportPairingsButton()}<button class="primary" data-action="generate">Generate forecast <span aria-hidden="true">↗</span></button>`)}
    ${renderMyForecast()}
    ${forecast?.assumptions?.length ? `<div class="notice source-notice"><strong>Forecast assumptions still to confirm</strong><ul>${forecast.assumptions.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : ''}
    ${isDemo ? '<div class="notice success source-notice"><strong>Complete synthetic demo</strong><p>This separate example demonstrates the forecasting workflow. These are not results from your workbook. <button class="text-button" data-action="reset">Restore workbook</button></p></div>' : ''}
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-top">Active players <span class="stat-icon" aria-hidden="true">♙</span></div><div class="stat-value">${active.length}<small>/ ${players.length}</small></div><div class="stat-detail">${round > Number(tournament.rounds) ? 'Event complete' : requestedByes.length ? `${requestedByes.length} requested bye${requestedByes.length === 1 ? '' : 's'} · ${available.length} to pair` : `${players.length - active.length} withdrawn or inactive`}</div></div>
      <div class="stat-card"><div class="stat-top">Forecast round <span class="stat-icon" aria-hidden="true">▦</span></div><div class="stat-value">${round}<small>of ${tournament.tracking?.roundsKnown === false ? 'unknown' : escapeHtml(tournament.rounds || '—')}</small></div><div class="stat-detail">${escapeHtml(tournament.completedRounds || 0)} rounds of recorded history</div></div>
      <div class="stat-card"><div class="stat-top">${baku ? 'Recorded score groups' : 'Pairing score groups'} <span class="stat-icon" aria-hidden="true">≋</span></div><div class="stat-value">${scoreGroups}</div><div class="stat-detail">${baku ? state.rules.profile === 'fide-dutch' ? 'Native Baku adjustments applied in engine' : 'Baku requires the native Dutch procedure' : 'Includes explicit virtual points'}</div></div>
      <div class="stat-card"><div class="stat-top">Review issues <span class="stat-icon" aria-hidden="true">⌁</span></div><div class="stat-value">${issues.length}</div><div class="stat-detail">${issues.length ? 'Source data or pairing constraints' : 'No reported source issues'}</div></div>
    </div>
    ${sourceNotice()}
    <div class="forecast-grid"><div class="content-stack">
      <section class="panel" aria-labelledby="pairings-title"><div class="panel-header"><div><h2 id="pairings-title">Round ${round} pairings</h2><p>Candidate pairings under your selected constraints</p></div><span class="pill ${forecast?.status === 'ok' ? '' : 'warning'}">${forecast?.status === 'ok' ? 'Forecast' : forecast?.status === 'incomplete' ? 'Incomplete source' : 'Needs review'}</span></div>${state.stale ? '<div class="stale-banner">Rules changed. Generate a new forecast to update these pairings.</div>' : ''}${renderPairings()}<div class="panel-footer"><span>${escapeHtml(forecast?.engine || 'Savvy constraint forecast')}</span><span>${forecastTiming(forecast)}</span></div></section>
      <section class="panel"><div class="panel-header"><h2>A forecast you can inspect</h2><span class="header-rule-pill">DIRECTOR REVIEW</span></div><div class="panel-body method-grid"><div class="method-item"><div class="step-number">01</div><h3>Check the history</h3><p>Scores, opponents, colors, and byes are checked before pairing.</p></div><div class="method-item"><div class="step-number">02</div><h3>Apply your rules</h3><p>Exclude repeat opponents and respect the color limits you choose.</p></div><div class="method-item"><div class="step-number">03</div><h3>Compare candidates</h3><p>Review alternatives and the reason behind each proposed board.</p></div></div></section>
      ${engineScope()}
    </div><aside class="content-stack">${renderRules()}<div class="panel source-line"><span class="source-icon" aria-hidden="true">▤</span><div><strong>Source of record</strong><span class="source-name">${escapeHtml(tournament.provenance?.source || 'Manual tournament data')}</span></div></div></aside></div>`;
}

function managedWorkspaceNotice() {
  return `${heading('MANAGED LOCAL EVENT', 'Run this tournament', 'Issued pairings and round results are managed together in Run tournament.')}<section class="panel"><div class="panel-body"><p>Use its roster, round results and correction controls to preserve issued pairings and the event history.</p><a class="primary" href="#director">Open Run tournament</a></div></section>`;
}

function renderMyForecast() {
  const tracking = state.tournament.tracking;
  if (!tracking) return '';
  const me = state.tournament.players.find(player => player.id === tracking.focusPlayerId);
  if (!me) return '<div class="notice source-notice"><strong>Your predicted opponents</strong><p><a href="#follow">Select yourself in Follow tournament</a> when the roster is available.</p></div>';
  if (state.stale || state.forecast?.status !== 'ok') return `<div class="notice source-notice"><strong>${escapeHtml(me.name)} · awaiting a usable forecast</strong><p>Review missing results and assumptions, then generate the next round. <a href="#follow">Check your event and standings source</a>.</p></div>`;
  const candidates = state.forecast.candidates?.length ? state.forecast.candidates : [state.forecast];
  const seen = new Set();
  const opponents = candidates.map(candidate => {
    const board = (candidate.pairings || []).find(pair => pair.white.id === me.id || pair.black.id === me.id);
    if (!board) return '';
    const other = board.white.id === me.id ? board.black : board.white;
    const color = board.white.id === me.id ? 'White' : 'Black';
    const key = `${other.id}:${color}`;
    if (seen.has(key)) return '';
    seen.add(key);
    return `<li><strong>${escapeHtml(other.name)}</strong> · ${escapeHtml(pairingRating(other) || 'Unrated')} · ${points(other.score)} points · ${state.forecast.orientationUnconfirmed ? `${color} under the selected color hypothesis` : `you play ${color}`}</li>`;
  }).filter(Boolean);
  return `<section class="panel source-notice"><div class="panel-header"><h2>${escapeHtml(me.name)} · possible next opponents</h2><a class="text-button" href="#follow">Your event</a></div><div class="panel-body">${opponents.length ? `<ul class="issue-list">${opponents.join('')}</ul>` : '<p>No opponent is assigned in these candidates. Review your bye and availability.</p>'}<p class="muted small">These candidates follow the selected rules and current results. Their order is not a probability; undisclosed director choices or later results can change your pairing.</p></div></section>`;
}

function renderPairings() {
  const forecast = state.forecast;
  if (!forecast || forecast.status !== 'ok' || !forecast.pairings?.length && !forecast.bye && !forecast.byes?.length && !forecast.requestedByes?.length) {
    const reason = forecast?.blockingReason || (forecast?.issues?.length ? 'history' : 'none');
    const messages = {
      history: ['Complete the tournament history', 'Review the source issues, missing results, and color history before generating the next-round forecast.'],
      setup: ['Complete the event setup', 'Set the details you know in Follow tournament. Unknown details remain visible as assumptions.'],
      settings: ['Review schedule and pairing settings', 'Declare whether schedules pair separately or when they merge before generating a complete forecast.'],
      constraints: ['No pairing fits these constraints', 'The available opponents, colors, and bye restrictions cannot produce a valid round. Review the constraints and event data.'],
      'search-limit': forecast?.rules?.profile === 'fide-dutch' ? ['Dutch engine execution limit reached', 'The native engine did not finish within its execution limit. Review the event restrictions and execution settings. No approximate fallback was substituted.'] : ['Search budget reached', 'No complete candidate was found within the current search budget. Increase the time or node limit and try again.'],
      engine: ['Dutch engine could not pair this event', 'Review the specific engine diagnostic above. Missing setup, unsupported event input and execution errors have different remedies.'],
      none: ['No valid forecast yet', 'Review the tournament data and constraints, then generate the next round.'],
    };
    const [title, description] = messages[reason] || messages.none;
    const diagnostic = reason === 'engine' && forecast?.issues?.length ? `<p class="engine-diagnostic">${escapeHtml(forecast.issues[0])}</p>` : '';
    return `<div class="empty-state"><div class="empty-piece" aria-hidden="true">♞</div><h3>${title}</h3><p>${description}</p>${diagnostic}<div class="action-row center-actions">${['constraints','search-limit','engine'].includes(reason) ? '<button class="secondary" data-action="focus-rules">Review pairing controls</button>' : '<a href="#players" class="secondary">Review players & results</a>'}<button class="primary" data-action="demo">Try a complete demo</button></div></div>`;
  }
  const candidates = forecast.candidates?.length ? forecast.candidates : [{ id: 'best', pairings: forecast.pairings, bye: forecast.bye, byes: forecast.byes }];
  const current = candidates[state.candidate] || candidates[0];
  const allocatedByes = current.byes || (current.bye ? [{player:current.bye, points:state.rules.byePoints}] : []);
  const tabs = candidates.length > 1 ? `<div class="candidate-tabs" role="group" aria-label="Forecast candidates">${candidates.map((candidate, index) => `<button class="candidate-tab ${index === state.candidate ? 'active' : ''}" data-candidate="${index}" aria-pressed="${index === state.candidate}">${index === 0 ? 'Best candidate' : `Alternative ${index}`}</button>`).join('')}</div>` : '';
  const rows = (current.pairings || []).map((pair, index) => `<tr><td>${pair.board || index + 1}</td><td class="player-cell"><span class="player-name"><span class="piece white-piece" aria-hidden="true">♙</span>${escapeHtml(pair.white.name)}</span>${playerPairingMeta(pair.white)}</td><td class="versus">vs</td><td class="player-cell"><span class="player-name"><span class="piece black-piece" aria-hidden="true">♟</span>${escapeHtml(pair.black.name)}</span>${playerPairingMeta(pair.black)}</td><td><div class="pair-reason">${(pair.explanations || []).map(escapeHtml).join('<br>') || 'Selected by pairing procedure'}</div></td></tr>`).join('');
  return `${tabs}<div class="table-scroll"><table class="pairings-table"><thead><tr><th>Board</th><th>White</th><th aria-label="versus"></th><th>Black</th><th>Pairing rationale</th></tr></thead><tbody>${rows}</tbody></table></div>${allocatedByes.map(bye => `<div class="bye-row"><span><strong>Pairing bye</strong> &nbsp; ${escapeHtml(bye.player.name)} <span class="muted">#${escapeHtml(bye.player.id)}</span>${bye.schedule ? ` · ${escapeHtml(bye.schedule)}` : ''}</span><span>${escapeHtml(bye.points)} points</span></div>`).join('')}${(forecast.requestedByes || []).map(bye => `<div class="bye-row"><span><strong>Requested bye</strong> &nbsp; ${escapeHtml(bye.player.name)} <span class="muted">#${escapeHtml(bye.player.id)}</span></span><span>${escapeHtml(bye.points)} point${Number(bye.points) === 1 ? '' : 's'}</span></div>`).join('')}${forecast.warnings?.length ? `<div class="panel-body"><details class="details-issues"><summary>${forecast.warnings.length} forecast note${forecast.warnings.length === 1 ? '' : 's'}</summary><ul class="issue-list">${forecast.warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join('')}</ul></details></div>` : ''}`;
}

function checked(key) { return state.rules[key] ? 'checked' : ''; }
function selected(key, value) { return String(state.rules[key]) === String(value) ? 'selected' : ''; }
function renderRules() {
  const rules = state.rules;
  const exact = rules.profile === 'fide-dutch';
  const us = rules.profile === 'us-chess';
  const strict = rules.colorPolicy === 'strict' || !us;
  const dutch = state.engines.dutch || {};
  const disabled = exact ? 'disabled' : '';
  const transpositionControl = `<div class="field"><label for="us-transposition-style">Transposition method</label><select id="us-transposition-style" name="usTranspositionStyle"><option value="shift" ${selected('usTranspositionStyle','shift')}>Shift intervening players</option><option value="direct-swap" ${selected('usTranspositionStyle','direct-swap')}>Swap selected players directly</option></select><p class="field-help">Both methods are permitted. Use the method consistently selected by the director or pairing program.</p></div>`;
  const engineStatus = exact ? `<div class="engine-status ${dutch.available ? 'ready' : 'unavailable'}"><strong>${dutch.available ? 'Pinned Dutch engine available' : 'Dutch engine needs setup'}</strong><p>${escapeHtml(dutch.available ? [dutch.version, dutch.ruleEdition].filter(Boolean).join(' · ') || 'The configured binary is checked before pairing.' : dutch.reason || 'Install and verify the pinned engine before selecting this procedure.')}</p>${!dutch.available ? '<code>python scripts/setup_dutch_engine.py</code>' : ''}<p>Original TPNs, initial color, bye points, explicit virtual points and prohibited pairs apply. US Chess color switches and custom search preferences do not replace the Dutch procedure.</p></div>` : '';
  return `<section class="panel rules-panel" aria-labelledby="rules-title"><div class="panel-header"><div><h2 id="rules-title">Pairing controls</h2><p>Set the rules for this forecast</p></div><span aria-hidden="true" class="muted">☷</span></div><form id="rule-form" class="rule-form">
    <div class="field"><label for="profile">Pairing procedure</label><select id="profile" name="profile"><option value="us-chess" ${selected('profile','us-chess')}>US Chess · ordered Swiss</option><option value="fide-dutch" ${selected('profile','fide-dutch')}>FIDE Dutch · pinned engine</option><option value="fide-constraints" ${selected('profile','fide-constraints')}>Custom constraint forecast</option></select><p class="field-help">Select the procedure actually used by the director. Custom matching is a separate exploratory mode.</p></div>
    ${engineStatus}
    <div class="field"><label for="pairing-order">Player order</label><select id="pairing-order" name="pairingOrder" ${exact ? 'disabled' : ''}>${exact ? '<option value="pairing-number" selected>Original tournament pairing numbers</option>' : `<option value="rating" ${selected('pairingOrder','rating')}>Pairing rating, then stable seed</option><option value="pairing-number" ${selected('pairingOrder','pairing-number')}>Definitive pairing number</option>`}</select><p class="field-help">${exact ? 'Dutch uses the full original TPN order, including withdrawn entries. A reviewed initial order may be generated before round 1. Later rating overrides do not renumber an existing tournament.' : 'Use definitive numbers only when the original starting order is known. Federation membership IDs are not pairing numbers.'}</p></div>
    <div class="field"><label for="first-color">Top seed’s first-round color</label><select id="first-color" name="firstRoundColor"><option value="W" ${selected('firstRoundColor','W')}>White</option><option value="B" ${selected('firstRoundColor','B')}>Black</option></select></div>
    <div class="field"><label for="team-policy">Same-team pairings</label><select id="team-policy" name="teamPolicy"><option value="off" ${selected('teamPolicy','off')}>No restriction</option><option value="prefer" ${selected('teamPolicy','prefer')}>${exact ? 'Soft preference · inactive in Dutch' : 'Prefer different teams'}</option><option value="forbid" ${selected('teamPolicy','forbid')}>Prohibit same-team pairings</option></select><p class="field-help">${exact ? 'Soft preferences do not alter Dutch pairings. A prohibition is passed to the engine as explicit prohibited-pair restrictions (XXP); it can prevent the round from being paired.' : "Uses each player's team field. A prohibition can prevent the round from being paired."}</p></div>
    <div class="field"><label for="bye-points">Points awarded for a pairing bye</label><select id="bye-points" name="byePoints"><option value="1" ${selected('byePoints',1)}>1 point</option><option value="0.5" ${selected('byePoints',.5)}>½ point</option><option value="0" ${selected('byePoints',0)}>0 points</option></select><p class="field-help">${exact ? 'Native Dutch calculation uses this award. Historical pairing-allocated byes must have the same constant award.' : 'Pairing-allocated byes are separate from requested half-point or zero-point absences.'}</p></div>
    <fieldset class="procedure-settings" ${disabled} ${exact ? 'hidden' : ''}><legend>Custom and US Chess settings</legend>
    <div class="rule-section-label">OPPONENTS & COLORS</div>
    <label class="toggle-field"><input type="checkbox" name="avoidRematches" ${checked('avoidRematches')}><span>Prohibit repeat opponents<small>Unplayed forfeits do not create a played encounter.</small></span></label>
    ${us ? `<div class="field"><label for="color-policy">Color policy</label><select id="color-policy" name="colorPolicy"><option value="us-chess" ${selected('colorPolicy','us-chess')}>US Chess priorities and exceptions</option><option value="strict" ${selected('colorPolicy','strict')}>Custom hard color limits</option></select><p class="field-help">US Chess procedure balances colors within its pairing priorities. Choose hard limits to reject every assignment outside the custom limits.</p></div>` : ''}
    <div class="hard-color-settings ${strict ? '' : 'muted-settings'}">
      <label class="toggle-field"><input type="checkbox" name="strictColors" ${checked('strictColors')} ${strict ? '' : 'disabled'}><span>Enforce custom color limits<small>${strict ? 'Hard limits can make an otherwise normal US Chess round impossible.' : 'Inactive under US Chess color policy.'}</small></span></label>
      <div class="field-row"><div class="field"><label for="imbalance">Max color imbalance</label><input id="imbalance" name="maxColorImbalance" type="number" min="1" max="6" value="${escapeHtml(rules.maxColorImbalance)}" ${strict ? '' : 'disabled'}></div><div class="field"><label for="streak">Max same-color streak</label><input id="streak" name="maxConsecutiveColors" type="number" min="1" max="6" value="${escapeHtml(rules.maxConsecutiveColors)}" ${strict ? '' : 'disabled'}></div></div>
    </div>
    ${us ? `<details class="details-advanced us-variations"><summary>US Chess color switches</summary>${transpositionControl}<div class="field-row"><div class="field"><label for="alternation-limit">Alternation switch limit</label><input id="alternation-limit" name="usColorAlternationLimit" type="number" min="0" max="1000" value="${escapeHtml(rules.usColorAlternationLimit)}"></div><div class="field"><label for="equalization-limit">Equalization switch limit</label><input id="equalization-limit" name="usColorEqualizationLimit" type="number" min="0" max="1000" value="${escapeHtml(rules.usColorEqualizationLimit)}"></div></div><p class="field-help">Default 80 / 200 points. Limits compare the players exchanged in a transposition or interchange, not the two eventual opponents.</p><div class="field extra-top-space"><label for="us-color-method">Conflicting due colors</label><select id="us-color-method" name="usColorMethod"><option value="look-ahead" ${selected('usColorMethod','look-ahead')}>Look ahead through the group</option><option value="top-down" ${selected('usColorMethod','top-down')}>Allocate from the top board</option></select></div><label class="toggle-field"><input type="checkbox" name="usEqualizationPriority" ${checked('usEqualizationPriority')}><span>Equalization over ratings · 29E5h<small>Use only when this variation applies. Removes the usual rating limits for correcting color.</small></span></label><label class="toggle-field"><input type="checkbox" name="allowLastRoundExceptions" ${checked('allowLastRoundExceptions')}><span>Permit last-round color exceptions<small>Explicit director choice. Any exception used is reported with the candidate.</small></span></label></details>` : ''}
    <div class="rule-section-label">BYE ELIGIBILITY OVERRIDE</div>
    <label class="toggle-field"><input type="checkbox" name="allowRepeatBye" ${checked('allowRepeatBye')}><span>Override repeat-bye restriction<small>Off by default. An override is reported as a departure from standard eligibility.</small></span></label>
    <details class="details-advanced search-settings"><summary>Search settings</summary><div class="field-row"><div class="field"><label for="search-ms">Time budget (ms)</label><input id="search-ms" type="number" name="searchMillis" min="50" max="5000" step="50" value="${escapeHtml(rules.searchMillis)}"></div><div class="field"><label for="max-nodes">Max search nodes</label><input id="max-nodes" type="number" name="maxNodes" min="100" max="500000" step="100" value="${escapeHtml(rules.maxNodes)}"></div></div><div class="field"><label for="candidate-count">Candidates to retain</label><select id="candidate-count" name="candidateCount">${[1,2,3,4,5].map(value => `<option value="${value}" ${selected('candidateCount',value)}>${value}</option>`).join('')}</select></div></details>
    </fieldset>
    <div class="generate-wrap"><button type="submit" class="primary" data-action="generate-form">Generate forecast <span aria-hidden="true">↗</span></button></div><p class="rules-note">${exact ? 'The Dutch engine returns one prescribed candidate. It does not silently fall back to the custom optimizer.' : 'Search completion and any used exceptions are reported. An alternative is a reviewable candidate, not a predicted percentage.'}</p>
  </form></section>`;
}

function resultToken(result) {
  if (result.result === 'UNPLAYED') return `<span class="round-token missing" title="Round ${result.round}: no completed result">R${result.round} ?${result.opponentId ? ` #${escapeHtml(result.opponentId)}` : ''}</span>`;
  const display = { BYE: 'bye', HBYE: '½ bye', ZERO: '0 bye', FORFEIT_WIN: '+', FORFEIT_LOSS: '−', ADJOURNED: 'adjourned · ½ for pairing' }[result.result] || result.result;
  const regular = ['W','D','L','ADJOURNED'].includes(result.result);
  const unplayed = regular && result.played === false;
  const colorDescription = regular && !unplayed ? result.color ? `, ${result.color === 'W' ? 'White' : 'Black'}` : ', color unknown' : ', does not count toward played color history';
  return `<span class="round-token" title="Round ${result.round}: ${escapeHtml(display)}${result.opponentId ? ` against #${escapeHtml(result.opponentId)}` : ''}${colorDescription}">R${result.round} ${escapeHtml(display)}${unplayed ? ' (unplayed)' : ''}${result.opponentId ? ` · ${regular && !unplayed ? result.color || '?' : '#'}${escapeHtml(result.opponentId)}` : ''}</span>`;
}

function playerRows() {
  const term = state.playerSearch.toLowerCase();
  const players = (state.tournament.players || []).filter(player => `${player.name} ${player.id} ${player.team || ''}`.toLowerCase().includes(term));
  if (!players.length) return '<tr><td colspan="7" class="muted">No players match your search.</td></tr>';
  return players.map(player => `<tr><td class="muted">${escapeHtml(player.id)}${player.pairingNumber != null ? `<span class="player-sub">Seed ${escapeHtml(player.pairingNumber)}</span>` : ''}</td><td><span class="player-name">${escapeHtml(player.name)}</span>${player.team ? `<span class="player-sub">${escapeHtml(player.team)}</span>` : ''}</td><td>${escapeHtml(pairingRating(player) || '—')}${player.pairingRating != null ? `<span class="player-sub">Source: ${escapeHtml(player.rating || 'Unrated')}</span>` : ''}</td><td><span class="score-chip">${points(score(player))}</span>${pairingScore(player) !== score(player) ? `<span class="pairing-score">Pairs on ${points(pairingScore(player))}</span>` : ''}${typeof player.reportedScore === 'number' && player.reportedScore !== score(player) ? `<span class="player-sub">Source: ${points(player.reportedScore)}</span>` : ''}</td><td><div class="round-tokens">${(player.results || []).slice().sort((a,b) => a.round - b.round).map(resultToken).join('') || '<span class="muted small">No results recorded</span>'}</div></td><td><span class="pill ${!eligibleForRound(player) ? 'neutral' : ''}">${availabilityLabel(player)}</span></td><td><button class="icon-button small" data-edit-player="${escapeHtml(player.id)}" aria-label="Edit ${escapeHtml(player.name)}">Edit</button></td></tr>`).join('');
}

function restrictionsText(pairs, forced = false) {
  return (pairs || []).map(pair => Array.isArray(pair) ? pair.join(', ') : forced ? `W:${pair.whiteId}, B:${pair.blackId}` : '').join('\n');
}

function parseRestrictions(text, forced = false) {
  return text.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map((line, index) => {
    const pair = line.split(',').map(value => value.trim());
    if (pair.length !== 2 || pair.some(value => !value)) throw new Error(`Restriction line ${index + 1} needs two player IDs separated by a comma.`);
    if (forced && /^W:/i.test(pair[0]) && /^B:/i.test(pair[1])) return { whiteId: pair[0].slice(2).trim(), blackId: pair[1].slice(2).trim() };
    if (forced && pair.some(value => /^[WB]:/i.test(value))) throw new Error(`Restriction line ${index + 1}: use W:player, B:player for a color-locked pairing.`);
    return pair;
  });
}

function renderPlayers() {
  if (state.tournament.managed?.enabled) return managedWorkspaceNotice();
  const tournament = state.tournament;
  return `${heading('THE SOURCE OF EVERY PAIRING', 'Players & results', 'Review the actual history. Missing colors and results stay visible.', '<button class="secondary" data-action="json-editor">Edit tournament JSON</button><a class="secondary" href="/api/export?format=json" download>↓ Export data</a>')}${sourceNotice()}
    <section class="panel"><div class="panel-header"><div><h2>Tournament details</h2><p>Set the recorded round boundary explicitly when correcting source data.</p></div></div><form id="metadata-form" class="panel-body"><div class="player-meta"><div class="field"><label for="tournament-name">Tournament name</label><input id="tournament-name" name="name" value="${escapeHtml(tournament.name)}" required></div><div class="field"><label for="tournament-rounds">Total rounds</label><input id="tournament-rounds" name="rounds" type="number" min="1" max="50" value="${tournament.tracking?.roundsKnown === false ? '' : escapeHtml(tournament.rounds)}" placeholder="Not known yet"></div><div class="field"><label for="completed-rounds">Recorded rounds</label><input id="completed-rounds" name="completedRounds" type="number" min="0" max="50" value="${escapeHtml(tournament.completedRounds || 0)}" required></div></div><div class="field extra-top-space"><label for="automatic-acceleration">Automatic acceleration</label><select id="automatic-acceleration" name="automaticAcceleration"><option value="" ${!bakuAcceleration(tournament) ? 'selected' : ''}>None · explicit manual virtual points still apply</option><option value="baku" ${bakuAcceleration(tournament) ? 'selected' : ''}>FIDE Baku · native Dutch engine</option></select><p class="field-help">Choose the announced event method. Baku requires the FIDE Dutch procedure; saving this choice does not switch profiles. Do not combine Baku with nonzero manual virtual points. Clearing Baku preserves all per-player virtual-point records.</p></div><div class="action-row"><button class="secondary small" type="submit" data-action="save-metadata">Save details</button><span class="muted small">Scores are calculated from recorded results, not copied totals.</span></div></form></section>
    <section class="panel separated-panel"><details class="director-settings"><summary>Director restrictions for the next round</summary><form id="director-form" class="panel-body"><div class="field-row"><div class="field"><label for="prohibited-pairs">Prohibited pairings</label><textarea id="prohibited-pairs" name="prohibitedPairs" rows="3" placeholder="1, 2">${escapeHtml(restrictionsText(tournament.prohibitedPairs))}</textarea><p class="field-help">One pair of player IDs per line. Membership IDs and seed numbers may differ from the IDs shown in the register.</p></div><div class="field"><label for="forced-pairs">Locked pairings</label><textarea id="forced-pairs" name="forcedPairs" rows="3" placeholder="1, 3&#10;W:2, B:4">${escapeHtml(restrictionsText(tournament.forcedPairs, true))}</textarea><p class="field-help">Use “1, 3” for an opponent lock or “W:2, B:4” to lock colors too. Unsupported external-engine restrictions are reported explicitly.</p></div></div><button type="submit" class="secondary small" data-action="save-restrictions">Save restrictions</button></form></details></section>
    <section class="panel separated-panel"><div class="toolbar"><div><h2>Player register <span class="muted small">(${tournament.players.length})</span></h2></div><label class="hidden" for="player-search">Search players</label><input type="search" id="player-search" class="search-input" placeholder="Search name, number, or team…" value="${escapeHtml(state.playerSearch)}" aria-label="Search players"></div><div class="table-scroll"><table class="players-table"><thead><tr><th>ID / seed</th><th>Player</th><th>Pairing rating</th><th>Score</th><th>Recorded rounds</th><th>Status</th><th><span class="hidden">Actions</span></th></tr></thead><tbody id="player-rows">${playerRows()}</tbody></table></div><div class="panel-footer"><span>W / D / L = result · W / B before an opponent number = color</span><span>? = missing source information</span></div></section>`;
}

function renderImport() {
  if (state.tournament.managed?.enabled) return managedWorkspaceNotice();
  return `${heading('BRING THE RESULTS TOGETHER', 'Import tournament results', 'Review an import before replacing the active tournament.', '<button class="secondary" data-action="reset">Restore original workbook</button>')}
    <div class="import-grid"><div class="content-stack"><section class="panel"><div class="panel-header"><h2>Import a file</h2><span class="pill neutral">LOCAL PARSING</span></div><div class="panel-body"><div class="drop-zone" id="drop-zone"><div class="upload-symbol" aria-hidden="true">↥</div><h3>Your results, one workspace</h3><p>Choose a workbook, crosstable, or tournament export.</p><input type="file" id="import-file" accept=".xlsx,.json,.csv,.trf,.txt,.html,.htm" aria-label="Choose tournament results file"><p class="field-help">XLSX · JSON · CSV · TRF · HTML</p></div><div class="divider">OR PASTE RESULTS</div><form id="text-import-form"><div class="field"><label for="text-format">Format</label><select id="text-format"><option value="results.csv">CSV</option><option value="tournament.json">Tournament JSON</option><option value="results.trf">FIDE TRF</option><option value="results.html">US Chess HTML</option></select></div><div class="field"><label for="import-text">Results text</label><textarea id="import-text" rows="7" placeholder="Paste the original export here…" required></textarea></div><button class="secondary" type="submit" data-action="preview-text">Preview import</button></form></div></section>
    <section class="panel"><div class="panel-header"><div><h2>Import from a source URL</h2><p>Supported public sources only. Every response is reviewed before use.</p></div></div><form class="panel-body" id="url-import-form"><div class="field"><label for="import-url">Official results URL</label><input id="import-url" name="url" type="url" placeholder="https://…" required><p class="field-help">US Chess and FIDE sources use different layouts. Protected, dynamic, or unsupported pages may require downloading an export first.</p></div><button class="secondary" type="submit" data-action="preview-url">Fetch & preview</button></form></section></div>
    <div class="content-stack"><section class="panel"><div class="panel-header"><h2>What can be imported?</h2></div><div class="panel-body"><ul class="format-list"><li><strong>The original forecast workbook</strong><span>Reads its actual results without turning blank cells or predicted opponents into played games.</span></li><li><strong>Normalized tournament JSON</strong><span>The complete player and round history. Export the current workspace to inspect the schema.</span></li><li><strong>CSV result tables</strong><span>Use the supported result-column schema documented in the repository. Unknown layouts are flagged rather than guessed.</span></li><li><strong>FIDE tournament reports</strong><span>TRF support is format-specific. Review player numbers, colors, byes, and round results in the preview.</span></li><li><strong>US Chess crosstable HTML</strong><span>Legacy layouts vary. HTML often omits color history, so follow-up data may be required for a forecast.</span></li></ul></div></section><div class="notice"><strong>Import is not automatic approval.</strong><p>Different sources omit different facts. A published total cannot replace the opponent and color history needed to predict pairings accurately.</p></div></div></div>
    <div id="import-preview">${renderImportPreview()}</div>`;
}

function renderImportPreview() {
  const imported = state.importPreview?.tournament;
  if (!imported) return '';
  return `<section class="panel import-preview"><div class="panel-header"><div><h2>Review import: ${escapeHtml(imported.name || 'Untitled tournament')}</h2><p>${escapeHtml(imported.provenance?.source || '')}</p></div><span class="pill warning">NOT APPLIED</span></div><div class="panel-body"><div class="preview-stats"><div class="preview-stat"><strong>${imported.players?.length || 0}</strong><span>Players</span></div><div class="preview-stat"><strong>${escapeHtml(imported.completedRounds || 0)}</strong><span>Recorded rounds</span></div><div class="preview-stat"><strong>${imported.warnings?.length || 0}</strong><span>Source warnings</span></div></div>${imported.warnings?.length ? `<div class="notice"><strong>Review these source issues</strong><ul>${imported.warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join('')}</ul></div>` : '<p class="muted small">No source warnings were returned. Review the sample records below before applying.</p>'}<div class="table-scroll"><table><thead><tr><th>No.</th><th>Player</th><th>Rating</th><th>Results</th></tr></thead><tbody>${(imported.players || []).slice(0,8).map(player => `<tr><td>${escapeHtml(player.id)}</td><td>${escapeHtml(player.name)}</td><td>${escapeHtml(player.rating || '—')}</td><td>${(player.results || []).length} recorded</td></tr>`).join('')}</tbody></table></div><p class="source-attribution">Showing up to eight players. Applying replaces the active workspace; the original workbook remains available using Restore original workbook.</p><div class="action-row"><button class="primary" data-action="apply-import">Apply imported tournament</button><button class="secondary" data-action="discard-import">Discard preview</button></div></div></section>`;
}

function renderRulebook() {
  return `${heading('KNOW WHAT THE FORECAST KNOWS', 'Rules & sources', 'The rules behind the controls, and the limits of the current engine.', '<a class="secondary" href="#simulation">Open replay lab</a>')}
    <div class="notice source-notice"><strong>Match the event’s actual procedure and inputs.</strong><p>A Dutch engine result and a custom matching search answer different questions. Original pairing numbers, entered ratings, announced variations, virtual points, withdrawals and director restrictions can change the official next round. No mode guarantees agreement when those inputs are missing.</p></div>
    <div class="rulebook-grid"><section class="panel"><div class="panel-header"><h2>Official rule references</h2><span class="pill neutral">2026 EDITIONS</span></div><article class="source-card"><h3>US Chess Swiss pairing</h3><p>The ordered Swiss forecast follows score brackets, natural upper-half versus lower-half pairings and bounded changes for color. Default 80 / 200-point switch limits compare exchanged players. Color equalization precedes alternation; announced variations and director choices remain explicit controls.</p><a href="https://new.uschess.org/sites/default/files/media/documents/us-chess-rule-book-online-2026.pdf" target="_blank" rel="noopener noreferrer">US Chess Official Rules of Chess · 2026 ↗</a></article><article class="source-card"><h3>FIDE Swiss principles</h3><p>The general rules distinguish played encounters, unplayed outcomes, color history, participation and pairing-allocated byes. An adjourned game contributes a provisional half point for pairing; it is not silently converted into a final draw.</p><a href="https://handbook.fide.com/chapter/C0401" target="_blank" rel="noopener noreferrer">FIDE C.04.1 · Basic rules ↗</a></article><article class="source-card"><h3>FIDE Dutch system</h3><p>The Dutch procedure effective February 1, 2026 specifies ordered brackets, criteria and candidate construction. Select FIDE Dutch to invoke the pinned BBP engine on supported input. Availability, rule edition and rejected settings are visible; failures do not substitute the custom optimizer.</p><a href="https://handbook.fide.com/chapter/C0403202602" target="_blank" rel="noopener noreferrer">FIDE Dutch system · effective February 1, 2026 ↗</a><p class="extra-top-space"><a href="https://github.com/BieremaBoyzProgramming/bbpPairings" target="_blank" rel="noopener noreferrer">BBP Pairings engine documentation ↗</a></p></article></section>
    <div class="content-stack"><section class="panel"><div class="panel-header"><h2>Inputs that change a pairing</h2></div><div class="panel-body"><ul class="format-list"><li><strong>Starting order and rating</strong><span>Keep a player’s internal ID, federation ID, definitive pairing number and entered pairing rating separate. An assigned rating can override an unrated or published value.</span></li><li><strong>Color procedure</strong><span>US Chess color switches and strict custom limits are separate choices. The forecast reports exceptions rather than hiding a third consecutive color or another imbalance.</span></li><li><strong>Virtual pairing points</strong><span>Enter the director’s actual acceleration points by round. These affect grouping while the recorded score stays unchanged. Historical virtual points remain available in tournament JSON.</span></li><li><strong>Director restrictions</strong><span>Record prohibited pairs, locked pairings and requested byes in Players & results. A restriction that an engine cannot express is rejected explicitly.</span></li><li><strong>Reviewable candidates</strong><span>US/custom search can return alternatives and report a search limit. Dutch returns its prescribed engine result. Candidate order is not a likelihood.</span></li></ul></div></section><section class="panel"><div class="panel-header"><h2>Official results onboarding</h2></div><div class="panel-body"><p class="rule-note">Structured exports are the most reliable route. Review source records before applying them, especially when published crosstables omit colors or original starting order. US Chess API limits were reduced in November 2026.</p><p><a href="https://new.uschess.org/news/us-chess-api-limits-lowered" target="_blank" rel="noopener noreferrer" class="small">US Chess API limits update ↗</a></p><a href="#import" class="secondary small">Open results import</a></div></section></div></div>`;
}

function readRules(form) {
  const values = new FormData(form);
  const rules = { ...state.rules };
  const editable = key => { const field = form.elements.namedItem(key); return field && !field.matches(':disabled'); };
  for (const key of ['avoidRematches','strictColors','allowRepeatBye','usEqualizationPriority','allowLastRoundExceptions']) if (editable(key)) rules[key] = values.has(key);
  for (const key of ['maxColorImbalance','maxConsecutiveColors','byePoints','searchMillis','maxNodes','candidateCount','usColorAlternationLimit','usColorEqualizationLimit']) if (editable(key)) rules[key] = Number(values.get(key));
  for (const key of ['profile','firstRoundColor','pairingOrder','colorPolicy','usColorMethod','usTranspositionStyle','teamPolicy']) if (editable(key)) rules[key] = values.get(key);
  if (editable('teamPolicy')) rules.avoidSameTeam = rules.teamPolicy !== 'off';
  return rules;
}

async function generate() {
  const form = $('#rule-form');
  if (form && !form.reportValidity()) return;
  if (form) state.rules = readRules(form);
  await runAction(async () => {
    state.forecast = await api('/api/forecast', { method: 'POST', body: { rules: state.rules } });
    state.rules = { ...state.rules, ...(state.forecast.rules || {}) };
    if (state.rules.profile === 'fide-dutch' || state.tournament.tracking) {
      const refreshed = await api('/api/state');
      state.engines = refreshed.engines || state.engines;
      state.tournament = refreshed.tournament || state.tournament;
      state.followStatus = refreshed.followStatus || null;
    }
    state.candidate = 0;
    state.stale = false;
    cacheWorkspace();
    render();
  }, 'Forecast updated.');
}

function bindView() {
  main.onclick = event => {
    const exportLink = event.target.closest('a[download]');
    if (exportLink && exportLink.getAttribute('href')?.startsWith('/api/')) {
      event.preventDefault();
      runAction(() => download(exportLink.getAttribute('href')));
      return;
    }
    const reconnect = event.target.closest('[data-action="reconnect"]');
    if (reconnect) { loadWorkspace(); return; }
    if (state.offline && event.target.closest('button,input,select,textarea')) {
      event.preventDefault();
      toast('This saved view is read-only. Reconnect to the tournament server first.', true);
      return;
    }
    const candidate = event.target.closest('[data-candidate]');
    if (candidate) { state.candidate = Number(candidate.dataset.candidate); render(); return; }
    const edit = event.target.closest('[data-edit-player]');
    if (edit) { openPlayerEditor(edit.dataset.editPlayer); return; }
    const actionElement = event.target.closest('[data-action]');
    if (!actionElement) return;
    const action = actionElement.dataset.action;
    if (action === 'generate') generate();
    if (action === 'focus-rules') {
      const form = $('#rule-form');
      const customBudget = state.forecast?.blockingReason === 'search-limit' && state.rules.profile !== 'fide-dutch';
      if (customBudget && form) $('.search-settings', form).open = true;
      form?.scrollIntoView({ behavior:'smooth', block:'center' });
      $(customBudget ? '#search-ms' : '#profile')?.focus({ preventScroll:true });
    }
    if (action === 'demo') runAction(async () => acceptState(await api('/api/demo', { method: 'POST' })), 'Opened the complete synthetic demo.');
    if (action === 'reset') runAction(async () => { state.importPreview = null; acceptState(await api('/api/reset', { method: 'POST' })); }, 'Restored the original workbook.');
    if (action === 'json-editor') openJsonEditor();
    if (action === 'discard-import') { state.importPreview = null; $('#import-preview').innerHTML = ''; }
    if (action === 'apply-import') runAction(async () => { const imported = state.importPreview.tournament; const data = await api('/api/tournament', { method: 'PUT', body: imported }); state.importPreview = null; state.view = 'players'; history.replaceState(null, '', '#players'); acceptState(data); }, 'Imported tournament applied.');
  };
  const ruleForm = $('#rule-form');
  ruleForm?.addEventListener('submit', event => { event.preventDefault(); generate(); });
  ruleForm?.addEventListener('change', event => {
    state.rules = readRules(ruleForm);
    state.stale = true;
    if (['profile','colorPolicy'].includes(event.target.name)) {
      const fieldId = event.target.id;
      render();
      document.getElementById(fieldId)?.focus({preventScroll:true});
      return;
    }
    const exportButton = $('#export-pairings');
    if (exportButton) exportButton.outerHTML = exportPairingsButton();
    const panel = $('#pairings-title')?.closest('.panel');
    if (panel && !$('.stale-banner', panel)) $('.panel-header', panel).insertAdjacentHTML('afterend', '<div class="stale-banner">Rules changed. Generate a new forecast to update these pairings.</div>');
  });
  $('#player-search')?.addEventListener('input', event => { state.playerSearch = event.target.value; $('#player-rows').innerHTML = playerRows(); });
  $('#metadata-form')?.addEventListener('submit', event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const updated = clone(state.tournament);
    updated.name = form.get('name').trim();
    updated.rounds = form.get('rounds') ? Number(form.get('rounds')) : 50;
    if (updated.tracking) updated.tracking.roundsKnown = Boolean(form.get('rounds'));
    updated.completedRounds = Number(form.get('completedRounds'));
    setAutomaticAcceleration(updated, form.get('automaticAcceleration'));
    runAction(async () => acceptState(await api('/api/tournament', { method: 'PUT', body: updated })), 'Tournament details saved.');
  });
  $('#director-form')?.addEventListener('submit', event => {
    event.preventDefault();
    const updated = clone(state.tournament);
    try {
      updated.prohibitedPairs = parseRestrictions($('#prohibited-pairs').value);
      updated.forcedPairs = parseRestrictions($('#forced-pairs').value, true);
    } catch (error) { toast(error.message, true); return; }
    runAction(async () => acceptState(await api('/api/tournament', {method:'PUT', body:updated})), 'Director restrictions saved.');
  });
  $('#import-file')?.addEventListener('change', event => { if (event.target.files[0]) importFile(event.target.files[0]); });
  const dropZone = $('#drop-zone');
  dropZone?.addEventListener('dragover', event => { event.preventDefault(); dropZone.classList.add('dragging'); });
  dropZone?.addEventListener('dragleave', () => dropZone.classList.remove('dragging'));
  dropZone?.addEventListener('drop', event => { event.preventDefault(); dropZone.classList.remove('dragging'); if (event.dataTransfer.files[0]) importFile(event.dataTransfer.files[0]); });
  $('#text-import-form')?.addEventListener('submit', event => {
    event.preventDefault();
    runAction(async () => showImportPreview(await api('/api/import/text', { method: 'POST', body: { text: $('#import-text').value, filename: $('#text-format').value } })));
  });
  $('#url-import-form')?.addEventListener('submit', event => {
    event.preventDefault();
    runAction(async () => showImportPreview(await api('/api/import/url', { method: 'POST', body: { url: $('#import-url').value } })));
  });
}

function showImportPreview(data) {
  if (!data.tournament) throw new Error('The import did not contain a tournament.');
  state.importPreview = data;
  $('#import-preview').innerHTML = renderImportPreview();
  $('#import-preview').scrollIntoView({ behavior: 'smooth', block: 'start' });
  toast('Import parsed. Review it before applying.');
}

function importFile(file) {
  if (file.size > 8 * 1024 * 1024) { toast('Choose a file smaller than 8 MB.', true); return; }
  runAction(async () => showImportPreview(await api('/api/import', { method: 'POST', headers: { 'X-Filename': encodeURIComponent(file.name), 'Content-Type': 'application/octet-stream' }, body: await file.arrayBuffer() })));
}

function openPlayerEditor(id) {
  const player = state.tournament.players.find(item => String(item.id) === String(id));
  if (!player) return;
  const dialog = $('#player-dialog');
  const rounds = Math.max(Number(state.tournament.completedRounds || 0), ...(player.results || []).map(result => Number(result.round)), 0);
  const upcomingRound = Number(state.tournament.completedRounds || 0) + 1;
  const hasUpcomingRound = upcomingRound <= Number(state.tournament.rounds);
  const prizeExceptionApplies = state.rules.profile === 'us-chess' && state.rules.colorPolicy === 'strict' && state.rules.allowLastRoundExceptions && upcomingRound === Number(state.tournament.rounds);
  const requestedBye = (player.scheduledByes || []).find(bye => Number(bye.round) === upcomingRound);
  const virtualPoints = (player.virtualPoints || []).filter(entry => Number(entry.round) === upcomingRound).reduce((sum, entry) => sum + Number(entry.points || 0), 0);
  const resultOptions = [['UNPLAYED','Unknown / unplayed'],['W','Win'],['D','Draw'],['L','Loss'],['ADJOURNED','Adjourned · ½ for pairing'],['BYE','Full-point bye'],['HBYE','Half-point bye'],['ZERO','Zero-point bye'],['FORFEIT_WIN','Forfeit win'],['FORFEIT_LOSS','Forfeit loss']];
  dialog.innerHTML = `<form id="player-editor-form">
    <div class="dialog-header"><h2 id="player-editor-title">${escapeHtml(player.name)}</h2><button type="button" class="icon-button" data-close-dialog aria-label="Close player editor">×</button></div>
    <div class="dialog-body"><div class="player-meta">
      <div class="field"><label for="edit-name">Player name</label><input id="edit-name" name="name" value="${escapeHtml(player.name)}" required></div>
      <div class="field"><label for="edit-rating">Source rating</label><input id="edit-rating" name="rating" type="number" min="0" max="4000" value="${escapeHtml(player.rating || 0)}" required></div>
      <div class="field"><label for="edit-team">Team (optional)</label><input id="edit-team" name="team" value="${escapeHtml(player.team || '')}"></div>
    </div><div class="player-pairing-inputs extra-top-space">
      <div class="field"><label for="edit-pairing-number">Definitive pairing number</label><input id="edit-pairing-number" name="pairingNumber" type="number" min="1" max="9999" value="${escapeHtml(player.pairingNumber ?? '')}" placeholder="Original seed: ${escapeHtml(player.seed ?? 'unknown')}"><p class="field-help">Optional original starting number, separate from player ID. Never copy a final standings rank here.</p></div>
      <div class="field"><label for="edit-pairing-rating">Assigned pairing rating</label><input id="edit-pairing-rating" name="pairingRating" type="number" min="0" max="4000" value="${escapeHtml(player.pairingRating ?? '')}" placeholder="Use source rating"><p class="field-help">Blank uses the source rating. Enter the director’s assigned value only when known.</p></div>
      ${hasUpcomingRound ? `<div class="field"><label for="edit-virtual-points">Virtual points for round ${upcomingRound}</label><input id="edit-virtual-points" name="virtualPoints" type="number" min="0" max="50" step="0.5" value="${escapeHtml(virtualPoints)}"><p class="field-help">Explicit acceleration points for pairing only. Actual score and earlier rounds are preserved.</p></div>` : ''}
    </div><div class="player-availability extra-top-space">
      <div class="field"><label for="edit-reported-score">Reported score (optional)</label><input id="edit-reported-score" name="reportedScore" type="number" min="0" max="100" step="0.5" value="${escapeHtml(player.reportedScore ?? '')}"><p class="field-help">Leave blank to remove the source total. Recorded results still calculate the score.</p></div>
      <div class="field"><label for="edit-entry-round">First participating round</label><input id="edit-entry-round" name="entryRound" type="number" min="1" max="${Number(state.tournament.rounds) + 1}" value="${escapeHtml(player.entryRound ?? 1)}"></div>
      <div class="field"><label for="edit-withdrawal-round">Last participating round</label><input id="edit-withdrawal-round" name="withdrawnAfterRound" type="number" min="0" max="${escapeHtml(state.tournament.rounds)}" value="${escapeHtml(player.withdrawnAfterRound ?? '')}" placeholder="Through end of event"><p class="field-help">Blank means the player remains in the event.</p></div>
    </div><label class="toggle-inline"><input type="checkbox" name="active" ${player.active !== false ? 'checked' : ''}>Enabled for pairing when within the participation rounds</label>
    <div class="player-special-cases extra-top-space"><div class="field"><label for="edit-reentry">Reentry of an earlier entry</label><select id="edit-reentry" name="reentryOf"><option value="">Not a reentry</option>${state.tournament.players.filter(item => item.id !== player.id).map(item => `<option value="${escapeHtml(item.id)}" ${String(player.reentryOf || '') === String(item.id) ? 'selected' : ''}>#${escapeHtml(item.id)} ${escapeHtml(item.name)}${item.active === false ? ' · inactive' : ''}</option>`).join('')}</select><p class="field-help">Choose the entry retained by the director and mark abandoned entries inactive separately. Earlier-entry colors are not inherited. Earlier encounters still matter for opponents who did not reenter. Scores are reviewed explicitly; the app does not automatically pick the best entry. US Chess reentries are not supported as FIDE Dutch entries.</p></div><div class="field"><label class="toggle-inline"><input type="checkbox" name="prizeContender" ${player.prizeContender ? 'checked' : ''}>Director-confirmed prize contender</label><p class="field-help">${prizeExceptionApplies ? 'The selected US Chess strict-policy final-round exception may use this qualification after ordinary pairings fail.' : 'US Chess qualification only. It cannot relax colors unless the final round, strict color policy and explicit last-round exception are all selected.'} Class-prize eligibility is not guessed from the overall score; this does not replace FIDE’s topscorer definition.</p></div></div>
    ${hasUpcomingRound ? `<div class="field extra-top-space"><label for="next-bye">Requested bye in round ${upcomingRound}</label><select id="next-bye" name="next-bye"><option value="" ${!requestedBye ? 'selected' : ''}>No requested bye</option>${[0,.5,1].map(value => `<option value="${value}" ${requestedBye && Number(requestedBye.points) === value ? 'selected' : ''}>${points(value)} point${value === 1 ? '' : 's'}</option>`).join('')}</select><p class="field-help">Excludes this player from next-round pairings. Other scheduled byes remain unchanged.</p></div>` : '<p class="editor-note">All scheduled rounds are complete. No next-round bye can be requested.</p>'}
    <div class="round-history-heading">RECORDED ROUND HISTORY · PLAYER #${escapeHtml(player.id)}</div>${rounds ? '<div class="result-edit-row"><span></span><span class="field-label">Result</span><span class="field-label">Opponent</span><span class="field-label">Color</span></div>' : '<p class="muted small">No history yet. Increase the recorded-round count in tournament details to add results.</p>'}${Array.from({ length: rounds }, (_, index) => {
    const round = index + 1;
    const result = player.results?.find(item => Number(item.round) === round) || {};
    const regularResult = ['W','D','L','ADJOURNED'].includes(result.result);
    return `<div class="result-edit-row" data-result-round="${round}" data-regular-result="${regularResult}"><span class="round-label">R${round}</span><div class="field"><select name="result-${round}" aria-label="Round ${round} result" data-result-code>${resultOptions.map(([value,label]) => `<option value="${value}" ${(result.result || 'UNPLAYED') === value ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div class="field"><select name="opponent-${round}" aria-label="Round ${round} opponent"><option value="">Unknown / no opponent</option>${state.tournament.players.filter(item => item.id !== player.id).map(item => `<option value="${escapeHtml(item.id)}" ${String(result.opponentId) === String(item.id) ? 'selected' : ''}>#${escapeHtml(item.id)} ${escapeHtml(item.name)}</option>`).join('')}</select></div><div class="field"><select name="color-${round}" aria-label="Round ${round} color"><option value="" ${!result.color ? 'selected' : ''}>?</option><option value="W" ${result.color === 'W' ? 'selected' : ''}>White</option><option value="B" ${result.color === 'B' ? 'selected' : ''}>Black</option></select></div><label class="toggle-inline result-played-toggle"><input type="checkbox" name="played-${round}" ${regularResult && result.played !== false ? 'checked' : ''} ${regularResult ? '' : 'disabled'}>Game played <span class="muted">(counts toward color history)</span></label></div>`;
  }).join('')}<p class="editor-note">Changed game results also update the named opponent’s reciprocal result. A result with missing opponent or color remains incomplete. Byes and forfeits do not count as played color history. The original source file is preserved.</p></div><div class="dialog-actions"><button type="button" class="secondary" data-close-dialog>Cancel</button><button type="submit" class="primary" id="save-player">Save player & results</button></div></form>`;
  $$('[data-close-dialog]', dialog).forEach(button => button.onclick = () => dialog.close());
  $$('[data-result-code]', dialog).forEach(select => select.addEventListener('change', () => {
    const row = select.closest('[data-result-round]');
    const played = $(`input[name="played-${row.dataset.resultRound}"]`, row);
    const regular = ['W','D','L','ADJOURNED'].includes(select.value);
    if (regular && row.dataset.regularResult !== 'true') played.checked = true;
    if (!regular) played.checked = false;
    played.disabled = !regular;
    row.dataset.regularResult = String(regular);
  }));
  $('#player-editor-form').onsubmit = event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const updated = clone(state.tournament);
    const edited = updated.players.find(item => item.id === player.id);
    edited.name = form.get('name').trim();
    edited.rating = Number(form.get('rating'));
    edited.team = form.get('team').trim();
    edited.active = form.has('active');
    edited.prizeContender = form.has('prizeContender');
    if (form.get('reentryOf')) edited.reentryOf = form.get('reentryOf');
    else delete edited.reentryOf;
    for (const field of ['reportedScore','entryRound','withdrawnAfterRound','pairingNumber','pairingRating']) {
      const value = form.get(field);
      if (value === '') delete edited[field];
      else edited[field] = Number(value);
    }
    if (hasUpcomingRound) {
      edited.scheduledByes = (edited.scheduledByes || []).filter(bye => Number(bye.round) !== upcomingRound);
      if (form.get('next-bye') !== '') edited.scheduledByes.push({ round: upcomingRound, points: Number(form.get('next-bye')) });
      edited.virtualPoints = (edited.virtualPoints || []).filter(entry => Number(entry.round) !== upcomingRound);
      if (Number(form.get('virtualPoints')) > 0) edited.virtualPoints.push({round:upcomingRound, points:Number(form.get('virtualPoints'))});
    }
    edited.results = edited.results || [];
    const changes = [];
    for (let round = 1; round <= rounds; round++) {
      const old = player.results?.find(item => Number(item.round) === round);
      const code = form.get(`result-${round}`);
      const bye = ['BYE','HBYE','ZERO'].includes(code);
      const next = { ...(old || {}), round, result: code, opponentId: bye ? null : form.get(`opponent-${round}`) || null, color: bye ? null : form.get(`color-${round}`) || null, played: ['W','D','L','ADJOURNED'].includes(code) && form.has(`played-${round}`) };
      if (old && next.result !== old.result) delete next.points;
      const changed = !old ? code !== 'UNPLAYED' || next.opponentId || next.color : next.result !== old.result || next.opponentId !== (old.opponentId || null) || next.color !== (old.color || null) || next.played !== (old.played ?? ['W','D','L','ADJOURNED'].includes(old.result));
      if (!changed) continue;
      if ((player.results || []).filter(item => Number(item.round) === round).length > 1) {
        toast(`Round ${round}: this player has conflicting records. Resolve them in Edit tournament JSON before changing this round.`, true); return;
      }
      if (old?.opponentId && old.opponentId !== next.opponentId) {
        const previousOpponent = updated.players.find(item => item.id === old.opponentId);
        if ((previousOpponent?.results || []).filter(item => Number(item.round) === round).length > 1) {
          toast(`Round ${round}: the previous opponent has conflicting records. Resolve them in Edit tournament JSON first.`, true); return;
        }
      }
      if (next.opponentId) {
        const opponent = updated.players.find(item => item.id === next.opponentId);
        const opponentRecords = (opponent?.results || []).filter(item => Number(item.round) === round);
        if (opponentRecords.length > 1) {
          toast(`Round ${round}: the chosen opponent has conflicting records. Resolve them in Edit tournament JSON first.`, true); return;
        }
        const existing = opponentRecords[0];
        if (existing?.opponentId && existing.opponentId !== player.id) { toast(`Round ${round}: the chosen opponent already has a different opponent. Correct that game first.`, true); return; }
        if (existing && !existing.opponentId && existing.result !== 'UNPLAYED') {
          toast(`Round ${round}: the chosen opponent has a separate bye or result. Resolve it in Edit tournament JSON before adding this game.`, true); return;
        }
      }
      changes.push({ old, next, round });
    }
    for (const {old,next,round} of changes) {
      edited.results = edited.results.filter(item => Number(item.round) !== round);
      edited.results.push(next);
      if (old?.opponentId && old.opponentId !== next.opponentId) {
        const previous = updated.players.find(item => item.id === old.opponentId);
        if (previous) previous.results = (previous.results || []).filter(item => !(Number(item.round) === round && item.opponentId === player.id));
      }
      if (next.opponentId) {
        const opponent = updated.players.find(item => item.id === next.opponentId);
        const reciprocal = { round, opponentId: player.id, color: next.color ? next.color === 'W' ? 'B' : 'W' : null, result: ({ W:'L',L:'W',D:'D',ADJOURNED:'ADJOURNED',FORFEIT_WIN:'FORFEIT_LOSS',FORFEIT_LOSS:'FORFEIT_WIN',UNPLAYED:'UNPLAYED' })[next.result] || 'UNPLAYED', played: next.played };
        const previousReciprocal = (opponent.results || []).find(item => Number(item.round) === round && (item.opponentId === player.id || !item.opponentId && item.result === 'UNPLAYED'));
        opponent.results = [...(opponent.results || []).filter(item => item !== previousReciprocal), reciprocal];
      }
    }
    const button = $('#save-player');
    button.disabled = true;
    runAction(async () => { const data = await api('/api/tournament', { method: 'PUT', body: updated }); dialog.close(); acceptState(data); }, 'Player and results saved.').finally(() => { button.disabled = false; });
  };
  dialog.showModal();
}

function openJsonEditor() {
  const dialog = $('#json-dialog');
  dialog.innerHTML = `<form id="json-editor-form"><div class="dialog-header"><h2 id="json-editor-title">Edit tournament data</h2><button type="button" class="icon-button" data-close-json aria-label="Close JSON editor">×</button></div><div class="dialog-body"><p class="muted small">Use the normalized schema to add players, section metadata, or additional round history. Changes are validated when saved.</p><div class="field"><label for="json-data">Tournament JSON</label><textarea id="json-data" class="json-small" spellcheck="false" required>${escapeHtml(JSON.stringify(state.tournament,null,2))}</textarea></div></div><div class="dialog-actions"><button type="button" class="secondary" data-close-json>Cancel</button><button class="primary" type="submit" id="save-json">Validate & save</button></div></form>`;
  $$('[data-close-json]', dialog).forEach(button => button.onclick = () => dialog.close());
  $('#json-editor-form').onsubmit = event => {
    event.preventDefault();
    let updated;
    try { updated = JSON.parse($('#json-data').value); } catch (error) { toast(`Invalid JSON: ${error.message}`,true); return; }
    const button = $('#save-json');
    button.disabled = true;
    runAction(async () => { const data = await api('/api/tournament', { method:'PUT',body:updated }); dialog.close(); acceptState(data); }, 'Tournament data saved.').finally(() => { button.disabled = false; });
  };
  dialog.showModal();
}

function changeView() {
  const requested = location.hash.slice(1);
  state.view = ['director','follow','forecast','players','import','rulebook','simulation'].includes(requested) ? requested : 'follow';
  render();
  window.scrollTo({ top:0, behavior:'instant' });
}

window.addEventListener('hashchange',changeView);
state.view = ['director','follow','forecast','players','import','rulebook','simulation'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'follow';

function renderAccessKey(error = '') {
  state.offline = false;
  main.onclick = null;
  main.innerHTML = `${heading('PRIVATE TOURNAMENT WORKSPACE', 'Connect to your tournament', 'Enter the access key supplied by the workspace owner.')}<section class="panel"><form id="workspace-access-form" class="panel-body"><div class="field"><label for="workspace-access-key">Tournament access key</label><input id="workspace-access-key" type="password" minlength="32" required autocomplete="off"><p class="field-help">The key is kept for this tab session and sent only to this app’s server.</p></div>${error ? `<p class="notice danger">${escapeHtml(error)}</p>` : ''}<button type="submit" class="primary">Connect</button></form></section>`;
  $('#workspace-access-form').onsubmit = async event => {
    event.preventDefault();
    memoryAccessKey = $('#workspace-access-key').value.trim();
    try { sessionStorage.setItem(ACCESS_KEY, memoryAccessKey); } catch { /* In-memory access still works when storage is disabled. */ }
    await loadWorkspace();
  };
}

async function loadWorkspace() {
  try {
    const data = await api('/api/state');
    acceptState(data);
  } catch (error) {
    if (error.status === 401) { renderAccessKey(error.message); return; }
    if (state.offline) {
      try {
        const saved = JSON.parse(localStorage.getItem(SNAPSHOT_KEY) || 'null');
        if (saved?.version === 1 && saved.data?.tournament) {
          acceptState(saved.data, true, saved.savedAt);
          return;
        }
      } catch { /* A missing or malformed saved view is not an online result. */ }
    }
    main.innerHTML = `${heading('WORKSPACE CONNECTION', 'The tournament could not be opened', 'A connection to the tournament server is required for new forecasts.')}<div class="notice danger"><strong>${escapeHtml(error.message)}</strong><p><button class="secondary" id="retry-load">Reconnect</button></p></div>`;
    $('#retry-load').onclick = loadWorkspace;
  }
}

window.SavvySimulation?.configure({ request: api, getAppState: () => state, renderApp: render, toast, download });
window.SavvyFollow?.configure({ request: api, getAppState: () => state, acceptState, renderApp: render, toast });
window.SavvyDirector?.configure({ request: api, getAppState: () => state, acceptState, renderApp: render, toast, download });
document.addEventListener('savvy:reconnect', loadWorkspace);
loadWorkspace();
