#!/usr/bin/env python3
"""safetynet: before a redesign or migration, which journeys through the surface does an e2e suite actually pin down?

  safetynet.py <repo> --facts facts.json --navscan navscan.json [--scope depgraph.json] [--json out.json]

Finds e2e suites (Maestro yaml, Detox, Cypress, Appium, argent `.argent/flows/*.yaml`, simwalk `flows/*.mjs`), extracts
what each flow references (testIDs, visible text, deep links), follows includes (Maestro runFlow, argent run, relative
JS imports), and cross-references the app: navscan routes -> screen component file (+ its relative-import children) ->
testIDs/text there; deep links -> linking config. Reports per screen: covered by which flows and how; uncovered screens;
testIDs in scope no flow touches; flow ids that no longer exist in code (stale); flows that are skipped or never run in CI.
Read-only on the repo.
"""
import json, os, re, sys

SKIP_DIRS = {'node_modules', '.git', 'Pods', 'build', 'dist', 'coverage', '.expo', 'vendor', '.gradle', 'DerivedData'}
EXTS = ['.tsx', '.ts', '.jsx', '.js', '.ios.tsx', '.android.tsx', '.native.tsx']
SHARED_MAX = 3  # an id from shared code counts (weakly) for a screen only if <= this many analysed screens render it
TEXT_MAX = 2  # a flow's visible-text ref identifies a screen only if <= this many source files show it
URL = re.compile(r'^[a-z][\w+.-]*://\S+$', re.I)
TESTID_PROP = re.compile(r'''\b(?:[a-z]\w*)?[tT]est[Ii][Dd]\w*\s*[=:]\s*\{?\s*(['"`])([^'"`\n]+)\1''')


def read(p):
    try:
        return open(p, encoding='utf-8', errors='replace').read()
    except OSError:
        return ''


def strip_js_comments(src):
    src = re.sub(r'/\*.*?\*/', lambda m: '\n' * m.group().count('\n'), src, flags=re.S)
    return re.sub(r'(^|[\s;{}(])//.*$', r'\1', src, flags=re.M)  # keeps scheme:// inside strings


def lineno(src, pos):
    return src.count('\n', 0, pos) + 1


def pattern(s):
    """'nav-tab-${id}' -> compiled regex; None when not a template or too generic (< 4 literal chars, e.g. `${id}`)."""
    if '${' not in s or len(re.sub(r'\$\{[^}]*\}', '', s)) < 4:
        return None
    return re.compile('^' + re.sub(r'\\\$\\\{[^}]*\\\}', '.+', re.escape(s)) + '$')


# ---------------------------------------------------------------- suites
def discover(repo):
    out = []
    for d, dirs, files in os.walk(repo):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not (x.startswith('.') and x not in ('.maestro', '.argent'))]
        rel = os.path.relpath(d, repo)
        parts = set(rel.split(os.sep))
        for f in files:
            p = os.path.join(rel, f) if rel != '.' else f
            if f.endswith(('.yaml', '.yml')):
                if '.argent' in parts and 'flows' in parts:
                    out.append(('argent', p))
                elif re.search(r'^appId:', read(os.path.join(repo, p)), re.M) or 'maestro' in parts or '.maestro' in parts:
                    if not f.startswith('config.'):
                        out.append(('maestro', p))
            elif re.search(r'\.(m?js|tsx?)$', f) and not f.endswith('.d.ts'):
                src = None
                if 'cypress' in parts or re.search(r'\.cy\.[jt]sx?$', f):
                    out.append(('cypress', p))
                elif parts & {'appium', 'wdio'}:
                    out.append(('appium', p))
                elif 'e2e' in parts:
                    src = read(os.path.join(repo, p))
                    if re.search(r'\bby\.(id|text|label)\(|\bdevice\.|from [\'"]detox', src):
                        out.append(('detox', p))
                    elif re.search(r'AppiumBy|accessibilityId\(|\$\([\'"]~', src):
                        out.append(('appium', p))
                elif f.endswith('.mjs') and os.path.basename(rel) == 'flows':
                    src = read(os.path.join(repo, p))
                    if re.search(r'export default async function', src) and 'step(' in src:
                        out.append(('simwalk', p))
    return out


