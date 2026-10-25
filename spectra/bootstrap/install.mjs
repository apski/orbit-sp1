// `spectra install <repo>`: fit orbit-SP1's Spectra into a React Native repo. Idempotent; writes no app code.
//   - every skill under spectra/skills/ -> <repo>/.claude/skills/ and <repo>/.agents/skills/ (Claude Code and Codex)
//   - <repo>/.spectra/: `spectra` shim (calls this checkout), config.json, .gitignore, jest harness (if absent)
//   - a marked block in AGENTS.md telling agents Spectra is here and where to start
//   - runs `spectra detect`, then the spectra-bootstrap skill takes over (confirm the profile, fit the harness, first screen)
import { chmodSync, cpSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { detect, scan } from './detect.mjs';

const BLOCK_START = '<!-- spectra:start -->', BLOCK_END = '<!-- spectra:end -->';

export function install(repo, home) {
  const pkg = existsSync(join(repo, 'package.json')) ? JSON.parse(readFileSync(join(repo, 'package.json'), 'utf8')) : {};
  if (!{ ...pkg.dependencies, ...pkg.devDependencies }['react-native']) console.warn(`warning: ${repo} has no react-native dependency (Figma-only use still works)`);
  const dot = join(repo, '.spectra');
  mkdirSync(dot, { recursive: true });

  const skills = readdirSync(join(home, 'skills'), { withFileTypes: true }).filter(d => d.isDirectory() && existsSync(join(home, 'skills', d.name, 'SKILL.md')));
  if (!process.argv.includes('--no-skills'))  // orbit install places them itself, without overwriting the repo's own
    for (const root of ['.claude/skills', '.agents/skills']) for (const s of skills) cpSync(join(home, 'skills', s.name), join(repo, root, s.name), { recursive: true });

  const shim = join(dot, 'spectra');
  // Finds the orbit-SP1 checkout at run time, so the launcher can be committed and works on any machine.
  writeFileSync(shim, `#!/bin/sh
# Spectra CLI for this repo, run from the orbit-SP1 checkout ($ORBIT_HOME or ~/code/orbit-sp1).
op=""
for d in "$ORBIT_HOME" "$HOME/code/orbit-sp1"; do
  [ -n "$d" ] && [ -f "$d/ORBIT-SP1.md" ] && { op="$d"; break; }
done
[ -n "$op" ] || { echo "orbit-SP1 checkout not found: clone it to ~/code/orbit-sp1 or set ORBIT_HOME" >&2; exit 127; }
exec node "$op/spectra/bin/spectra.mjs" --repo "$(cd "$(dirname "$0")/.." && pwd)" "$@"
`);
  chmodSync(shim, 0o755);
  let rev = null;
  try { rev = execFileSync('git', ['-C', dirname(home), 'rev-parse', '--short', 'HEAD'], { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim(); } catch {}
  writeFileSync(join(dot, 'config.json'), JSON.stringify({ revision: rev, installedAt: new Date().toISOString().slice(0, 10),
    skills: skills.map(s => s.name) }, null, 1) + '\n');
  writeFileSync(join(dot, '.gitignore'), '# Spectra: cache and build output stay local; profile, jest harness and registry are committed.\nfigma/raw/\npreview/\nout/\nir/\nsecrets.env\n*.frames.log\n');

  const jest = join(dot, 'jest');
  if (!existsSync(join(jest, 'jest.config.cjs'))) {
    const preset = scan(repo).jest.value?.preset ?? 'react-native';
    const presetSetup = existsSync(join(repo, 'node_modules', preset, 'jest', 'setup.js')) ? `require.resolve('${preset}/jest/setup.js'), ` : '';
    mkdirSync(jest, { recursive: true });
    const t = join(home, 'bootstrap', 'templates', 'jest');
    writeFileSync(join(jest, 'jest.config.cjs'), readFileSync(join(t, 'jest.config.cjs'), 'utf8').replace('{{PRESET}}', preset).replace('{{PRESET_SETUP}}', presetSetup));
    for (const f of ['setup.js', 'wrap.js']) cpSync(join(t, f), join(jest, f));
  }

  const agents = join(repo, 'AGENTS.md');
  const block = `${BLOCK_START}
## Spectra (RN / Figma -> native UI compiler, from orbit-SP1)

CLI: \`.spectra/spectra <command>\` (no args lists commands). Profile: \`.spectra/profile.json\`.
Start with the \`spectra-bootstrap\` skill (fits Spectra to this repo), then \`spectra\` (convert a screen),
\`spectra-figma\` (from a Figma frame via the REST API; never paste raw Figma JSON into context)${skills.some(s => s.name === 'spectra-components') ? ', `spectra-components` (design-system components)' : ''}.
Generated code is checked by a judge against the source's own layout; a screen is done when \`spectra verify\` passes.
${BLOCK_END}`;
  const cur = existsSync(agents) ? readFileSync(agents, 'utf8') : '';
  const next = cur.includes(BLOCK_START) ? cur.replace(new RegExp(`${BLOCK_START}[\\s\\S]*?${BLOCK_END}`), block) : `${cur}${cur && !cur.endsWith('\n') ? '\n' : ''}${cur ? '\n' : ''}${block}\n`;
  writeFileSync(agents, next);

  console.log(`installed Spectra into ${repo}`);
  console.log(`  skills: ${skills.map(s => s.name).join(', ')} -> .claude/skills, .agents/skills`);
  console.log(`  cli:    .spectra/spectra   jest harness: .spectra/jest/   AGENTS.md block`);
  detect(repo);
  console.log('\nnext: ask the agent to run the spectra-bootstrap skill (it confirms the profile and fits the jest harness).');
}
