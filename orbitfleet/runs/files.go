package runs

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"
)

// crashes copies the host's .ips reports for the run's app written since the run started. A report lands a second
// or two after the crash, so when the launched app is already dead and nothing matched yet, it looks again briefly.
func (r *Runs) crashes(run Run) []string {
	if run.App == "" || r.cfg.CrashDir == "" {
		return nil
	}
	for try := 0; ; try++ {
		var out []string
		entries, _ := os.ReadDir(r.cfg.CrashDir)
		for _, e := range entries {
			info, err := e.Info()
			if err != nil || !strings.HasSuffix(e.Name(), ".ips") || info.ModTime().Before(run.Started) {
				continue
			}
			src := filepath.Join(r.cfg.CrashDir, e.Name())
			if ipsBundle(src) != run.App {
				continue
			}
			dst := filepath.Join(run.Dir, "crash", e.Name())
			if os.MkdirAll(filepath.Dir(dst), 0o700) == nil && copyFile(src, dst) == nil {
				out = append(out, dst)
			}
		}
		err := syscall.Kill(run.PID, 0)
		appAlive := run.PID > 0 && (err == nil || err == syscall.EPERM)
		if len(out) > 0 || run.PID == 0 || appAlive || try == 3 {
			return out
		}
		time.Sleep(time.Second)
	}
}

// ipsBundle reads the bundleID from an .ips report's first line, a JSON header.
func ipsBundle(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	line, _ := bufio.NewReader(f).ReadBytes('\n')
	var h struct {
		BundleID string `json:"bundleID"`
	}
	json.Unmarshal(line, &h)
	return h.BundleID
}

func save(run Run) error {
	b, err := json.MarshalIndent(run, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(run.Dir, "run.json")
	if err := os.WriteFile(path+".tmp", b, 0o600); err != nil {
		return err
	}
	return os.Rename(path+".tmp", path)
}

func load(dir string) (Run, error) {
	b, err := os.ReadFile(filepath.Join(dir, "run.json"))
	if err != nil {
		return Run{}, errf("not-found", "no run %s", filepath.Base(dir))
	}
	var run Run
	return run, json.Unmarshal(b, &run)
}

func move(src, dst string) error {
	if os.Rename(src, dst) == nil {
		return nil
	}
	if err := copyFile(src, dst); err != nil {
		return err
	}
	return os.Remove(src)
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

// size is a file's size, or a bundle's total (a .trace is a directory).
func size(p string) int64 {
	var n int64
	filepath.WalkDir(p, func(_ string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			if fi, err := d.Info(); err == nil {
				n += fi.Size()
			}
		}
		return nil
	})
	return n
}

// Events returns a run's timeline.
func (r *Runs) Events(id string) ([]Event, error) {
	run, err := r.Get(id)
	if err != nil {
		return nil, err
	}
	f, err := os.Open(filepath.Join(run.Dir, "events.ndjson"))
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []Event
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var e Event
		if json.Unmarshal(sc.Bytes(), &e) == nil {
			out = append(out, e)
		}
	}
	return out, sc.Err()
}

const tailWindow = 256 << 10 // the first tail of a live log shows its last 256 KB
const tailLines = 3000       // a finished log shows its last 3000 lines

// Tail returns complete device-log lines after byte offset from (-1: the last tailWindow) and the offset to ask for
// next. A partial last line is left for the next call. A finished run's log (usually compressed) comes back whole,
// its last tailLines lines, with done set.
func (r *Runs) Tail(id string, from int64) (lines []string, next int64, done bool, err error) {
	run, err := r.Get(id)
	if err != nil {
		return nil, 0, false, err
	}
	var path string
	for _, a := range run.Artifacts {
		if a.Kind == "device-log" {
			path = a.Path
		}
	}
	if path == "" {
		return nil, 0, true, errors.New("run has no device log")
	}
	if run.Status != "running" {
		rc, err := r.sim.Open(path)
		if err != nil {
			return nil, 0, true, err
		}
		defer rc.Close()
		sc := bufio.NewScanner(rc)
		sc.Buffer(make([]byte, 64<<10), 4<<20)
		for sc.Scan() {
			lines = append(lines, sc.Text())
			if len(lines) > 2*tailLines {
				lines = slices.Clone(lines[len(lines)-tailLines:])
			}
		}
		return lines[max(0, len(lines)-tailLines):], 0, true, sc.Err()
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, false, err
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil {
		return nil, 0, false, err
	}
	size, start := fi.Size(), from
	fresh := from < 0 || from > size // first call, or the file was replaced
	if fresh {
		start = max(0, size-tailWindow)
	}
	b := make([]byte, min(size-start, 1<<20))
	n, err := f.ReadAt(b, start)
	if err != nil && !errors.Is(err, io.EOF) {
		return nil, from, false, err
	}
	b = b[:n]
	skip := 0
	if fresh && start > 0 { // started mid-line
		skip = bytes.IndexByte(b, '\n') + 1
	}
	end := max(skip, bytes.LastIndexByte(b, '\n')+1)
	if body := strings.TrimSuffix(string(b[skip:end]), "\n"); body != "" {
		lines = strings.Split(body, "\n")
	}
	return lines, start + int64(end), false, nil
}
