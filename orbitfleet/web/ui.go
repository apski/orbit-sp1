package web

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"net/http"
	"os"
	"slices"
	"strconv"
	"sync"
	"time"

	"orbitfleet/fleet"
	"orbitfleet/runs"
)

//go:embed static
var static embed.FS

// Tools are the browser UI's ties to the Mac (package sim in the daemon, fakes in tests). A nil one answers 501.
type Tools struct {
	Stats         func(context.Context) (map[string]fleet.Stat, error)   // booted simulators' footprint and slim state
	Thumb         func(ctx context.Context, udid string) ([]byte, error) // a JPEG of the screen
	OpenSimulator func(udid string) error
	Reveal        func(path string) error
	OpenPath      func(path string) error // a run's trace in Instruments, its video in QuickTime
	LoginURL      string                  // what `orbitfleet ui` opens; empty when the UI is off
	Version       Version
	Restart       func() error // stops the daemon so a newer binary can start; refuses while busy
}

// Version identifies the running daemon, so a CLI built later can tell it is stale and restart it.
type Version struct {
	Binary string    `json:"binary"` // the daemon's executable, symlinks resolved
	Mtime  time.Time `json:"mtime"`  // that file's mtime when the daemon started
	Args   []string  `json:"args"`   // to start the next one the same way
	PID    int       `json:"pid"`
}

// SimView is one card: a pool or leased device, its active (or last) run, its footprint.
type SimView struct {
	fleet.Entry
	Run  *runs.Run   `json:"run,omitempty"`
	Stat *fleet.Stat `json:"stat,omitempty"`
}

type State struct {
	Sims       []SimView `json:"sims"`
	Booted     int       `json:"booted"`
	MaxBooted  int       `json:"maxBooted"`
	Waiting    int       `json:"waiting"`
	FleetBytes int64     `json:"fleetBytes"` // summed footprint of every booted simulator
}

type Tail struct {
	Lines []string `json:"lines"`
	Next  int64    `json:"next"`
	Done  bool     `json:"done"` // the run ended: no more lines will come
}

type ui struct {
	f  *fleet.Fleet
	rs *runs.Runs
	t  Tools

	mu         sync.Mutex
	stats      map[string]fleet.Stat
	statsAt    time.Time
	refreshing bool
	thumbs     map[string]thumb
	shots      chan struct{} // at most 4 screenshots at once (0.37 s each, DESIGN.md "Measured")

	// One loop computes the state for every open page while any is watching: each state costs a `simctl list`,
	// over 1 s when other sessions keep CoreSimulator busy, so one per page per 2 s piled up past the page's wait.
	smu      sync.Mutex
	snap     State
	seq      int
	changed  chan struct{} // closed and replaced on every new snapshot
	watchers int
	looping  bool
}

type thumb struct {
	at   time.Time
	jpeg []byte
}

func uiRoutes(mux *http.ServeMux, f *fleet.Fleet, rs *runs.Runs, t Tools) {
	u := &ui{f: f, rs: rs, t: t, thumbs: map[string]thumb{}, shots: make(chan struct{}, 4), changed: make(chan struct{})}
	sub, _ := fs.Sub(static, "static")
	mux.Handle("GET /", http.FileServerFS(sub))
	mux.HandleFunc("GET /api/ui", func(w http.ResponseWriter, r *http.Request) {
		write(w, map[string]string{"url": t.LoginURL}, nil)
	})
	mux.HandleFunc("GET /api/version", func(w http.ResponseWriter, r *http.Request) { write(w, t.Version, nil) })
	mux.HandleFunc("POST /api/restart", func(w http.ResponseWriter, r *http.Request) {
		if t.Restart == nil {
			http.Error(w, "not available", http.StatusNotImplemented)
			return
		}
		write(w, struct{}{}, t.Restart())
	})
	mux.HandleFunc("POST /api/runs/{id}/open", func(w http.ResponseWriter, r *http.Request) {
		var in struct{ Path string }
		if !decode(w, r, &in) {
			return
		}
		run, err := rs.Get(r.PathValue("id"))
		if err == nil && !slices.ContainsFunc(run.Artifacts, func(a runs.Artifact) bool { return a.Path == in.Path }) {
			err = &fleet.Error{Code: "not-found", Message: "not an artifact of this run"}
		}
		if err == nil && t.OpenPath == nil {
			err = &fleet.Error{Code: "bad-request", Message: "opening files is not available"}
		}
		if err == nil {
			err = t.OpenPath(in.Path)
		}
		write(w, struct{}{}, err)
	})
	mux.HandleFunc("GET /api/state", func(w http.ResponseWriter, r *http.Request) {
		st, err := u.state(r.Context())
		write(w, st, err)
	})
	mux.HandleFunc("GET /api/events", u.events)
	mux.HandleFunc("GET /api/sims/{udid}/thumb", u.thumb)
	mux.HandleFunc("POST /api/sims/{udid}/open", func(w http.ResponseWriter, r *http.Request) {
		if t.OpenSimulator == nil {
			http.Error(w, "not available", http.StatusNotImplemented)
			return
		}
		write(w, struct{}{}, t.OpenSimulator(r.PathValue("udid")))
	})
	mux.HandleFunc("POST /api/runs/{id}/reveal", func(w http.ResponseWriter, r *http.Request) {
		run, err := rs.Get(r.PathValue("id"))
		if err == nil && t.Reveal == nil {
			http.Error(w, "not available", http.StatusNotImplemented)
			return
		}
		if err == nil {
			err = t.Reveal(run.Dir)
		}
		write(w, struct{}{}, err)
	})
	mux.HandleFunc("GET /api/runs/{id}/tail", func(w http.ResponseWriter, r *http.Request) {
		from, err := strconv.ParseInt(r.URL.Query().Get("from"), 10, 64)
		if err != nil {
			from = -1
		}
		var t Tail
		t.Lines, t.Next, t.Done, err = rs.Tail(r.PathValue("id"), from)
		write(w, t, err)
	})
	mux.HandleFunc("GET /api/runs/{id}/events", func(w http.ResponseWriter, r *http.Request) {
		evs, err := rs.Events(r.PathValue("id"))
		write(w, evs, err)
	})
	mux.HandleFunc("GET /api/runs/{id}/file", u.file)
}

