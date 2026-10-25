#!/usr/bin/env python3
"""ds: design system and component evidence: inventory, near-duplicates, token adherence, RN <-> native parity.

  ds.py <facts.json> [--ds src/components,src/styleguide] [--scope depgraph.json] [--native-ds <dir with .swift>] [--json out]

- inventory: every component rendered, where it is defined (design system / app / library / RN primitive), uses, files, props.
- families: components of the same kind (last CamelCase word: Button, Card, Input, Modal, Header, Row, Text...) with
  overlapping prop sets: the "seven buttons" problem. Overlap = Jaccard of prop names seen at call sites.
- tokens: per file literal colors / magic numbers vs theme/token references; palette drift = distinct literal colors.
- parity (--native-ds): SwiftUI `struct X: View` / UIKit `class X: UIView` names matched to RN design-system components by
  normalized name; the RN components a migration surface uses with no native counterpart are the port list.
- --scope: limit inventory/parity to what one feature renders (what would have to exist natively to migrate it).
Design-system roots default to adapter facts.designSystem, else dirs named components|ui|design-system|styleguide|theme.
"""
import json, os, re, sys
from collections import Counter, defaultdict

KIND = re.compile(r'(Button|Card|Input|TextInput|Field|Modal|Sheet|Header|Footer|Row|Cell|Item|List|Text|Title|Label|Badge|Chip|Tag|Avatar|Icon|Image|Toggle|Switch|Checkbox|Radio|Tab|Tabs|Banner|Toast|Alert|Dialog|Loader|Spinner|Skeleton|Divider|Screen|Layout|Container|Wrapper|Pill|Link|Menu|Picker|Select|Slider)s?$')
DS_PKG = re.compile(r'(^|[/@-])(ui|design|designsystem|design-system|ds|kit|components|primitives|tokens)([/-]|$)', re.I)
DS_DIRS = re.compile(r'(^|/)(components|ui|design-system|designsystem|styleguide|theme|primitives|atoms|molecules)(/|$)', re.I)
NORM = re.compile(r'^(App|Base|Custom|Styled|Common|Shared|Core|Ds|DS|New|Old|My|Primary|Secondary|Tertiary|Default|Main)|(V\d+|Component|View|Container)$')