def resolve_js(base_dir, spec, repo):
    for cand in [spec] + [spec + e for e in EXTS] + [os.path.join(spec, 'index' + e) for e in EXTS]:
        p = os.path.normpath(os.path.join(base_dir, cand))
        if os.path.isfile(os.path.join(repo, p)):
            return p
    return None


def extract(kind, path, repo):
    """-> refs [{t: id|text|link, v, line, pat}], includes [path], skipped bool, tags [str]"""
    raw = read(os.path.join(repo, path))
    refs, inc, tags = [], [], []
    add = lambda t, v, ln: v and refs.append({'t': t, 'v': v.strip(), 'line': ln})
    base = os.path.dirname(path)
    if kind in ('maestro', 'argent'):
        lines = raw.split('\n')
        in_tags = False
        for i, l in enumerate(lines, 1):
            s = l.split(' #')[0] if not l.lstrip().startswith('#') else ''
            if not s.strip():
                continue
            if re.match(r'^tags:\s*$', s):
                in_tags = True
                continue
            if in_tags:
                m = re.match(r'^\s*-\s*(\S+)', s)
                if m and not s.startswith('- ') and s.strip() != '---':
                    tags.append(m.group(1).strip('\'"'))
                    continue
                in_tags = False
            val = lambda g: g.strip().strip('\'"')
            if m := re.search(r'\bid:\s*(.+?)\s*[,}]?\s*$', s):
                add('id', val(m.group(1).split(',')[0].rstrip('}')), i)
            if m := re.search(r'^\s*-\s*(?:tapOn|assertVisible|doubleTapOn|longPressOn|scrollUntilVisible|tap|copyTextFrom):\s*(\S.*)$', s):
                if not m.group(1).startswith('{'):
                    add('text', val(m.group(1)), i)
            if m := re.search(r'\b(?:text|visible|label):\s*([\'"]?)([^{\n][^\n]*?)\1\s*[,}]?\s*$', s):
                if not re.search(r'\bid:', s):
                    add('text', val(m.group(2).split(',')[0].rstrip('}')), i)
            if m := re.search(r'\b(?:openLink|link|url):\s*[\'"]?([a-z][\w+.-]*://[^\s\'"]+)', s):
                add('link', m.group(1), i)
            if m := re.search(r'\b(?:runFlow|file|run):\s*[\'"]?([\w./-]+\.ya?ml)', s):
                inc.append(os.path.normpath(os.path.join(base, m.group(1))))
        return refs, inc, False, tags
    src = strip_js_comments(raw)
    consts = {}
    for m in re.finditer(r'\bconst\s+(\w+)\s*=\s*([\'"`])([^\'"`\n]*)\2', src):
        consts[m.group(1)] = re.sub(r'\$\{(\w+)\}', lambda x: consts.get(x.group(1), x.group(0)), m.group(3))
    S = r'([\'"`])((?:(?!\1).)+)\1'
    sel = {'detox': [('id', r'\bby\.id\(\s*' + S), ('text', r'\bby\.(?:text|label)\(\s*' + S)],
           'cypress': [('id', r'data-test(?:id|-id)?=[\'"]?([^\'"\]]+)'), ('id', r'findByTestId\(\s*' + S),
                       ('text', r'\bcy\.contains\(\s*' + S)],
           'appium': [('id', r'\$\(\s*[\'"]~([^\'"]+)'), ('id', r'(?:accessibilityId|ACCESSIBILITY_ID,|By\.id)\(?\s*' + S)],
           'simwalk': [('id', r'\{\s*id:\s*' + S), ('text', r'\{\s*label:\s*' + S), ('text', r'\b(?:d\.tap|d\.waitFor|until:)\(?\s*' + S)]}
    for t, rx in sel.get(kind, []):
        for m in re.finditer(rx, src):
            add(t, m.groups()[-1], lineno(src, m.start()))
    for m in re.finditer(r'([\'"`])([a-z][\w+.-]*://[^\'"`\s]+)\1', src):
        add('link', re.sub(r'\$\{(\w+)\}', lambda x: consts.get(x.group(1), x.group(0)), m.group(2)), lineno(src, m.start()))
    for m in re.finditer(r'\burl:\s*(\w+)\b', src):
        if m.group(1) in consts:
            add('link', consts[m.group(1)], lineno(src, m.start()))
    for m in re.finditer(r'from\s+[\'"](\.{1,2}/[^\'"]+)[\'"]', src):
        if p := resolve_js(base, m.group(1), repo):
            inc.append(p)
    # ponytail: whole-file skip only; it.skip / xit inside an active file still count as covering
    skipped = bool(re.search(r'\b(?:describe\.skip|xdescribe)\(', src))
    for r in refs:
        if r['t'] == 'link':
            r['v'] = re.sub(r'\$\{(\w+)\}', lambda x: consts.get(x.group(1), x.group(0)), r['v'])
    return refs, inc, skipped, tags


