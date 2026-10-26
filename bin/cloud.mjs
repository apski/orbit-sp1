// cloud: where a pack command runs. One interface, three kinds of target:
//   local   this Mac, in place (the default). The only one with simulators, Xcode, Instruments, argent
//   <host>  a machine you already own, by ssh (~/.orbit-cloud/hosts.json, e.g. linux-box): persistent, free
//   do      a throwaway DigitalOcean droplet (Linux; DO has no Macs): created on demand, TTL, destroyed
//   auto    route by the command: macOS tools -> local, anything else -> the first reachable host, else local
//
//   orbit cloud run [--on local|auto|do|<host>] [--pull <path>]... [--detach] -- <cmd>
//                                  sync the repo here up, run in the same subdir, stream output, pull paths back;
//                                  --detach: run in tmux on the target and return (long streams), follow with logs
//   orbit cloud up [--on do|<host>] [--name n] [--size s-4vcpu-8gb] [--region lon1] [--ttl 2h]
//                                  create (do) and set up: bin/cloud-setup.sh (zsh + oh-my-zsh, node 22+, python
//                                  3.13+, uv), then the pack = origin at this checkout's HEAD + its uncommitted work
//   orbit cloud jobs|logs <id> [-f]|attach <id> [--on t]   detached runs on a target
//   orbit cloud sync|ssh [--on t]    the repo here -> the target / a shell there
//   orbit cloud ls | status          targets: hosts (reachable, load, jobs) and DO boxes ($/h, expiry)
//   orbit cloud down [--name n | --all]   destroy DO boxes (hosts persist)
//
// Remote layout: <work>/<repo> and <work>/orbit-sp1 under the remote home (<work> = "orbit-cloud", or the host's
// "work"), so a sync's --delete never reaches anything else there. Sync = what git sees, no .git, no secrets.
// Safety (do): only droplets named orbit-cloud-* AND tagged orbit-cloud are touched; ssh open only to this machine's
// current public IP (firewall `orbit-cloud`, by tag, before boot); expired boxes destroyed at the start of each
// command. Nothing ever listens on this Mac.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs';
import { availableParallelism, homedir, loadavg } from 'node:os';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HOME = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const STATE = join(homedir(), '.orbit-cloud'), KNOWN = join(STATE, 'known_hosts');
const TAG = 'orbit-cloud', PREFIX = 'orbit-cloud-', EXP = 'orbit-cloud-exp-';
const IMAGE = 'ubuntu-26-04-x64';
// voyager's secret patterns (vy.py), plus .git: a target gets the tree, never history or remotes with tokens
const EXCLUDE = ['.git', '.env*', '*.pem', '*.key', '*.p8', '*.p12', '*.crt', '*.mobileprovision', 'certs/', 'secrets.env'];
const OUT = ['tcp', 'udp'].map(p => `protocol:${p},ports:all,address:0.0.0.0/0,address:::/0`).join(' ') + ' protocol:icmp,address:0.0.0.0/0,address:::/0';
// commands that need macOS (simulators, Xcode, Instruments, the pack's device tools): auto keeps them local
export const MAC_ONLY = /\b(xcrun|xcodebuild|xcodegen|simctl|xctrace|instruments|swiftc?|pod|codesign|fastlane|osascript|open|launchctl|security|argent(-sim)?|simwalk|orbitfleet|run-ios|run:ios|spectra (verify|preview|web)|gate-device|sim (claim|release))\b/;

