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
		for range 50 { // until the old daemon has let go of the socket
			if c.Do(ctx, "GET", "/api/version", nil, nil) != nil {
				break
			}
			time.Sleep(100 * time.Millisecond)
		}
	} else if c.Do(ctx, "GET", "/api/ls", nil, nil) == nil {
		return c, nil // a daemon from before /api/version: leave it
	}
	return c, startDaemon(ctx, c, dir, args)
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
	for range 100 {
		time.Sleep(100 * time.Millisecond)
		if c.Do(ctx, "GET", "/api/ls", nil, nil) == nil {
			return nil
		}
	}
	return fmt.Errorf("daemon did not start; see %s", filepath.Join(dir, "serve.log"))
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
