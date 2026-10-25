// Parity judge: reference frames in the IR (RN runtime: Yoga on device; Figma: absoluteBoundingBox) vs the generated
// SwiftUI screen's frames (logged by Flex as "ORBIT_FRAMES <composition> [[x,y,w,h],...]"; the last line per
// composition wins). Both sides are compared relative to the screen root, so window/frame origins don't matter.
// CLI: node judge.mjs <ir.json> <Out.generated.map.json> <native-frames.log> [--tol 1]
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { normalize } from '../ir/ir.mjs';

export function judge(input, map, log, tol = 1) {
  const ir = normalize(input);
  const native = {};
  for (const line of log.split('\n')) {
    const m = /ORBIT_FRAMES (\d+) (\[.*\])\s*$/.exec(line);
    if (m) native[m[1]] = JSON.parse(m[2]);
  }
  // IR nodes in preorder with parents, so a nested composition is offset by its host (the ScrollView) frame.
  const nodes = [];
  (function walk(list, parent) { for (const n of list) { const i = nodes.push({ n, parent }) - 1; walk(n.children ?? [], i); } })(ir.roots, -1);
  const offset = {};
  nodes.forEach((x, i) => {
    const [c] = map[i];
    if (c !== 0 && offset[c] === undefined) {
      const [pc, pk] = map[x.parent], host = native[pc][pk];
      offset[c] = [host[0] + (offset[pc]?.[0] ?? 0), host[1] + (offset[pc]?.[1] ?? 0)];
    }
  });
  const abs = i => { const [c, k] = map[i], f = native[c]?.[k], o = offset[c] ?? [0, 0]; return f && [f[0] + o[0], f[1] + o[1], f[2], f[3]]; };
  const natOrigin = abs(0) ?? [0, 0], refOrigin = nodes[0]?.n.frame ?? [0, 0];
  const rows = nodes.map((x, i) => {
    const f = abs(i), r = x.n.frame;
    const got = f && [f[0] - natOrigin[0], f[1] - natOrigin[1], f[2], f[3]];
    const want = r && [r[0] - refOrigin[0], r[1] - refOrigin[1], r[2], r[3]];
    const d = got && want ? Math.max(...want.map((v, j) => Math.abs(v - got[j]))) : Infinity;
    return { i, label: x.n.text ? JSON.stringify(x.n.text.slice(0, 28)) : (x.n.name ?? x.n.type), d, got, want, ok: d <= tol };
  });
  const within = rows.filter(r => r.ok).length;
  const worst = rows.reduce((w, r) => (r.d > w.d ? r : w), rows[0] ?? { d: 0, i: 0 });
  return { rows, within, total: rows.length, worst, pass: within === rows.length };
}

export function report(r, tol) {
  return r.rows.map(x => `${String(x.i).padStart(3)} ${x.ok ? 'ok ' : 'BAD'} ${x.d.toFixed(2).padStart(6)}  ${x.label}`).join('\n')
    + `\n\nPARITY ${r.within}/${r.total} nodes within ${tol}pt; worst ${r.worst.d.toFixed(2)}pt (node ${r.worst.i})`;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [irPath, mapPath, logPath] = process.argv.slice(2);
  const ti = process.argv.indexOf('--tol'), tol = ti > 0 ? +process.argv[ti + 1] : 1;
  const r = judge(JSON.parse(readFileSync(irPath, 'utf8')), JSON.parse(readFileSync(mapPath, 'utf8')).map, readFileSync(logPath, 'utf8'), tol);
  console.log(report(r, tol));
  process.exitCode = r.pass ? 0 : 1;
}
