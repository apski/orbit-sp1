// Package runs records what an agent does on a leased simulator: the device log, app output, marks, screenshots and
// crash reports, one folder per run (DESIGN.md "Data model"). Processes are Sim's job (package sim, or a fake).
package runs

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"sync"
	"time"

	"orbitfleet/fleet"
)

type Sim interface {
	// StartLog streams the device log as ndjson into path until stop is called.
	StartLog(udid, level, predicate, path string) (stop func() error, err error)
	// Launch starts app (terminating a running copy) with stdout/stderr captured under the name capture, and returns
	// the host pid and the capture files, which sit in the simulator's data dir.
	Launch(ctx context.Context, udid, app string, wait bool, args []string, capture string) (pid int, out, errPath string, err error)
	// Screenshot writes base plus an extension (.avif, or .png without avifenc) and returns the path.
	Screenshot(ctx context.Context, udid, base string) (string, error)
	// Compress replaces path with a compressed copy and returns its path.
	Compress(path string) (string, error)
	// Open reads a device log, compressed or not.
	Open(path string) (io.ReadCloser, error)
	Debugger
}

type Config struct {
	Dir      string // one folder per run
	CrashDir string // where the host writes .ips crash reports
	Now      func() time.Time
}

type Runs struct {
	sim    Sim
	cfg    Config
	mu     sync.Mutex
	active map[string]*active // by run id
	last   map[string]Run     // by udid: the last run that ended since the daemon started
}

type active struct {
	run       Run
	stopLog   func() error
	launches  int
	seq       map[string]int // numbers handed out per artifact kind (profile-1.trace, video-2.mov)
	video     func() error   // stops the running screen recording
	videoPath string
}

func errf(code, format string, a ...any) error {
	return &fleet.Error{Code: code, Message: fmt.Sprintf(format, a...)}
}

// New loads the runs folder. A run still marked running belonged to a daemon that died; its capture processes are
// gone (they write through a pipe to the daemon), so it is aborted.
func New(s Sim, cfg Config) (*Runs, error) {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if err := os.MkdirAll(cfg.Dir, 0o700); err != nil {
		return nil, err
	}
	r := &Runs{sim: s, cfg: cfg, active: map[string]*active{}, last: map[string]Run{}}
	all, err := r.List("", time.Time{})
	if err != nil {
		return nil, err
	}
	for _, run := range all {
		if run.Status == "running" {
			now := cfg.Now()
			run.Status, run.Reason, run.Ended = "aborted", "daemon restarted", &now
			if err := save(run); err != nil {
				return nil, err
			}
		}
	}
	return r, nil
}

func (r *Runs) Start(ctx context.Context, req StartRequest) (Run, error) {
	if req.UDID == "" || req.Holder == "" {
		return Run{}, errf("bad-request", "udid and holder are required")
	}
	level := cmp.Or(req.LogLevel, "info")
	if !slices.Contains([]string{"info", "debug", "default"}, level) {
		return Run{}, errf("bad-request", "log level %q: use info, debug or default", level)
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, a := range r.active {
		if a.run.UDID == req.UDID {
			return Run{}, errf("run-active", "%s already has run %s; stop it first", req.UDID, a.run.ID)
		}
	}
	now := r.cfg.Now()
	base := "r-" + now.UTC().Format("20060102-150405") + "-" + req.UDID[:min(4, len(req.UDID))]
	id := base
	for n := 2; exists(filepath.Join(r.cfg.Dir, id)); n++ {
		id = fmt.Sprintf("%s-%d", base, n)
	}
	dir := filepath.Join(r.cfg.Dir, id)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return Run{}, err
	}
	logPath := filepath.Join(dir, "device.ndjson")
	stop, err := r.sim.StartLog(req.UDID, level, req.Predicate, logPath)
	if err != nil {
		os.RemoveAll(dir)
		return Run{}, err
	}
	run := Run{ID: id, UDID: req.UDID, Holder: req.Holder, Label: req.Label, App: req.App, Status: "running",
		Started: now, Dir: dir, Artifacts: []Artifact{{Kind: "device-log", Path: logPath}}}
	r.active[id] = &active{run: run, stopLog: stop, seq: map[string]int{}}
	r.event(run, "start", strings.TrimSpace(fmt.Sprintf("%s %s, device log at %s", req.Label, req.App, level)))
	return run, save(run)
}

