#!/bin/bash
# P1, P2, P4 and auto-restart live acceptance (DESIGN.md "Phases"); P3 is scripts/ui-flow.sh. Runs its own daemon on its own pool (ofaccept-01, -02, created on first
# run, iPhone 17 Pro on $RUNTIME) so it never touches other simulators. Exit 0 = every check passed.
set -euo pipefail
cd "$(dirname "$0")/.."
RUNTIME=${RUNTIME:-com.apple.CoreSimulator.SimRuntime.iOS-26-4}
go build -o orbitfleet .
export ORBITFLEET_HOME=$(mktemp -d /tmp/ofa.XXXX)   # short: unix socket paths cap at 104 bytes
OF=./orbitfleet
fails=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; fails=$((fails + 1)); }
udid_of() { xcrun simctl list devices -j | jq -r --arg n "$1" '[.devices[][] | select(.name == $n)][0].udid // empty'; }

for n in ofaccept-01 ofaccept-02; do
  [ -n "$(udid_of $n)" ] || xcrun simctl create "$n" com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro "$RUNTIME" >/dev/null
done

$OF serve --pool-prefix ofaccept- --max-booted 64 >"$ORBITFLEET_HOME/serve.log" 2>&1 &
DAEMON=$!
# Watchdog: if this script is killed (orbit verify's timeout sends SIGKILL, so no trap runs), stop its daemon too.
( while kill -0 $$ 2>/dev/null; do sleep 2; done; kill "$(cat "$ORBITFLEET_HOME/daemon.pid" 2>/dev/null)" 2>/dev/null ) &
trap 'kill "$(cat "$ORBITFLEET_HOME/daemon.pid" 2>/dev/null)" 2>/dev/null; rm -rf "$ORBITFLEET_HOME"' EXIT

# 1. Two parallel claims get two different, ready, booted, slim simulators.
t0=$(date +%s)
$OF claim --holder agent-a --json >"$ORBITFLEET_HOME/a.json" & A=$!
$OF claim --holder agent-b --json >"$ORBITFLEET_HOME/b.json" & B=$!
wait $A $B
UA=$(jq -r .udid "$ORBITFLEET_HOME/a.json"); UB=$(jq -r .udid "$ORBITFLEET_HOME/b.json")
echo "     claims took $(($(date +%s) - t0)) s"
if [ -z "$UA" ] || [ "$UA" = null ] || [ -z "$UB" ] || [ "$UB" = null ] || [ "$UA" = "$UB" ]; then
  fail "claims: a=$(cat "$ORBITFLEET_HOME/a.json") b=$(cat "$ORBITFLEET_HOME/b.json")"; tail -5 "$ORBITFLEET_HOME/serve.log"; exit 1
fi
pass "two claims, two sims ($UA, $UB)"
for f in a b; do w=$(jq -r '.warning // empty' "$ORBITFLEET_HOME/$f.json"); [ -z "$w" ] || echo "     note $f: $w"; done
for u in "$UA" "$UB"; do
  st=$(xcrun simctl list devices -j | jq -r --arg u "$u" '.devices[][] | select(.udid == $u) | .state')
  n=$(xcrun simctl spawn "$u" launchctl print-disabled system | grep -c '=> disabled' || true)
  [ "$st" = Booted ] && [ "$n" -ge 100 ] && pass "$u booted and slim ($n services disabled)" || fail "$u state=$st disabled=$n"
done