func (u *ui) state(ctx context.Context) (State, error) {
	o, err := u.f.Overview(ctx)
	if err != nil {
		return State{}, err
	}
	latest, stats := u.rs.Latest(), u.statsNow()
	st := State{Booted: o.Booted, MaxBooted: o.MaxBooted, Waiting: o.Waiting, Sims: []SimView{}}
	for _, s := range stats {
		st.FleetBytes += s.Bytes
	}
	for _, e := range o.Entries {
		v := SimView{Entry: e}
		if run, ok := latest[e.UDID]; ok {
			v.Run = &run
		}
		if s, ok := stats[e.UDID]; ok {
			v.Stat = &s
		}
		st.Sims = append(st.Sims, v)
	}
	return st, nil
}

// statsNow returns the last measurement and refreshes it in the background when older than 10 s: measuring walks
// every simulator's process tree, too slow for every 2 s state push.
func (u *ui) statsNow() map[string]fleet.Stat {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.t.Stats != nil && !u.refreshing && time.Since(u.statsAt) > 10*time.Second {
		u.refreshing = true
		go func() {
			s, err := u.t.Stats(context.Background())
			u.mu.Lock()
			defer u.mu.Unlock()
			if err == nil {
				u.stats = s
			}
			u.statsAt, u.refreshing = time.Now(), false
		}()
	}
	return u.stats
}

// events pushes each new snapshot as a server-sent event until the browser goes away. Headers go out at once, so
// the page counts as connected even while the first snapshot is being computed.
func (u *ui) events(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	fmt.Fprint(w, ": connected\n\n")
	flusher.Flush()
	u.smu.Lock()
	u.watchers++
	if !u.looping {
		u.looping = true
		go u.loop()
	}
	u.smu.Unlock()
	defer func() { u.smu.Lock(); u.watchers--; u.smu.Unlock() }()
	sent := 0
	for {
		u.smu.Lock()
		snap, seq, changed := u.snap, u.seq, u.changed
		u.smu.Unlock()
		if seq != sent {
			b, _ := json.Marshal(snap)
			fmt.Fprintf(w, "data: %s\n\n", b)
			flusher.Flush()
			sent = seq
		}
		select {
		case <-r.Context().Done():
			return
		case <-changed:
		}
	}
}

// loop refreshes the shared snapshot every 2 s and stops when no page is watching.
func (u *ui) loop() {
	for {
		st, err := u.state(context.Background())
		u.smu.Lock()
		if err == nil {
			u.snap, u.seq = st, u.seq+1
			close(u.changed)
			u.changed = make(chan struct{})
		}
		if u.watchers == 0 {
			u.looping = false
			u.smu.Unlock()
			return
		}
		u.smu.Unlock()
		time.Sleep(2 * time.Second)
	}
}

func (u *ui) thumb(w http.ResponseWriter, r *http.Request) {
	if u.t.Thumb == nil {
		http.Error(w, "not available", http.StatusNotImplemented)
		return
	}
	udid := r.PathValue("udid")
	u.mu.Lock()
	c, ok := u.thumbs[udid]
	u.mu.Unlock()
	if !ok || time.Since(c.at) > 2*time.Second {
		select {
		case u.shots <- struct{}{}:
		case <-r.Context().Done():
			return
		}
		jpeg, err := u.t.Thumb(r.Context(), udid)
		<-u.shots
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		c = thumb{at: time.Now(), jpeg: jpeg}
		u.mu.Lock()
		u.thumbs[udid] = c
		u.mu.Unlock()
	}
	w.Header().Set("Content-Type", "image/jpeg")
	w.Header().Set("Cache-Control", "no-store")
	w.Write(c.jpeg)
}

// file serves one of a run's artifacts, and nothing else: the path must be in the run's artifact list. App output
// comes back as its last 64 KB.
func (u *ui) file(w http.ResponseWriter, r *http.Request) {
	run, err := u.rs.Get(r.PathValue("id"))
	if err != nil {
		write(w, nil, err)
		return
	}
	path := r.URL.Query().Get("path")
	i := slices.IndexFunc(run.Artifacts, func(a runs.Artifact) bool { return a.Path == path })
	if i < 0 {
		http.NotFound(w, r)
		return
	}
	if k := run.Artifacts[i].Kind; k == "app-out" || k == "app-err" {
		b, err := tailBytes(path, 64<<10)
		if err != nil {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.Write(b)
		return
	}
	http.ServeFile(w, r, path)
}

func tailBytes(path string, n int64) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil {
		return nil, err
	}
	b := make([]byte, min(n, fi.Size()))
	_, err = f.ReadAt(b, fi.Size()-int64(len(b)))
	return b, err
}