def ci_info(repo):
    """Text of CI configs + package scripts, and the Maestro tags CI selects (None = CI doesn't filter by tag)."""
    txt = ''
    for d in ['.github/workflows', '.circleci', '.buildkite', '.gitlab', 'fastlane']:
        for root, _, fs in os.walk(os.path.join(repo, d)):
            txt += ''.join(read(os.path.join(root, f)) for f in fs if f.endswith(('.yml', '.yaml', 'Fastfile')))
    for f in ['bitrise.yml', '.gitlab-ci.yml', 'codemagic.yaml', 'eas.json']:
        txt += read(os.path.join(repo, f))
    jobs = []  # Maestro CI jobs as [include, exclude]; an exclude belongs to the include declared just before it
    for m in re.finditer(r'(\bTAGS\s*=\s*|--include-tags[= ]|EXCLUDE_?TAGS\s*=\s*|--exclude-tags[= ])[\'"]?([\w,-]+|\$\S+)', txt):
        vals = {'*'} if m.group(2).startswith('$') else set(m.group(2).split(','))  # $VAR / ${{ inputs.tags }}: chosen at dispatch
        if 'EXCLUDE' in m.group(1).upper():
            if jobs:
                jobs[-1][1] |= vals
        else:
            jobs.append([vals, set()])
    pkg = json.loads(read(os.path.join(repo, 'package.json')) or '{}').get('scripts', {})
    detox_scripts = [k for k, v in pkg.items() if 'detox' in v]
    return {'maestro': 'maestro' in txt.lower(), 'maestro_jobs': jobs or None,
            'detox': 'detox' in txt.lower() or any(re.search(r'\b' + re.escape(k) + r'\b', txt) for k in detox_scripts),
            'cypress': 'cypress' in txt.lower(), 'appium': 'appium' in txt.lower(), 'argent': False, 'simwalk': False}


