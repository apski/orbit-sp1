// orbitfleet browser UI. The fleet arrives as server-sent events every 2 s (/api/events); an open drawer polls its
// run's log, app output and timeline. Text from the API is set with textContent, never parsed as HTML: device logs
// and labels are untrusted. Deep links: #sim=<udid>&tab=log|app|timeline|crash, #run=<id>, #runs.
'use strict';

const W = { 'X-Orbitfleet': '1', 'Content-Type': 'application/json' };
const TABS = [['timeline', 'Timeline'], ['log', 'Device log'], ['app', 'App output'], ['crash', 'Crashes']];
const $ = (s) => document.querySelector(s);
let state = null; // last /api/events push
let view = parseHash();
let drawerKey = ''; // what the drawer was built for; rebuilt when it changes
let poll = null; // the open tab's refresh timer
let log = { run: '', next: -1, lines: [], done: false };
let history = null; // the run a #run= link shows
let runsFilter = 'all';
const thumbs = new Map(); // udid -> {img, bucket, loading}: the shown <img>, swapped only once the next one loaded

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'class') e.className = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k instanceof Node ? k : String(k));
  return e;
}

function parseHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  return { page: p.has('runs') ? 'runs' : 'fleet', sim: p.get('sim') || '', run: p.get('run') || '', tab: p.get('tab') || 'log' };
}
const go = (p) => { location.hash = new URLSearchParams(p).toString(); };

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: method === 'GET' ? {} : W, body: body ? JSON.stringify(body) : undefined });
  const data = (r.headers.get('content-type') || '').includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data && data.error ? `${data.error.code}: ${data.error.message}` : String(data).trim());
  return data;
}

