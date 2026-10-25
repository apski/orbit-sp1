// Figma REST client for Spectra. The CLI pulls once into a local zstd cache and everything downstream (inspect, lower,
// gen, judge) reads the cache: agents never see raw Figma JSON, and repeat runs cost no API calls.
// Token: FIGMA_TOKEN (or FIGMA_ACCESS_TOKEN) env var, or a `FIGMA_TOKEN=...` line in .spectra/secrets.env or
// ~/.spectra/secrets.env. Never pass it on the command line or paste it into chat.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import * as zlib from 'node:zlib';

const API = 'https://api.figma.com/v1';

/** figma.com/design|file|proto/<key>[/branch/<branchKey>]/<name>?node-id=1-23 -> { fileKey, nodeId: '1:23' } */
export function parseUrl(url) {
  const u = new URL(url);
  const m = /^\/(?:design|file|proto|board)\/([A-Za-z0-9]+)(?:\/branch\/([A-Za-z0-9]+))?/.exec(u.pathname);
  if (!m) throw new Error(`not a Figma file URL: ${url}`);
  const id = u.searchParams.get('node-id');
  return { fileKey: m[2] ?? m[1], nodeId: id ? id.replace(/-/g, ':') : null };
}

export function token(repo = process.cwd()) {
  if (process.env.FIGMA_TOKEN || process.env.FIGMA_ACCESS_TOKEN) return process.env.FIGMA_TOKEN || process.env.FIGMA_ACCESS_TOKEN;
  for (const f of [join(repo, '.spectra', 'secrets.env'), join(homedir(), '.spectra', 'secrets.env')]) {
    if (!existsSync(f)) continue;
    const m = /^\s*FIGMA_(?:ACCESS_)?TOKEN\s*=\s*(.+?)\s*$/m.exec(readFileSync(f, 'utf8'));
    if (m) return m[1].replace(/^["']|["']$/g, '');
  }
  return null;
}

async function get(path, tok, tries = 4) {
  for (let i = 0; ; i++) {
    const r = await fetch(`${API}${path}`, { headers: { 'X-Figma-Token': tok } });
    if (r.status === 429 && i < tries) {  // rate limited: honour Retry-After (seconds), else back off
      const wait = Number(r.headers.get('retry-after')) || 2 ** i * 5;
      console.error(`figma: rate limited, retrying in ${wait}s`);
      await new Promise(res => setTimeout(res, wait * 1000));
      continue;
    }
    if (!r.ok) throw new Error(`figma ${r.status} on ${path.split('?')[0]}: ${(await r.text()).slice(0, 300)}`);
    return r.json();
  }
}

// zstd needs Node >= 22.15; older Node caches plain JSON instead.
const ZSTD = typeof zlib.zstdCompressSync === 'function';
export const cachePath = (dir, fileKey, nodeId) => join(dir, 'raw', fileKey, `${nodeId.replace(/:/g, '-')}.json${ZSTD ? '.zst' : ''}`);
export const writeCache = (p, json) => {
  mkdirSync(dirname(p), { recursive: true });
  const buf = Buffer.from(JSON.stringify(json));
  writeFileSync(p, p.endsWith('.zst') ? zlib.zstdCompressSync(buf, { params: { [zlib.constants.ZSTD_c_compressionLevel]: 19 } }) : buf);
};
export const readCache = p => JSON.parse((p.endsWith('.zst') ? zlib.zstdDecompressSync(readFileSync(p)) : readFileSync(p)).toString());

/** Pull one node subtree (+ its components map) and a reference PNG render into the cache. Returns the cache paths. */
export async function pull({ fileKey, nodeId, dir, tok, scale = 3, render = true }) {
  if (!nodeId) throw new Error('the URL has no node-id: select the frame in Figma and copy its link');
  const nodes = await get(`/files/${fileKey}/nodes?ids=${encodeURIComponent(nodeId)}`, tok);
  const entry = nodes.nodes?.[nodeId];
  if (!entry) throw new Error(`node ${nodeId} not found in ${fileKey}`);
  const doc = { fileKey, nodeId, name: nodes.name, lastModified: nodes.lastModified, version: nodes.version, pulledAt: new Date().toISOString(),
    document: entry.document, components: entry.components ?? {}, componentSets: entry.componentSets ?? {}, styles: entry.styles ?? {} };
  const raw = cachePath(dir, fileKey, nodeId);
  writeCache(raw, doc);
  let png = null;
  if (render) {  // Figma's own render of the frame: the pixel-diff reference
    const img = await get(`/images/${fileKey}?ids=${encodeURIComponent(nodeId)}&format=png&scale=${scale}`, tok);
    const url = img.images?.[nodeId];
    if (url) {
      png = raw.replace(/\.json(\.zst)?$/, `@${scale}x.png`);
      writeFileSync(png, Buffer.from(await (await fetch(url)).arrayBuffer()));
    }
  }
  return { raw, png, bytes: JSON.stringify(doc).length };
}
