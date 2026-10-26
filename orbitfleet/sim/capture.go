package sim

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/mobai-app/simslim"

	"orbitfleet/fleet"
)

// StartLog streams `log stream --style ndjson` into path. The output goes through a pipe the daemon owns, so if
// the daemon dies the stream gets SIGPIPE on its next write instead of running on forever.
func (s *Sims) StartLog(udid, level, predicate, path string) (func() error, error) {
	set, err := s.set(context.Background(), udid)
	if err != nil {
		return nil, err
	}
	args := []string{"spawn", udid, "log", "stream", "--style", "ndjson", "--level", level}
	if predicate != "" {
		args = append(args, "--predicate", predicate)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, err
	}
	pr, pw, err := os.Pipe()
	if err != nil {
		f.Close()
		return nil, err
	}
	cmd := exec.Command("xcrun", simctlArgs(set, args...)...)
	cmd.Stdout = pw
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} // stop signals simctl and its child together
	if err := cmd.Start(); err != nil {
		f.Close()
		pr.Close()
		pw.Close()
		return nil, err
	}
	pw.Close()
	copied := make(chan error, 1)
	go func() { _, err := io.Copy(f, pr); copied <- err }()
	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()

	var once sync.Once
	return func() error {
		var err error
		once.Do(func() {
			syscall.Kill(-cmd.Process.Pid, syscall.SIGINT)
			select {
			case <-exited:
			case <-time.After(3 * time.Second):
				syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
				<-exited
			}
			pr.Close() // a writer outside our process group now gets SIGPIPE
			<-copied
			err = f.Close()
		})
		return err
	}, nil
}

// Launch starts app with stdout and stderr captured. simctl resolves those paths inside the simulator, so they go
// under the device's own data/tmp (a host path is silently never created). As in Xcode's console:
// OS_ACTIVITY_DT_MODE mirrors os_log and NSLog to stderr, and NSUnbufferedIO makes print() arrive as it happens
// (stdout to a file is block-buffered: without it "hello stdout" showed up only when the app exited).
func (s *Sims) Launch(ctx context.Context, udid, app string, wait bool, args []string, capture string) (int, string, string, error) {
	d, err := simslim.FindDevice(ctx, udid, "")
	if err != nil {
		return 0, "", "", err
	}
	if d.DataPath == "" {
		return 0, "", "", fmt.Errorf("%s: simctl reported no data path", udid)
	}
	dir := filepath.Join(d.DataPath, "tmp", "orbitfleet")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return 0, "", "", err
	}
	out, errPath := filepath.Join(dir, capture+".out"), filepath.Join(dir, capture+".err")
	la := []string{"launch", "--terminate-running-process", "--stdout=" + out, "--stderr=" + errPath}
	if wait {
		la = append(la, "--wait-for-debugger")
	}
	la = append(append(la, udid, app), args...)
	cmd := exec.CommandContext(ctx, "xcrun", simctlArgs(d.Set, la...)...)
	cmd.Env = append(os.Environ(), "SIMCTL_CHILD_OS_ACTIVITY_DT_MODE=enable", "SIMCTL_CHILD_NSUnbufferedIO=YES")
	b, err := cmd.CombinedOutput()
	if err != nil {
		return 0, "", "", fmt.Errorf("simctl launch %s: %w: %s", app, err, strings.TrimSpace(string(b)))
	}
	// "com.example.app: 48213"
	_, pidText, _ := strings.Cut(strings.TrimSpace(string(b)), ": ")
	pid, err := strconv.Atoi(strings.TrimSpace(pidText))
	if err != nil {
		return 0, "", "", fmt.Errorf("simctl launch: no pid in %q", b)
	}
	return pid, out, errPath, nil
}

