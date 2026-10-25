#!/usr/bin/env node
// facts: one AST pass over an RN codebase -> per-file facts every architecture skill reads.
//
//   node facts.mjs <repo> [--json <repo>/.navlab/facts.json]
//
// Per file: components declared; JSX usage with props (+ where each component comes from); style literals vs token
// references; API calls (fetch, axios-like clients, GraphQL/Relay operations); navigation calls and deep-link config;
// storage keys; analytics events; feature flags; native modules; state reads/writes (Redux, Zustand, MobX, Context,
// Recoil, Jotai); effects (deps shape, fetch inside); sequential awaits (waterfalls); testIDs; package imports.
// Codebase conventions go in navlab.adapter.json -> "facts": { analytics: [...], flags: [...], designSystem: [...],
// apiClients: [...], storage: [...] } (names of callees / module paths). Heuristic by design: every fact has a line.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { EXTS, makeResolver, parseCode, walk } from '../scan/lib.mjs';

if (process.argv[2] === '--selftest') {  // arch/fixtures/facts: shapes real repos broke (social-app: XRPC calls, array push, Map.delete)
  const me = fileURLToPath(import.meta.url), tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'facts-')), 'f.json');
  spawnSync('node', [me, path.join(path.dirname(me), 'fixtures', 'facts'), '--json', tmp]);
  const F = JSON.parse(fs.readFileSync(tmp, 'utf8')).facts.find((f) => f.file === 'src/agent.ts');
  const api = F.api.map((x) => `${x.kind} ${x.target}`), nav = F.nav.map((x) => x.route);
  const want = { api: ['rpc chat.bsky.convo.sendMessage', 'http /users'], nav: ['Profile', 'Home'], flags: ['ax.features.GroupChatsDisable'] };
  const flags = F.flags.map((x) => x.flag);
  const ok = JSON.stringify(api) === JSON.stringify(want.api) && JSON.stringify(nav) === JSON.stringify(want.nav) && JSON.stringify(flags) === JSON.stringify(want.flags);
  console.log(ok ? 'selftest ok' : `selftest FAIL: api ${JSON.stringify(api)} nav ${JSON.stringify(nav)} flags ${JSON.stringify(flags)}; want ${JSON.stringify(want)}`);
  process.exit(ok ? 0 : 1);
}

const argv = process.argv.slice(2);
const opt = (k, d) => (argv.includes(k) ? argv[argv.indexOf(k) + 1] : d);
const REPO = path.resolve(argv.find((a, i) => !a.startsWith('--') && !argv[i - 1]?.startsWith('--')) || '.');
const adapterPath = path.join(REPO, 'navlab.adapter.json');
const adapter = fs.existsSync(adapterPath) ? JSON.parse(fs.readFileSync(adapterPath, 'utf8')) : {};
const cfg = adapter.facts || {};
const { resolveModule } = makeResolver(REPO, adapter);
const rel = (f) => path.relative(REPO, f);

const ANALYTICS = new Set(['track', 'trackEvent', 'logEvent', 'capture', 'logScreenView', 'trackScreen', 'recordEvent', ...(cfg.analytics || [])]);
const FLAGS = new Set(['useFeatureFlag', 'useFlag', 'getFeatureFlag', 'isFeatureEnabled', 'isEnabled', 'variation', 'useFeatureFlagEnabled',
  'useGate', 'checkGate', 'useFeatureIsOn', 'getBoolean', ...(cfg.flags || [])]);