# ---------------------------------------------------------------- app side
class App:
    def __init__(self, repo, facts):
        self.repo = repo
        self.facts = {f['file']: f for f in facts['facts']}
        self.src = {}
        by_stem = {}
        for f in self.facts:
            stem = re.sub(r'(\.(ios|android|native|web))?\.[jt]sx?$', '', f)
            by_stem.setdefault(stem, []).append(f)
            if stem.endswith('/index'):
                by_stem.setdefault(stem[:-6], []).append(f)
        self.by_stem = by_stem
        self.ids = {}       # literal testID -> [file:line]
        self.id_pats = []   # (regex, 'file:line', template)
        for f, F in self.facts.items():
            if F.get('test'):
                continue
            s = self.source(f)
            for tid in F.get('testIDs', []):
                self.ids.setdefault(tid, []).append(f'{f}:{self.line_of(s, tid)}')
            for m in TESTID_PROP.finditer(s):  # non-StringLiteral forms facts skips: testID={'x'}, {`x-${y}`}, fooTestID="x"
                v = m.group(2)
                loc = f'{f}:{lineno(s, m.start())}'
                if '${' in v:
                    if rx := pattern(v):
                        self.id_pats.append((rx, loc, v))
                elif loc not in self.ids.get(v, []) and not any(x.startswith(f + ':') for x in self.ids.get(v, [])):
                    self.ids.setdefault(v, []).append(loc)

    def text_files(self, t):
        """how many app source files show this text; generic copy ('Continue', 'Save') identifies no screen."""
        if not hasattr(self, '_tf'):
            self._tf = {}
        if t not in self._tf:
            rx = re.compile(r'[>"\'`]\s*' + re.escape(t) + r'\s*[<"\'`]')
            self._tf[t] = sum(1 for f, F in self.facts.items() if not F.get('test') and t in self.source(f) and rx.search(self.source(f)))
        return self._tf[t]

    def source(self, f):
        if f not in self.src:
            self.src[f] = read(os.path.join(self.repo, f))
        return self.src[f]

    @staticmethod
    def line_of(s, needle):
        for q in ('"', "'", '`'):
            i = s.find(q + needle + q)
            if i >= 0:
                return lineno(s, i)
        return 0

    def id_loc(self, v):
        """where a flow id lives in code: ('literal'|'template'|'string'|None, loc)"""
        if v in self.ids:
            return 'literal', self.ids[v][0]
        for rx, loc, _ in self.id_pats:
            if rx.match(v):
                return 'template', loc
        for f, F in self.facts.items():  # ponytail: linear scan of all sources per miss; fine for a few hundred misses
            if not F.get('test') and (line := self.line_of(self.source(f), v)):
                return 'string', f'{f}:{line}'
        return None, None

    def resolve_spec(self, f, spec):
        if spec.startswith('.'):
            stem = os.path.normpath(os.path.join(os.path.dirname(f), spec))
            return self.by_stem[stem][0] if stem in self.by_stem else None
        segs = spec.lstrip('@~/').split('/')
        for k in range(len(segs)):  # alias: match the longest path suffix (tsconfig paths / babel aliases)
            tail = '/'.join(segs[k:])
            hits = [stem for stem in self.by_stem if stem == tail or stem.endswith('/' + tail)]
            if len(hits) == 1:
                return self.by_stem[hits[0]][0]
            if hits:
                return None
        return None

    def resolve_import(self, f, ident):
        s = self.source(f)
        m = (re.search(r'import\s+' + ident + r'\b[^;]*?from\s+[\'"]([^\'"]+)', s)
             or re.search(r'import\s*(?:\w+\s*,\s*)?\{[^}]*\b(?:\w+\s+as\s+)?' + ident + r'\b[^}]*\}\s*from\s+[\'"]([^\'"]+)', s))
        if m and (hit := self.resolve_spec(f, m.group(1))):
            if os.path.basename(hit).startswith('index.'):  # barrel: follow `export { X } from` / `export * from` one level
                b = self.source(hit)
                r = (re.search(r'export\s*\{[^}]*\b' + ident + r'\b[^}]*\}\s*from\s*[\'"]([^\'"]+)', b)
                     or re.search(r'export\s+(?:default|\{\s*default\s*\})\s+from\s*[\'"]([^\'"]+)', b))
                for spec in [r.group(1)] if r else re.findall(r'export\s*\*\s*from\s*[\'"]([^\'"]+)', b):
                    y = self.resolve_spec(hit, spec)
                    if y and (r or re.search(r'export\s+(?:default\s+)?(?:function|const|class)\s+' + ident + r'\b', self.source(y))):
                        return y
            return hit
        named = [x for x, F in self.facts.items() if not F.get('test') and any(c['name'] == ident and c.get('exported') for c in F.get('components', []))]
        return named[0] if len(named) == 1 else None

    def closure(self, f, owned_edges=None, stop=(), depth=2):
        """screen file + its private children: relative imports, plus (with --scope) any import into a feature-owned file."""
        seen, todo = {f}, [(f, 0)]
        while todo:
            x, d = todo.pop()
            if d >= depth:
                continue
            nxt = [y for m in re.finditer(r'from\s+[\'"](\.{1,2}/[^\'"]+)[\'"]', self.source(x))
                   for y in self.by_stem.get(os.path.normpath(os.path.join(os.path.dirname(x), m.group(1))), [])[:1]]
            for y in nxt + (owned_edges or {}).get(x, []):
                if y not in seen and y in self.facts and not self.facts[y].get('test') and y not in stop:  # stop at other screens
                    seen.add(y)
                    todo.append((y, d + 1))
        return sorted(seen)

    def screen_elems(self, f):
        """every <X.Screen name=.. component=..> in a file -> {route: (line, [component files])}"""
        s, out = self.source(f), {}
        elems = list(re.finditer(r'<\w+\.Screen\b', s))
        for i, m in enumerate(elems):
            body = s[m.start(): elems[i + 1].start() if i + 1 < len(elems) else len(s)]
            nm = re.search(r'\bname=\{?\s*([\'"]?)([\w.$-]+)\1', body)
            if not nm:
                continue
            comp = re.search(r'\b(?:component|getComponent)=\{([^}]*)\}', body)
            files = []
            for ident in re.findall(r'\b[A-Z]\w*\b', comp.group(1) if comp else body.split('>')[0]):
                if (cf := self.resolve_import(f, ident)) and cf not in files:
                    files.append(cf)
            out.setdefault(nm.group(2), (lineno(s, m.start()), files, f))
        return out

    def screens(self, navscan):
        nav_files = {n['id'].rsplit(':', 1)[0] for n in navscan['tree']}
        # screens can be declared in helper files spread into a navigator (navscan merges them); index every file once
        index = {}
        for f, F in self.facts.items():
            if not F.get('test') and '.Screen' in self.source(f):
                for route, v in self.screen_elems(f).items():
                    index.setdefault(route, []).append(v)
        out = []
        for n in navscan['tree']:
            nf = n['id'].rsplit(':', 1)[0]
            own = self.screen_elems(nf)
            for route in n.get('screens', []):
                line, files, at = own.get(route) or (index.get(route) or [(0, [], nf)])[0]
                out.append({'route': route, 'navigator': n['id'], 'declared': f'{at}:{line}', 'line': line, 'reached': n.get('reached', True),
                            'files': files,
                            # ponytail: unresolved component -> guess navigator by route name (…NAVIGATOR/…STACK/…TABS)
                            'is_navigator': any(x in nav_files for x in files) or (not files and bool(re.search(r'(NAVIGATOR|STACK|TABS?)$', route, re.I)))})
        return out

    def linking(self):
        links = [{'route': l['route'], 'path': l['path'], 'at': f"{f}:{l['line']}"} for f, F in self.facts.items() for l in F.get('linking', [])]
        if links:
            return links
        # ponytail: facts misses computed keys `[Routes.X]: { path: 'p' }`; regex fallback over files that look like a linking config
        for f, F in self.facts.items():
            s = self.source(f)
            if F.get('test') or 'path:' not in s or 'screens' not in s:
                continue
            for m in re.finditer(r'\[?([\w.]+)\]?\s*:\s*\{\s*path\s*:\s*[\'"]([^\'"]*)[\'"]', s):
                links.append({'route': m.group(1), 'path': m.group(2), 'at': f'{f}:{lineno(s, m.start())}'})
        return links


