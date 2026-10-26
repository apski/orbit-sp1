#!/bin/bash
# P3 browser acceptance (DESIGN.md "Phases"): drives the UI in a headless agent-browser session against its own daemon
# (pool ofaccept-, port 7499, own ORBITFLEET_HOME). Run scripts/accept.sh once first: it creates the ofaccept sims.
# UI_FLOW_SHOT=<path.png> keeps a screenshot of the drawer. Exit 0 = every check passed.
set -euo pipefail
cd "$(dirname "$0")/.."
go build -o orbitfleet .
export ORBITFLEET_HOME=$(mktemp -d /tmp/ofu.XXXX) ORBITFLEET_HOLDER=ui-flow-agent
OF=./orbitfleet AB="agent-browser --session ofui-$$" # own session: a command right after another run's close hit its dying daemon
fails=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; fails=$((fails + 1)); }
udid_of() { xcrun simctl list devices -j | jq -r --arg n "$1" '[.devices[][] | select(.name == $n)][0].udid // empty'; }
U=$(udid_of ofaccept-01)
[ -n "$U" ] || { echo "no ofaccept-01: run scripts/accept.sh first"; exit 2; }

# A free port of our own: a fixed one let a leaked daemon from an earlier run answer in our place (wrong token).
while :; do PORT=$((20000 + RANDOM % 20000)); lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null || break; done
$OF serve --pool-prefix ofaccept- --port $PORT --max-booted 64 >"$ORBITFLEET_HOME/serve.log" 2>&1 &
DAEMON=$!
# Watchdog: if this script is killed (orbit verify's timeout sends SIGKILL, so no trap runs), stop its daemon too.
( while kill -0 $$ 2>/dev/null; do sleep 2; done; kill "$(cat "$ORBITFLEET_HOME/daemon.pid" 2>/dev/null)" 2>/dev/null ) &
cleanup() { cat "$ORBITFLEET_HOME/ab-open.err" 2>/dev/null | sed "s/^/open-err: /" | head -5; $AB close >/dev/null 2>&1 || true; kill "$(cat "$ORBITFLEET_HOME/daemon.pid" 2>/dev/null)" 2>/dev/null || true; rm -rf "$ORBITFLEET_HOME"; }
trap cleanup EXIT
for _ in $(seq 50); do $OF ls >/dev/null 2>&1 && break; sleep 0.1; done

# 1. The UI listens on loopback only and refuses requests without the cookie or with a foreign Host.
bind=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN | awk 'NR>1 {print $2" "$9}' | sort -u | tr '\n' ' ')
[ "$bind" = "$(cat "$ORBITFLEET_HOME/daemon.pid") 127.0.0.1:$PORT " ] && pass "our daemon listens on 127.0.0.1:$PORT only" || fail "listeners on $PORT: $bind (our daemon: $(cat "$ORBITFLEET_HOME/daemon.pid"))"
c1=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/api/state)
c2=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: evil.example:$PORT" http://127.0.0.1:$PORT/api/state)
[ "$c1" = 401 ] && [ "$c2" = 403 ] && pass "no cookie -> $c1, foreign Host -> $c2" || fail "no cookie $c1, foreign Host $c2"

# 2. Sign in through the link `orbitfleet ui` opens; one card per pool simulator.
URL=$($OF ui --print)
for _ in 1 2 3 4 5; do $AB open "$URL" >/dev/null 2>>"$ORBITFLEET_HOME/ab-open.err" && break; sleep 1; done # the session's browser starts on first use
$AB set viewport 1440 1000 >/dev/null
$AB wait '[data-testid^="sim-"]' >/dev/null
n=$($AB get count '[data-testid^="sim-"]')
want=$($OF ls --json | jq length)
[ "$n" = "$want" ] && pass "$n cards for $want pool sims" || fail "cards $n, pool $want"

# 3. A claim from the CLI shows its holder on the card within 3 s.
$OF claim --device ofaccept-01 --json >/dev/null
t0=$(date +%s)
for _ in $(seq 30); do
  [[ "$($AB get text '[data-testid="holder-ofaccept-01"]')" == *ui-flow-agent* ]] && break
  sleep 0.2
