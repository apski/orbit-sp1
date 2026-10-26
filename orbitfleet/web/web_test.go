package web

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"orbitfleet/fleet"
	"orbitfleet/runs"
)

type oneSim struct{ state string }

func (s *oneSim) List(context.Context) ([]fleet.Device, error) {
	return []fleet.Device{{UDID: "U1", Name: "fleet-01", State: s.state, OS: "iOS 26.4"}}, nil
}
func (s *oneSim) Prepare(context.Context, string) (string, error) { s.state = "Booted"; return "", nil }
func (s *oneSim) Shutdown(context.Context, string) error          { s.state = "Shutdown"; return nil }

type logSim struct{}

func (logSim) StartLog(udid, level, predicate, path string) (func() error, error) {
	lines := `{"eventMessage":"boot ok"}` + "\n" + `{"eventMessage":"login failed"}` + "\n"
	return func() error { return nil }, os.WriteFile(path, []byte(lines), 0o600)
}
func (logSim) Launch(context.Context, string, string, bool, []string, string) (int, string, string, error) {
	return 0, "", "", errors.New("not used")
}
func (logSim) Screenshot(context.Context, string, string) (string, error) {
	return "", errors.New("not used")
}
func (logSim) Compress(p string) (string, error)    { return p, nil }
func (logSim) Open(p string) (io.ReadCloser, error) { return os.Open(p) }
func (logSim) Profile(context.Context, string, int, string, time.Duration, string) error {
	return errors.New("not used")
}
func (logSim) StartVideo(string, string) (func() error, error) { return nil, errors.New("not used") }
func (logSim) Dyld(context.Context, string) (string, error)    { return "", nil }
func (logSim) OpenTerminal(string) error                       { return nil }

func newRuns(t *testing.T) *runs.Runs {
	r, err := runs.New(logSim{}, runs.Config{Dir: t.TempDir()})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

// shortDir keeps the socket path under macOS's 104-byte sun_path limit (t.TempDir() is too long).
func shortDir(t *testing.T) string {
	d, err := os.MkdirTemp("/tmp", "of")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(d) })
	return d
}

func TestSocketRoundTrip(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sock := filepath.Join(shortDir(t), "of.sock")
	f := fleet.New(&oneSim{state: "Booted"}, fleet.Config{PoolPrefix: "fleet-", MaxBooted: 4, TTL: time.Hour}, nil)
	errc := make(chan error, 1)
	go func() { errc <- Serve(ctx, Handler(f, newRuns(t), Tools{}), sock) }()
	c := Client{Socket: sock}
	waitUp(t, c)

	if fi, err := os.Stat(sock); err != nil || fi.Mode().Perm() != 0o600 {
		t.Fatalf("socket mode = %v, %v; want 0600", fi.Mode().Perm(), err)
	}
	var l fleet.Lease
	if err := c.Do(ctx, "POST", "/api/claim", fleet.ClaimRequest{Holder: "a"}, &l); err != nil || l.Name != "fleet-01" || !l.Ready {
		t.Fatalf("claim = %+v, %v", l, err)
	}
	var fe *fleet.Error
	if err := c.Do(ctx, "POST", "/api/claim", fleet.ClaimRequest{Holder: "b"}, &l); !errors.As(err, &fe) || fe.Code != "capacity" {
		t.Fatalf("second claim err = %v; want *fleet.Error capacity", err)
	}
	if err := c.Do(ctx, "POST", "/api/renew", fleet.ReleaseRequest{UDID: "U1"}, &l); err != nil || l.Holder != "a" {
		t.Fatalf("renew = %+v, %v", l, err)
	}

	var run runs.Run
	if err := c.Do(ctx, "POST", "/api/runs", runs.StartRequest{UDID: "U1", Holder: "b"}, &run); !errors.As(err, &fe) || fe.Code != "not-holder" {
		t.Fatalf("run start by non-holder: %v", err)
	}
	if err := c.Do(ctx, "POST", "/api/runs", runs.StartRequest{UDID: "U1", Holder: "a", Label: "login"}, &run); err != nil || run.Status != "running" {
		t.Fatalf("run start = %+v, %v", run, err)
	}
	var ev runs.Event
	if err := c.Do(ctx, "POST", "/api/runs/"+run.ID+"/mark", map[string]string{"text": "tapped login"}, &ev); err != nil || ev.Kind != "mark" {
		t.Fatalf("mark = %+v, %v", ev, err)
	}
	var text string
	if err := c.Do(ctx, "GET", "/api/runs/"+run.ID+"/logs?grep=failed", nil, &text); err != nil || strings.TrimSpace(text) != `{"eventMessage":"login failed"}` {
		t.Fatalf("logs?grep=failed = %q, %v", text, err)
	}
	var ls []fleet.Entry
	if err := c.Do(ctx, "GET", "/api/ls", nil, &ls); err != nil || len(ls) != 1 || ls[0].Lease == nil {
		t.Fatalf("ls = %+v, %v", ls, err)
	}
	if err := c.Do(ctx, "POST", "/api/release", fleet.ReleaseRequest{UDID: "U1", Holder: "a"}, nil); err != nil {
		t.Fatal(err)
	}
	if err := c.Do(ctx, "GET", "/api/runs/"+run.ID, nil, &run); err != nil || run.Status != "aborted" || run.Reason != "lease released" {
		t.Fatalf("run after release = %+v, %v; release must abort it", run, err)
	}

	if err := Serve(ctx, Handler(f, newRuns(t), Tools{}), sock); err == nil {
		t.Fatal("second Serve on a live socket must fail")
	}
	cancel()
	if err := <-errc; err != nil {
		t.Fatalf("Serve after cancel: %v", err)
	}
}

func TestServeReplacesStaleSocket(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	sock := filepath.Join(shortDir(t), "of.sock")
	if err := os.WriteFile(sock, nil, 0o600); err != nil { // left behind by a daemon that died
		t.Fatal(err)
	}
	f := fleet.New(&oneSim{state: "Booted"}, fleet.Config{PoolPrefix: "fleet-", MaxBooted: 4}, nil)
	errc := make(chan error, 1)
	go func() { errc <- Serve(ctx, Handler(f, newRuns(t), Tools{}), sock) }()
	waitUp(t, Client{Socket: sock})
	cancel()
	if err := <-errc; err != nil {
		t.Fatal(err)
	}
}

func waitUp(t *testing.T, c Client) {
	t.Helper()
	for range 100 {
		if c.Do(context.Background(), "GET", "/api/ls", nil, nil) == nil {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("server did not come up")
}