const HTTP = new Set(['get', 'post', 'put', 'patch', 'delete', 'request', 'head']);
// default: the receiver's own name (last segment) ends in a client word, so this.chatClient matches, requestedPollIntervals doesn't
const CLIENT_RE = cfg.apiClients?.length ? new RegExp(cfg.apiClients.join('|'), 'i') : /^axios|(api|client|http|request|service)s?$/i;
const API_CLIENT = { test: (on) => CLIENT_RE.test(cfg.apiClients?.length ? on : String(on).split('.').pop()) };
// navigation verbs that are also array/collection methods: count them only with a route string or a navigation-like receiver
const NAV_AMBIGUOUS = /^(push|replace|reset)$/, NAV_RECEIVER = /nav|router|stackactions|commonactions|tabactions|drawer/i;
const STORAGE_OBJ = /AsyncStorage|SecureStore|storage|mmkv|Keychain|EncryptedStorage|MMKV/;
const STORAGE_FN = /^(getItem|setItem|removeItem|mergeItem|multiGet|getItemAsync|setItemAsync|deleteItemAsync|getString|getNumber|getBoolean|set|delete|setGenericPassword|getGenericPassword|setInternetCredentials)$/;
// The codebase's own storage wrappers. "file#name" = only when `name` is imported from that file (functions, or instances
// whose methods take the key, e.g. device.get(['key'])); a plain "name" matches any callee with that name.
const STORAGE_WRAP = new Set((cfg.storage || []).filter((x) => !x.includes('#')));
const STORAGE_Q = new Map((cfg.storage || []).filter((x) => x.includes('#')).map((x) => [x.split('#')[1], x.split('#')[0]]));
const STORAGE_METHOD = /^(get|set|remove|delete|has|getItem|setItem|removeItem|read|write|load|save|clear)$/;
const STATE_READ = /^(useSelector|useAppSelector|useStores?|useRecoilState|useRecoilValue|useAtom|useAtomValue|useSnapshot|useQuery|useInfiniteQuery|useSWR|useContext)$|^use\w*Store$/;
const STYLE_NUM = /^(margin|padding|gap|rowGap|columnGap|fontSize|lineHeight|letterSpacing|borderRadius|borderWidth|width|height|top|left|right|bottom|minHeight|minWidth|maxWidth|maxHeight)/;
const COLOR = /^(#[0-9a-f]{3,8}|rgba?\(|hsla?\()/i;
const SKIP = new Set(['node_modules', 'ios', 'android', '.git', 'build', 'dist', 'coverage', '.expo', 'Pods', 'vendor', '__mocks__']);
const TEST = /\.(test|spec|stories|story|mock|e2e)\.[jt]sx?$|\.test\.enzyme\.[jt]sx?$|(^|\/)(__tests__|__mocks__|e2e|cypress|maestro|test-utils|testing)\//;

const files = [];
const NON_APP = new Set(['scripts', 'dev-env', 'docs', 'tools', 'tooling', '__e2e__', 'e2e', 'cypress', 'maestro', 'storybook', '.storybook']);
const cfgRoots = (adapter.roots || []).map((r) => path.resolve(REPO, r));
// packages the app links to itself (link:./modules/x, file:./packages/y, workspace deps) ARE app code
const appPkg = (() => { try { return JSON.parse(fs.readFileSync(path.join(REPO, 'package.json'), 'utf8')); } catch { return {}; } })();
const LINKED = new Set(Object.values({ ...appPkg.dependencies, ...appPkg.devDependencies }).filter((v) => /^(link|file):\.{1,2}\//.test(String(v)))
  .map((v) => path.resolve(REPO, String(v).replace(/^(link|file):/, ''))));
(function w(d) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    if (e.name.startsWith('.') || SKIP.has(e.name)) continue;
    const p = path.join(d, e.name);
    // a sub-folder with its own package.json is another package in a monorepo (servers, web, tooling), not app code;
    // adapter "roots" overrides when the app itself lives in a sub-package
    if (e.isDirectory()) { if ((cfgRoots.length || LINKED.has(p) || !fs.existsSync(path.join(p, 'package.json'))) && !NON_APP.has(e.name)) w(p); }
    else if (EXTS.includes(path.extname(e.name)) && !e.name.endsWith('.d.ts')) files.push(p);
  }
})(REPO);
if (cfgRoots.length) files.splice(0, files.length, ...files.filter((f) => cfgRoots.some((r) => f.startsWith(r))));