done
dt=$(($(date +%s) - t0))
[[ "$($AB get text '[data-testid="holder-ofaccept-01"]')" == *ui-flow-agent* ]] && [ $dt -le 3 ] && pass "holder on the card after ${dt}s" || fail "holder not shown within 3s (${dt}s)"

# 4. The drawer's live device log grows; the timeline shows the run's events.
R=$($OF run start "$U" --label "ui flow" --app com.apple.Preferences --json | jq -r .id)
$OF run launch "$R" >/dev/null
$AB open "http://127.0.0.1:$PORT/#sim=$U&tab=log" >/dev/null
$AB wait '[data-testid="log-line"]' >/dev/null
a=$($AB get count '[data-testid="log-line"]')
$OF run mark "$R" "ui flow mark" >/dev/null
sleep 3
b=$($AB get count '[data-testid="log-line"]')
[ "$b" -gt "$a" ] && pass "log tail grew $a -> $b lines" || fail "log tail did not grow ($a -> $b)"
$AB click '[data-testid="tab-timeline"]' >/dev/null
$AB wait '[data-testid="timeline"] li' >/dev/null
tl=$($AB get text '[data-testid="timeline"]')
[[ "$tl" == *"ui flow mark"* && "$tl" != *null* ]] && pass "timeline shows the mark" || fail "timeline: $tl"
for _ in $(seq 20); do # a busy sim's screenshot can take seconds
  w=$($AB eval "document.querySelector('[data-testid=\"sim-$U\"] img')?.naturalWidth || 0" | tr -d '"')
  [ "${w:-0}" -gt 0 ] && break
  sleep 0.5
done
[ "${w:-0}" -gt 0 ] && pass "card thumbnail loaded (${w}px wide)" || fail "card thumbnail empty (naturalWidth $w)"
[ ! -e ./- ] && pass "no stray screenshot file" || fail "simctl wrote ./-"
[ -z "${UI_FLOW_SHOT:-}" ] || $AB screenshot "$UI_FLOW_SHOT" >/dev/null

# P4 in the UI: record a video from the drawer, relaunch the app paused for a debugger. (Attach LLDB opens a
#    Terminal window and Profile takes 30 s: accept.sh covers both through the CLI.)
$AB click '[data-testid="debug-video"]' >/dev/null
for _ in $(seq 20); do [[ "$($AB get text '[data-testid="debug-video"]')" == *"Stop video"* ]] && break; sleep 0.3; done
sleep 2
$AB click '[data-testid="debug-video"]' >/dev/null
for _ in $(seq 30); do [[ "$($AB get text '[data-testid="toast"]')" == *"Saved video-"* ]] && break; sleep 0.3; done
nv=$($OF run show "$R" --json | jq '[.artifacts[] | select(.kind == "video")] | length')
[ "$nv" = 1 ] && pass "video recorded from the UI" || fail "videos after UI start/stop: $nv ($($AB get text '[data-testid="toast"]'))"
$AB click '[data-testid="debug-wait"]' >/dev/null
for _ in $(seq 30); do [[ "$($AB get text '[data-testid="toast"]')" == *suspended* ]] && break; sleep 0.3; done
pw=$($OF run show "$R" --json | jq -r .pid)
[[ "$($AB get text '[data-testid="toast"]')" == *"pid $pw"* && "$(ps -o stat= -p "$pw" | tr -d ' ')" == T* ]] && pass "relaunched paused from the UI (pid $pw)" || fail "paused relaunch: $($AB get text '[data-testid="toast"]')"

# 5. Force release from the UI (two clicks) frees the sim and aborts its run.
$AB click '[data-testid="lease-force"]' >/dev/null
$AB click '[data-testid="lease-force"]' >/dev/null
sleep 3
[[ "$($AB get text '[data-testid="holder-ofaccept-01"]')" == "no lease" ]] && pass "force release from the UI" || fail "card after force release: $($AB get text '[data-testid="holder-ofaccept-01"]')"
st=$($OF run show "$R" --json | jq -r '.status + " " + .reason')
[ "$st" = "aborted lease released" ] && pass "run aborted by the release" || fail "run after release: $st"

$OF release "$U" --force --shutdown >/dev/null 2>&1 || xcrun simctl shutdown "$U" 2>/dev/null || true
echo "ui-flow: $([ $fails = 0 ] && echo ok || echo "$fails failed")"
exit $fails