func (r *Runs) Launch(ctx context.Context, id string, req LaunchRequest) (Run, error) {
	r.mu.Lock()
	a, err := r.find(id)
	if err != nil {
		r.mu.Unlock()
		return Run{}, err
	}
	app := cmp.Or(req.App, a.run.App)
	if app == "" {
		r.mu.Unlock()
		return Run{}, errf("bad-request", "no app: pass --app here or to run start")
	}
	a.launches++
	capture := fmt.Sprintf("%s-app-%d", id, a.launches)
	udid := a.run.UDID
	r.mu.Unlock()

	pid, out, errPath, err := r.sim.Launch(ctx, udid, app, req.WaitForDebugger, req.Args, capture)
	if err != nil {
		return Run{}, err
	}
	return r.update(id, func(run *Run) {
		run.PID, run.App = pid, cmp.Or(run.App, app)
		run.Artifacts = append(run.Artifacts, Artifact{Kind: "app-out", Path: out}, Artifact{Kind: "app-err", Path: errPath})
		wait := ""
		if req.WaitForDebugger {
			wait = ", waiting for a debugger"
		}
		r.event(*run, "launch", fmt.Sprintf("%s pid %d%s", app, pid, wait))
	})
}

func (r *Runs) Mark(id, text string) (Event, error) {
	if text = strings.TrimSpace(text); text == "" {
		return Event{}, errf("bad-request", "mark needs text")
	}
	var e Event
	_, err := r.update(id, func(run *Run) { e = r.event(*run, "mark", text) })
	return e, err
}

var unsafeName = regexp.MustCompile(`[^A-Za-z0-9._-]`)

func (r *Runs) Shot(ctx context.Context, id, name string) (Artifact, error) {
	r.mu.Lock()
	a, err := r.find(id)
	if err != nil {
		r.mu.Unlock()
		return Artifact{}, err
	}
	n := 1
	for _, art := range a.run.Artifacts {
		if art.Kind == "shot" {
			n++
		}
	}
	name = cmp.Or(unsafeName.ReplaceAllString(name, "-"), fmt.Sprintf("shot-%d", n))
	udid, dir := a.run.UDID, filepath.Join(a.run.Dir, "shots")
	r.mu.Unlock()

	if err := os.MkdirAll(dir, 0o700); err != nil {
		return Artifact{}, err
	}
	path, err := r.sim.Screenshot(ctx, udid, filepath.Join(dir, name))
	if err != nil {
		return Artifact{}, err
	}
	art := Artifact{Kind: "shot", Path: path, Bytes: size(path)}
	_, err = r.update(id, func(run *Run) {
		run.Artifacts = append(run.Artifacts, art)
		r.event(*run, "shot", fmt.Sprintf("%s (%d KB)", filepath.Base(path), art.Bytes/1024))
	})
	return art, err
}

func (r *Runs) Stop(ctx context.Context, id string, req StopRequest) (Run, error) {
	if req.Status != "passed" && req.Status != "failed" {
		return Run{}, errf("bad-request", "status %q: use passed or failed", req.Status)
	}
	r.mu.Lock()
	a, err := r.find(id)
	if err == nil {
		delete(r.active, id)
	}
	r.mu.Unlock()
	if err != nil {
		return Run{}, err
	}
	return r.finish(a, req.Status, req.Reason)
}

// AbortUDID ends the active run on a device, if any: the lease expired or was released.
func (r *Runs) AbortUDID(ctx context.Context, udid, reason string) {
	r.mu.Lock()
	var hit []*active
	for id, a := range r.active {
		if udid == "" || a.run.UDID == udid {
			hit = append(hit, a)
			delete(r.active, id)
		}
	}
	r.mu.Unlock()
	for _, a := range hit {
		r.finish(a, "aborted", reason)
	}
}

// AbortAll ends every active run (daemon shutdown).
func (r *Runs) AbortAll(ctx context.Context, reason string) { r.AbortUDID(ctx, "", reason) }

