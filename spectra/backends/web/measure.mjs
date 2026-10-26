// Lays a rendered screen out in headless Chrome and judges its boxes with verify/judge.mjs (the native back-ends' judge),
// by handing them over in the ORBIT_FRAMES format as one composition. No dependencies: Chrome's --dump-dom runs the
// page's probe script and prints the DOM, boxes included.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { judge } from '../../verify/judge.mjs';
import { render } from './render.mjs';

export function chrome() {
  return [process.env.CHROME, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium'].find(p => p && existsSync(p));
}

// measure(html) -> [[x, y, w, h], ...] in data-i order (CSS px = points at scale 1). html must be rendered with probe.
export function measure(html) {
  const bin = chrome();
  if (!bin) throw new Error('Chrome unavailable: install Google Chrome or set CHROME=<binary>');
  const dir = mkdtempSync(join(tmpdir(), 'spectra-web-'));
  try {
    const page = join(dir, 'screen.html');
    writeFileSync(page, html);
    // Chrome on macOS sometimes exits 2 at shutdown after a complete dump, so the boxes decide, not the status.
    const r = spawnSync(bin, ['--headless=new', '--disable-gpu', '--no-first-run', `--user-data-dir=${join(dir, 'profile')}`,
      '--force-device-scale-factor=1', '--hide-scrollbars', '--dump-dom', `file://${page}`],
    { encoding: 'utf8', timeout: 60_000, stdio: ['ignore', 'pipe', 'pipe'] });
    const dom = r.stdout ?? '', tag = '<pre id="spectra-boxes">', a = dom.lastIndexOf(tag), b = dom.lastIndexOf('</pre>');
    if (a < 0 || b < a) {
      const why = r.error?.message ?? `exit ${r.status}: ${(r.stderr ?? '').trim().split('\n').slice(-3).join(' | ')}`;
      throw new Error(`no boxes from Chrome (${why})`);
    }
    return JSON.parse(dom.slice(a + tag.length, b));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

// judgeWeb(ir, opts, tol) -> judge()'s result: every node's browser box vs its reference frame, root-relative.
export function judgeWeb(ir, opts = {}, tol = 1) {
  const r = render(ir, { ...opts, probe: true });
  const boxes = measure(r.html);
  const map = boxes.map((_, i) => [0, i]);
  return judge(r.ir, map, `ORBIT_FRAMES 0 ${JSON.stringify(boxes)}`, tol);
}
