package web

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"orbitfleet/fleet"
	"orbitfleet/runs"
)

func TestServeTCPRefusesNonLoopback(t *testing.T) {
	for _, addr := range []string{"0.0.0.0:0", ":0", "192.168.1.5:0", "localhost:0", "[::]:0"} {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		err := ServeTCP(ctx, http.NotFoundHandler(), addr)
		cancel()
		if err == nil || !strings.Contains(err.Error(), "loopback") {
			t.Errorf("ServeTCP(%q) = %v; must refuse anything but a loopback IP", addr, err)
		}
	}
}

func TestGuard(t *testing.T) {
	g := Guard(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(299) }), "s3cret", 7421)
	do := func(method, target, host, cookie string, hdr bool) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, target, nil)
		req.Host = host
		if cookie != "" {
			req.AddCookie(&http.Cookie{Name: "orbitfleet", Value: cookie})
		}
		if hdr {
			req.Header.Set("X-Orbitfleet", "1")
		}
		w := httptest.NewRecorder()
		g.ServeHTTP(w, req)
		return w
	}
	ok := "127.0.0.1:7421"
	cases := []struct {
		name, method, target, host, cookie string
		hdr                                bool
		want                               int
	}{
		{"no cookie", "GET", "/", ok, "", false, 401},
		{"wrong cookie", "GET", "/api/state", ok, "nope", false, 401},
		{"foreign host (DNS rebinding)", "GET", "/api/state", "evil.example:7421", "s3cret", false, 403},
		{"wrong port", "GET", "/api/state", "127.0.0.1:80", "s3cret", false, 403},
		{"cookie, read", "GET", "/api/state", ok, "s3cret", false, 299},
		{"localhost name", "GET", "/api/state", "localhost:7421", "s3cret", false, 299},
		{"write without header (CSRF)", "POST", "/api/renew", ok, "s3cret", false, 403},
		{"write with header", "POST", "/api/renew", ok, "s3cret", true, 299},
		{"bad login token", "GET", "/login?t=nope", ok, "", false, 401},
	}
	for _, c := range cases {
		if got := do(c.method, c.target, c.host, c.cookie, c.hdr).Code; got != c.want {
			t.Errorf("%s: %d, want %d", c.name, got, c.want)
		}
	}
	w := do("GET", "/login?t=s3cret&next="+url.QueryEscape("/#sim=U1"), ok, "", false)
	ck := w.Result().Cookies()
	if w.Code != 302 || w.Header().Get("Location") != "/#sim=U1" || len(ck) != 1 || ck[0].Value != "s3cret" ||
		!ck[0].HttpOnly || ck[0].SameSite != http.SameSiteStrictMode {
		t.Fatalf("login: %d %q %+v", w.Code, w.Header().Get("Location"), ck)
	}
	if loc := do("GET", "/login?t=s3cret&next="+url.QueryEscape("//evil.example/"), ok, "", false).Header().Get("Location"); loc != "/" {
		t.Fatalf("open redirect: next=//evil.example -> %q", loc)
	}
}