const gb = (b) => (b / 2 ** 30).toFixed(2) + ' GB';
const clock = (t) => new Date(t).toLocaleTimeString([], { hour12: false });
const fileURL = (run, p) => `/api/runs/${encodeURIComponent(run.id)}/file?path=${encodeURIComponent(p)}`;
const base = (p) => p.split('/').pop();
function dur(a, b) {
  const s = Math.max(0, Math.round(((b ? Date.parse(b) : Date.now()) - Date.parse(a)) / 1000));
  return s < 3600 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function status(s) {
  if (!s.lease) return ['free', s.run && s.run.status !== 'running' ? `free · last ${s.run.status}` : 'free'];
  if (!s.lease.ready) return ['booting', 'booting'];
  if (s.run && s.run.status === 'running') return ['running', 'running'];
  if (s.run) return [s.run.status, `${s.run.status} · idle`];
  return ['leased', 'leased'];
}

function render() {
  if (!state) return;
  renderStats();
  const runsPage = view.page === 'runs' && !view.run;
  $('#fleet').hidden = runsPage;
  $('#runs').hidden = !runsPage;
  document.querySelectorAll('.nav a').forEach((a) => a.toggleAttribute('aria-current', (a.getAttribute('href') === '#runs') === runsPage));
  if (runsPage) {
    if (drawerKey !== 'runs') { drawerKey = 'runs'; stopPoll(); renderRuns(); }
    return;
  }
  if (drawerKey === 'runs') drawerKey = '';
  renderGrid();
  renderDrawer();
}

function renderStats() {
  const s = state, leased = s.sims.filter((x) => x.lease).length;
  const chip = (id, ...kids) => h('span', { class: 'chip', 'data-testid': id }, ...kids);
  const chips = [
    chip('stat-booted', h('b', {}, s.booted), ` of ${s.maxBooted} booted`),
    chip('stat-ram', h('b', {}, s.fleetBytes ? gb(s.fleetBytes) : 'measuring…'), ' simulator RAM'),
    chip('stat-leased', h('b', {}, leased), ' leased · ', h('b', {}, s.sims.length - leased), ' free'),
  ];
  if (s.waiting) chips.push(h('span', { class: 'chip warn', 'data-testid': 'stat-waiting' }, h('b', {}, s.waiting), ' claims waiting'));
  $('#stats').replaceChildren(...chips);
}

function renderGrid() {
  const cards = state.sims.map(card);
  $('#grid').replaceChildren(...(cards.length ? cards : [h('p', { class: 'empty' },
    'No pool simulators. Create some named fleet-01, fleet-02, … (simslim clone, or xcrun simctl create).')]));
}

function card(s) {
  const [cls, label] = status(s), run = s.run;
  let line = 'Ready for a claim';
  if (s.lease && !s.lease.ready) line = 'Booting and slimming…';
  else if (run) line = `${run.label || run.id} · ${run.status === 'running' ? dur(run.started) : run.status}`;
  else if (s.lease) line = s.lease.purpose || 'Leased, no run yet';
  return h('button', { class: 'card', type: 'button', 'data-testid': `sim-${s.udid}`, 'aria-pressed': String(view.sim === s.udid),
    onclick: () => go({ sim: s.udid, tab: view.tab }) },
  h('span', { class: 'row' }, h('span', { class: 'name' }, s.name), h('span', { class: `pill ${cls}`, 'data-testid': `status-${s.name}` }, label)),
  h('span', { class: 'holder', 'data-testid': `holder-${s.name}` }, s.lease ? `held by ${s.lease.holder}` : 'no lease'),
  phone(s),
  h('span', { class: 'runline' }, line),
  h('span', { class: 'meta' }, h('span', {}, s.stat ? gb(s.stat.bytes) : '–'),
    h('span', {}, s.stat ? `slim ${s.stat.managedDisabled}/${s.stat.managedTotal}` : s.state), h('span', {}, s.os)));
}

// phone shows the latest screenshot. A new one loads off-screen and replaces the old only when it has arrived:
// setting src on the shown <img> would cancel a slow load (a busy sim can take over 3 s), so it never showed.
function phone(s) {
  if (s.state !== 'Booted') return h('span', { class: 'phone' }, s.state);
  let t = thumbs.get(s.udid);
  if (!t) { t = { img: h('img', { alt: `Screen of ${s.name}` }), bucket: '', loading: false }; thumbs.set(s.udid, t); }
  const bucket = String(Math.floor(Date.now() / 3000));
  if (t.bucket !== bucket && !t.loading) {
    t.bucket = bucket;
    t.loading = true;
    const next = h('img', { alt: `Screen of ${s.name}` });
    next.onload = () => { t.img.replaceWith(next); t.img = next; t.loading = false; };
    next.onerror = () => { t.loading = false; };
    next.src = `/api/sims/${encodeURIComponent(s.udid)}/thumb?b=${bucket}`;
  }
  return h('span', { class: 'phone' }, t.img);
}

function renderDrawer() {
  const d = $('#drawer');
  let sim = null, run = null;
  if (view.sim) {
    sim = state.sims.find((x) => x.udid === view.sim) || null;
    run = sim && sim.run;
  } else if (view.run) {
    if (!history || history.id !== view.run) {
      history = { id: view.run, loading: true };
      api('GET', `/api/runs/${encodeURIComponent(view.run)}`)
        .then((r) => { history = r; render(); })
        .catch((e) => { history = { id: view.run, error: e.message }; render(); });
    }
    run = history.loading || history.error ? null : history;
    const live = run && state.sims.find((x) => x.run && x.run.id === run.id && x.run.status === 'running');
    if (live) { sim = live; run = live.run; }
  } else {
    d.hidden = true; drawerKey = ''; stopPoll();
    return;
  }
  const key = [view.sim, view.run, run && run.id, run && run.status, run && run.pid, run && run.recording, view.tab, !!(sim && sim.lease)].join('|');
  if (key !== drawerKey) { drawerKey = key; buildDrawer(sim, run); return; }
  const lease = $('#lease-line');
  if (lease) lease.textContent = leaseText(sim);
  if (run) $('#runbox').replaceWith(runBox(run));
}

function leaseText(sim) {
  if (!sim) return 'Run history';
  if (!sim.lease) return 'Free · not leased';
  const mins = Math.max(0, Math.round((Date.parse(sim.lease.expires) - Date.now()) / 60000));
  return `Leased to ${sim.lease.holder} · expires in ${mins} min${sim.lease.warning ? ` · ${sim.lease.warning}` : ''}`;
}

function buildDrawer(sim, run) {
  stopPoll();
  const d = $('#drawer');
  d.hidden = false;
  const title = sim ? sim.name : run ? run.udid : history && history.error ? history.error : 'Loading…';
  d.replaceChildren(
    h('div', { class: 'row' },
      h('div', {}, h('h2', {}, title), h('div', { class: 'mono dim' }, sim ? sim.udid : ''), h('div', { id: 'lease-line', class: 'dim' }, leaseText(sim))),
      leaseButtons(sim)),
    runBox(run),
    h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Run output' }, ...TABS.map(([id, label]) => h('button', {
      class: 'tab', type: 'button', role: 'tab', 'aria-selected': String(view.tab === id), 'data-testid': `tab-${id}`,
      onclick: () => go(view.sim ? { sim: view.sim, tab: id } : { run: view.run, tab: id }) }, label))),
    h('div', { id: 'tabbody' }),
    ...debugButtons(sim, run),
    h('span', { id: 'toast', class: 'toast', role: 'status', 'data-testid': 'toast', hidden: true }));
  loadTab(run);
}

