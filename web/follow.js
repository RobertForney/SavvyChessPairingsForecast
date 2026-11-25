'use strict';

// A player can start following an event before the roster or pairings exist.
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const profiles = { unknown: 'Not known yet', 'us-chess': 'US Chess Swiss', 'fide-dutch': 'FIDE Dutch Swiss', other: 'Another pairing system' };
  const policies = { unknown: 'Not known yet', single: 'One pairing pool', separate: 'Schedules stay separate', merge: 'Separate schedules, then merge' };
  const state = { loading: false, error: '', preview: null, status: null, statusKey: null, statusPending: false, newOpen: false, newDraft: null, draft: null, draftKey: '', autoCheck: false, timer: null, checkedAt: null, checkPending: false, backgroundMessage: '', sourceDraft: '', pasteDraft: '' };
  let hooks;
  const app = () => hooks?.getAppState() || {};
  const tournament = () => app().tournament || {};
  const tracking = () => tournament().tracking || {};
  const offline = () => Boolean(app().offline);
  const busy = () => state.loading || offline();
  const redraw = () => hooks?.renderApp();
  const count = value => Array.isArray(value) ? value.length : Number.isFinite(Number(value)) ? Number(value) : 0;
  const datetime = value => {
    if (!value) return 'Not checked yet';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Not recorded' : date.toLocaleString();
  };
  const options = (values, selected) => Object.entries(values).map(([key, label]) => `<option value="${esc(key)}"${String(selected) === key ? ' selected' : ''}>${esc(label)}</option>`).join('');

  function key() {
    const t = tournament();
    return JSON.stringify([t.name, t.section, t.completedRounds, t.players?.length, t.tracking]);
  }

  function values() {
    const t = tournament(), tr = tracking();
    const draftKey = JSON.stringify([t.name, t.section, tr.createdAt]);
    if (state.draft && state.draftKey === draftKey) return state.draft;
    state.draftKey = draftKey;
    state.draft = {
      name: t.name || '', section: t.section || '', playerQuery: tr.playerQuery || '', sourceUrl: tr.sourceUrl || '',
      rounds: t.tracking ? (tr.roundsKnown ? String(t.rounds) : '') : (t.rounds ? String(t.rounds) : ''),
      profile: tr.profile || 'unknown', firstRoundColor: tr.firstRoundColor || '',
      focusPlayerId: tr.focusPlayerId || '', startingOrderKnown: tr.startingOrderKnown === true,
      acceleration: tr.acceleration || 'unknown',
      schedulePolicy: tr.schedulePolicy || t.sourceSettings?.schedulePolicy || (t.sourceSettings?.mergeRound ? 'merge' : 'unknown'), mergeRound: (tr.mergeRound ?? t.sourceSettings?.mergeRound) == null ? '' : String(tr.mergeRound ?? t.sourceSettings?.mergeRound)
    };
    return state.draft;
  }

  function readForm(form) {
    const data = new FormData(form);
    const value = name => String(data.get(name) || '').trim();
    return { name: value('name'), section: value('section'), playerQuery: value('playerQuery'), sourceUrl: value('sourceUrl'),
      rounds: value('rounds') ? Number(value('rounds')) : null,
      profile: value('profile') || 'unknown', firstRoundColor: value('firstRoundColor') || null,
      acceleration: value('acceleration') || 'unknown',
      focusPlayerId: value('focusPlayerId') || null, startingOrderKnown: data.has('startingOrderKnown'),
      schedulePolicy: value('schedulePolicy') || 'unknown', mergeRound: value('schedulePolicy') === 'merge' && value('mergeRound') ? Number(value('mergeRound')) : null };
  }

  async function operate(work) {
    if (!hooks || busy()) return;
    state.loading = true;
    state.error = '';
    redraw();
    try { await work(); }
    catch (error) {
      state.error = error.message || 'The tournament could not be updated.';
      if (error.status === 409) state.preview = null;
      hooks.toast?.(state.error, true);
    } finally { state.loading = false; redraw(); }
  }

  function stopTimer() {
    if (state.timer != null) clearInterval(state.timer);
    state.timer = null;
  }

  function syncTimer() {
    const eligible = state.autoCheck && app().view === 'follow' && !offline() && tracking().sourceUrl;
    if (!eligible) { stopTimer(); return; }
    if (state.timer == null) state.timer = setInterval(() => {
      if (app().view !== 'follow' || offline() || !state.autoCheck || !tracking().sourceUrl) { stopTimer(); return; }
      if (state.loading || state.checkPending || state.preview) return;
      void checkSource(true);
    }, 120000);
  }

  async function checkSource(background = false) {
    const url = background ? tracking().sourceUrl : state.sourceDraft || tracking().sourceUrl;
    if (!url) { state.error = 'Enter the published standings URL or paste/upload an export.'; redraw(); return; }
    if (background) {
      if (busy() || state.checkPending) return;
      state.checkPending = true;
      const baseKey = key();
      try {
        const preview = await hooks.request('/api/follow/preview', { method: 'POST', body: { url, section: tournament().section || undefined } });
        if (key() !== baseKey) return;
        state.preview = preview;
        state.checkedAt = new Date().toISOString();
        state.backgroundMessage = 'A standings check is ready to review. It has not changed your tournament.';
        // Keep unsaved form fields and keyboard focus in place during a check.
        const target = document.querySelector('#follow-background-status');
        if (target) target.textContent = state.backgroundMessage;
        const review = document.querySelector('[data-follow-action="review-update"]');
        if (review) review.hidden = false;
      } catch (error) {
        state.backgroundMessage = error.message || 'The source check failed. Your tournament is unchanged.';
        const target = document.querySelector('#follow-background-status');
        if (target) target.textContent = state.backgroundMessage;
      } finally { state.checkPending = false; }
      return;
    }
    return operate(async () => {
      state.preview = await hooks.request('/api/follow/preview', { method: 'POST', body: { url, section: tournament().section || undefined } });
      state.checkedAt = new Date().toISOString();
    });
  }

  async function upload(file) {
    if (!file) return;
    return operate(async () => {
      if (file.size > 8 * 1024 * 1024) throw new Error('Choose an export smaller than 8 MB.');
      const response = await hooks.request('/api/import', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name) }, body: await file.arrayBuffer() });
      state.preview = await hooks.request('/api/follow/preview', { method: 'POST', body: { tournament: response.tournament } });
      state.checkedAt = new Date().toISOString();
    });
  }

  function setupForm(create = false) {
    const t = tournament(), v = create ? state.newDraft || { name: '', section: '', playerQuery: '', sourceUrl: '', rounds: '', profile: 'unknown', firstRoundColor: '', acceleration: 'unknown', schedulePolicy: 'unknown', mergeRound: '', startingOrderKnown: false } : values();
    const prefix = create ? 'follow-new' : 'follow-settings';
    const locked = busy() ? ' disabled' : '';
    return `<form id="${prefix}" data-follow-form="${create ? 'new' : 'settings'}" class="panel-body">
      <div class="simulation-controls">
        <div class="field"><label for="${prefix}-name">Tournament name</label><input id="${prefix}-name" name="name" value="${esc(v.name)}" maxlength="240" required${locked}></div>
        <div class="field"><label for="${prefix}-section">Section</label><input id="${prefix}-section" name="section" value="${esc(v.section)}" placeholder="Set when announced" maxlength="120"${locked}></div>
        <div class="field"><label for="${prefix}-player">Your name or member ID</label><input id="${prefix}-player" name="playerQuery" value="${esc(v.playerQuery)}" placeholder="You can identify yourself later" maxlength="160"${locked}></div>
      </div>
      <div class="field extra-top-space"><label for="${prefix}-source">Published standings URL</label><input id="${prefix}-source" name="sourceUrl" type="url" value="${esc(v.sourceUrl)}" placeholder="https://… — optional until results are published"${locked}><p class="field-help">Save the results page you plan to follow. A JavaScript-only page may require a downloadable export.</p></div>
      <details class="separated-panel"><summary>Details you can confirm when the event begins</summary><div class="simulation-controls extra-top-space">
        <div class="field"><label for="${prefix}-rounds">Total rounds</label><input id="${prefix}-rounds" name="rounds" type="number" min="1" max="50" value="${esc(v.rounds)}" placeholder="Not known yet"${locked}></div>
        <div class="field"><label for="${prefix}-profile">Pairing system</label><select id="${prefix}-profile" name="profile"${locked}>${options(profiles, v.profile)}</select></div>
        <div class="field"><label for="${prefix}-color">Top seed’s first-round color</label><select id="${prefix}-color" name="firstRoundColor"${locked}>${options({ '': 'Not known yet', W: 'White', B: 'Black' }, v.firstRoundColor)}</select></div>
        <div class="field"><label for="${prefix}-acceleration">Acceleration</label><select id="${prefix}-acceleration" name="acceleration"${locked}>${options({ unknown: 'Not known yet', none: 'No acceleration', baku: 'FIDE Baku', manual: 'Manual virtual points' }, v.acceleration || 'unknown')}</select><p class="field-help">Baku requires confirmed total rounds and the FIDE Dutch procedure. Configure manual virtual points in <a href="#players">Players &amp; results</a> when the announced schedule is known.</p></div>
        <div class="field"><label for="${prefix}-schedule">Schedules / pairing pools</label><select id="${prefix}-schedule" name="schedulePolicy"${locked}>${options(policies, v.schedulePolicy)}</select></div>
        <div class="field"><label for="${prefix}-merge">First merged round</label><input id="${prefix}-merge" name="mergeRound" type="number" min="1" max="50" value="${esc(v.mergeRound)}" placeholder="Only for merging schedules"${locked}></div>
      </div><p class="field-help">Unknown details stay unknown. Standings rank is not the original pairing number. Another pairing system can be tracked, but Swiss forecasts may be unavailable.</p><label class="toggle-inline"><input type="checkbox" name="startingOrderKnown"${v.startingOrderKnown ? ' checked' : ''}${locked}> Original pairing numbers are confirmed</label></details>
      ${!create && t.players?.length ? `<div class="field separated-panel"><label for="${prefix}-focus">Find yourself in the published roster</label><select id="${prefix}-focus" name="focusPlayerId"${locked}><option value="">Select when your entry appears</option>${t.players.map(p => `<option value="${esc(p.id)}"${String(v.focusPlayerId) === String(p.id) ? ' selected' : ''}>${esc(p.name)}${p.usChessId ? ` · US Chess ${esc(p.usChessId)}` : ''}</option>`).join('')}</select></div>` : ''}
      ${create && (t.players?.length || t.tracking?.createdAt) ? '<p class="notice separated-panel">Starting another event replaces this device’s active server workspace. Export the current tournament first if you need to keep it.</p>' : ''}
      <div class="action-row separated-panel"><button class="primary" type="submit"${locked}>${create ? 'Start following this event' : 'Save known details'}</button>${create ? '<button type="button" class="secondary" data-follow-action="cancel-new">Cancel</button>' : ''}</div>
    </form>`;
  }

  function readiness() {
    const t = tournament(), tr = tracking();
    const status = app().followStatus || state.status || {};
    const ready = status.readiness || status;
    const issues = [...new Set([...(ready.issues || []), ...(ready.missingDetails || [])].map(item => typeof item === 'string' ? item : item.message || item.label || '').filter(Boolean))];
    const roster = t.players?.length || 0;
    return `<section class="panel separated-panel"><div class="panel-header"><div><h2>Ready for the next round?</h2><p>Only published evidence and confirmed event settings support a forecast.</p></div><span class="pill ${ready.ready ? '' : 'warning'}">${ready.ready ? 'Ready to forecast' : 'More information needed'}</span></div><div class="panel-body">
      <div class="preview-stats"><div class="preview-stat"><strong>${roster}</strong><span>Published players</span></div><div class="preview-stat"><strong>${Number(ready.completedRounds ?? t.completedRounds ?? 0)}</strong><span>Reviewed rounds</span></div><div class="preview-stat"><strong>${tr.roundsKnown ? Number(t.rounds) : 'Unknown'}</strong><span>Total rounds</span></div></div>
      ${!roster ? '<p>No opponent list is needed to start following. Add the published roster or standings when they appear.</p>' : ''}
      ${issues.length ? `<ul class="issue-list">${issues.map(issue => `<li>${esc(issue)}</li>`).join('')}</ul>` : !ready.ready ? '<p>Confirm the pairing system and add enough round history to establish who can meet next.</p>' : '<p>The known section is ready. Forecasts remain dependent on announced byes, withdrawals and director choices.</p>'}
      <p class="source-attribution">Last source check: ${esc(datetime(state.checkedAt || tr.lastCheckedAt || tr.lastRefreshedAt || ready.lastUpdatedAt))}</p>
      <a class="secondary" href="#forecast">${ready.ready ? 'See my pairing forecast' : 'Review forecast requirements'}</a>
    </div></section>`;
  }

  function focusForecast() {
    const focus = tracking().focusPlayerId;
    if (!focus) return '';
    const forecast = app().forecast;
    const player = tournament().players?.find(p => String(p.id) === String(focus));
    if (!player) return '';
    const board = forecast?.status === 'ok' && !app().stale && !offline() ? forecast.pairings?.find(b => [b.white.id, b.black.id].some(id => String(id) === String(focus))) : null;
    const other = board ? String(board.white.id) === String(focus) ? board.black : board.white : null;
    return `<section class="notice separated-panel"><strong>Following ${esc(player.name)}</strong><p>${other ? `The current candidate pairs you with ${esc(other.name)} ${forecast.orientationUnconfirmed ? `under the selected ${String(board.white.id) === String(focus) ? 'White' : 'Black'} color hypothesis` : `as ${String(board.white.id) === String(focus) ? 'White' : 'Black'}`}. This is a forecast, not a published assignment.` : 'Your future opponent is not known yet. New published results and confirmed availability can change the next pairing.'}</p><a href="#forecast">Open the forecast and its assumptions</a></section>`;
  }

  function preview() {
    const p = state.preview;
    if (!p) return '';
    const summary = p.summary || {};
    const warnings = [summary.warnings, summary.issues, summary.conflicts].flatMap(items => Array.isArray(items) ? items : []).map(item => typeof item === 'string' ? item : item.message || JSON.stringify(item));
    for (const correction of summary.corrections || []) warnings.push(`${correction.name || correction.playerId}: round ${correction.round} changes a previously recorded result. Review this correction before applying.`);
    const disabled = busy() || p.applyAllowed === false || summary.applyAllowed === false ? ' disabled' : '';
    return `<section class="panel separated-panel" aria-label="Standings update preview"><div class="panel-header"><div><h2>Review the new standings</h2><p>No changes have been applied to your event.</p></div><span class="pill warning">Review required</span></div><div class="panel-body">
      <div class="preview-stats"><div class="preview-stat"><strong>${count(summary.addedPlayers)}</strong><span>New players</span></div><div class="preview-stat"><strong>${count(summary.addedResults ?? summary.newResults) + count(summary.updatedResults)}</strong><span>New / updated round records</span></div><div class="preview-stat"><strong>${count(summary.conflicts) + count(summary.corrections)}</strong><span>Corrections / conflicts</span></div></div>
      ${summary.message ? `<p>${esc(summary.message)}</p>` : ''}${warnings.length ? `<div class="notice"><ul>${warnings.slice(0, 30).map(warning => `<li>${esc(warning)}</li>`).join('')}</ul>${warnings.length > 30 ? `<p>${warnings.length - 30} more issues are included in the source review.</p>` : ''}</div>` : ''}
      <p class="field-help">The update preserves your confirmed event setup. Missing rows do not withdraw a player. Reported totals alone cannot supply missing opponents or colors.</p>
      <div class="table-scroll"><table><thead><tr><th>Player</th><th>Recorded rounds</th></tr></thead><tbody>${(p.tournament?.players || []).slice(0, 8).map(player => `<tr><td>${esc(player.name)}</td><td>${player.results?.length || 0}</td></tr>`).join('')}</tbody></table></div>
      <div class="action-row separated-panel"><button class="primary" data-follow-action="apply"${disabled}>Apply reviewed standings</button><button class="secondary" data-follow-action="discard"${busy() ? ' disabled' : ''}>Discard this update</button></div>
    </div></section>`;
  }

  function sourceControls() {
    const locked = busy() ? ' disabled' : '';
    return `<section class="panel separated-panel"><div class="panel-header"><div><h2>Follow published results</h2><p>Check the standings as rounds finish. Review new evidence before it changes your forecast.</p></div></div><div class="panel-body">
      <form id="follow-source-form"><div class="field"><label for="follow-check-url">Standings or export URL</label><input id="follow-check-url" name="url" type="url" value="${esc(state.sourceDraft || tracking().sourceUrl || '')}" placeholder="https://…" required${locked}></div><div class="action-row"><button class="secondary" type="submit"${locked}>Check published standings</button></div></form>
      <p class="field-help">Supported result exports can be fetched directly. Some publishers require a downloadable file, pasted table or an authenticated API connection; the app does not invent records from an unsupported page.</p>
      <label class="toggle-inline separated-panel"><input id="follow-auto-check" type="checkbox"${state.autoCheck ? ' checked' : ''}${locked || !tracking().sourceUrl ? ' disabled' : ''}> Check saved URL every two minutes while this view is open</label><p class="field-help">Checks prepare a review only. Nothing is applied automatically, and an existing preview is kept until you decide.</p>
      <p id="follow-background-status" class="field-help" role="status">${esc(state.backgroundMessage)}</p><button class="secondary" data-follow-action="review-update"${!state.preview ? ' hidden' : ''}>Review checked standings</button>
      <details class="separated-panel"><summary>Use a file or pasted results instead</summary><div class="field extra-top-space"><label for="follow-file">Results export</label><input type="file" id="follow-file" accept=".csv,.tsv,.json,.xlsx,.trf,.trf16,.txt,.html,.htm"${locked}><p class="field-help">The source is reviewed and merged into this event; it does not replace confirmed settings.</p></div><form id="follow-paste-form"><div class="field"><label for="follow-paste">Pasted standings or CSV</label><textarea id="follow-paste" name="text" rows="6" placeholder="Paste a supported results table or CSV export" required${locked}>${esc(state.pasteDraft)}</textarea></div><button class="secondary" type="submit"${locked}>Preview pasted results</button></form></details>
    </div></section>`;
  }

  function render() {
    const t = tournament();
    return `<div id="follow-workspace" aria-busy="${state.loading}"><div class="page-heading"><div><p class="eyebrow">YOUR TOURNAMENT</p><h1>Follow the next pairing</h1><p>Start with the event you’re joining. Add details and published results as they become known.</p></div><button class="secondary" data-follow-action="new"${busy() ? ' disabled' : ''}>Follow another event</button></div>
      ${offline() ? '<div class="notice source-notice"><strong>Saved view · connection required</strong><p>Reconnect to check published standings, save event details or calculate another forecast.</p></div>' : ''}
      ${state.error ? `<div class="notice danger source-notice" role="alert">${esc(state.error)}</div>` : ''}
      ${state.newOpen || (!t.tracking && !t.players?.length) ? `<section class="panel"><div class="panel-header"><h2>Start following an event</h2></div>${setupForm(true)}</section>` : `<section class="panel"><div class="panel-header"><div><h2>${esc(t.name || 'Set up your event')}</h2><p>You can save the event with the roster, opponent and rule details still unknown.</p></div></div>${setupForm(false)}</section>${focusForecast()}${readiness()}${sourceControls()}${preview()}`}
      <p class="field-help separated-panel">Historical sample replay remains available for testing. Your live tournament uses its own settings, roster and published results.</p></div>`;
  }

  function bind() {
    const root = document.querySelector('#follow-workspace');
    if (!root) return;
    root.addEventListener('input', event => {
      const target = event.target;
      if (target.id === 'follow-check-url') state.sourceDraft = target.value;
      if (target.id === 'follow-paste') state.pasteDraft = target.value;
      if (target.closest?.('#follow-settings')) state.draft = readForm(target.closest('#follow-settings'));
      if (target.closest?.('#follow-new')) state.newDraft = readForm(target.closest('#follow-new'));
    });
    root.addEventListener('change', event => {
      const target = event.target;
      if (busy()) return;
      if (target.closest?.('#follow-settings')) state.draft = readForm(target.closest('#follow-settings'));
      if (target.closest?.('#follow-new')) state.newDraft = readForm(target.closest('#follow-new'));
      if (target.id === 'follow-auto-check') { state.autoCheck = target.checked; syncTimer(); }
      if (target.id === 'follow-file') void upload(target.files?.[0]);
    });
    root.addEventListener('submit', event => {
      const form = event.target;
      event.preventDefault();
      if (busy() || !form.reportValidity()) return;
      if (form.id === 'follow-source-form') { state.sourceDraft = String(new FormData(form).get('url') || '').trim(); void checkSource(); return; }
      if (form.id === 'follow-paste-form') {
        state.pasteDraft = String(new FormData(form).get('text') || '');
        void operate(async () => { state.preview = await hooks.request('/api/follow/preview', { method: 'POST', body: { text: state.pasteDraft, filename: 'standings.csv', section: tournament().section || undefined } }); });
        return;
      }
      if (!form.dataset.followForm) return;
      const body = readForm(form), create = form.dataset.followForm === 'new';
      if (!create) state.draft = body;
      else state.newDraft = body;
      void operate(async () => {
        const data = await hooks.request(create ? '/api/follow/new' : '/api/follow/settings', { method: create ? 'POST' : 'PUT', body });
        state.draft = null; state.statusKey = null; state.newOpen = false; state.sourceDraft = '';
        if (create) { state.preview = null; state.autoCheck = false; state.newDraft = null; state.pasteDraft = ''; state.checkedAt = null; stopTimer(); }
        hooks.acceptState(data);
        hooks.toast?.(create ? 'Following this event. Add published standings when they appear.' : 'Known tournament details saved.');
      });
    });
    root.addEventListener('click', event => {
      const button = event.target.closest('[data-follow-action]');
      if (!button || button.disabled || busy()) return;
      const action = button.dataset.followAction;
      if (action === 'new') { state.newOpen = true; state.error = ''; redraw(); }
      if (action === 'cancel-new') { state.newOpen = false; redraw(); }
      if (action === 'review-update') redraw();
      if (action === 'discard') { state.preview = null; state.backgroundMessage = ''; redraw(); }
      if (action === 'apply' && state.preview) {
        const previewId = state.preview.previewId;
        void operate(async () => {
          const data = await hooks.request('/api/follow/apply', { method: 'POST', body: { previewId } });
          state.preview = null; state.backgroundMessage = ''; state.statusKey = null;
          hooks.acceptState(data);
          hooks.toast?.('Reviewed standings applied. Your event settings were preserved.');
        });
      }
    });
  }

  async function ensureLoaded() {
    if (app().view !== 'follow') { stopTimer(); return; }
    syncTimer();
    if (offline() || state.statusPending || app().followStatus) return;
    const current = key();
    if (state.statusKey === current) return;
    state.statusKey = current;
    state.statusPending = true;
    try { state.status = await hooks.request('/api/follow/status'); }
    catch (error) { state.error = error.message || 'The follow status could not be checked.'; }
    finally { state.statusPending = false; if (app().view === 'follow') redraw(); }
  }

  window.addEventListener?.('hashchange', () => { if (window.location?.hash !== '#follow') stopTimer(); });
  window.SavvyFollow = { configure(configuration) { hooks = configuration; }, render, bind, ensureLoaded, onViewChange(view) { if (view !== 'follow') stopTimer(); } };
})();
