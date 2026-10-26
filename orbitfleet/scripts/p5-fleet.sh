#!/bin/bash
# P5 fleet smoke (DESIGN.md "Phases"): N real Claude Code agents (`claude -p`, default 8, Sonnet) work in parallel,
# each told only to follow skills/orbitfleet/SKILL.md: claim, install the fixture app, run, launch, mark, screenshot,
# stop, release. Own daemon and pool (ofp5-01..N, created on first run), so nothing else is touched.
# It SPENDS MONEY (N agent sessions): it is in the opt-in `fleet` check set, never in `live`.
# Verified from the daemon's records, not from what the agents say. Exit = number of failed agents.
#   N=8 MODEL=sonnet BUDGET=1 (USD per agent) RUNTIME=com.apple.CoreSimulator.SimRuntime.iOS-26-4 scripts/p5-fleet.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd) N=${N:-8} MODEL=${MODEL:-sonnet} BUDGET=${BUDGET:-1} RUNTIME=${RUNTIME:-com.apple.CoreSimulator.SimRuntime.iOS-26-4}
go build -o orbitfleet .
APP=$(pwd)/$(scripts/testapp.sh)
export ORBITFLEET_HOME=$(mktemp -d /tmp/of5.XXXX)
OF=$REPO/orbitfleet
OUT=${P5_OUT:-$ORBITFLEET_HOME/agents}
mkdir -p "$OUT"
udid_of() { xcrun simctl list devices -j | jq -r --arg n "$1" '[.devices[][] | select(.name == $n)][0].udid // empty'; }
for i in $(seq -w 1 "$N"); do
  [ -n "$(udid_of "ofp5-$i")" ] || xcrun simctl create "ofp5-$i" com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro "$RUNTIME" >/dev/null
done

$OF serve --pool-prefix ofp5- --port 0 --max-booted 64 >"$ORBITFLEET_HOME/serve.log" 2>&1 &
# Watchdog: if this script is killed (orbit verify's timeout sends SIGKILL, so no trap runs), stop its daemon too.
( while kill -0 $$ 2>/dev/null; do sleep 2; done; kill "$(cat "$ORBITFLEET_HOME/daemon.pid" 2>/dev/null)" 2>/dev/null ) &
cleanup() {
  for u in $($OF ls --json 2>/dev/null | jq -r '.[] | select(.lease) | .udid'); do $OF release "$u" --force --shutdown >/dev/null 2>&1 || true; done
  for u in $($OF ls --json 2>/dev/null | jq -r '.[] | select(.state == "Booted") | .udid'); do xcrun simctl shutdown "$u" 2>/dev/null || true; done
  kill "$(cat "$ORBITFLEET_HOME/daemon.pid" 2>/dev/null)" 2>/dev/null || true
}
trap cleanup EXIT
for _ in $(seq 50); do $OF ls >/dev/null 2>&1 && break; sleep 0.1; done