function runBox(run) {
  if (!run) return h('div', { class: 'runbox', id: 'runbox' }, h('b', {}, 'No run yet'),
    h('span', { class: 'dim' }, 'Agents record one with orbitfleet run start <udid>.'));
  const meta = [`started ${clock(run.started)}`, dur(run.started, run.ended), run.app, run.pid ? `pid ${run.pid}` : '', run.reason];
  return h('div', { class: 'runbox', id: 'runbox' },
    h('span', { class: 'row' }, h('b', {}, `${run.label || 'run'} (${run.id})`), h('span', { class: `pill ${run.status}`, 'data-testid': 'run-status' }, run.status)),
    h('span', { class: 'mono dim' }, meta.filter(Boolean).join(' · ')),
    ...(run.warnings || []).map((w) => h('span', { class: 'dim' }, `warning: ${w}`)));
}

function leaseButtons(sim) {
  if (!sim || !sim.lease) return null;
  let armed = false;
  const force = h('button', { class: 'btn danger', type: 'button', 'data-testid': 'lease-force', onclick: () => {
    if (!armed) {
      armed = true; force.textContent = 'Click again to release';
      setTimeout(() => { armed = false; force.textContent = 'Force release'; }, 4000);
      return;
    }
    act(() => api('POST', '/api/release', { udid: sim.udid, holder: 'person', force: true }),
      `Released ${sim.name}. ${sim.lease.holder} gets not-leased on its next call.`);
  } }, 'Force release');
  return h('div', { class: 'btns' }, h('button', { class: 'btn', type: 'button', 'data-testid': 'lease-extend',
    onclick: () => act(() => api('POST', '/api/renew', { udid: sim.udid }), 'Lease extended') }, 'Extend lease'), force);
}

function debugButtons(sim, run) {
  const b = (id, label, hint, fn) => h('button', { class: 'btn', type: 'button', 'data-testid': `debug-${id}`, onclick: fn }, label, h('small', {}, hint));
  const list = [];
  if (run && run.status === 'running') {
    const r = `/api/runs/${encodeURIComponent(run.id)}`;
    if (run.pid) {
      list.push(b('lldb', 'Attach LLDB', `xcrun lldb -p ${run.pid}`,
        () => act(async () => `Opened Terminal: ${(await api('POST', `${r}/debug/lldb`, { open: true })).command}`)));
    }
    if (run.app) {
      list.push(b('wait', 'Relaunch, wait for debugger', 'simctl launch -w', () => act(async () =>
        `Relaunched suspended as pid ${(await api('POST', `${r}/launch`, { waitForDebugger: true })).pid}. Attach LLDB, then continue.`)));
    }
    if (run.pid) {
      list.push(b('profile', 'Profile 30 s', 'xctrace · Time Profiler', () => act(async () => {
        const a = await api('POST', `${r}/debug/profile`, { limit: 30e9 });
        return `Saved ${base(a.path)}${a.note ? ` · ${a.note}` : ''}`;
      }, '', 'Recording 30 s with Instruments…')));
    }
    list.push(b('video', run.recording ? 'Stop video' : 'Record video', run.recording ? 'saves it to the run' : 'HEVC screen recording',
      () => act(async () => {
        const a = await api('POST', `${r}/debug/video`, { action: run.recording ? 'stop' : 'start' });
        return run.recording ? `Saved ${base(a.path)}` : 'Recording the screen…';
      })));
    list.push(b('shot', 'Screenshot', 'saved to the run as AVIF',
      () => act(async () => `Saved ${base((await api('POST', `${r}/shot`, { name: '' })).path)}`)));
  }
  if (sim && sim.state === 'Booted') {
    list.push(b('simulator', 'Open in Simulator.app', 'focus this device', () => act(() => api('POST', `/api/sims/${sim.udid}/open`), `Opened ${sim.name}`)));
  }
  if (run) list.push(b('folder', 'Reveal run folder', run.dir, () => act(() => api('POST', `/api/runs/${run.id}/reveal`), 'Revealed in Finder')));
  return list.length ? [h('h3', {}, 'Debug'), h('div', { class: 'debug' }, ...list)] : [];
}

async function act(fn, ok, pending) {
  const t = $('#toast');
  if (pending) { t.textContent = pending; t.hidden = false; }
  try {
    const msg = await fn();
    t.textContent = typeof msg === 'string' ? msg : ok;
  } catch (e) {
    t.textContent = e.message;
  }
  t.hidden = false;
}

