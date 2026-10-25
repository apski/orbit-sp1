#!/usr/bin/env node
// orbit-rag subsystem: index a repo's docs and code into <repo>/.orbit-rag/, register the read-only MCP server, and run
// the search UI as a login service on 127.0.0.1:<port> (default 7420). Safe to re-run; each step skips what is done.
//
//   node orbit-rag/install.mjs <repo> [--port 7420] [--no-service] [--no-mcp]
//   node orbit-rag/install.mjs status <repo>
//
// Engine source: this directory (Rust workspace + apps/viewer), or $ORBIT_RAG_SRC. Built with cargo and npm, installed to
// ~/.local/share/orbit-rag/bin/orbit-rag (off PATH, so it never shadows Orbit). The UI binds loopback only.
import { spawnSync } from 'node:child_process';
import { chmodSync, copyFileSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { homedir, platform } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const H = homedir();
const HERE = dirname(fileURLToPath(import.meta.url));
const BIN = join(H, '.local/share/orbit-rag/bin/orbit-rag');
const SKIP = /^(scratchpad|tmp|vendor|node_modules|dist|build|target|\.[^/]+)\//;  // throwaway and generated trees
const EXT = ['md', 'rs', 'ts', 'tsx', 'js', 'jsx', 'py'];
const run = (bin, args, opts = {}) => spawnSync(bin, args, { encoding: 'utf8', maxBuffer: 1 << 28, ...opts });
const has = bin => run('sh', ['-c', `command -v ${bin}`]).status === 0;
const read = p => { try { return readFileSync(p, 'utf8'); } catch { return ''; } };
const argv = process.argv.slice(2);
const flag = (k, d) => (argv.includes(k) ? argv[argv.indexOf(k) + 1] : d);
const port = Number(flag('--port', '7420'));
const label = `com.orbit-rag.viewer.${port}`;
const plist = join(H, 'Library/LaunchAgents', `${label}.plist`);

function source() {
  for (const d of [process.env.ORBIT_RAG_SRC, HERE]) if (d && existsSync(join(d, 'crates/cli/Cargo.toml'))) return d;
  return null;
}

/** Build the engine and viewer, then install the binary by rename so running servers keep their old inode. */
function installBinary(src) {
  const b = run('cargo', ['build', '--release', '-p', 'orbit-rag'], { cwd: src, stdio: ['ignore', 'ignore', 'pipe'] });
  if (b.status !== 0) throw new Error(`cargo build failed: ${b.stderr.trim().split('\n').slice(-3).join(' ')}`);
  const viewer = join(src, 'apps/viewer');
  if (!existsSync(join(viewer, 'dist/index.html'))) {
    if (!existsSync(join(viewer, 'node_modules'))) run('npm', ['ci', '--silent'], { cwd: viewer, stdio: 'inherit' });
    if (run('npm', ['run', 'build', '--silent'], { cwd: viewer }).status !== 0) throw new Error('viewer build failed (apps/viewer: npm run build)');
  }
  mkdirSync(join(BIN, '..'), { recursive: true });
  const built = join(src, 'target/release/orbit-rag');
  const fresh = !existsSync(BIN) || statSync(built).mtimeMs > statSync(BIN).mtimeMs;
  if (fresh) { copyFileSync(built, `${BIN}.new`); chmodSync(`${BIN}.new`, 0o755); renameSync(`${BIN}.new`, BIN); }
  return fresh ? 'installed' : 'up to date';
}

/** ollama when it's available (pulls the embedding model once); keyword + prefix search otherwise. */
function embedder() {
  if (!has('ollama')) return 'none';
  if (!run('ollama', ['list']).stdout.includes('nomic-embed-text')) run('ollama', ['pull', 'nomic-embed-text'], { stdio: 'inherit' });
  return 'ollama:nomic-embed-text';
}

function index(repo) {
  const exclude = join(repo, '.git/info/exclude');
  if (existsSync(join(repo, '.git')) && !read(exclude).split('\n').includes('.orbit-rag/')) {
    mkdirSync(join(repo, '.git/info'), { recursive: true });
    writeFileSync(exclude, `${read(exclude).replace(/\n?$/, '\n')}# orbit-rag (local docs/decisions index)\n.orbit-rag/\n`);
  }
  const env = { ...process.env, ORBIT_EMBED: 'none' };  // register fast; the service embeds in the background
  if (!existsSync(join(repo, '.orbit-rag'))) run(BIN, ['init'], { cwd: repo, env });
  // tracked files that still exist (ls-files also lists ones deleted in the working tree)
  const files = run('git', ['ls-files', '-z', ...EXT.map(e => `*.${e}`)], { cwd: repo }).stdout.split('\0').filter(f => f && !SKIP.test(f) && existsSync(join(repo, f)));
  for (let i = 0; i < files.length; i += 500) {
    const r = run(BIN, ['add', ...files.slice(i, i + 500)], { cwd: repo, env });
    if (r.status !== 0) throw new Error(`orbit-rag add: ${r.stderr.trim().split('\n').slice(-1)[0]}`);
  }
  return `${files.length} files registered`;
}

function mcp(repo, emb) {
  if (!has('claude')) return 'skipped: claude CLI not found';
  if (run('claude', ['mcp', 'get', 'orbit-rag'], { cwd: repo }).status === 0) return 'already registered';
  const r = run('claude', ['mcp', 'add', 'orbit-rag', '-s', 'local', '-e', `ORBIT_EMBED=${emb}`, '--', BIN, 'mcp'], { cwd: repo });
  return r.status === 0 ? 'registered (local scope)' : `FAILED: ${r.stderr.trim()}`;
}

/** A per-user LaunchAgent: starts at login, restarts if it dies, serves one repo on one port. */
function service(repo, emb) {
  if (platform() !== 'darwin') return `skipped: no launchd; run \`${BIN} viewer --port ${port} --no-open\` in ${repo}`;
  const current = read(plist).match(/<key>WorkingDirectory<\/key>\s*<string>([^<]*)<\/string>/)?.[1];
  if (current && current !== repo) return `skipped: port ${port} already serves ${current}; pass --port <n>`;
  const listener = run('lsof', ['-nP', `-iTCP:${port}`, '-sTCP:LISTEN', '-Fc']).stdout;
  if (listener && !listener.includes('orbit-rag')) return `skipped: port ${port} is taken by ${listener.match(/^c(.*)$/m)?.[1]}; pass --port <n>`;
  const x = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const log = join(H, 'Library/Logs', `orbit-rag-viewer-${port}.log`);
  writeFileSync(plist, `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${label}</string>
  <key>ProgramArguments</key><array><string>${x(BIN)}</string><string>viewer</string><string>--port</string><string>${port}</string><string>--no-open</string></array>
  <key>WorkingDirectory</key><string>${x(repo)}</string>
  <key>EnvironmentVariables</key><dict><key>ORBIT_EMBED</key><string>${emb}</string><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${x(log)}</string>
  <key>StandardErrorPath</key><string>${x(log)}</string>
</dict></plist>
`);
  const uid = process.getuid();
  run('launchctl', ['bootout', `gui/${uid}/${label}`]);  // reload: picks up a new binary or settings
  const r = run('launchctl', ['bootstrap', `gui/${uid}`, plist]);
  return r.status === 0 ? `http://127.0.0.1:${port} (LaunchAgent ${label}, log ${log})` : `FAILED: launchctl ${r.stderr.trim()}`;
}

export function status(repo) {
  const up = run('curl', ['-s', '-o', '/dev/null', '-w', '%{http_code}', '-m', '2', `http://127.0.0.1:${port}/`]).stdout === '200';
  const served = read(plist).match(/<key>WorkingDirectory<\/key>\s*<string>([^<]*)<\/string>/)?.[1];
  const indexed = existsSync(join(repo, '.orbit-rag/orbit.db'));
  const state = !existsSync(BIN) ? 'not installed' : !indexed ? 'binary only' : 'installed';
  const next = !existsSync(BIN) || !indexed ? 'orbit install <repo> --only orbit-rag'
    : served !== repo ? `UI serves ${served ?? 'nothing'} on ${port}`
    : up ? `online: http://127.0.0.1:${port}` : `service down: launchctl kickstart -k gui/${process.getuid()}/${label}`;
  return ['orbit-rag', state, next];
}

export function install(repo) {
  const src = source();
  if (!src) return [['orbit-rag', 'FAILED', `engine source not found in ${HERE} (or $ORBIT_RAG_SRC)`]];
  if (!has('cargo')) return [['orbit-rag', 'FAILED', 'cargo not found (install Rust: https://rustup.rs)']];
  const out = [];
  try {
    out.push(['orbit-rag', 'binary', installBinary(src)]);
    const emb = embedder();
    out.push(['orbit-rag', 'index', `${index(repo)}; embeddings: ${emb}`]);
    if (!argv.includes('--no-mcp')) out.push(['orbit-rag', 'mcp', mcp(repo, emb)]);
    if (!argv.includes('--no-service')) out.push(['orbit-rag', 'ui', service(repo, emb)]);
  } catch (e) {
    out.push(['orbit-rag', 'FAILED', e.message]);
  }
  return out;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [cmd, target] = argv.filter((a, i) => !a.startsWith('--') && !argv[i - 1]?.startsWith('--'));
  if (cmd === 'status') console.log(status(resolve(target ?? '.')).join('  '));
  else if (cmd) for (const row of install(resolve(cmd))) console.log(row.join('  '));
  else { console.log(read(new URL(import.meta.url)).split('\n').filter(l => l.startsWith('//')).map(l => l.slice(3)).join('\n')); process.exit(1); }
}
