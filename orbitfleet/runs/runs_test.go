package runs

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"orbitfleet/fleet"
)

type fakeSim struct {
	mu         sync.Mutex
	deviceTmp  string // stands in for the simulator's data/tmp
	logStopped int
	launches   int
	compressed string // the run's status on disk while its log was being compressed
	terminal   string // the .command OpenTerminal ran
	profiled   string // "pid template limit out"
	videos     int    // recordings still running
	dyld       string
}

func (s *fakeSim) Profile(ctx context.Context, udid string, pid int, template string, limit time.Duration, out string) error {
	s.profiled = fmt.Sprintf("%d %s %s %s", pid, template, limit, filepath.Base(out))
	return os.MkdirAll(out, 0o700) // a .trace is a bundle
}

func (s *fakeSim) StartVideo(udid, path string) (func() error, error) {
	s.videos++
	return func() error { s.videos--; return os.WriteFile(path, []byte("mov"), 0o600) }, nil
}

func (s *fakeSim) Dyld(ctx context.Context, udid string) (string, error) { return s.dyld, nil }
func (s *fakeSim) OpenTerminal(path string) error                        { s.terminal = path; return nil }

func (s *fakeSim) StartLog(udid, level, predicate, path string) (func() error, error) {
	if err := os.WriteFile(path, []byte(`{"eventMessage":"hello","messageType":"Default"}`+"\n"), 0o600); err != nil {
		return nil, err
	}
	return func() error { s.mu.Lock(); s.logStopped++; s.mu.Unlock(); return nil }, nil
}

func (s *fakeSim) Launch(ctx context.Context, udid, app string, wait bool, args []string, capture string) (int, string, string, error) {
	s.launches++
	pid := os.Getpid() // a live process, as a launched app is
	out := filepath.Join(s.deviceTmp, capture+".out")
	errp := filepath.Join(s.deviceTmp, capture+".err")
	os.WriteFile(out, []byte("stdout\n"), 0o600)
	os.WriteFile(errp, []byte("stderr\n"), 0o600)
	return pid, out, errp, nil
}

func (s *fakeSim) Screenshot(ctx context.Context, udid, base string) (string, error) {
	return base + ".avif", os.WriteFile(base+".avif", []byte("avif"), 0o600)
}

func (s *fakeSim) Compress(path string) (string, error) {
	var run Run
	b, _ := os.ReadFile(filepath.Join(filepath.Dir(path), "run.json"))
	json.Unmarshal(b, &run)
	s.compressed = run.Status
	return path + ".zst", os.Rename(path, path+".zst")
}

func (s *fakeSim) Open(path string) (io.ReadCloser, error) { return os.Open(path) }

func setup(t *testing.T) (*Runs, *fakeSim, string) {
	t.Helper()
	base := t.TempDir()
	s := &fakeSim{deviceTmp: filepath.Join(base, "device")}
	for _, d := range []string{"device", "crashes", "runs"} {
		os.MkdirAll(filepath.Join(base, d), 0o700)
	}
	r, err := New(s, Config{Dir: filepath.Join(base, "runs"), CrashDir: filepath.Join(base, "crashes")})
	if err != nil {
		t.Fatal(err)
	}
	return r, s, base
}

func code(err error) string {
	var e *fleet.Error
	if errors.As(err, &e) {
		return e.Code
	}
	return ""
}

func writeIPS(t *testing.T, dir, name, bundle string, mtime time.Time) {
	t.Helper()
	p := filepath.Join(dir, name)
	head := `{"app_name":"X","bundleID":"` + bundle + `","bug_type":"309"}` + "\n{\n  \"body\": 1\n}\n"
	if err := os.WriteFile(p, []byte(head), 0o600); err != nil {
		t.Fatal(err)
	}
	os.Chtimes(p, mtime, mtime)
}