const str = (n) => n?.type === 'StringLiteral' ? n.value : n?.type === 'TemplateLiteral' ? n.quasis.map((q) => q.value.cooked).join(':param') : null;
const text = (n) => n?.type === 'Identifier' ? n.name : n?.type === 'MemberExpression' && !n.computed ? `${text(n.object)}.${n.property.name ?? n.property.value}` : str(n) ?? null;
const calleeName = (c) => c?.type === 'Identifier' ? c.name : c?.type === 'MemberExpression' && !c.computed ? c.property.name : null;
const objName = (c) => c?.type === 'MemberExpression' ? text(c.object) : null;
const pathOf = (u) => u && u.replace(/^\w+:\/\/[^/]+/, '').split('?')[0].replace(/\/[0-9a-f-]{6,}|\/\d+/gi, '/:id');

const out = [];
const t0 = Date.now();
for (const f of files) {
  const code = fs.readFileSync(f, 'utf8');
  let ast;
  try { ast = parseCode(f, code); } catch { continue; }
  const F = { file: rel(f), loc: code.split('\n').length, test: TEST.test(rel(f)), components: [], jsx: {}, styles: { colors: {}, numbers: 0, tokenRefs: 0 },
    api: [], nav: [], linking: [], storage: [], analytics: [], flags: [], native: [], state: { reads: [], writes: 0 }, effects: [], waterfalls: [], testIDs: [], imports: [] };
  const imports = new Map();
  for (const st of ast.program.body) if (st.type === 'ImportDeclaration') {
    const target = resolveModule(f, st.source.value);
    const src = target ? rel(target) : st.source.value;
    if (!target && !st.source.value.startsWith('.')) F.imports.push(st.source.value);
    for (const s of st.specifiers) imports.set(s.local.name, src);
  }
  // components: capitalized top-level functions/arrows/classes
  for (const st of ast.program.body) {
    const d = st.type.startsWith('Export') ? st.declaration : st;
    if (!d) continue;
    const exported = st.type.startsWith('Export');
    if ((d.type === 'FunctionDeclaration' || d.type === 'ClassDeclaration') && /^[A-Z]/.test(d.id?.name || '')) F.components.push({ name: d.id.name, line: d.loc.start.line, exported });
    if (d.type === 'VariableDeclaration') for (const v of d.declarations)
      if (/^[A-Z]/.test(v.id?.name || '') && v.init && /Function|Call/.test(v.init.type)) F.components.push({ name: v.id.name, line: v.loc.start.line, exported });
  }
  const styleObject = (o) => walk(o, (n) => {
    if (n.type !== 'ObjectProperty') return;
    const key = n.key.name ?? n.key.value, v = n.value;
    if (v.type === 'StringLiteral' && COLOR.test(v.value)) F.styles.colors[v.value.toLowerCase()] = (F.styles.colors[v.value.toLowerCase()] || 0) + 1;
    else if (v.type === 'NumericLiteral' && STYLE_NUM.test(key || '') && v.value !== 0 && v.value !== 1) F.styles.numbers++;
    else if (v.type === 'MemberExpression' && /theme|token|color|spacing|palette|typography|size|radius|font/i.test(text(v) || '')) F.styles.tokenRefs++;
  });
  walk(ast.program, (n) => {
    const line = n.loc?.start.line;
    if (n.type === 'JSXOpeningElement') {
      const name = n.name.type === 'JSXIdentifier' ? n.name.name : n.name.type === 'JSXMemberExpression' ? `${n.name.object.name}.${n.name.property.name}` : null;
      if (name && /^[A-Z]/.test(name)) {
        const j = (F.jsx[name] ||= { src: imports.get(name.split('.')[0]) || 'local', count: 0, props: {} });
        j.count++;
        for (const a of n.attributes) if (a.type === 'JSXAttribute') {
          const pn = a.name.name;
          j.props[pn] = (j.props[pn] || 0) + 1;
          if (pn === 'testID' && a.value?.type === 'StringLiteral') F.testIDs.push(a.value.value);
          if (pn === 'style' && a.value?.expression?.type === 'ObjectExpression') styleObject(a.value.expression);
        }
      }
      return;
    }
    if (n.type === 'TaggedTemplateExpression' && /^(gql|graphql)$/.test(text(n.tag) || '')) {
      const m = n.quasi.quasis.map((q) => q.value.cooked).join(' ').match(/\b(query|mutation|subscription|fragment)\s+(\w+)/);
      if (m) F.api.push({ kind: n.tag.name === 'graphql' ? 'relay' : 'gql', op: m[1], target: m[2], line });
      return false;
    }
    if (n.type === 'ObjectExpression' && n.properties.some((p) => p.key?.name === 'prefixes') && n.properties.some((p) => p.key?.name === 'config')) {
      const visit = (o, prefix) => o?.properties?.forEach((p) => {
        const v = p.value;
        if (v?.type === 'StringLiteral') F.linking.push({ route: p.key.name ?? p.key.value, path: prefix + v.value, line: p.loc.start.line });
        else if (v?.type === 'ObjectExpression') {
          const pth = v.properties.find((q) => q.key?.name === 'path')?.value;
          if (pth?.type === 'StringLiteral') F.linking.push({ route: p.key.name ?? p.key.value, path: prefix + pth.value, line: p.loc.start.line });
          const sc = v.properties.find((q) => q.key?.name === 'screens')?.value;
          if (sc) visit(sc, pth?.type === 'StringLiteral' ? prefix + pth.value + '/' : prefix);
        }
      });
      const conf = n.properties.find((p) => p.key?.name === 'config')?.value;
      visit(conf?.properties?.find((p) => p.key?.name === 'screens')?.value, '');
    }
    if (n.type === 'MemberExpression' && text(n.object) === 'NativeModules' && !n.computed) F.native.push({ module: n.property.name, via: 'NativeModules', line });
    if (n.type === 'CallExpression') {
      const cn = calleeName(n.callee), on = objName(n.callee), a0 = n.arguments[0];
      if (cn === 'create' && on === 'StyleSheet' && a0?.type === 'ObjectExpression') styleObject(a0);
      else if (cn === 'fetch' && n.callee.type === 'Identifier') F.api.push({ kind: 'fetch', method: 'GET', target: pathOf(str(a0)) ?? text(a0) ?? '?', line });
      else if (n.callee.type === 'MemberExpression' && HTTP.has(cn) && on && API_CLIENT.test(on) && a0 && (str(a0) || text(a0)))
        F.api.push({ kind: 'http', client: on, method: cn.toUpperCase(), target: pathOf(str(a0)) ?? text(a0), line });
      else if (cn === 'call' && on && API_CLIENT.test(on) && a0?.type === 'MemberExpression' && (text(a0) || '').split('.').length >= 3)
        F.api.push({ kind: 'rpc', method: 'CALL', target: text(a0), line });  // XRPC / JSON-RPC: client.call(chat.bsky.convo.sendMessage, ...)
      else if (/^(useQuery|useMutation|useLazyQuery|useSubscription|useFragment|useLazyLoadQuery|usePreloadedQuery|commitMutation)$/.test(cn || '') && a0 && text(a0))
        F.api.push({ kind: 'gql-ref', op: cn, target: text(a0), line });
      else if (/^(navigate|push|replace|jumpTo|reset|popTo|navigateDeprecated)$/.test(cn || '') && n.callee.type === 'MemberExpression' && a0 && (str(a0) || text(a0))
          && (!NAV_AMBIGUOUS.test(cn) || str(a0) || NAV_RECEIVER.test(on || '')))
        F.nav.push({ call: cn, route: str(a0) ?? text(a0), line });
      else if ((((STORAGE_FN.test(cn || '') && on && STORAGE_OBJ.test(on)) || STORAGE_WRAP.has(cn) || STORAGE_WRAP.has(`${on}.${cn}`))
          || (n.callee.type === 'Identifier' && STORAGE_Q.get(cn) && imports.get(cn) === STORAGE_Q.get(cn))
          || (on && STORAGE_Q.get(on) && imports.get(on) === STORAGE_Q.get(on) && STORAGE_METHOD.test(cn || '')))
          && (str(a0) || text(a0) || a0?.type === 'ArrayExpression'))
        F.storage.push({ api: on ? `${on}.${cn}` : cn, key: str(a0) ?? (a0.type === 'ArrayExpression' ? a0.elements.map((e) => str(e) ?? text(e) ?? '?').join('.') : text(a0)), line });
      else if (ANALYTICS.has(cn) && a0 && (str(a0) || (a0.type === 'MemberExpression' && text(a0))))
        F.analytics.push({ call: on ? `${on}.${cn}` : cn, event: str(a0) ?? text(a0), line });
      else if ((FLAGS.has(cn) || FLAGS.has(`${on}.${cn}`)) && a0 && (str(a0) || text(a0)))  // qualified: ax.features.enabled
        F.flags.push({ call: cn, flag: str(a0) ?? text(a0), line });
      else if (/^(requireNativeModule|requireOptionalNativeModule|requireNativeComponent|requireNativeViewManager)$/.test(cn || '') || (on === 'TurboModuleRegistry' && /^get/.test(cn || '')))
        F.native.push({ module: str(a0) ?? text(a0) ?? '?', via: on ? `${on}.${cn}` : cn, line });
      else if (STATE_READ.test(cn || '') && n.callee.type === 'Identifier')
        F.state.reads.push({ hook: cn, arg: a0 ? (text(a0) ?? a0.type) : '', line });
      else if (cn === 'dispatch' || cn === 'setState' || (cn === 'set' && n.callee.type === 'Identifier')) F.state.writes++;
      if ((cn === 'useEffect' || cn === 'useLayoutEffect') && n.callee.type === 'Identifier') {
        const deps = n.arguments[1];
        const shape = !deps ? 'none' : deps.type !== 'ArrayExpression' ? 'expr' : !deps.elements.length ? 'empty'
          : deps.elements.some((e) => /Object|Array|Arrow|Function/.test(e?.type)) ? 'unstable' : 'list';
        let fetches = false;
        walk(a0, (x) => { if (x.type === 'CallExpression' && (calleeName(x.callee) === 'fetch' || (HTTP.has(calleeName(x.callee)) && API_CLIENT.test(objName(x.callee) || '')))) fetches = true; });
        F.effects.push({ deps: shape, fetch: fetches, line });
      }
    }
    // waterfalls: >= 2 sequential awaits of API-looking calls in one function body (not inside Promise.all)
    if (/Function/.test(n.type) && n.async && n.body?.type === 'BlockStatement') {
      const awaits = [];
      walk(n.body, (x) => {
        if (x !== n.body && /Function/.test(x.type)) return false;
        if (x.type === 'CallExpression' && text(x.callee) === 'Promise.all') return false;
        if (x.type === 'AwaitExpression' && x.argument?.type === 'CallExpression') {
          const c = x.argument.callee, nm = calleeName(c), ob = objName(c) || '';
          if (nm === 'fetch' || (HTTP.has(nm) && API_CLIENT.test(ob)) || /^(get|fetch|load|post|create|update)[A-Z]/.test(nm || '')) awaits.push(x.loc.start.line);
        }
      });
      if (awaits.length >= 2) F.waterfalls.push({ line, awaits });
    }
  });
  out.push(F);
}
const sum = (k) => out.reduce((a, f) => a + (Array.isArray(f[k]) ? f[k].length : 0), 0);
const result = { repo: REPO, generated: new Date().toISOString(), files: out.length, ms: Date.now() - t0,
  totals: { components: sum('components'), api: sum('api'), nav: sum('nav'), linking: sum('linking'), storage: sum('storage'), analytics: sum('analytics'),
    flags: sum('flags'), native: sum('native'), effects: sum('effects'), waterfalls: sum('waterfalls'), testIDs: sum('testIDs') }, facts: out };
const dest = opt('--json', path.join(REPO, '.navlab', 'facts.json'));
fs.mkdirSync(path.dirname(dest), { recursive: true });
fs.writeFileSync(dest, JSON.stringify(result));
console.log(`facts ${REPO}: ${out.length} files in ${result.ms}ms -> ${path.relative(process.cwd(), dest)}`);
console.log('  ' + Object.entries(result.totals).map(([k, v]) => `${k} ${v}`).join(' · '));