def match_link(url, links):
    """longest linking path that the URL path ends with (':param' = one segment, ':param?' = optional)."""
    path = '/' + re.sub(r'^[a-z][\w+.-]*://[^/]*', '', url).split('?')[0].strip('/')
    best = None
    for l in links:
        segs = [s for s in l['path'].strip('/').split('/') if s]
        rx = ''.join('(?:/[^/]+)?' if g.startswith(':') and g.endswith('?') else '/[^/]+' if g.startswith(':') else '/' + re.escape(g) for g in segs)
        if segs and re.search(rx + '$', path) and (not best or len(l['path']) > len(best['path'])):
            best = l
    return best


# ---------------------------------------------------------------- main
def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    repo = os.path.abspath(a[0])
    facts, navscan = json.load(open(opt('--facts'))), json.load(open(opt('--navscan')))
    scope = json.load(open(opt('--scope'))) if opt('--scope') else None
    app = App(repo, facts)
    ci = ci_info(repo)

    suites = discover(repo)
    flows = {}
    for kind, p in suites:
        refs, inc, skipped, tags = extract(kind, p, repo)
        flows[p] = {'kind': kind, 'refs': refs, 'includes': inc, 'skipped': skipped, 'tags': tags}
    included = {i for f in flows.values() for i in f['includes']}
    for p, f in flows.items():
        f['helper'] = p in included and not f['tags']
        if f['kind'] == 'maestro' and ci['maestro_jobs'] is not None:
            runs = [inc for inc, exc in ci['maestro_jobs'] if (set(f['tags']) & inc or '*' in inc) and not set(f['tags']) & exc]
            f['ci'] = True if any('*' not in inc for inc in runs) else 'manual' if runs else False
        else:
            f['ci'] = ci.get(f['kind'])

    def all_refs(p, seen=()):  # own refs + included files' refs (transitive)
        if p in seen or p not in flows:
            return []
        return flows[p]['refs'] + [r for i in flows[p]['includes'] for r in all_refs(i, seen + (p,))]

    # stale ids + id resolution
    stale, native_ids = [], set()
    for p, f in flows.items():
        for r in f['refs']:
            if r['t'] != 'id':
                continue
            if re.search(r':id/|^android:|^com\.', r['v']):
                native_ids.add(r['v'])
                r['kind'] = 'native'
                continue
            if '${' in r['v']:
                r['kind'], r['pat'] = 'pattern', pattern(r['v'])
                if not r['pat']:
                    r['t'] = 'opaque-id'
                continue
            r['kind'], r['loc'] = app.id_loc(r['v'])
            if r['kind'] is None:
                stale.append({'flow': f'{p}:{r["line"]}', 'id': r['v'], 'skipped': f['skipped']})

    # app-facing check (Cypress often drives a web dashboard / mail catcher, not the app)
    links = app.linking()
    for p, f in flows.items():
        rs = all_refs(p)
        f['app_facing'] = any(r.get('kind') in ('literal', 'template', 'string') for r in rs) or any(
            r['t'] == 'link' and match_link(r['v'], links) for r in rs) or f['helper'] or f['kind'] != 'cypress'

    # screens in scope
    all_screens = app.screens(navscan)
    screen_files = {f for s in all_screens for f in s['files']}
    nav_files = {n['id'].rsplit(':', 1)[0] for n in navscan['tree']}
    screens = [s for s in all_screens if not s['is_navigator']]
    # one row per route: apps that register a shared screen list in every tab repeat each route per stack
    seen_routes, uniq = set(), []
    for s in screens:
        if s['route'] not in seen_routes:
            seen_routes.add(s['route']); uniq.append(s)
    screens = uniq
    if scope:
        owned = {o['file'] for o in scope['owned']} | set(scope['entries'])
        screens = [s for s in screens if s['navigator'].rsplit(':', 1)[0] in owned or set(s['files']) & owned]
        scope_files = owned
        owned_edges = {}
        for a_, b_ in scope.get('edges', []):
            if b_ in owned:
                owned_edges.setdefault(a_, []).append(b_)
    else:
        scope_files, owned_edges = {f for f, F in app.facts.items() if not F.get('test')}, None

    entry_flows = [p for p, f in flows.items() if not f['helper'] and f['app_facing']]
    rows, pre = [], []
    for s in screens:
        cl = sorted({x for cf in s['files'] for x in app.closure(cf, owned_edges, screen_files - set(s['files']))})
        ids = {tid for x in cl for tid in app.facts[x].get('testIDs', [])} | {v for v, locs in app.ids.items() for loc in locs if loc.rsplit(':', 1)[0] in cl}
        pats = [(rx, t) for rx, loc, t in app.id_pats if loc.rsplit(':', 1)[0] in cl]
        # one hop into shared code the screen renders (alias imports): those ids exist on other screens too -> weak only
        stop = screen_files | nav_files
        shared = {y for x in cl for m in re.finditer(r'from\s+[\'"]([^\'".][^\'"]*)[\'"]', app.source(x))
                  if (y := app.resolve_spec(x, m.group(1))) and y not in cl and y not in stop and not app.facts[y].get('test')}
        ids_shared = {v for v, locs in app.ids.items() for loc in locs if loc.rsplit(':', 1)[0] in shared} - ids
        pre.append((s, cl, ids, pats, ids_shared))
    # a shared id rendered by > SHARED_MAX analysed screens (a header back button) identifies none of them
    freq = {}
    for *_, sh in pre:
        for v in sh:
            freq[v] = freq.get(v, 0) + 1
    for s, cl, ids, pats, ids_shared in pre:
        ids_shared = {v for v in ids_shared if freq[v] <= SHARED_MAX}
        text = '\n'.join(app.source(x) for x in cl)
        dl = [l for l in links if l['route'] == s['route']]
        hits = []
        for p in entry_flows:
            how = set()
            for r in all_refs(p):
                # 'id~' = weak: a template on either side, or a generic id defined in > TEXT_MAX files ('continue-button')
                if r['t'] == 'id' and r['v'] in ids:
                    how.add(('id:' if len({l.rsplit(':', 1)[0] for l in app.ids[r['v']]}) <= TEXT_MAX else 'id~') + r['v'])
                elif r['t'] == 'id' and (r['v'] in ids_shared or any(rx.match(r['v']) for rx, _ in pats) or (r.get('pat') and any(r['pat'].match(i) for i in ids))):
                    how.add('id~' + r['v'])
                elif r['t'] == 'text' and len(r['v']) >= 4 and '${' not in r['v'] and re.search(r'[>"\'`]\s*' + re.escape(r['v']) + r'\s*[<"\'`]', text):
                    # ponytail: distinctive = shown by <= TEXT_MAX files app-wide; i18n keys / server copy are invisible here
                    if app.text_files(r['v']) <= TEXT_MAX:
                        how.add('text:' + r['v'])
                elif r['t'] == 'link' and (l := match_link(r['v'], links)) and l['route'] == s['route']:
                    how.add('link:' + l['path'])
            if how:
                hits.append({'flow': p, 'kind': flows[p]['kind'], 'skipped': flows[p]['skipped'], 'ci': flows[p]['ci'], 'how': sorted(how, key=lambda x: (x.startswith('id~'), x))})
        strong = [h for h in hits if not h['skipped'] and any(not x.startswith('id~') for x in h['how'])]
        status = 'covered' if strong else 'weak' if any(not h['skipped'] for h in hits) else 'skipped-only' if hits else 'uncovered'
        rows.append({**s, 'closure': cl, 'testIDs': sorted(ids), 'shared_testIDs': sorted(ids_shared), 'deeplinks': [l['path'] for l in dl], 'flows': hits, 'status': status,
                     'ci_covered': any(h['ci'] is True for h in strong)})

    used = {r['v'] for p in flows for r in flows[p]['refs'] if r['t'] == 'id' and not flows[p]['skipped']}
    used_pats = [r['pat'] for p in flows for r in flows[p]['refs'] if r.get('pat') and not flows[p]['skipped']]
    unused = sorted({(v, locs[0]) for v, locs in app.ids.items() if any(l.rsplit(':', 1)[0] in scope_files for l in locs)
                     and v not in used and not any(px.match(v) for px in used_pats)})

    gaps = []
    for nav in sorted({r['navigator'] for r in rows}):
        miss = [r for r in rows if r['navigator'] == nav and r['status'] != 'covered']
        if miss:
            gaps.append({'navigator': nav, 'screens': [{'route': r['route'], 'status': r['status'], 'file': (r['files'] or ['?'])[0],
                                                        'entry_deeplink': (r['deeplinks'] or [None])[0], 'selectors': r['testIDs'][:6], 'shared_selectors': r['shared_testIDs'][:6]} for r in miss]})

    ci['maestro_jobs'] = ci['maestro_jobs'] and [[sorted(i), sorted(e)] for i, e in ci['maestro_jobs']]
    out = {'repo': repo, 'scope': scope and scope['entries'], 'ci': ci,
           'suites': {p: {k: v for k, v in f.items() if k != 'refs'} | {'refs': len(f['refs'])} for p, f in flows.items()},
           'screens': [{k: v for k, v in r.items() if k != 'closure'} for r in rows],
           'unused_testIDs': [{'id': v, 'at': l} for v, l in unused], 'stale': stale, 'native_ids': sorted(native_ids), 'gaps': gaps}
    report(out, flows)
    if opt('--json'):
        os.makedirs(os.path.dirname(os.path.abspath(opt('--json'))), exist_ok=True)
        json.dump(out, open(opt('--json'), 'w'), indent=1, default=str)
        print(f"\n-> {opt('--json')}")