func events(t *testing.T, run Run) []Event {
	t.Helper()
	f, err := os.Open(filepath.Join(run.Dir, "events.ndjson"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var out []Event
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var e Event
		json.Unmarshal(sc.Bytes(), &e)
		out = append(out, e)
	}
	return out
}

func kinds(evs []Event) string {
	var k []string
	for _, e := range evs {
		k = append(k, e.Kind)
	}
	return strings.Join(k, ",")
}

var ctx = context.Background()

func TestRunLifecycle(t *testing.T) {
	r, s, base := setup(t)
	run, err := r.Start(ctx, StartRequest{UDID: "U1", Holder: "a", Label: "login", App: "com.example.app"})
	if err != nil || run.Status != "running" {
		t.Fatalf("start = %+v, %v", run, err)
	}
	if _, err := r.Start(ctx, StartRequest{UDID: "U1", Holder: "a"}); code(err) != "run-active" {
		t.Fatalf("second start on U1: %v; want run-active", err)
	}
	if run, err = r.Launch(ctx, run.ID, LaunchRequest{}); err != nil || run.PID != os.Getpid() {
		t.Fatalf("launch = %+v, %v", run, err)
	}
	if _, err := r.Mark(run.ID, "tapped Sign in"); err != nil {
		t.Fatal(err)
	}
	if a, err := r.Shot(ctx, run.ID, "after login!"); err != nil || filepath.Base(a.Path) != "after-login-.avif" {
		t.Fatalf("shot = %+v, %v (name must be sanitised)", a, err)
	}

	crashes := filepath.Join(base, "crashes")
	writeIPS(t, crashes, "App-new.ips", "com.example.app", time.Now())
	writeIPS(t, crashes, "Other-new.ips", "com.other", time.Now())
	writeIPS(t, crashes, "App-old.ips", "com.example.app", time.Now().Add(-time.Hour))

	run, err = r.Stop(ctx, run.ID, StopRequest{Status: "failed", Reason: "crashed on login"})
	if err != nil || run.Status != "failed" || run.Ended == nil || run.Reason != "crashed on login" {
		t.Fatalf("stop = %+v, %v", run, err)
	}
	if s.logStopped != 1 {
		t.Fatalf("log stopped %d times", s.logStopped)
	}
	if s.compressed != "failed" {
		t.Fatalf("run.json said %q during compression; the final status must be saved before the slow steps", s.compressed)
	}
	got := map[string][]string{}
	for _, a := range run.Artifacts {
		if !strings.HasPrefix(a.Path, run.Dir) {
			t.Errorf("artifact %s outside the run dir after stop", a.Path)
		}
		if _, err := os.Stat(a.Path); err != nil {
			t.Errorf("artifact missing: %v", err)
		}
		got[a.Kind] = append(got[a.Kind], filepath.Base(a.Path))
	}
	want := map[string]string{"device-log": "device.ndjson.zst", "app-out": "app-1.out", "app-err": "app-1.err", "shot": "after-login-.avif", "crash": "App-new.ips"}
	for k, v := range want {
		if strings.Join(got[k], ",") != v {
			t.Errorf("%s artifacts = %v; want %s", k, got[k], v)
		}
	}
	if k := kinds(events(t, run)); k != "start,launch,mark,shot,crash,stop" {
		t.Fatalf("events = %s", k)
	}
	if _, err := r.Mark(run.ID, "late"); code(err) != "run-ended" {
		t.Fatalf("mark after stop: %v; want run-ended", err)
	}
	if _, err := r.Start(ctx, StartRequest{UDID: "U1", Holder: "a"}); err != nil {
		t.Fatalf("new run after stop: %v", err)
	}
}

func TestStopValidatesStatus(t *testing.T) {
	r, _, _ := setup(t)
	run, _ := r.Start(ctx, StartRequest{UDID: "U1", Holder: "a"})
	if _, err := r.Stop(ctx, run.ID, StopRequest{Status: "aborted"}); code(err) != "bad-request" {
		t.Fatalf("stop aborted: %v; agents report passed or failed", err)
	}
	if _, err := r.Stop(ctx, "r-nope", StopRequest{Status: "passed"}); code(err) != "not-found" {
		t.Fatalf("unknown run: %v", err)
	}
}

func TestAbortAndRestart(t *testing.T) {
	r, s, base := setup(t)
	a, _ := r.Start(ctx, StartRequest{UDID: "U1", Holder: "a"})
	b, _ := r.Start(ctx, StartRequest{UDID: "U2", Holder: "b"})
	r.AbortUDID(ctx, "U1", "lease expired")
	r.AbortUDID(ctx, "U9", "no run here") // no-op
	if got, _ := r.Get(a.ID); got.Status != "aborted" || got.Reason != "lease expired" || s.logStopped != 1 {
		t.Fatalf("aborted run = %+v, log stops %d", got, s.logStopped)
	}
	// A daemon restart cannot reattach to capture processes: whatever was running is aborted.
	r2, err := New(s, Config{Dir: filepath.Join(base, "runs"), CrashDir: filepath.Join(base, "crashes")})
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := r2.Get(b.ID); got.Status != "aborted" || got.Reason != "daemon restarted" {
		t.Fatalf("after restart = %+v", got)
	}
	if _, err := r2.Start(ctx, StartRequest{UDID: "U2", Holder: "b"}); err != nil {
		t.Fatalf("start after restart: %v", err)
	}
}

func TestListFilters(t *testing.T) {
	r, _, _ := setup(t)
	for _, h := range []string{"a", "b", "a"} {
		run, _ := r.Start(ctx, StartRequest{UDID: "U-" + h, Holder: h})
		r.Stop(ctx, run.ID, StopRequest{Status: "passed"})
	}
	all, err := r.List("", time.Time{})
	if err != nil || len(all) != 3 {
		t.Fatalf("list = %d, %v", len(all), err)
	}
	if !all[0].Started.After(all[2].Started) && !all[0].Started.Equal(all[2].Started) {
		t.Fatal("list must be newest first")
	}
	if a, _ := r.List("a", time.Time{}); len(a) != 2 {
		t.Fatalf("holder a: %d runs", len(a))
	}
	if none, _ := r.List("", time.Now().Add(time.Hour)); len(none) != 0 {
		t.Fatalf("since the future: %d runs", len(none))
	}
}

func TestDebug(t *testing.T) {
	r, s, _ := setup(t)
	run, _ := r.Start(ctx, StartRequest{UDID: "U1", Holder: "a", App: "dev.orbitfleet.hello"})
	if _, err := r.LLDB(ctx, run.ID, false); code(err) != "bad-request" {
		t.Fatalf("lldb before launch: %v; want bad-request (no app to attach to)", err)
	}
	run, _ = r.Launch(ctx, run.ID, LaunchRequest{})

	d, err := r.LLDB(ctx, run.ID, true)
	want := fmt.Sprintf("xcrun lldb -p %d", run.PID)
	if err != nil || d.Command != want || d.PID != run.PID {
		t.Fatalf("lldb = %+v, %v; want %q", d, err, want)
	}
	if b, _ := os.ReadFile(s.terminal); !strings.Contains(string(b), "exec "+want) || filepath.Base(s.terminal) != "lldb.command" {
		t.Fatalf("Terminal got %q: %q", s.terminal, b)
	}

	s.dyld = "/x/libArgentInjectionBootstrap.dylib"
	a, err := r.Profile(ctx, run.ID, "", 0)
	if err != nil || a.Kind != "trace" || filepath.Base(a.Path) != "profile-1.trace" || !strings.Contains(a.Note, "DYLD_INSERT_LIBRARIES") {
		t.Fatalf("profile = %+v, %v", a, err)
	}
	if want := fmt.Sprintf("%d Time Profiler 30s profile-1.trace", run.PID); s.profiled != want {
		t.Fatalf("profiled %q; want %q (defaults: Time Profiler, 30 s)", s.profiled, want)
	}

	if _, err := r.Video(ctx, run.ID, true); err != nil {
		t.Fatal(err)
	}
	if got, _ := r.Get(run.ID); !got.Recording {
		t.Fatal("run must say it is recording")
	}
	if _, err := r.Video(ctx, run.ID, true); code(err) != "bad-request" {
		t.Fatalf("second video start: %v", err)
	}
	if a, err := r.Video(ctx, run.ID, false); err != nil || filepath.Base(a.Path) != "video-1.mov" || a.Kind != "video" {
		t.Fatalf("video stop = %+v, %v", a, err)
	}
	r.Video(ctx, run.ID, true) // left running: stopping the run must stop it too
	run, _ = r.Stop(ctx, run.ID, StopRequest{Status: "passed"})
	var vids []string
	for _, a := range run.Artifacts {
		if a.Kind == "video" {
			vids = append(vids, filepath.Base(a.Path))
		}
	}
	if s.videos != 0 || strings.Join(vids, ",") != "video-1.mov,video-2.mov" || run.Recording {
		t.Fatalf("after stop: %d recordings running, videos %v, recording %v", s.videos, vids, run.Recording)
	}
	if k := kinds(events(t, run)); k != "start,launch,debug,debug,debug,debug,debug,stop" {
		t.Fatalf("events = %s", k)
	}
}
