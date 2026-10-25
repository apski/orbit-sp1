#!/usr/bin/env bash
# First vertical slice (plan §11), end to end through the CLI on a scratch copy.
set -euo pipefail
ORBIT="${ORBIT:-$(cd "$(dirname "$0")/.." && pwd)/target/release/orbit-rag}"
DIR=$(mktemp -d); trap 'rm -rf "$DIR"' EXIT
cp -R "$(dirname "$0")/../fixtures/demo/." "$DIR"; cd "$DIR"
git init -q && git add . && git -c user.email=t@t -c user.name=t commit -qm spec
step() { printf '\n== %s\n' "$*"; }

step "1-3. open spec, highlight + categorise decisions, save record + anchor"
$ORBIT init >/dev/null
A=$($ORBIT capture docs/navigation.md --text "Native owns navigation" --category decision --note "UIKit coordinators, not react-navigation")
B=$($ORBIT capture docs/navigation.md --text "Screen transitions must complete within 16 ms per frame" --category constraint)
C=$($ORBIT capture docs/navigation.md --text "Sync happens in a background" --category decision)
$ORBIT context link "$B" depends_on "$A"
$ORBIT context show "$A"

step "4. modify the spec: insert section above, edit the constraint, negate the data decision"
python3 - <<'PY'
p='docs/navigation.md'; s=open(p).read()
s=s.replace("# Mobile app architecture\n","# Mobile app architecture\n\n## Goals\n\nShip a fast, boring app.\n")
s=s.replace("within 16 ms per frame","within 8 ms per frame")
s=s.replace("The app stores user data in SQLite with WAL enabled. Sync happens","We no longer think this holds: Sync happens")
open(p,'w').write(s)
PY

step "5. re-anchor or flag (runs automatically on every command)"
$ORBIT sync
$ORBIT context review list
$ORBIT context review show "$B"
$ORBIT context impact docs/navigation.md

step "6. hybrid retrieval (semantic query with no shared keywords for A)"
$ORBIT context search "who controls screen routing"

step "7. source-cited, token-budgeted bundle"
$ORBIT context bundle --task "Implement native navigation" --scope mobile-app-architecture --tokens 1200