function stopPoll() { clearInterval(poll); poll = null; }

function loadTab(run) {
  const body = $('#tabbody');
  if (!run) { body.replaceChildren(); return; }
  const fn = { log: tabLog, app: tabApp, timeline: tabTimeline, crash: tabCrash }[view.tab] || tabLog;
  const tick = (first) => fn(liveRun(run), body, first).catch((e) => body.append(h('p', { class: 'dim' }, e.message)));
  tick(true);
  if (run.status === 'running') poll = setInterval(() => tick(false), view.tab === 'log' ? 1000 : 2000);
}

// liveRun is the latest copy of a run from the state push (new artifacts, status), or the one given.
function liveRun(run) {
  const s = state.sims.find((x) => x.run && x.run.id === run.id);
  return s ? s.run : run;
}

async function tabLog(run, body, first) {
  if (first) {
    log = { run: run.id, next: -1, lines: [], done: false };
    body.replaceChildren(
      h('div', { class: 'filters' },
        h('label', {}, 'Filter', h('input', { type: 'search', id: 'logq', 'data-testid': 'log-filter', placeholder: 'text, process or subsystem', oninput: drawLog })),
        h('label', {}, 'Level', h('select', { id: 'logl', 'data-testid': 'log-level', onchange: drawLog },
          h('option', { value: 'all' }, 'all'), h('option', { value: 'info' }, 'info and up'), h('option', { value: 'error' }, 'errors'))),
        h('span', { id: 'logn', class: 'dim' })),
      h('pre', { class: 'code', id: 'logbox', 'data-testid': 'log-tail' }));
  }
  if (log.done) return;
  const t = await api('GET', `/api/runs/${encodeURIComponent(run.id)}/tail?from=${log.next}`);
  if (log.run !== run.id) return; // the drawer moved on meanwhile
  log.next = t.next;
  log.done = t.done;
  for (const l of t.lines) { try { log.lines.push(JSON.parse(l)); } catch { /* log stream's header line */ } }
  if (log.lines.length > 3000) log.lines = log.lines.slice(-3000);
  drawLog();
  if (t.done) stopPoll();
}

// drawLog redraws the log box from the buffer. ponytail: rebuilds up to 1500 rows a second; append-only if it lags.
function drawLog() {
  const box = $('#logbox');
  if (!box) return;
  const q = $('#logq').value.toLowerCase(), lvl = $('#logl').value;
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
  const rows = [];
  for (const e of log.lines) {
    const type = e.messageType || '';
    if ((lvl === 'info' && type === 'Debug') || (lvl === 'error' && type !== 'Error' && type !== 'Fault')) continue;
    const proc = base(e.processImagePath || '');
    if (q && !`${proc} ${e.subsystem || ''} ${e.category || ''} ${e.eventMessage || ''}`.toLowerCase().includes(q)) continue;
    const cls = type === 'Error' || type === 'Fault' ? 'err' : type === 'Debug' ? 'dbg' : '';
    rows.push(h('div', { class: cls, 'data-testid': 'log-line' }, h('span', { class: 't' }, (e.timestamp || '').slice(11, 23)),
      ` ${type.padEnd(7)} `, h('span', { class: 'p' }, proc), `: ${e.eventMessage || ''}`));
  }
  box.replaceChildren(...rows.slice(-1500));
  $('#logn').textContent = `${rows.length} of ${log.lines.length} lines${log.done ? '' : ' · live'}`;
  if (atBottom) box.scrollTop = box.scrollHeight;
}

async function tabApp(run, body) {
  const arts = run.artifacts.filter((a) => a.kind === 'app-out' || a.kind === 'app-err').slice(-2).reverse(); // last launch, stderr first
  if (!arts.length) {
    body.replaceChildren(h('p', { class: 'empty' }, 'No app launched with orbitfleet run launch in this run.'));
    return;
  }
  const texts = await Promise.all(arts.map((a) => api('GET', fileURL(run, a.path)).catch((e) => e.message)));
  body.replaceChildren(...arts.flatMap((a, i) => [h('h3', {}, a.kind === 'app-err' ? 'stderr (NSLog, os_log)' : 'stdout'),
    h('pre', { class: 'code', 'data-testid': a.kind }, texts[i] || '(empty)')]));
}

