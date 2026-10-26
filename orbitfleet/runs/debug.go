package runs

import (
	"cmp"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

// Sim's debug half, kept apart from capture so a fake can stay small.
type Debugger interface {
	// Profile records an Instruments trace of pid for limit into out (a .trace bundle).
	Profile(ctx context.Context, udid string, pid int, template string, limit time.Duration, out string) error
	// StartVideo records the screen into path until stop is called.
	StartVideo(udid, path string) (stop func() error, err error)
	// Dyld returns DYLD_INSERT_LIBRARIES from the simulator's launchd ("" when unset).
	Dyld(ctx context.Context, udid string) (string, error)
	// OpenTerminal runs a .command script in Terminal.
	OpenTerminal(path string) error
}

func alive(pid int) bool {
	err := syscall.Kill(pid, 0)
	return pid > 0 && (err == nil || err == syscall.EPERM)
}

// target returns what a debug action needs from an active run whose app is running, and the next number for kind.
func (r *Runs) target(id, kind string) (udid, dir string, pid, n int, err error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	a, err := r.find(id)
	if err != nil {
		return "", "", 0, 0, err
	}
	if !alive(a.run.PID) {
		return "", "", 0, 0, errf("bad-request", "no app running in run %s: orbitfleet run launch first", id)
	}
	a.seq[kind]++
	return a.run.UDID, a.run.Dir, a.run.PID, a.seq[kind], nil
}

// LLDB returns the command that attaches lldb to the run's app; open also runs it in Terminal. Simulator apps are
// host processes, so lldb attaches by host pid. Apple's own apps refuse it ("Not allowed to attach"); apps built
// for debugging do not.
func (r *Runs) LLDB(ctx context.Context, id string, open bool) (Debug, error) {
	_, dir, pid, _, err := r.target(id, "lldb")
	if err != nil {
		return Debug{}, err
	}
	d := Debug{PID: pid, Command: fmt.Sprintf("xcrun lldb -p %d", pid)}
	text := d.Command
	if open {
		path := filepath.Join(dir, "lldb.command")
		if err := os.WriteFile(path, []byte("#!/bin/sh\nexec "+d.Command+"\n"), 0o700); err != nil {
			return Debug{}, err
		}
		if err := r.sim.OpenTerminal(path); err != nil {
			return Debug{}, err
		}
		text += " (opened in Terminal)"
	}
	_, err = r.update(id, func(run *Run) { r.event(*run, "debug", text) })
	return d, err
}

// Profile records an Instruments trace of the run's app (Time Profiler, 30 s by default; at most 10 min). It blocks
// for the recording. A trace taken while DYLD_INSERT_LIBRARIES is set (argent's dylib) carries a note: its timings
// include the injected code (orbit-SP1 HARNESS rule 2).
func (r *Runs) Profile(ctx context.Context, id, template string, limit time.Duration) (Artifact, error) {
	template, limit = cmp.Or(template, "Time Profiler"), cmp.Or(limit, 30*time.Second)
	if limit > 10*time.Minute {
		return Artifact{}, errf("bad-request", "time limit %s: at most 10m", limit)
	}
	udid, dir, pid, n, err := r.target(id, "trace")
	if err != nil {
		return Artifact{}, err
	}
	art := Artifact{Kind: "trace", Path: filepath.Join(dir, fmt.Sprintf("profile-%d.trace", n))}
	if v, _ := r.sim.Dyld(ctx, udid); v != "" {
		art.Note = "DYLD_INSERT_LIBRARIES was set (" + v + "): an injected dylib skews these timings"
	}
	if err := r.sim.Profile(ctx, udid, pid, template, limit, art.Path); err != nil {
		return Artifact{}, err
	}
	art.Bytes = size(art.Path)
	_, err = r.update(id, func(run *Run) {
		run.Artifacts = append(run.Artifacts, art)
		r.event(*run, "debug", fmt.Sprintf("profile %s %s -> %s", limit, template, filepath.Base(art.Path)))
	})
	return art, err
}

// Video starts or stops a screen recording. Stopping the run stops it too.
func (r *Runs) Video(ctx context.Context, id string, start bool) (Artifact, error) {
	r.mu.Lock()
	a, err := r.find(id)
	if err != nil {
		r.mu.Unlock()
		return Artifact{}, err
	}
	if !start {
		if a.video == nil {
			r.mu.Unlock()
			return Artifact{}, errf("bad-request", "run %s is not recording", id)
		}
		stop, path := a.video, a.videoPath
		a.video, a.run.Recording = nil, false
		r.mu.Unlock()
		err := stop()
		art := Artifact{Kind: "video", Path: path, Bytes: size(path)}
		if _, uerr := r.update(id, func(run *Run) {
			run.Artifacts = append(run.Artifacts, art)
			r.event(*run, "debug", fmt.Sprintf("video stopped: %s (%d KB)", filepath.Base(path), art.Bytes/1024))
		}); err == nil {
			err = uerr
		}
		return art, err
	}
	if a.video != nil {
		r.mu.Unlock()
		return Artifact{}, errf("bad-request", "run %s is already recording: debug video stop first", id)
	}
	a.seq["video"]++
	path := filepath.Join(a.run.Dir, fmt.Sprintf("video-%d.mov", a.seq["video"]))
	udid := a.run.UDID
	r.mu.Unlock()

	stop, err := r.sim.StartVideo(udid, path)
	if err != nil {
		return Artifact{}, err
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.active[id] != a { // the run ended while the recording started
		stop()
		return Artifact{}, errf("run-ended", "run %s has ended", id)
	}
	a.video, a.videoPath, a.run.Recording = stop, path, true
	r.event(a.run, "debug", "video recording -> "+filepath.Base(path))
	return Artifact{Kind: "video", Path: path}, save(a.run)
}