export const ttlSeconds = s => {
  const m = /^(\d+)([mhd])$/.exec(s ?? '');
  if (!m) throw new Error(`bad --ttl ${s} (30m, 2h, 1d)`);
  return m[1] * { m: 60, h: 3600, d: 86400 }[m[2]];
};
export const ours = d => d.name.startsWith(PREFIX) && (d.tags ?? []).includes(TAG);
export const expiry = d => Math.max(0, ...(d.tags ?? []).filter(t => t.startsWith(EXP)).map(t => +t.slice(EXP.length) || 0));
// one argument = a shell string ("npm ci && npm test"); several = argv, each quoted
export const shellCmd = a => (a.length === 1 ? a[0] : a.map(s => (/^[\w@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`)).join(' '));
export const needsMac = cmd => MAC_ONLY.exec(cmd)?.[0] ?? null;

function sh(bin, args, opts = {}) {
  const r = spawnSync(bin, args, { encoding: 'utf8', ...opts });
  if (r.status !== 0 && !opts.ok) throw new Error(`${bin} ${args.slice(0, 3).join(' ')}: ${`${r.stderr ?? ''}${r.stdout ?? ''}`.trim().split('\n').slice(-1)[0] || `exit ${r.status}`}`);
  return r;
}
const now = () => Math.floor(Date.now() / 1000);

// ---- targets: {label, addr, args (ssh options), work (dir under the remote home)}
const ssh = (t, cmd, opts) => sh('ssh', [...t.args, t.addr, cmd], opts);
const rsh = t => ['-e', ['ssh', ...t.args].join(' ')];
const env = t => `export PATH="$HOME/.local/bin:$HOME/${t.work}/orbit-sp1/bin:$PATH" ORBIT_HOME="$HOME/${t.work}/orbit-sp1";`;

const hosts = () => { try { return JSON.parse(readFileSync(join(STATE, 'hosts.json'), 'utf8')); } catch { return {}; } };
const HOST_SSH = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', '-o', 'ServerAliveInterval=30'];
const reached = new Map();
/** A host from hosts.json, by the first of its ssh aliases that answers (LAN first, then a jump; your ~/.ssh/config). */
function hostTarget(name) {
  if (reached.has(name)) return reached.get(name);
  const h = hosts()[name];
  if (!h) throw new Error(`unknown target ${name} (local | auto | do${Object.keys(hosts()).map(n => ` | ${n}`).join('')}; hosts: ~/.orbit-cloud/hosts.json)`);
  for (const addr of h.ssh) {
    if (sh('ssh', [...HOST_SSH, addr, 'true'], { ok: true }).status === 0) {
      const t = { label: name, addr, args: HOST_SSH, work: h.work ?? 'orbit-cloud', dev: h.dev };
      reached.set(name, t);
      return t;
    }
  }
  throw new Error(`${name}: unreachable via ${h.ssh.join(', ')}`);
}

/** auto: macOS work stays here; the rest goes to the first reachable host; nothing reachable -> here. Never DO (costs). */
// ponytail: first reachable host in hosts.json order, not the least loaded; rank by /proc/loadavg once there are two
function route(cmd) {
  const mac = needsMac(cmd);
  if (mac) { console.error(`cloud: auto -> local (needs macOS: ${mac})`); return 'local'; }
  for (const name of Object.keys(hosts())) {
    try { const t = hostTarget(name); console.error(`cloud: auto -> ${name} (via ${t.addr})`); return name; } catch { /* next */ }
  }
  console.error('cloud: auto -> local (no host reachable)');
  return 'local';
}

// ---- do: droplets
const doctl = (...a) => JSON.parse(sh('doctl', [...a, '-o', 'json']).stdout || '[]');
const boxes = () => doctl('compute', 'droplet', 'list', '--tag-name', TAG).filter(ours);
const ipOf = d => d.networks?.v4?.find(n => n.type === 'public')?.ip_address;

function destroy(d) {
  if (!ours(d)) throw new Error(`refusing to destroy ${d.name}: not an orbit-cloud box`);
  sh('doctl', ['compute', 'droplet', 'delete', String(d.id), '--force']);
  if (ipOf(d)) sh('ssh-keygen', ['-R', ipOf(d), '-f', KNOWN], { ok: true });
  for (const t of d.tags.filter(t => t.startsWith(EXP))) sh('doctl', ['compute', 'tag', 'delete', t, '--force'], { ok: true });
}
// ponytail: reaping happens only when a cloud command runs; a box nobody touches again lives on (and bills) until
// `orbit cloud ls` / `orbit status` shows it. A scheduled `orbit cloud ls` closes that gap if it ever matters.
function reap() {
  for (const d of boxes()) if (expiry(d) < now()) { console.error(`cloud: ${d.name} expired, destroying`); destroy(d); }
}

/** The local private key DigitalOcean knows, matched by fingerprint. */
function key() {
  const remote = doctl('compute', 'ssh-key', 'list'), dir = join(homedir(), '.ssh');
  for (const f of readdirSync(dir).filter(f => f.endsWith('.pub') && existsSync(join(dir, f.slice(0, -4))))) {
    const fp = sh('ssh-keygen', ['-E', 'md5', '-lf', join(dir, f)], { ok: true }).stdout?.split(' ')[1]?.replace(/^MD5:/, '');
    const k = remote.find(r => r.fingerprint === fp);
    if (k) return { id: k.id, path: join(dir, f.slice(0, -4)) };
  }
  throw new Error('no key in ~/.ssh is registered with DigitalOcean (doctl compute ssh-key import)');
}
const doArgs = k => ['-i', k.path, '-o', 'IdentitiesOnly=yes', '-o', `UserKnownHostsFile=${KNOWN}`, '-o', 'StrictHostKeyChecking=accept-new',
  '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=30', '-o', 'BatchMode=yes'];

/** SSH open to this machine's current public IP only; re-pointed when it changes (VPN, new network). */
function firewall() {
  const me = `${sh('curl', ['-fsS', '--max-time', '5', 'https://api.ipify.org']).stdout.trim()}/32`;
  const fw = doctl('compute', 'firewall', 'list').find(f => f.name === TAG);
  if (fw?.inbound_rules?.length === 1 && fw.inbound_rules[0].sources?.addresses?.join() === me) return;
  const a = ['--name', TAG, '--inbound-rules', `protocol:tcp,ports:22,address:${me}`, '--outbound-rules', OUT, '--tag-names', TAG];
  sh('doctl', ['compute', 'tag', 'create', TAG], { ok: true });   // the firewall applies by tag, which must exist first
  if (fw) doctl('compute', 'firewall', 'update', fw.id, ...a); else doctl('compute', 'firewall', 'create', ...a);
  console.error(`cloud: firewall ${TAG}: ssh from ${me} only`);
}

function extend(d, ttl) {
  const t = `${EXP}${now() + ttlSeconds(ttl)}`;
  sh('doctl', ['compute', 'tag', 'create', t], { ok: true });
  sh('doctl', ['compute', 'droplet', 'tag', String(d.id), '--tag-name', t]);
  for (const old of d.tags.filter(x => x.startsWith(EXP))) {
    sh('doctl', ['compute', 'droplet', 'untag', String(d.id), '--tag-name', old], { ok: true });
    sh('doctl', ['compute', 'tag', 'delete', old, '--force'], { ok: true });
  }
}

/** The named droplet as a target: created (and set up) if missing, extended if it would expire within 15 min. */
function doTarget(o, { create = true } = {}) {
  mkdirSync(STATE, { recursive: true });
  reap();
  firewall();
  const k = key();
  let d = boxes().find(b => b.name === PREFIX + o.name), fresh = false;
  if (d && expiry(d) - now() < 900) extend(d, o.ttl);
  if (!d) {
    if (!create) throw new Error(`no box ${PREFIX + o.name} (orbit cloud up --on do)`);
    console.error(`cloud: creating ${PREFIX + o.name} (${o.size}, ${o.region}, ttl ${o.ttl})`);
    [d] = doctl('compute', 'droplet', 'create', PREFIX + o.name, '--image', IMAGE, '--size', o.size, '--region', o.region,
      '--ssh-keys', String(k.id), '--tag-names', `${TAG},${EXP}${now() + ttlSeconds(o.ttl)}`, '--wait');
    fresh = true;
  }
  const t = { label: d.name, addr: `root@${ipOf(d)}`, args: doArgs(k), work: 'orbit-cloud', d };
  for (let i = 0; ssh(t, 'true', { ok: true }).status !== 0; i++) {
    if (i === 40) throw new Error(`${d.name} (${ipOf(d)}): no ssh after 2 min`);
    spawnSync('sleep', ['3']);
  }
  if (fresh) setup(t);
  return t;
}

const target = (o, opts) => (o.on === 'do' ? doTarget(o, opts) : hostTarget(o.on));

// ---- what every remote target gets

/** Working tree -> <work>/<repo>/ on the target: what git sees (.gitignore applied), no .git, no secrets. */
function push(src, t) {
  const dest = `${t.work}/${basename(src)}/`;
  ssh(t, `mkdir -p ${dest}`);
  // --delete never removes excluded paths on the target, so its node_modules / .venv / .git survive a sync
  sh('rsync', ['-az', '--no-owner', '--no-group', '--delete', '--filter=:- .gitignore', ...EXCLUDE.flatMap(p => ['--exclude', p]),
    ...rsh(t), `${src}/`, `${t.addr}:${dest}`], { stdio: ['ignore', 'ignore', 'inherit'] });
}

function setup(t) {
  console.error(`cloud: setting up ${t.label} (${t.addr})`);
  ssh(t, `WORK=${t.work} bash -s`, { input: readFileSync(join(HOME, 'bin', 'cloud-setup.sh')), stdio: ['pipe', 'inherit', 'inherit'] });
  // the pack: clone origin with this Mac's agent forwarded for that one command (no key lands on the target), move it
  // to this checkout's HEAD (pushed over ssh: local commits may be unpushed), then overlay the working tree, so the
  // target runs exactly what this Mac runs and `git status` there matches here
  const origin = sh('git', ['-C', HOME, 'remote', 'get-url', 'origin'], { ok: true }).stdout?.trim();
  const p = `${t.work}/orbit-sp1`;
  if (origin) {
    let r = sh('ssh', [...t.args, '-A', t.addr, `if [ -d ${p}/.git ]; then git -C ${p} fetch -q; else rm -rf ${p} && git clone -q ${origin} ${p}; fi`], { ok: true });
    if (r.status === 0) {
      sh('git', ['-C', HOME, 'push', '-qf', `${t.addr}:${p}`, 'HEAD:refs/orbit-cloud/head'], { env: { ...process.env, GIT_SSH_COMMAND: ['ssh', ...t.args].join(' ') } });
      r = ssh(t, `git -C ${p} reset -q --hard refs/orbit-cloud/head`, { ok: true });
    }
    if (r.status !== 0) console.error(`cloud: git for the pack failed (${r.stderr.trim().split('\n').pop()}); ${t.label} gets the working tree without history`);
  }
  push(HOME, t);
  ssh(t, `${env(t)} orbit sync --quiet`, { ok: true });
  if (t.dev) devCheckout(t, origin);
  console.error(`cloud: ${t.label} ready: orbit cloud ssh --on ${t.d ? 'do' : t.label}`);
}

/** A host's own orbit-sp1 checkout to improve the pack in (host "dev": "code/orbit-sp1"): created once at this Mac's
 *  HEAD, then never touched by `up` again (unlike the <work>/orbit-sp1 mirror). This Mac gets a remote of the host's
 *  name: `git fetch linux-box` brings the box's commits here. */
function devCheckout(t, origin) {
  const p = t.dev;
  if (ssh(t, `test -e ${p}`, { ok: true }).status !== 0) {
    sh('ssh', [...t.args, '-A', t.addr, `git clone -q ${origin} ${p}`]);
    sh('git', ['-C', HOME, 'push', '-qf', `${t.addr}:${p}`, 'HEAD:refs/orbit-cloud/head'], { env: { ...process.env, GIT_SSH_COMMAND: ['ssh', ...t.args].join(' ') } });
    ssh(t, `git -C ${p} checkout -q -B main refs/orbit-cloud/head && git -C ${p} branch -q -u origin/main`);
    console.error(`cloud: ${t.label}:~/${p} created at this checkout's HEAD (edit, commit, push from there)`);
  }
  if (sh('git', ['-C', HOME, 'remote', 'get-url', t.label], { ok: true }).status !== 0) {
    sh('git', ['-C', HOME, 'remote', 'add', t.label, `${t.addr}:${p}`]);
    console.error(`cloud: remote ${t.label} -> ${t.addr}:${p} (git fetch ${t.label})`);
  }
}

function run(o, cmd) {
  const here = process.cwd(), on = o.on === 'auto' ? route(cmd) : o.on;
  if (on === 'local') {
    const [l] = loadavg(), n = availableParallelism();
    if (l > 2 * n) console.error(`cloud: load ${l.toFixed(0)} on ${n} cores here: timings will be noisy (Linux work: --on auto)`);
    return spawnSync('sh', ['-c', cmd], { stdio: 'inherit', cwd: here }).status ?? 1;
  }
  const repo = sh('git', ['-C', here, 'rev-parse', '--show-toplevel'], { ok: true }).stdout?.trim() || here;
  const t = target({ ...o, on });
  push(repo, t);
  const cwd = join(t.work, basename(repo), relative(repo, here));   // relative to the remote home, where ssh starts
  if (o.detach) return detach(t, cwd, cmd, on);
  const r = spawnSync('ssh', [...t.args, ...(process.stdin.isTTY ? ['-t'] : []), t.addr, `${env(t)} cd ${cwd} && ${cmd}`], { stdio: 'inherit' });
  for (const p of o.pull) {
    sh('rsync', ['-az', '--no-owner', '--no-group', ...rsh(t), `${t.addr}:${t.work}/${basename(repo)}/${p}`, join(repo, dirname(p)) + '/'], { stdio: ['ignore', 'ignore', 'inherit'] });
    console.error(`cloud: pulled ${p}`);
  }
  return r.status ?? 1;
}

/** A long run in tmux on the target: survives this ssh, this Mac sleeping, the session ending. Log + exit status kept. */
function detach(t, cwd, cmd, on) {
  const id = `${new Date().toISOString().slice(5, 16).replace(/[-:T]/g, '')}-${Math.random().toString(36).slice(2, 6)}`;
  const log = `$HOME/${t.work}/jobs/${id}.log`;
  const script = `${env(t)}\ncd ${cwd} || exit 1\n{ ${cmd}\n} >${log} 2>&1\necho "[exit $?]" >>${log}\n`;
  ssh(t, `mkdir -p ${t.work}/jobs && cat >${t.work}/jobs/${id}.sh && tmux new-session -d -s oc-${id} "bash $HOME/${t.work}/jobs/${id}.sh"`, { input: script });
  const flag = on === 'do' ? '--on do' : `--on ${on}`;
  console.log(`${id}  detached on ${t.label}: orbit cloud logs ${id} ${flag} [-f] | attach ${id} ${flag} | jobs ${flag}`);
  if (t.d) console.error(`cloud: ${t.label} is destroyed at its expiry, job or not (orbit cloud up --on do --ttl 8h to extend)`);
  return 0;
}

const JOBS = w => `cd ~/${w}/jobs 2>/dev/null || exit 0; for l in $(ls -t *.log 2>/dev/null | head -20); do id=\${l%.log}; ` +
  `e=$(tail -1 "$l" | grep -o '^\\[exit [0-9]*\\]'); if [ -n "$e" ]; then s="$e"; elif tmux has-session -t "oc-$id" 2>/dev/null; then s=running; else s=died; fi; ` +
  `printf '%-15s %-10s %s\\n' "$id" "$s" "$(sed -n 's/^{ //p' "$id.sh" | head -1 | cut -c1-90)"; done`;

function ls() {
  for (const [name, h] of Object.entries(hosts())) {
    let t;
    try { t = hostTarget(name); } catch { console.log(`${name.padEnd(24)} unreachable via ${h.ssh.join(', ')}`); continue; }
    const r = ssh(t, `read a b c _ </proc/loadavg; echo "$a $(nproc) $(ls ~/${t.work}/jobs/*.log 2>/dev/null | wc -l)"`, { ok: true }).stdout.trim().split(' ');
    console.log(`${name.padEnd(24)} host via ${t.addr.padEnd(20)} load ${r[0]} on ${r[1]} cores, ${r[2]} job log(s)`);
  }
  if (spawnSync('sh', ['-c', 'command -v doctl']).status !== 0) return;
  mkdirSync(STATE, { recursive: true });
  reap();
  for (const d of boxes()) {
    const left = expiry(d) - now();
    console.log(`${d.name.padEnd(24)} do   ${d.size_slug.padEnd(14)} ${(ipOf(d) ?? '-').padEnd(16)} $${d.size?.price_hourly ?? '?'}/h  ${left > 0 ? `expires in ${Math.round(left / 60)} min` : 'EXPIRED'}`);
  }
}

/** One status row, read-only (no ssh, never reaps): `cloud  <state>  next: <step>`. */
function status() {
  const hs = Object.keys(hosts());
  const hostNote = hs.length ? `hosts ${hs.join(', ')}` : 'no hosts (~/.orbit-cloud/hosts.json)';
  let rows = [];
  try { rows = spawnSync('sh', ['-c', 'command -v doctl']).status === 0 ? boxes() : []; } catch { /* doctl not authed */ }
  console.log(`cloud  online  next: local, ${hostNote}${rows.length ? `; DO up: ${rows.map(d => `${d.name} $${d.size?.price_hourly}/h`).join(', ')} (orbit cloud down when done)` : ''}; route with --on auto`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const all = process.argv.slice(2), dd = all.indexOf('--');
  const a = dd < 0 ? all : all.slice(0, dd), rest = dd < 0 ? [] : all.slice(dd + 1);
  const opt = (f, d) => (a.includes(f) ? a[a.indexOf(f) + 1] : d);
  const c = a[0], id = a[1] && !a[1].startsWith('--') ? a[1] : null;
  const o = {
    on: opt('--on', c === 'run' ? 'local' : c === 'up' || c === 'ssh' || c === 'sync' ? 'do' : 'auto'), name: opt('--name', 'default'),
    size: opt('--size', 's-4vcpu-8gb'), region: opt('--region', 'lon1'), ttl: opt('--ttl', '2h'),
    pull: a.flatMap((x, i) => (x === '--pull' ? [a[i + 1]] : [])), detach: a.includes('--detach'),
  };
  // jobs/logs/attach with no --on: the first reachable host
  const jobTarget = () => target(o.on === 'auto' ? { ...o, on: route('') } : o, { create: false });
  try {
    if (c === 'run' && rest.length) process.exit(run(o, shellCmd(rest)));
    else if (c === 'up') { const t = target(o); if (!t.d) setup(t); }
    else if (c === 'sync') push(sh('git', ['rev-parse', '--show-toplevel']).stdout.trim(), target(o, { create: false }));
    else if (c === 'ssh') {
      const t = target(o, { create: false });
      process.exit(spawnSync('ssh', [...t.args.map(x => (x === 'BatchMode=yes' ? 'BatchMode=no' : x)), '-t', t.addr, `cd ${t.work} 2>/dev/null; exec zsh -l`], { stdio: 'inherit' }).status ?? 1);
    } else if (c === 'jobs') { const t = jobTarget(); process.stdout.write(ssh(t, JOBS(t.work)).stdout || 'no jobs\n'); }
    else if (c === 'logs' && id) { const t = jobTarget(); process.exit(spawnSync('ssh', [...t.args, t.addr, `tail -n 200 ${a.includes('-f') ? '-f ' : ''}${t.work}/jobs/${id}.log`], { stdio: 'inherit' }).status ?? 1); }
    else if (c === 'attach' && id) { const t = jobTarget(); process.exit(spawnSync('ssh', [...t.args, '-t', t.addr, `tmux attach -t oc-${id}`], { stdio: 'inherit' }).status ?? 1); }
    else if (c === 'ls') ls();
    else if (c === 'status') status();
    else if (c === 'down') {
      const gone = boxes().filter(d => a.includes('--all') || d.name === PREFIX + o.name);
      gone.forEach(destroy);
      console.log(gone.length ? `destroyed ${gone.map(d => d.name).join(', ')}` : 'nothing to destroy (hosts persist)');
    } else {
      console.error(readFileSync(fileURLToPath(import.meta.url), 'utf8').split('\nimport')[0].replace(/^\/\/ ?/gm, ''));
      process.exit(2);
    }
  } catch (e) { console.error(`cloud: ${e.message}`); process.exit(1); }
}
