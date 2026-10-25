#!/usr/bin/env node
// Spectra: React Native / Figma -> native UI compiler. One CLI, no dependencies, no model calls.
// Everything per-repo lives in <repo>/.spectra/ (profile, figma cache, jest harness, generated code, preview app).
//
//   spectra detect | check [step] | confirm <section>      L1 bootstrap: repo profile (what Spectra needs, what's missing)
//   spectra figma pull <url> [--no-render]                  REST pull into the zstd cache (+ Figma's PNG render)
//   spectra figma inspect <url|raw> [--depth N]             one line per node: the agent's view of a frame
//   spectra figma lower <url|raw> [-o ir.json] [--root fill|fixed]
//   spectra capture runtime <Screen> [-o] [--port] [--device]   from a running RN app (reference frames)
//   spectra capture jest <file> <Export> [-o]               from a jest render (any state, no simulator)
//   spectra gen <ir.json> [-o Out.generated.swift] [--ignore-safe-area all|bottom|none]
//   spectra judge <ir.json> <map.json> <frames.log> [--tol 1]
//   spectra verify <ir.json> [--backend flex|swiftui|uikit] [--udid U] [--frame x,y,w,h] [--bg r,g,b] [--release]
//   spectra install <repo>                                  skills + .spectra/ shim + detect
import { execFileSync, spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HOME = resolve(dirname(fileURLToPath(import.meta.url)), '..');  // the spectra/ directory of orbit-SP1
const argv = process.argv.slice(2);
const flag = (k, d) => { const i = argv.indexOf(`--${k}`); return i >= 0 ? (argv[i + 1]?.startsWith('--') || argv[i + 1] === undefined ? true : argv[i + 1]) : d; };
const opt = (k, short, d) => flag(k, undefined) ?? (argv.indexOf(short) >= 0 ? argv[argv.indexOf(short) + 1] : d);
const positional = argv.filter((a, i) => !a.startsWith('-') && !(i > 0 && argv[i - 1].startsWith('-') && !['--no-render', '--release'].includes(argv[i - 1])));
const repo = resolve(flag('repo', process.cwd()));
const dot = join(repo, '.spectra');
const die = m => { console.error(`spectra: ${m}`); process.exit(1); };
const readJson = p => JSON.parse(readFileSync(p, 'utf8'));
const writeJson = (p, j) => { mkdirSync(dirname(p), { recursive: true }); writeFileSync(p, JSON.stringify(j, null, 1)); };
const pascal = s => s.replace(/[^A-Za-z0-9]+(.)?/g, (_, c) => (c ? c.toUpperCase() : '')).replace(/^./, c => c.toUpperCase());

async function figmaDoc(ref) {
  const api = await import('../frontends/figma/api.mjs');
  if (ref.startsWith('http')) {
    const { fileKey, nodeId } = api.parseUrl(ref);
    const p = api.cachePath(join(dot, 'figma'), fileKey, nodeId ?? '');
    if (!existsSync(p)) die(`not pulled yet: spectra figma pull '${ref}'`);
    return api.readCache(p);
  }
  return api.readCache(resolve(ref));
}

function booted() {
  const d = JSON.parse(execFileSync('xcrun', ['simctl', 'list', 'devices', 'booted', '-j']).toString()).devices;
  return Object.values(d).flat().find(x => /iPhone/.test(x.name))?.udid;
}

const commands = {
  async detect() { (await import('../bootstrap/detect.mjs')).detect(repo); },
  async check() { process.exitCode = (await import('../bootstrap/detect.mjs')).check(repo, positional[1]); },
  async confirm() { (await import('../bootstrap/detect.mjs')).confirm(repo, positional[1]); },
  async install() { (await import('../bootstrap/install.mjs')).install(resolve(positional[1] ?? '.'), HOME); },

  async figma() {
    const [, sub, ref] = positional;
    if (!ref) die('usage: spectra figma pull|inspect|lower <url|raw>');
    if (sub === 'pull') {
      const api = await import('../frontends/figma/api.mjs');
      const tok = api.token(repo) ?? die('no Figma token: set FIGMA_TOKEN, or FIGMA_TOKEN=... in .spectra/secrets.env or ~/.spectra/secrets.env');
      const { fileKey, nodeId } = api.parseUrl(ref);
      const r = await api.pull({ fileKey, nodeId, dir: join(dot, 'figma'), tok, render: !flag('no-render', false) });
      console.log(`pulled ${fileKey} ${nodeId}: ${r.bytes} bytes raw -> ${r.raw}${r.png ? `\nrender: ${r.png}` : ''}`);
      console.log(`next: spectra figma inspect '${ref}'   (or lower / verify)`);
    } else if (sub === 'inspect') {
      console.log((await import('../frontends/figma/inspect.mjs')).inspect(await figmaDoc(ref), { depth: Number(flag('depth', Infinity)) }));
    } else if (sub === 'lower') {
      const { ir, gaps } = (await import('../frontends/figma/lower.mjs')).lower(await figmaDoc(ref), { root: flag('root', 'fill') });
      const out = opt('out', '-o', join(dot, 'ir', `${pascal(ir.screen)}.figma.json`));
      writeJson(out, ir);
      console.log(`${out}: ${ir.screen}, ${gaps.length} gaps${gaps.map(g => `\n  - ${g}`).join('')}`);
    } else die(`unknown figma command ${sub}`);
  },

  async capture() {
    const [, how, a, b] = positional;
    if (how === 'runtime') {
      const ir = await (await import('../frontends/rn-runtime/run.mjs')).captureRuntime({ screen: a, port: flag('port', 8081), device: flag('device', '') });
      const out = opt('out', '-o', join(dot, 'ir', `${a}.runtime.json`));
      writeJson(out, ir); console.log(out);
    } else if (how === 'jest') {
      if (!a || !b) die('usage: spectra capture jest <file> <Export>');
      const jestDir = join(dot, 'jest');
      if (!existsSync(join(jestDir, 'jest.config.cjs'))) die('no jest harness yet: run the spectra-bootstrap skill (spectra check capture-jest)');
      const out = resolve(opt('out', '-o', join(dot, 'ir', `${b}.jest.json`)));
      mkdirSync(dirname(out), { recursive: true });
      const test = join(jestDir, `${b}.spectra.test.js`);
      writeFileSync(test, `// Generated by spectra capture jest. Props and providers come from wrap.js (written once per repo).
const React = require('react');
const { captureScreen } = require(${JSON.stringify(join(HOME, 'frontends/rn-jest/jest-capture.js'))});
const { wrap, props } = require('./wrap.js');
const Screen = require(${JSON.stringify(resolve(a))})[${JSON.stringify(b)}];
test('spectra capture ${b}', () => { captureScreen(wrap(React.createElement(Screen, props(${JSON.stringify(b)}))), ${JSON.stringify(b)}, ${JSON.stringify(out)}); });
`);
      const r = spawnSync('npx', ['jest', '--config', join(jestDir, 'jest.config.cjs'), test], { cwd: repo, stdio: 'inherit' });
      if (r.status !== 0) die('jest capture failed (fix mocks in .spectra/jest/setup.js or providers in wrap.js)');
      console.log(out);
    } else die('usage: spectra capture runtime|jest ...');
  },

  async gen() {
    const ir = positional[1] ?? die('usage: spectra gen <ir.json>');
    const doc = readJson(ir);
    const out = opt('out', '-o', join(dot, 'out', `${pascal(doc.screen)}.generated.swift`));
    mkdirSync(dirname(out), { recursive: true });
    const r = (await import('../backends/swiftui/codegen.mjs')).generateFile(ir, out, { ignoreEdges: flag('ignore-safe-area', doc.source === 'figma' ? 'none' : 'all') });
    console.log(`${out}: ${r.compositions} compositions, ${r.nodes} nodes, ${r.gaps.length} gaps${r.gaps.map(g => `\n  - ${g}`).join('')}`);
    return out;
  },

  async judge() {
    const [, ir, map, log] = positional;
    const { judge, report } = await import('../verify/judge.mjs');
    const tol = Number(flag('tol', 1));
    const r = judge(readJson(ir), readJson(map).map, readFileSync(log, 'utf8'), tol);
    console.log(report(r, tol)); process.exitCode = r.pass ? 0 : 1;
  },

  async verify() {
    const irPath = positional[1] ?? die('usage: spectra verify <ir.json>');
    const doc = readJson(irPath);
    // Back-ends: flex = one Flex layout per composition (FlexCore); swiftui / uikit = lowered to native stacks.
    const BACKENDS = { flex: ['../backends/swiftui/codegen.mjs', ''], swiftui: ['../backends/swiftui/lower.mjs', 'SwiftUI'], uikit: ['../backends/uikit/lower.mjs', 'UIKit'] };
    const backend = flag('backend', 'flex');
    const [modPath, suffix] = BACKENDS[backend] ?? die(`unknown backend ${backend} (${Object.keys(BACKENDS).join(', ')})`);
    const name = pascal(doc.screen) + suffix;
    const pv = join(dot, 'preview');
    const gen = join(pv, 'Sources', 'Generated');
    mkdirSync(gen, { recursive: true });
    for (const f of readdirSync(join(HOME, 'preview', 'Sources')).filter(f => f.endsWith('.swift')))  // host + helpers
      cpSync(join(HOME, 'preview', 'Sources', f), join(pv, 'Sources', f));
    writeFileSync(join(pv, 'project.yml'), readFileSync(join(HOME, 'preview', 'project.yml.template'), 'utf8')
      .replaceAll('{{FLEXLAYOUT}}', relative(pv, join(HOME, 'backends', 'swiftui', 'FlexLayout'))));  // xcodegen wants it relative
    const out = join(gen, `${name}.generated.swift`);
    const r = (await import(modPath)).generateFile(irPath, out, { name, sourceName: basename(irPath), ignoreEdges: flag('ignore-safe-area', doc.source === 'figma' ? 'none' : 'all') });
    // Registry of every generated screen in the preview, so one build hosts them all.
    const names = readdirSync(gen).filter(f => f.endsWith('.generated.swift')).map(f => f.replace('.generated.swift', '')).sort();
    writeFileSync(join(pv, 'Sources', 'Screens.generated.swift'), `// Generated by spectra verify. Do not edit.
import SwiftUI

let spectraScreenNames = ${JSON.stringify(names)}

@MainActor func spectraScreen(_ name: String, onFrames: @escaping (Int, [CGRect]) -> Void) -> AnyView {
  switch name {
${names.map(n => `  case "${n}": AnyView(${n}(onFrames: onFrames))`).join('\n')}
  default: AnyView(Text("no generated screen named \\(name)"))
  }
}
`);
    const udid = flag('udid', null) || booted() || die('no booted iPhone simulator (pass --udid)');
    const config = flag('release', false) ? 'Release' : 'Debug';
    const sh = (cmd, args, o = {}) => { const x = spawnSync(cmd, args, { cwd: pv, encoding: 'utf8', ...o }); if (x.status !== 0) die(`${cmd} ${args[0]} failed:\n${(x.stdout + x.stderr).split('\n').filter(l => /error/i.test(l)).slice(0, 15).join('\n')}`); return x.stdout; };
    sh('xcodegen', ['-q']);
    sh('xcodebuild', ['-project', 'SpectraPreview.xcodeproj', '-scheme', 'SpectraPreview', '-configuration', config,
      '-destination', `id=${udid}`, '-derivedDataPath', join(pv, 'build'), '-quiet', 'build']);
    sh('xcrun', ['simctl', 'install', udid, join(pv, 'build', 'Build', 'Products', `${config}-iphonesimulator`, 'SpectraPreview.app')]);
    const frame = flag('frame', doc.container ? `0,0,${doc.container[0]},${doc.container[1]}` : null);
    const started = new Date(Date.now() - 1000).toISOString().replace('T', ' ').slice(0, 19);
    sh('xcrun', ['simctl', 'launch', '--terminate-running-process', udid, 'app.spectra.preview', '-screen', name,
      ...(frame ? ['-frame', frame] : []), ...(flag('bg', null) ? ['-bg', flag('bg')] : [])]);
    await new Promise(res => setTimeout(res, 3500));
    const log = sh('xcrun', ['simctl', 'spawn', udid, 'log', 'show', '--start', started, '--style', 'compact',
      '--predicate', 'process == "SpectraPreview" AND eventMessage CONTAINS "ORBIT_FRAMES"']);
    const logPath = join(pv, `${name}.frames.log`);
    writeFileSync(logPath, log);
    const { judge, report } = await import('../verify/judge.mjs');
    const tol = Number(flag('tol', 1));
    const j = judge(doc, r.map, log, tol);
    if (r.coverage) console.log(`coverage (${backend}): ${JSON.stringify(r.coverage)}`);
    console.log(`${name} (${backend}, ${config}, ${udid}), ${r.gaps.length} codegen gaps${r.gaps.map(g => `\n  - ${g}`).join('')}\n${report(j, tol)}`);
    if (!doc.roots?.[0]?.frame) console.log('(no reference frames in this IR: a jest capture; judge it with a runtime capture of the same screen)');
    process.exitCode = j.pass ? 0 : 1;
  },
};

const cmd = positional[0];
if (!commands[cmd]) {
  console.log(readFileSync(fileURLToPath(import.meta.url), 'utf8').split('\n').filter(l => l.startsWith('//   spectra') || l.startsWith('// Spectra')).map(l => l.slice(3)).join('\n'));
  process.exit(cmd ? 1 : 0);
}
await commands[cmd]();