def report(o, flows):
    by = {}
    for p, f in flows.items():
        by.setdefault(f['kind'], []).append(f)
    print(f"safety-net {os.path.basename(o['repo'])}" + (f"  scope: {', '.join(o['scope'])}" if o['scope'] else '  scope: whole app'))
    for k, fs in sorted(by.items()):
        print(f"  {k:<8} {len(fs):>3} files: {sum(not f['helper'] for f in fs)} flows, {sum(f['helper'] for f in fs)} included helpers, "
              f"{sum(f['skipped'] for f in fs)} skipped, {sum(not f['app_facing'] for f in fs)} not app-facing, "
              f"CI {str(sum(f['ci'] is True for f in fs if not f['helper'])) + ' flows selected by ' + str(len(o['ci']['maestro_jobs'])) + ' tag jobs' if k == 'maestro' and o['ci']['maestro_jobs'] else o['ci'].get(k)}")
    if not flows:
        print('  no e2e suites found (Maestro, Detox, Cypress, Appium, argent, simwalk): every screen below is unprotected')
    S = o['screens']
    c = lambda st: sum(r['status'] == st for r in S)
    print(f"\n{len(S)} screens: {c('covered')} covered by an active flow ({sum(r['ci_covered'] for r in S)} of them by a flow CI runs), "
          f"{c('weak')} weak (shared/generic/template ids only), {c('skipped-only')} only by skipped suites, {c('uncovered')} uncovered")
    print(f"\n{'status':<12} {'route':<46} {'ids':>3}  flows (how)")
    for r in sorted(S, key=lambda r: (r['status'] != 'covered', r['navigator'], r['line'])):
        fl = '; '.join(f"{os.path.basename(h['flow'])}{' [skip]' if h['skipped'] else ''}{'' if h['ci'] is True else ' [CI: manual tags]' if h['ci'] == 'manual' else ' [no CI]'} "
                       f"({', '.join(h['how'][:3])}{' +' + str(len(h['how']) - 3) if len(h['how']) > 3 else ''})" for h in r['flows'][:3])
        print(f"{r['status']:<12} {r['route'][:46]:<46} {len(r['testIDs']):>3}  {fl or '-'}"
              + ('' if r['files'] else '   (component file unresolved)'))
    print(f"\ntestIDs in scope never used by an active flow: {len(o['unused_testIDs'])}")
    for u in o['unused_testIDs'][:15]:
        print(f"  {u['id']:<50} {u['at']}")
    print(f"\nstale flow refs (id not found in code): {len(o['stale'])}")
    for s in o['stale'][:20]:
        print(f"  {s['id']:<50} {s['flow']}{' [skipped suite]' if s['skipped'] else ''}")
    if o['native_ids']:
        print(f"  ({len(o['native_ids'])} native resource ids skipped, e.g. {o['native_ids'][0]})")
    print(f"\ngaps -> argent-qa-flows ({sum(len(g['screens']) for g in o['gaps'])} screens in {len(o['gaps'])} navigators)")
    for g in o['gaps']:
        print(f"  {g['navigator']}")
        for s in g['screens'][:8]:
            print(f"    {s['route']:<46} {s['status']:<12} entry: {s['entry_deeplink'] or 'walk'}  selectors: {', '.join(s['selectors'][:3]) or ('shared only: ' + ', '.join(s['shared_selectors'][:2]) if s['shared_selectors'] else 'NONE: add testIDs first')}")
        if len(g['screens']) > 8:
            print(f"    ... +{len(g['screens']) - 8} more (see --json)")


def selftest():
    links = [{'route': 'R.CREDIT', 'path': 'account/credit-limit'}, {'route': 'R.ACCOUNT', 'path': 'account/:action?'},
             {'route': 'R.TX', 'path': 'tx/:id'}]
    assert match_link('acme://example.com/cards/app-link/account/credit-limit', links)['route'] == 'R.CREDIT'  # longest wins
    assert match_link('acme://example.com/account', links)['route'] == 'R.ACCOUNT'                           # optional param
    assert match_link('https://example.com/tx/42?x=1', links)['route'] == 'R.TX'
    assert match_link('acme://example.com/nope', links) is None
    assert pattern('nav-tab-${id}').match('nav-tab-repay') and pattern('${a}-${b}') is None and pattern('plain') is None
    print('selftest ok')


if __name__ == '__main__':
    selftest() if sys.argv[1:2] == ['--selftest'] else main()