async function tabTimeline(run, body) {
  const evs = await api('GET', `/api/runs/${encodeURIComponent(run.id)}/events`);
  const shots = run.artifacts.filter((a) => a.kind === 'shot');
  const files = run.artifacts.filter((a) => a.kind === 'trace' || a.kind === 'video');
  body.replaceChildren(...[
    h('ol', { class: 'timeline', 'data-testid': 'timeline' }, ...evs.map((e) => h('li', {},
      h('span', { class: 'mono dim' }, clock(e.t)), h('span', { class: `kind ${e.kind}` }, e.kind), h('span', {}, e.text)))),
    shots.length ? h('div', { class: 'shots' }, ...shots.map((a) => h('a', { href: fileURL(run, a.path), target: '_blank' },
      h('img', { src: fileURL(run, a.path), alt: base(a.path) })))) : null,
    files.length ? h('div', { class: 'btns' }, ...files.map((a) => h('button', { class: 'btn', type: 'button', 'data-testid': `open-${base(a.path)}`,
      onclick: () => act(() => api('POST', `/api/runs/${encodeURIComponent(run.id)}/open`, { path: a.path }), `Opened ${base(a.path)}`) },
    `Open ${base(a.path)}`, h('small', {}, a.note || (a.kind === 'trace' ? 'in Instruments' : 'in QuickTime'))))) : null].filter(Boolean));
}

async function tabCrash(run, body) {
  const cs = run.artifacts.filter((a) => a.kind === 'crash');
  body.replaceChildren(cs.length
    ? h('ul', { class: 'timeline', 'data-testid': 'crashes' }, ...cs.map((a) => h('li', {}, h('a', { href: fileURL(run, a.path), target: '_blank', class: 'mono' }, base(a.path)))))
    : h('p', { class: 'empty', 'data-testid': 'crashes' }, run.status === 'running' ? 'Crash reports are collected when the run stops.' : 'No crash reports in this run window.'));
}

async function renderRuns() {
  const page = $('#runs');
  let list;
  try { list = await api('GET', '/api/runs'); } catch (e) { page.replaceChildren(h('p', {}, e.message)); return; }
  const names = new Map(state.sims.map((s) => [s.udid, s.name]));
  const count = (r, k) => r.artifacts.filter((a) => a.kind === k).length;
  const draw = () => {
    const q = $('#runq').value.toLowerCase();
    const rows = list.filter((r) => (runsFilter === 'all' || r.status === runsFilter) && (!q || `${r.id} ${r.label || ''} ${r.holder}`.toLowerCase().includes(q)));
    $('#runrows').replaceChildren(...rows.map((r) => h('tr', { class: 'click', 'data-testid': `run-${r.id}`, onclick: () => go({ run: r.id, tab: 'timeline' }) },
      h('td', {}, h('span', { class: `pill ${r.status}` }, r.status)),
      h('td', {}, h('b', {}, r.label || '(no label)'), h('div', { class: 'mono dim' }, r.id + (r.reason ? ` · ${r.reason}` : ''))),
      h('td', {}, r.holder), h('td', { class: 'mono' }, names.get(r.udid) || r.udid.slice(0, 8)),
      h('td', { class: 'mono' }, new Date(r.started).toLocaleString()), h('td', { class: 'mono' }, dur(r.started, r.ended)),
      h('td', {}, [`${count(r, 'shot')} shots`, count(r, 'crash') ? `${count(r, 'crash')} crash` : ''].filter(Boolean).join(' · ')))));
  };
  page.replaceChildren(
    h('div', { class: 'filters' },
      h('label', {}, 'Search runs', h('input', { type: 'search', id: 'runq', 'data-testid': 'runs-search', placeholder: 'label, holder or run id', oninput: draw })),
      h('div', { class: 'btns', role: 'group', 'aria-label': 'Status' }, ...['all', 'running', 'failed', 'passed', 'aborted'].map((s) =>
        h('button', { class: 'btn', type: 'button', 'aria-pressed': String(s === runsFilter), 'data-testid': `filter-${s}`, onclick: () => { runsFilter = s; renderRuns(); } }, s)))),
    h('div', { class: 'tablebox' }, h('table', { 'data-testid': 'runs-table' },
      h('thead', {}, h('tr', {}, ...['Status', 'Run', 'Holder', 'Simulator', 'Started', 'Took', 'Artifacts'].map((c) => h('th', { scope: 'col' }, c)))),
      h('tbody', { id: 'runrows' }))));
  draw();
}

window.addEventListener('hashchange', () => { view = parseHash(); render(); });
const es = new EventSource('/api/events');
es.onopen = () => { $('#conn').textContent = 'live'; };
es.onmessage = (ev) => { state = JSON.parse(ev.data); render(); };
es.onerror = async () => {
  const r = await fetch('/api/state').catch(() => null);
  $('#conn').textContent = r && r.status === 401 ? 'not signed in: run orbitfleet ui' : 'reconnecting…';
};
