// simwalk driver: barebones iOS-simulator driver. Talks straight to argent's two
// native binaries (simulator-server = SimulatorKit/Indigo HID over WebSocket,
// ax-service = in-sim AXRuntime over unix-socket NDJSON). No MCP, no tool-server,
// no screenshots in the loop, no npm deps.
// ponytail: borrows argent's installed binaries; swap for our own Swift HID/AX helpers if they ever go away.
import { spawn, execFileSync } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';

const ARGENT = path.resolve(path.dirname(fs.realpathSync(execFileSync('which', ['argent']).toString().trim())), '..');
const BIN = `${ARGENT}/bin/darwin`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function connect(udid) {
  // AX prefs: argent writes these pre-boot; AutomationEnabled also works post-boot.
  execFileSync('xcrun', ['simctl', 'spawn', udid, 'defaults', 'write', 'com.apple.Accessibility', 'AutomationEnabled', '-bool', 'true']);

  // --- HID: own simulator-server instance
  const sim = spawn(`${BIN}/simulator-server`, ['ios', '--id', udid], { stdio: ['pipe', 'pipe', 'ignore'] });
  const apiUrl = await new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('simulator-server not ready')), 30000);
    readline.createInterface({ input: sim.stdout }).on('line', (l) => {
      const m = l.match(/^api_ready .*?(http:\/\/\S+)/);
      if (m) { clearTimeout(t); res(m[1]); }
    });
  });
  const ws = new WebSocket(`ws://${new URL(apiUrl).host}/ws`);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const pendingWs = new Map(); let wsId = 0;
  ws.onmessage = (e) => { const a = JSON.parse(e.data); pendingWs.get(a.id)?.(a); pendingWs.delete(a.id); };
  const cmd = (c) => new Promise((res, rej) => {
    const id = String(++wsId);
    pendingWs.set(id, (a) => (a.status === 'ok' ? res() : rej(new Error(a.error ?? 'sim cmd failed'))));
    ws.send(JSON.stringify({ id, ...c }));
  });

  // --- AX: own ax-service instance inside the sim; we listen, it dials in
  const sock = `/tmp/sd-${udid.slice(0, 8)}.sock`;
  try { fs.unlinkSync(sock); } catch {}
  let axSock, ax, buf = '';
  const pendingAx = new Map(); let axId = 0;
  const server = net.createServer((s) => {
    axSock = s; buf = '';
    s.on('close', () => { if (axSock === s) axSock = undefined; for (const [id, f] of pendingAx) f({ result: { error: 'ax-service died' } }); pendingAx.clear(); });
    s.on('data', (d) => {
      buf += d; let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1);
        if (!line) continue; const m = JSON.parse(line); pendingAx.get(m.id)?.(m); pendingAx.delete(m.id);
      }
    });
  });
  await new Promise((r) => server.listen(sock, r));
  // argent 0.25's ax-service aborts on NaN frames mid-transition; respawn on demand.
  const spawnAx = async () => {
    axSock = undefined;
    ax = spawn('xcrun', ['simctl', 'spawn', udid, `${BIN}/ax-service`, '--socket', sock, '--timeout', '3600'], { stdio: 'ignore' });
    ax.on('exit', () => { axSock = undefined; });
    for (let i = 0; !axSock && i < 200; i++) await sleep(25);
    if (!axSock) throw new Error('ax-service did not connect');
  };
  await spawnAx();
  const axq1 = (command, timeoutMs) => new Promise((res, rej) => {
    const id = String(++axId);
    const t = setTimeout(() => { pendingAx.delete(id); rej(new Error(`ax-service ${command} timed out`)); }, timeoutMs);
    pendingAx.set(id, (m) => { clearTimeout(t); res(m); });
    axSock.write(JSON.stringify({ id, command }) + '\n');
  });
  let respawns = 0;
  const axq = async (command, timeoutMs = 2000) => {
    try { if (!axSock) throw 0; const m = await axq1(command, timeoutMs); if (m.result?.error === 'ax-service died') throw 0; return m; }
    catch { respawns++; ax?.kill(); await spawnAx(); return axq1(command, timeoutMs); }
  };

  const describe = async () => {
    const r = await axq('describe');
    const d = r.result ?? r;
    if (d.error) throw new Error(d.error);
    return d;
  };

  // selector: string (label/id substring, case-insensitive) | { id } (exact identifier) | { label } (exact label)
  const match = (el, sel) => {
    if (typeof sel === 'string') sel = { text: sel };
    const lc = (s) => (s ?? '').toLowerCase();
    if (sel.id && el.identifier !== sel.id) return false;
    if (sel.label && el.label !== sel.label) return false;
    if (sel.text && !lc(el.label).includes(lc(sel.text)) && !lc(el.identifier).includes(lc(sel.text))) return false;
    return true;
  };
  const find = async (sel) => {
    const d = await describe();
    const el = [...d.elements].find((e) => match(e, sel));
    return el && { el, d };
  };
  // Tight poll on the a11y tree instead of a fixed settle sleep.
  const waitFor = async (sel, timeoutMs = 5000) => {
    const t0 = performance.now();
    while (performance.now() - t0 < timeoutMs) {
      const r = await find(sel);
      if (r) return r;
      await sleep(15);
    }
    throw new Error(`timeout waiting for ${JSON.stringify(sel)}`);
  };
  const tapXY = async (x, y, holdMs = 50) => {
    await cmd({ cmd: 'touch', type: 'Down', x, y, second_x: null, second_y: null });
    await sleep(holdMs);
    await cmd({ cmd: 'touch', type: 'Up', x, y, second_x: null, second_y: null });
  };
  // ax-service already returns normalized [0,1] frames/tapPoints.
  const center = ({ el }) => {
    const p = el.tapPoint ?? { x: el.frame.x + el.frame.width / 2, y: el.frame.y + el.frame.height / 2 };
    return [p.x, p.y];
  };
  // During push/pop animations both screens are in the tree and frames slide:
  // only tap once the target's frame is identical on two consecutive reads.
  // UIKit can still swallow the touch (scroll view settling, transition
  // completion pending), so confirm via `until` (expected next element) or,
  // failing that, the target moving/vanishing; re-tap if neither happens.
  const tap = async (sel, { until, timeoutMs = 5000 } = {}) => {
    const t0 = performance.now(); let prev;
    while (performance.now() - t0 < timeoutMs) {
      const r = await find(sel);
      const f = r && JSON.stringify(r.el.frame);
      if (f && f === prev) {
        await tapXY(...center(r));
        for (const t1 = performance.now(); performance.now() - t1 < 700;) {
          if (until ? await find(until) : JSON.stringify((await find(sel))?.el.frame) !== f) return;
        }
        prev = undefined; continue;
      }
      prev = f;
    }
    throw new Error(`tap ${JSON.stringify(sel)} never took effect`);
  };
  const swipe = async (x1, y1, x2, y2, ms = 200, steps = 10) => {
    await cmd({ cmd: 'touch', type: 'Down', x: x1, y: y1, second_x: null, second_y: null });
    for (let i = 1; i <= steps; i++) { await sleep(ms / steps); await cmd({ cmd: 'touch', type: 'Move', x: x1 + ((x2 - x1) * i) / steps, y: y1 + ((y2 - y1) * i) / steps, second_x: null, second_y: null }); }
    await cmd({ cmd: 'touch', type: 'Up', x: x2, y: y2, second_x: null, second_y: null });
  };
  const launch = (bundleId) => execFileSync('xcrun', ['simctl', 'launch', '--terminate-running-process', udid, bundleId]);
  // simctl reads the live framebuffer; only used on failure, never in the step loop.
  const screenshot = (file) => execFileSync('xcrun', ['simctl', 'io', udid, 'screenshot', file], { stdio: 'ignore' });
  // Compact `id | label | value` list: what a human or agent needs to fix a broken step (~100-200 tokens).
  const outline = async () => (await describe()).elements
    .filter((e) => e.identifier || e.traits?.length)
    .map((e) => [e.identifier ?? '-', e.label ?? '', e.value ?? ''].join(' | ').trimEnd().replace(/ \|$/, ''))
    .filter((l, i, a) => a.indexOf(l) === i).join('\n');
  const close = () => { ws.close(); axSock?.destroy(); server.close(); sim.kill(); ax?.kill(); };

  return { udid, get respawns() { return respawns; }, describe, outline, find, waitFor, tap, tapXY, swipe, launch, screenshot, close, cmd };
}
