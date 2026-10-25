// Runs capture.js inside a running RN app over Metro's CDP inspector and returns Spectra IR. No agent needed.
// CLI: node run.mjs <ScreenComponentName> <out.json> [--port 8081] [--device "<device name substring>"]
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export async function captureRuntime({ screen, port = 8081, device = '' }) {
  const targets = await (await fetch(`http://localhost:${port}/json/list`)).json();
  const target = targets.find(t => t.webSocketDebuggerUrl && (t.deviceName ?? t.title ?? '').includes(device)
    && /React Native|Hermes/.test(t.title + (t.description ?? '')));
  if (!target) throw new Error(`no RN target on Metro :${port}: ${JSON.stringify(targets.map(t => t.title))}`);
  // Newer Metro refuses debugger sockets without a localhost Origin.
  const ws = new WebSocket(target.webSocketDebuggerUrl, { headers: { Origin: `http://localhost:${port}` } });
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); pending.get(m.id)?.(m); };
  const call = (method, params) => new Promise(res => { pending.set(++id, res); ws.send(JSON.stringify({ id, method, params })); });
  const src = readFileSync(new URL('capture.js', import.meta.url), 'utf8');
  const r = await call('Runtime.evaluate', { expression: `${src}\n__orbitCapture(${JSON.stringify(screen)})`, returnByValue: true });
  ws.close();
  const value = r.result?.result?.value;
  if (!value) throw new Error(`capture failed: ${JSON.stringify(r.result ?? r.error).slice(0, 500)}`);
  const ir = JSON.parse(value);
  if (ir.error) throw new Error(ir.error);
  return ir;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [screen, out] = process.argv.slice(2);
  const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
  const ir = await captureRuntime({ screen, port: arg('port', '8081'), device: arg('device', '') });
  writeFileSync(out, JSON.stringify(ir, null, 1));
  console.log(`${out}: ${screen}`);
}