func TestStateTailFiles(t *testing.T) {
	ctx := context.Background()
	f := fleet.New(&oneSim{state: "Booted"}, fleet.Config{PoolPrefix: "fleet-", MaxBooted: 4, TTL: time.Hour}, nil)
	rs := newRuns(t)
	tools := Tools{Stats: func(context.Context) (map[string]fleet.Stat, error) {
		return map[string]fleet.Stat{"U1": {Bytes: 900 << 20, ManagedDisabled: 168, ManagedTotal: 172}}, nil
	}}
	h := Handler(f, rs, tools)
	get := func(target string, out any) int {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", target, nil))
		if out != nil {
			json.Unmarshal(w.Body.Bytes(), out)
		}
		return w.Code
	}
	if _, err := f.Claim(ctx, fleet.ClaimRequest{Holder: "a"}); err != nil {
		t.Fatal(err)
	}
	run, err := rs.Start(ctx, runs.StartRequest{UDID: "U1", Holder: "a", Label: "login"})
	if err != nil {
		t.Fatal(err)
	}

	var st State
	get("/api/state", &st)
	if len(st.Sims) != 1 || st.Sims[0].Lease == nil || st.Sims[0].Lease.Holder != "a" || st.Sims[0].Run == nil ||
		st.Sims[0].Run.ID != run.ID || st.Booted != 1 || st.MaxBooted != 4 {
		t.Fatalf("state = %+v", st)
	}
	time.Sleep(50 * time.Millisecond) // stats refresh in the background
	get("/api/state", &st)
	if st.Sims[0].Stat == nil || st.Sims[0].Stat.ManagedDisabled != 168 || st.FleetBytes != 900<<20 {
		t.Fatalf("stats not in state: %+v", st)
	}

	var tail Tail
	get("/api/runs/"+run.ID+"/tail?from=-1", &tail)
	if len(tail.Lines) != 2 || !strings.Contains(tail.Lines[0], "boot ok") || tail.Next <= 0 || tail.Done {
		t.Fatalf("first tail = %+v", tail)
	}
	logPath := run.Artifacts[0].Path
	fh, _ := os.OpenFile(logPath, os.O_APPEND|os.O_WRONLY, 0)
	fh.WriteString(`{"eventMessage":"new line"}` + "\n" + `{"eventMessage":"partial`)
	fh.Close()
	next := tail.Next
	get("/api/runs/"+run.ID+"/tail?from="+itoa(next), &tail)
	if len(tail.Lines) != 1 || !strings.Contains(tail.Lines[0], "new line") {
		t.Fatalf("incremental tail = %+v; want only the new complete line", tail)
	}

	var evs []runs.Event
	if get("/api/runs/"+run.ID+"/events", &evs); len(evs) != 1 || evs[0].Kind != "start" {
		t.Fatalf("events = %+v", evs)
	}
	if code := get("/api/runs/"+run.ID+"/file?path="+url.QueryEscape("/etc/passwd"), nil); code != 404 {
		t.Fatalf("file outside the run's artifacts: %d, want 404", code)
	}
	if code := get("/api/runs/"+run.ID+"/file?path="+url.QueryEscape(logPath), nil); code != 200 {
		t.Fatalf("artifact file: %d", code)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/", nil))
	if w.Code != 200 || !strings.Contains(w.Body.String(), "orbitfleet") {
		t.Fatalf("index: %d", w.Code)
	}
}

func itoa(n int64) string { b, _ := json.Marshal(n); return string(b) }

func TestVersionAndRestart(t *testing.T) {
	f := fleet.New(&oneSim{state: "Booted"}, fleet.Config{PoolPrefix: "fleet-", MaxBooted: 4}, nil)
	busy := true
	tools := Tools{
		Version: Version{Binary: "/bin/orbitfleet", Mtime: time.Unix(100, 0), Args: []string{"serve", "--port", "0"}, PID: 42},
		Restart: func() error {
			if busy {
				return &fleet.Error{Code: "busy", Message: "1 run active"}
			}
			return nil
		},
	}
	h := Handler(f, newRuns(t), tools)
	do := func(method, target string) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest(method, target, nil))
		return w
	}
	var v Version
	json.Unmarshal(do("GET", "/api/version").Body.Bytes(), &v)
	if v.PID != 42 || v.Binary != "/bin/orbitfleet" || len(v.Args) != 3 || !v.Mtime.Equal(time.Unix(100, 0)) {
		t.Fatalf("version = %+v", v)
	}
	if w := do("POST", "/api/restart"); w.Code != 409 {
		t.Fatalf("restart while busy: %d, want 409", w.Code)
	}
	busy = false
	if w := do("POST", "/api/restart"); w.Code != 200 {
		t.Fatalf("restart when idle: %d", w.Code)
	}
}

// Two pages share one state loop; each gets the snapshot over SSE, and the loop stops when both leave.
func TestEventsShareOneLoop(t *testing.T) {
	f := fleet.New(&oneSim{state: "Booted"}, fleet.Config{PoolPrefix: "fleet-", MaxBooted: 4}, nil)
	srv := httptest.NewServer(Handler(f, newRuns(t), Tools{}))
	defer srv.Close()
	read := func(ctx context.Context) (string, error) {
		req, _ := http.NewRequestWithContext(ctx, "GET", srv.URL+"/api/events", nil)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return "", err
		}
		defer resp.Body.Close()
		buf := make([]byte, 0, 4096)
		chunk := make([]byte, 1024)
		for !strings.Contains(string(buf), "data: ") || !strings.HasSuffix(string(buf), "\n\n") {
			n, err := resp.Body.Read(chunk)
			if err != nil {
				return string(buf), err
			}
			buf = append(buf, chunk[:n]...)
		}
		return string(buf), nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	got := make(chan string, 2)
	for range 2 {
		go func() { s, _ := read(ctx); got <- s }()
	}
	for range 2 {
		if s := <-got; !strings.Contains(s, ": connected") || !strings.Contains(s, `"name":"fleet-01"`) {
			t.Fatalf("page got %q", s)
		}
	}
}