def norm(name):
    n = name
    for _ in range(3):
        n = NORM.sub('', n) or n
    return n.lower()


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    facts = json.load(open(a[0]))
    repo = facts['repo']
    adapter_p = os.path.join(repo, 'navlab.adapter.json')
    ds_roots = (opt('--ds') or ','.join((json.load(open(adapter_p)).get('facts', {}) if os.path.exists(adapter_p) else {}).get('designSystem', []))).split(',')
    ds_roots = [r.strip().rstrip('/') for r in ds_roots if r.strip()]
    is_ds = (lambda src: any(src == r or src.startswith(r + '/') for r in ds_roots)) if ds_roots else (lambda src: bool(DS_DIRS.search(src)))
    scope = {o['file'] for o in json.load(open(opt('--scope')))['owned']} if '--scope' in a else None
    files = [f for f in facts['facts'] if not f.get('test')]
    declared = {}
    for f in files:
        for c in f['components']:
            declared.setdefault(c['name'], f"{f['file']}:{c['line']}")

    # inventory: keyed by (name, where it really comes from); one name can be several components
    decl_files = defaultdict(set)
    for f in files:
        for c in f['components']:
            decl_files[c['name']].add(f['file'])
    def source(name, src):
        if src in ('local', None):
            src = (declared.get(name) or '').split(':')[0] or 'local'
        elif '/' in src and src.endswith(('.tsx', '.ts', '.js', '.jsx')) and src not in decl_files.get(name, ()):  # re-exported
            homes = sorted(decl_files.get(name, ()))
            src = homes[0] if len(homes) == 1 else src
        return src
    def origin(src):
        if src == 'react-native':
            return 'rn-primitive'
        if not src.endswith(('.tsx', '.ts', '.js', '.jsx')) and src != 'local':
            return 'design-system' if (is_ds(src) if ds_roots else DS_PKG.search(src)) else 'library'
        return 'design-system' if is_ds(src) else 'app'
    inv = defaultdict(lambda: {'uses': 0, 'files': set(), 'props': Counter()})
    for f in files:
        if scope is not None and f['file'] not in scope:
            continue
        for name, j in f['jsx'].items():
            if '.' in name:
                continue
            e = inv[(name, source(name, j['src']))]
            e['uses'] += j['count']
            e['files'].add(f['file'])
            e['props'].update(j['props'])
    rows = sorted(({'component': n, 'origin': origin(src), 'src': src, 'uses': e['uses'], 'files': len(e['files']),
                    'props': [p for p, _ in e['props'].most_common(8)]} for (n, src), e in inv.items()), key=lambda r: -r['uses'])
    by_origin = Counter(r['origin'] for r in rows)
    # the same component name from several sources: competing design systems / migrations in progress
    names = defaultdict(list)
    for r in rows:
        names[r['component']].append(r)
    competing = sorted(([n, rs] for n, rs in names.items() if len(rs) > 1 and sum(r['origin'] != 'rn-primitive' for r in rs) >= 1),
                       key=lambda x: -sum(r['uses'] for r in x[1]))

    # families of near-duplicates (design system + app components only)
    fam = defaultdict(list)
    for r in rows:
        if r['origin'] in ('design-system', 'app'):
            m = KIND.search(r['component'])
            if m:
                fam[m.group(1).rstrip('s')].append(r)
    families = []
    for kind, rs in fam.items():
        if len(rs) < 3:
            continue
        pairs = []
        for i, x in enumerate(rs):
            for y in rs[i + 1:]:
                px, py = set(inv[(x['component'], x['src'])]['props']), set(inv[(y['component'], y['src'])]['props'])
                if min(len(px), len(py)) < 2:
                    continue
                j = len(px & py) / len(px | py) if px | py else 0
                if j >= 0.5:
                    pairs.append((round(j, 2), x['component'], y['component']))
        families.append({'kind': kind, 'members': [r['component'] for r in rs], 'uses': sum(r['uses'] for r in rs), 'overlapping': sorted(pairs, reverse=True)[:6]})
    families.sort(key=lambda f: -len(f['members']))

    # token adherence
    lit = tok = num = 0
    palette = Counter()
    worst = []
    for f in files:
        if scope is not None and f['file'] not in scope:
            continue
        c = sum(f['styles']['colors'].values())
        lit, num, tok = lit + c, num + f['styles']['numbers'], tok + f['styles']['tokenRefs']
        palette.update(f['styles']['colors'])
        if c + f['styles']['numbers']:
            worst.append((c + f['styles']['numbers'], f['file']))
    adherence = tok / (tok + lit + num) if tok + lit + num else None

    # parity with a native design system
    parity = None
    if '--native-ds' in a:
        native = {}
        for dp, _, fs in os.walk(opt('--native-ds')):
            for fn in fs:
                if fn.endswith('.swift'):
                    for m in re.finditer(r'(?:struct|class|final class)\s+(\w+)\s*:\s*[^{]*\b(View|UIView|UIControl|UIButton|UILabel)\b', open(os.path.join(dp, fn), errors='ignore').read()):
                        native[norm(m.group(1))] = f'{os.path.join(dp, fn)}:{m.group(1)}'
        ds_rows = [r for r in rows if r['origin'] == 'design-system']
        parity = [{'component': r['component'], 'uses': r['uses'], 'native': native.get(norm(r['component']))} for r in ds_rows]

    print(f'ds {"(feature scope: " + str(len(scope)) + " files)" if scope else ""}: {len(rows)} components rendered: '
          + ', '.join(f'{k} {v}' for k, v in by_origin.most_common()) + (f'   ds roots: {", ".join(ds_roots)}' if ds_roots else '   ds roots: guessed by folder name'))
    print('\nmost used (uses, files, origin):')
    for r in rows[:12]:
        print(f'  {r["uses"]:5} {r["files"]:4}  {r["origin"]:<13} {r["component"]:<20} {r["src"][-38:]:<38} {", ".join(r["props"][:4])}')
    if competing:
        print('\nsame name, several sources (competing design systems or a migration in progress):')
        for n, rs in competing[:8]:
            print(f'  {n:<16} ' + ' | '.join(f'{r["src"][-34:]} x{r["uses"]}' for r in rs[:4]))
    if families:
        print('\nnear-duplicate families (same kind, overlapping props):')
        for f in families[:8]:
            ov = '; '.join(f'{x}~{y} {j}' for j, x, y in f['overlapping'][:3])
            print(f'  {f["kind"]:<10} {len(f["members"]):3} components, {f["uses"]:5} uses: {", ".join(f["members"][:6])}{" ..." if len(f["members"]) > 6 else ""}')
            if ov:
                print(f'             overlapping: {ov}')
    if adherence is not None:
        print(f'\ntokens: {tok} token refs vs {lit} literal colors + {num} magic numbers -> adherence {adherence:.0%}; '
              f'{len(palette)} distinct literal colors (palette drift)')
        print('  most common literals: ' + ', '.join(f'{c} x{n}' for c, n in palette.most_common(8)))
        print('  worst files: ' + ', '.join(f'{f.split("/")[-1]} ({n})' for n, f in sorted(worst, reverse=True)[:5]))
    if parity is not None:
        missing = [p for p in parity if not p['native']]
        print(f'\nparity with native DS: {len(parity) - len(missing)}/{len(parity)} design-system components have a native counterpart')
        for p in sorted(missing, key=lambda p: -p['uses'])[:12]:
            print(f'  missing natively: {p["component"]} ({p["uses"]} uses)')
    if '--json' in a:
        json.dump({'ds_roots': ds_roots, 'components': rows, 'competing': [[n, rs] for n, rs in competing], 'families': families, 'tokens': {'refs': tok, 'literal_colors': lit, 'magic_numbers': num,
                   'adherence': adherence, 'palette': palette.most_common()}, 'parity': parity}, open(opt('--json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