# P2. A run records the device log, app output, a mark, an AVIF screenshot and a crash report, then compresses the
#     log, moves app output into its folder and leaves no capture process behind.
streams() { { pgrep -f 'log stream --style ndjson' || true; } | wc -l | tr -d ' '; }
before=$(streams)
R=$($OF run start "$UA" --holder agent-a --label accept --app com.apple.Preferences --json | jq -r .id)
out=$($OF run start "$UA" --holder agent-a --json || true)
[ "$(jq -r .error.code <<<"$out")" = run-active ] && pass "one active run per sim" || fail "second run start: $out"
out=$($OF run start "$UB" --holder agent-a --json || true)
[ "$(jq -r .error.code <<<"$out")" = not-holder ] && pass "run start needs the lease" || fail "run on another's sim: $out"
PID=$($OF run launch "$R" --json | jq -r .pid)
sleep 3
$OF run mark "$R" "opened Settings" >/dev/null
SHOT=$($OF run shot "$R" home --json | jq -r .path)
show=$($OF run show "$R" --json)
LOG=$(jq -r '.artifacts[] | select(.kind=="device-log") | .path' <<<"$show")
ERR=$(jq -r '.artifacts[] | select(.kind=="app-err") | .path' <<<"$show")
[ -s "$LOG" ] && [ -s "$ERR" ] && pass "live device log ($(wc -c <"$LOG" | tr -d ' ') B), app stderr ($(wc -c <"$ERR" | tr -d ' ') B)" || fail "log=$LOG err=$ERR"
[[ "$SHOT" == *.avif ]] && file "$SHOT" | grep -qi avif && pass "screenshot $(basename "$SHOT") $(wc -c <"$SHOT" | tr -d ' ') B" || fail "shot $SHOT: $(file "$SHOT")"
kill -ABRT "$PID"
stop=$($OF run stop "$R" --status failed --reason "abort test" --json)
RD=$(jq -r .dir <<<"$stop")
crash=$(jq '[.artifacts[] | select(.kind=="crash")] | length' <<<"$stop")
zlog=$(jq -r '.artifacts[] | select(.kind=="device-log") | .path' <<<"$stop")
moved=$(jq --arg d "$RD" '[.artifacts[] | select(.kind | startswith("app-")) | .path | startswith($d)] | all' <<<"$stop")
[ "$crash" -ge 1 ] && pass "crash report collected ($crash)" || fail "no crash report: $(jq -c .artifacts <<<"$stop")"
[[ "$zlog" == *.zst ]] && [ ! -e "${zlog%.zst}" ] && pass "device log compressed ($(wc -c <"$zlog" | tr -d ' ') B zst)" || fail "log $zlog"
[ "$moved" = true ] && pass "app output moved into the run folder" || fail "app output not moved: $(jq -c .artifacts <<<"$stop")"
n=$($OF run logs "$R" | wc -l | tr -d ' ')
[ "$n" -gt 0 ] && pass "run logs reads the compressed log ($n lines)" || fail "run logs empty"
ev=$(jq -r .kind "$RD/events.ndjson" | tr '\n' ,)
[ "$ev" = "start,launch,mark,shot,crash,stop," ] && pass "timeline $ev" || fail "events $ev"
sleep 1
[ "$(streams)" = "$before" ] && pass "no log stream left running" || fail "log streams before=$before after=$(streams)"
[ "$(jq -r .warnings <<<"$stop")" = null ] || echo "     warnings: $(jq -c .warnings <<<"$stop")"

# P4. Debug on our fixture app (Apple's apps refuse lldb): lldb attaches by the pid the run reports, a paused launch
#     waits for it, Instruments and video record into the run, and print() reaches app.out while the app runs.
APP=$(scripts/testapp.sh)
timeout 120 xcrun simctl install "$UA" "$APP"
R4=$($OF run start "$UA" --holder agent-a --label debug --app dev.orbitfleet.hello --json | jq -r .id)
out=$($OF debug "$R4" lldb --json || true)
[ "$(jq -r .error.code <<<"$out")" = bad-request ] && pass "lldb refused before a launch" || fail "lldb before launch: $out"
P4=$(timeout 60 $OF run launch "$R4" --json | jq -r .pid)
sleep 2
OUT=$($OF run show "$R4" --json | jq -r '.artifacts[] | select(.kind=="app-out") | .path')
[ "$(cat "$OUT")" = "hello stdout" ] && pass "print() reached app.out while running" || fail "app.out: '$(cat "$OUT")'"
d=$($OF debug "$R4" lldb --json)
lp=$(xcrun simctl spawn "$UA" launchctl list | awk '/UIKitApplication:dev.orbitfleet.hello/ {print $1}')
[ "$(jq -r .pid <<<"$d")" = "$lp" ] && [ "$lp" = "$P4" ] && pass "debug lldb names pid $lp (launchctl agrees)" || fail "lldb pid $(jq -r .pid <<<"$d"), launchctl $lp, launch $P4"
cmd=$(jq -r .command <<<"$d")
timeout 60 $cmd --batch -o "process status" -o detach 2>&1 | grep -q "Process $P4 stopped" && pass "'$cmd' attaches" || fail "lldb did not attach to $P4"
PW=$(timeout 60 $OF run launch "$R4" --wait-for-debugger --json | jq -r .pid)
sleep 1
s1=$(ps -o stat= -p "$PW" | tr -d ' ')
timeout 60 xcrun lldb -p "$PW" --batch -o "process continue" -o detach >/dev/null 2>&1 || true
sleep 1
s2=$(ps -o stat= -p "$PW" | tr -d ' ')
[[ "$s1" == T* && "$s2" != T* ]] && pass "paused launch ($s1) runs after lldb continue ($s2)" || fail "wait-for-debugger: $s1 -> $s2"
tr=$($OF debug "$R4" profile --time-limit 5s --json | jq -r .path)
[ -d "$tr" ] && [ "$(du -sk "$tr" | cut -f1)" -gt 100 ] && pass "Instruments trace $(basename "$tr") $(du -sh "$tr" | cut -f1 | tr -d ' ')" || fail "trace $tr"
$OF debug "$R4" video start >/dev/null
sleep 2
vid=$($OF debug "$R4" video stop --json | jq -r .path)
file "$vid" | grep -q QuickTime && pass "video $(basename "$vid") $(wc -c <"$vid" | tr -d ' ') B" || fail "video: $(file "$vid")"
$OF run stop "$R4" --status passed >/dev/null