// finish stops capture, moves app output into the run folder, collects crash reports and compresses the log.
// The run is already out of r.active, so nothing else touches it. A step that fails becomes a warning on the run:
// the run still ends, and what was captured stays.
func (r *Runs) finish(a *active, status, reason string) (Run, error) {
	run := &a.run
	warn := func(s string) { run.Warnings = append(run.Warnings, s) }
	now := r.cfg.Now()
	run.Status, run.Reason, run.Ended = status, reason, &now
	save(*run) // the final status first: readers must not see "running" while the slow steps below run
	if a.video != nil {
		if err := a.video(); err != nil {
			warn("stop video: " + err.Error())
		}
		run.Artifacts = append(run.Artifacts, Artifact{Kind: "video", Path: a.videoPath})
		run.Recording = false
	}
	if err := a.stopLog(); err != nil {
		warn("stop log: " + err.Error())
	}
	for i, art := range run.Artifacts {
		switch art.Kind {
		case "app-out", "app-err":
			dst := filepath.Join(run.Dir, strings.TrimPrefix(filepath.Base(art.Path), run.ID+"-"))
			if err := move(art.Path, dst); err != nil {
				warn("move app output: " + err.Error())
				continue
			}
			run.Artifacts[i].Path = dst
		case "device-log":
			if p, err := r.sim.Compress(art.Path); err != nil {
				warn("compress log: " + err.Error())
			} else {
				run.Artifacts[i].Path = p
			}
		}
	}
	for _, c := range r.crashes(*run) {
		run.Artifacts = append(run.Artifacts, Artifact{Kind: "crash", Path: c})
		r.event(*run, "crash", filepath.Base(c))
	}
	for i := range run.Artifacts {
		run.Artifacts[i].Bytes = size(run.Artifacts[i].Path)
	}
	r.event(*run, "stop", strings.TrimSpace(status+" "+reason))
	r.mu.Lock()
	r.last[run.UDID] = *run
	r.mu.Unlock()
	return *run, save(*run)
}

// Latest returns, per udid, the active run or else the last one that ended since the daemon started.
func (r *Runs) Latest() map[string]Run {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := maps.Clone(r.last)
	for _, a := range r.active {
		out[a.run.UDID] = a.run
	}
	return out
}

func (r *Runs) Get(id string) (Run, error) {
	r.mu.Lock()
	if a := r.active[id]; a != nil {
		defer r.mu.Unlock()
		return a.run, nil
	}
	r.mu.Unlock()
	return load(filepath.Join(r.cfg.Dir, id))
}

// Logs opens a run's device log (ndjson), live or compressed.
func (r *Runs) Logs(id string) (io.ReadCloser, error) {
	run, err := r.Get(id)
	if err != nil {
		return nil, err
	}
	for _, a := range run.Artifacts {
		if a.Kind == "device-log" {
			return r.sim.Open(a.Path)
		}
	}
	return nil, errf("not-found", "run %s has no device log", id)
}

// List returns runs newest first, optionally only one holder's and only those started since a time.
// ponytail: reads every run.json; add an index when the folder holds thousands.
func (r *Runs) List(holder string, since time.Time) ([]Run, error) {
	entries, err := os.ReadDir(r.cfg.Dir)
	if err != nil {
		return nil, err
	}
	var out []Run
	for _, e := range entries {
		run, err := load(filepath.Join(r.cfg.Dir, e.Name()))
		if err != nil || (holder != "" && run.Holder != holder) || run.Started.Before(since) {
			continue
		}
		out = append(out, run)
	}
	slices.SortFunc(out, func(a, b Run) int { return cmp.Or(b.Started.Compare(a.Started), cmp.Compare(b.ID, a.ID)) })
	return out, nil
}

// find returns an active run. Caller holds mu.
func (r *Runs) find(id string) (*active, error) {
	if a := r.active[id]; a != nil {
		return a, nil
	}
	if exists(filepath.Join(r.cfg.Dir, id, "run.json")) {
		return nil, errf("run-ended", "run %s has ended", id)
	}
	return nil, errf("not-found", "no run %s", id)
}

// update applies fn to an active run and saves it, unless it ended meanwhile.
func (r *Runs) update(id string, fn func(*Run)) (Run, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	a, err := r.find(id)
	if err != nil {
		return Run{}, err
	}
	fn(&a.run)
	return a.run, save(a.run)
}

func (r *Runs) event(run Run, kind, text string) Event {
	e := Event{T: r.cfg.Now(), Kind: kind, Text: text}
	f, err := os.OpenFile(filepath.Join(run.Dir, "events.ndjson"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err == nil {
		json.NewEncoder(f).Encode(e)
		f.Close()
	}
	return e
}
