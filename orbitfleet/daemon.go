package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"

	"orbitfleet/fleet"
	"orbitfleet/runs"
	"orbitfleet/web"
)

// connect returns a client for the daemon. It starts `orbitfleet serve` when none answers, and restarts one that runs
// an older build of this same binary (go build replaced the file since the daemon started), with the daemon's own
// arguments. A busy daemon (a run active, a claim preparing or queued) refuses; it restarts on a later command.
func connect(ctx context.Context) (web.Client, error) {
	dir, err := stateDir()
	if err != nil {
		return web.Client{}, err
	}
	c := web.Client{Socket: filepath.Join(dir, "orbitfleet.sock")}
	args := []string{"serve"}
	var v web.Version
	if err := c.Do(ctx, "GET", "/api/version", nil, &v); err == nil {
		if !newerThan(v) {
			return c, nil
		}
		if err := c.Do(ctx, "POST", "/api/restart", nil, nil); err != nil {
			fmt.Fprintf(os.Stderr, "orbitfleet: the daemon runs an older build; it restarts once idle (%v)\n", err)
			return c, nil
		}
		args = v.Args
		for range 200 { // until the old daemon has exited and dropped its lock (it exits within 15 s)
			if !daemonRunning(dir) {
				break
			}
			time.Sleep(100 * time.Millisecond)
		}
	} else if c.Do(ctx, "GET", "/api/ls", nil, nil) == nil {
		return c, nil // a daemon from before /api/version: leave it
	} else if daemonRunning(dir) {
		return c, waitUp(ctx, c, dir) // one is starting (another agent's first command): use it, do not start a second
	}
	if err := startDaemon(ctx, c, dir, args); err != nil {
		return c, err
	}
	return c, nil
}

// waitUp waits up to 10 s for a starting daemon to answer.
func waitUp(ctx context.Context, c web.Client, dir string) error {
	for range 100 {
		if c.Do(ctx, "GET", "/api/ls", nil, nil) == nil {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("daemon did not start; see %s", filepath.Join(dir, "serve.log"))
}

func startDaemon(ctx context.Context, c web.Client, dir string, args []string) error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	logf, err := os.OpenFile(filepath.Join(dir, "serve.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer logf.Close()
	cmd := exec.Command(self, args...)
	cmd.Stdout, cmd.Stderr = logf, logf
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true} // outlives the agent's shell
	if err := cmd.Start(); err != nil {
		return err
	}
	go cmd.Wait()
	return waitUp(ctx, c, dir)
}

// selfBinary is this executable with symlinks resolved (~/.local/bin/orbitfleet is one) and its mtime.
func selfBinary() (string, time.Time, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", time.Time{}, err
	}
	if exe, err = filepath.EvalSymlinks(exe); err != nil {
		return "", time.Time{}, err
	}
	fi, err := os.Stat(exe)
	if err != nil {
		return "", time.Time{}, err
	}
	return exe, fi.ModTime(), nil
}

// newerThan reports whether this CLI is a later build of the binary the daemon runs. Another binary (a test copy
// elsewhere) never restarts it.
func newerThan(v web.Version) bool {
	self, mtime, err := selfBinary()
	return err == nil && self == v.Binary && mtime.After(v.Mtime)
}

func daemonVersion() (web.Version, error) {
	self, mtime, err := selfBinary()
	return web.Version{Binary: self, Mtime: mtime, Args: os.Args[1:], PID: os.Getpid()}, err
}

// restartIfIdle stops the daemon for a newer binary unless that would cut work off: an active run loses its capture
// processes, a claim mid-prepare or queued loses its request. Leases survive (leases.json).
// ponytail: a claim arriving in the 200 ms before stop is cut off; add a drain flag if that ever bites.
func restartIfIdle(f *fleet.Fleet, rs *runs.Runs, stop context.CancelFunc) func() error {
	return func() error {
		o, err := f.Overview(context.Background())
		if err != nil {
			return err
		}
		active, preparing := 0, 0
		for _, r := range rs.Latest() {
			if r.Status == "running" {
				active++
			}
		}
		for _, e := range o.Entries {
			if e.Lease != nil && !e.Lease.Ready {
				preparing++
			}
		}
		if active+preparing+o.Waiting > 0 {
			return &fleet.Error{Code: "busy", Message: fmt.Sprintf("%d runs active, %d claims preparing, %d queued", active, preparing, o.Waiting)}
		}
		log.Print("restarting for a newer binary")
		time.AfterFunc(200*time.Millisecond, stop)
		return nil
	}
}

// lockDaemon takes the state dir's daemon lock (flock on daemon.lock) for this process's life; the kernel drops it
// when the process ends, SIGKILL included. A second daemon fails here, before it loads any state.
func lockDaemon(dir string) (release func(), err error) {
	f, err := os.OpenFile(filepath.Join(dir, "daemon.lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		f.Close()
		return nil, fmt.Errorf("another orbitfleet daemon is running for %s", dir)
	}
	return func() { f.Close() }, nil
}

// daemonRunning reports whether a daemon holds dir's lock (it may still be starting and not answer yet).
func daemonRunning(dir string) bool {
	release, err := lockDaemon(dir)
	if err != nil {
		return true
	}
	release()
	return false
}