# Peak footprint of every booted simulator, sampled from the daemon's state (simslim's measure, refreshed every 10 s).
sample() {
  local peak=0 b
  while :; do
    b=$(curl -s --unix-socket "$ORBITFLEET_HOME/orbitfleet.sock" http://of/api/state | jq '.fleetBytes // 0')
    [ "$b" -gt "$peak" ] && peak=$b && echo "$peak" >"$ORBITFLEET_HOME/peak"
    sleep 5
  done
}
sample & SAMPLER=$!
swap0=$(vm_stat | awk '/Swapouts/ {gsub("\\.", "", $2); print $2}')
t0=$(date +%s)

PIDS=()
for i in $(seq 1 "$N"); do
  prompt="You are agent p5-$i, testing orbitfleet, a service that leases iOS simulators to agents.
Read the protocol first: $REPO/skills/orbitfleet/SKILL.md. ORBITFLEET_HOME and ORBITFLEET_HOLDER are already set in
your environment; keep them. Use only the orbitfleet CLI plus the one install command below. No argent, no MCP
tools, and never touch a simulator you did not claim.
1. Claim a simulator (wait up to 10 minutes for one).
2. Install the app on it: xcrun simctl install <udid> $APP
3. Start a run labelled \"p5 agent $i\" for app dev.orbitfleet.hello, then launch the app.
4. Mark \"launched\", wait 3 seconds, take a screenshot named home, mark \"done\".
5. Stop the run as passed and release the simulator, also if a step failed (then stop it as failed).
Answer with exactly one line: RESULT <run id> <udid>, or FAILED <reason>."
  ORBITFLEET_HOLDER="p5-agent-$i" claude -p "$prompt" --model "$MODEL" --output-format json --max-budget-usd "$BUDGET" \
    --allowedTools "Bash(orbitfleet:*)" "Bash($OF:*)" "Bash(xcrun simctl install:*)" "Bash(sleep:*)" "Bash(jq:*)" "Read" \
    >"$OUT/agent-$i.json" 2>"$OUT/agent-$i.err" &
  PIDS+=($!)
done
for p in "${PIDS[@]}"; do wait "$p" || true; done # judged below from the daemon's records, not exit codes
elapsed=$(($(date +%s) - t0))
kill $SAMPLER 2>/dev/null || true
swap1=$(vm_stat | awk '/Swapouts/ {gsub("\\.", "", $2); print $2}')

# Verify from the daemon's records.
runs=$($OF runs --json)
fails=0
printf '%-11s %-7s %-28s %-9s %6s %8s %7s\n' AGENT STATUS RUN SIM TURNS COST SECONDS
for i in $(seq 1 "$N"); do
  r=$(jq -c --arg h "p5-agent-$i" '[.[] | select(.holder == $h)] | .[0] // {}' <<<"$runs")
  a="$OUT/agent-$i.json"
  turns=$(jq -r '.num_turns // "-"' "$a" 2>/dev/null || echo -)
  cost=$(jq -r '.total_cost_usd // 0 | . * 100 | round / 100' "$a" 2>/dev/null || echo -)
  secs=$(jq -r '(.duration_ms // 0) / 1000 | round' "$a" 2>/dev/null || echo -)
  st=$(jq -r '.status // "none"' <<<"$r")
  ok=$(jq -r '[.status == "passed", ([.artifacts[]? | select(.kind == "shot")] | length) > 0] | all' <<<"$r")
  if [ "$ok" = true ]; then
    marks=$(jq -r '.kind' "$(jq -r .dir <<<"$r")/events.ndjson" | grep -c mark || true)
    [ "$marks" -ge 2 ] || ok=false
  fi
  name=$($OF ls --json | jq -r --arg u "$(jq -r '.udid // ""' <<<"$r")" '.[] | select(.udid == $u) | .name')
  [ "$ok" = true ] || { fails=$((fails + 1)); st="$st!"; }
  printf '%-11s %-7s %-28s %-9s %6s %8s %7s\n' "p5-agent-$i" "$st" "$(jq -r '.id // "-"' <<<"$r")" "${name:--}" "$turns" "$cost" "$secs"
  [ "$ok" = true ] || echo "            says: $(jq -r '.result // empty' "$a" 2>/dev/null | tail -1 | cut -c1-120)"
done
# No simulator held by two runs at once.
overlap=$(jq '[group_by(.udid)[] | sort_by(.started) | . as $g | range(1; length) | select($g[.].started < $g[. - 1].ended)] | length' <<<"$runs")
left=$($OF ls --json | jq '[.[] | select(.lease)] | length')
total=$(jq -s 'map(.total_cost_usd // 0) | add | . * 100 | round / 100' "$OUT"/agent-*.json 2>/dev/null || echo -)
peak=$(cat "$ORBITFLEET_HOME/peak" 2>/dev/null || echo 0)
echo "agents ok: $((N - fails))/$N in ${elapsed}s · overlapping runs: $overlap · leases left: $left · cost: \$$total"
echo "host: swap-outs during the run: $((swap1 - swap0)) pages · peak simulator RAM: $(echo "scale=1; $peak / 1073741824" | bc) GB"
[ "$overlap" = 0 ] || fails=$((fails + 1))
[ "$left" = 0 ] || fails=$((fails + 1))
echo "p5-fleet: $([ $fails = 0 ] && echo ok || echo "$fails failed")"
exit $fails