# 2. No free sim: a claim with --wait queues, then fails with capacity.
t0=$(date +%s)
out=$($OF claim --holder agent-c --wait 2s --json || true)
dt=$(($(date +%s) - t0))
[ "$(jq -r .error.code <<<"$out")" = capacity ] && [ "$dt" -ge 2 ] && pass "third claim waited ${dt}s then capacity" || fail "third claim: $out after ${dt}s"

# 3. Only the holder releases.
out=$($OF release "$UA" --holder agent-b --json || true)
[ "$(jq -r .error.code <<<"$out")" = not-holder ] && pass "release by non-holder refused" || fail "non-holder release: $out"

# 4. A previous holder's DYLD_INSERT_LIBRARIES is cleared for the next one. An argent server attached to the sim may
#    re-inject its own dylib at once; then the lease must say so (warning) instead of claiming a clean sim.
$OF release "$UA" --holder agent-a >/dev/null
PLANT=/usr/lib/libobjc.A.dylib   # stands in for what a previous holder's argent left behind
xcrun simctl spawn "$UA" launchctl setenv DYLD_INSERT_LIBRARIES "$PLANT"
d=$($OF claim --holder agent-d --device "$UA" --json)
v=$(xcrun simctl spawn "$UA" launchctl getenv DYLD_INSERT_LIBRARIES)
w=$(jq -r '.warning // empty' <<<"$d")
if [[ "$v" == *"$PLANT"* ]]; then fail "planted DYLD survived re-claim: '$v'"
elif [ -z "$v" ]; then pass "re-claim cleared DYLD_INSERT_LIBRARIES"
elif [ -n "$w" ]; then pass "re-claim cleared ours; argent re-injected and the lease warns"
else fail "DYLD '$v' set but the lease has no warning"; fi

# 5. Leases survive a daemon restart.
kill $DAEMON; wait $DAEMON 2>/dev/null || true
$OF serve --pool-prefix ofaccept- --max-booted 64 >>"$ORBITFLEET_HOME/serve.log" 2>&1 &
DAEMON=$!
sleep 1
held=$($OF ls --json | jq -r '[.[] | select(.lease) | .lease.holder] | sort | join(",")')
[ "$held" = "agent-b,agent-d" ] && pass "leases restored after restart ($held)" || fail "after restart holders=$held"

# Auto-restart: a newer build of the same binary restarts an idle daemon with its own flags; a busy one waits.
pid0=$(cat "$ORBITFLEET_HOME/daemon.pid")
R5=$($OF run start "$UB" --holder agent-b --json | jq -r .id)
touch orbitfleet # what go build does to the file
note=$($OF ls 2>&1 >/dev/null)
[ "$(cat "$ORBITFLEET_HOME/daemon.pid")" = "$pid0" ] && [[ "$note" == *"restarts once idle"* ]] && pass "busy daemon kept while a run is active" || fail "busy: pid $(cat "$ORBITFLEET_HOME/daemon.pid") vs $pid0, note '$note'"
$OF run stop "$R5" --status passed >/dev/null 2>&1
held=$($OF ls --json | jq -r '[.[] | select(.lease) | .lease.holder] | sort | join(",")')
pid1=$(cat "$ORBITFLEET_HOME/daemon.pid")
args=$($OF version --json | jq -r '.daemon.args | join(" ")')
[ "$pid1" != "$pid0" ] && [ "$held" = "agent-b,agent-d" ] && [[ "$args" == *"--pool-prefix ofaccept-"* ]] && pass "idle daemon restarted ($pid0 -> $pid1), same flags, leases kept" || fail "restart: pid $pid0 -> $pid1, holders $held, args '$args'"

# 6. Release with --shutdown frees and stops both.
$OF release "$UA" --holder agent-d --shutdown >/dev/null
$OF release "$UB" --holder agent-b --shutdown >/dev/null
left=$($OF ls --json | jq '[.[] | select(.lease)] | length')
[ "$left" = 0 ] && pass "all released" || fail "$left leases left"

echo "accept: $([ $fails = 0 ] && echo ok || echo "$fails failed")"
exit $fails
