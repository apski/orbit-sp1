package sim

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

// Profile records `xctrace record --attach <pid>` on the simulator into out (a .trace bundle). It blocks for limit.
func (s *Sims) Profile(ctx context.Context, udid string, pid int, template string, limit time.Duration, out string) error {
	b, err := exec.CommandContext(ctx, "xcrun", "xctrace", "record", "--template", template, "--device", udid,
		"--attach", fmt.Sprint(pid), "--time-limit", fmt.Sprintf("%dms", limit.Milliseconds()), "--output", out).CombinedOutput()
	if err != nil {
		return fmt.Errorf("xctrace: %w: %s", err, strings.TrimSpace(string(b)))
	}
	return nil
}

// StartVideo runs `simctl io recordVideo` (HEVC, a QuickTime movie) until stop, which sends SIGINT so simctl
// finishes the file. It returns once simctl says "Recording started" (about 1.4 s): a SIGINT before that was
// ignored under load, and stop then timed out.
func (s *Sims) StartVideo(udid, path string) (func() error, error) {
	set, err := s.set(context.Background(), udid)
	if err != nil {
		return nil, err
	}
	cmd := exec.Command("xcrun", simctlArgs(set, "io", udid, "recordVideo", "--codec=hevc", "--force", path)...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	started := make(chan bool, 1)
	go func() {
		sc := bufio.NewScanner(stderr)
		for sc.Scan() {
			if strings.Contains(sc.Text(), "Recording started") {
				started <- true
				break
			}
		}
		io.Copy(io.Discard, stderr)
		started <- false
	}()
	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()
	kill := func() { syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL); <-exited }
	select {
	case ok := <-started:
		if !ok {
			kill()
			return nil, fmt.Errorf("recordVideo exited before recording")
		}
	case <-time.After(20 * time.Second):
		kill()
		return nil, fmt.Errorf("recordVideo did not start recording in 20 s")
	}
	return func() error {
		syscall.Kill(-cmd.Process.Pid, syscall.SIGINT)
		select {
		case <-exited:
			return nil
		case <-time.After(20 * time.Second):
			kill()
			return fmt.Errorf("recordVideo did not finish %s in 20 s", path)
		}
	}, nil
}

// Dyld returns DYLD_INSERT_LIBRARIES from the simulator's launchd ("" when unset).
func (s *Sims) Dyld(ctx context.Context, udid string) (string, error) {
	set, err := s.set(ctx, udid)
	if err != nil {
		return "", err
	}
	return simctl(ctx, set, "spawn", udid, "launchctl", "getenv", "DYLD_INSERT_LIBRARIES")
}

// OpenTerminal runs a .command script in a new Terminal window (no Automation permission needed, unlike osascript).
func (s *Sims) OpenTerminal(path string) error {
	return exec.Command("open", "-a", "Terminal", path).Run()
}

// OpenPath opens a file with its default app: a .trace in Instruments, a .mov in QuickTime.
func OpenPath(path string) error { return exec.Command("open", path).Run() }