// Screenshot saves base.avif (avifenc -q 60 -s 6: 96 KB vs 3.2 MB png, DESIGN.md D8), or base.png without avifenc.
func (s *Sims) Screenshot(ctx context.Context, udid, base string) (string, error) {
	d, err := simslim.FindDevice(ctx, udid, "")
	if err != nil {
		return "", err
	}
	png := base + ".png"
	if _, err := simctl(ctx, d.Set, "io", udid, "screenshot", "--type=png", png); err != nil {
		return "", err
	}
	enc, err := tool("avifenc")
	if err != nil {
		return png, nil
	}
	if b, err := exec.CommandContext(ctx, enc, "-q", "60", "-s", "6", png, base+".avif").CombinedOutput(); err != nil {
		s.log(fmt.Sprintf("avifenc failed, keeping png: %v: %s", err, b))
		return png, nil
	}
	os.Remove(png)
	return base + ".avif", nil
}

// Compress replaces path with path.zst. Level 15, not 19: on a real 7.4 MB log -19 took 3.64 s for 121 KB and -15
// 0.13 s for 137 KB (DESIGN.md D8); at an hour of logging -19 would hold `run stop` for minutes.
func (s *Sims) Compress(path string) (string, error) {
	z, err := tool("zstd")
	if err != nil {
		return path, err
	}
	if b, err := exec.Command(z, "-15", "--long", "-T0", "-q", "-f", "--rm", path).CombinedOutput(); err != nil {
		return path, fmt.Errorf("zstd: %w: %s", err, b)
	}
	return path + ".zst", nil
}

// Open reads a device log; a .zst is decompressed on the fly.
func (s *Sims) Open(path string) (io.ReadCloser, error) {
	if !strings.HasSuffix(path, ".zst") {
		return os.Open(path)
	}
	z, err := tool("zstd")
	if err != nil {
		return nil, err
	}
	cmd := exec.Command(z, "-dc", "--long=27", path)
	out, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	return &procReader{ReadCloser: out, cmd: cmd}, nil
}

type procReader struct {
	io.ReadCloser
	cmd *exec.Cmd
}

func (p *procReader) Close() error {
	p.ReadCloser.Close()
	p.cmd.Process.Kill()
	p.cmd.Wait()
	return nil
}

// tool finds a command on PATH or in Homebrew's prefixes: a daemon started by launchd gets a bare PATH.
func tool(name string) (string, error) {
	if p, err := exec.LookPath(name); err == nil {
		return p, nil
	}
	for _, dir := range []string{"/opt/homebrew/bin", "/usr/local/bin"} {
		if p := filepath.Join(dir, name); exists(p) {
			return p, nil
		}
	}
	return "", errors.New(name + " not found (brew install " + name + ")")
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

// Stats measures every booted simulator: summed phys_footprint and how many managed services are off.
func (s *Sims) Stats(ctx context.Context) (map[string]fleet.Stat, error) {
	top, err := simslim.FleetSnapshot(ctx, false)
	if err != nil {
		return nil, err
	}
	out := map[string]fleet.Stat{}
	for _, t := range top.Sims {
		st := fleet.Stat{ManagedTotal: t.ManagedTotal}
		if t.Memory != nil {
			st.Bytes = t.Memory.Bytes
		}
		if t.ManagedDisabled != nil {
			st.ManagedDisabled = *t.ManagedDisabled
		}
		out[t.UDID] = st
	}
	return out, nil
}

// Thumb is a JPEG of the screen (0.37 s, vs 0.48 s for png: DESIGN.md "Measured"). It goes through a temp file:
// Xcode 27's simctl writes `-` as a file named "-" in the working directory, not to stdout as its help says.
func (s *Sims) Thumb(ctx context.Context, udid string) ([]byte, error) {
	set, err := s.set(ctx, udid)
	if err != nil {
		return nil, err
	}
	f, err := os.CreateTemp("", "orbitfleet-thumb-*.jpg")
	if err != nil {
		return nil, err
	}
	f.Close()
	defer os.Remove(f.Name())
	if _, err := simctl(ctx, set, "io", udid, "screenshot", "--type=jpeg", f.Name()); err != nil {
		return nil, err
	}
	return os.ReadFile(f.Name())
}

// OpenSimulator brings the device's window up in Simulator.app.
func OpenSimulator(udid string) error {
	return exec.Command("open", "-a", "Simulator", "--args", "-CurrentDeviceUDID", udid).Run()
}

// Reveal shows path in Finder.
func Reveal(path string) error { return exec.Command("open", "-R", path).Run() }
