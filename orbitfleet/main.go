// orbitfleet leases slim iOS simulators to agents. Design: DESIGN.md. Agent protocol: skills/orbitfleet/SKILL.md.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"text/tabwriter"
	"time"

	"orbitfleet/fleet"
	"orbitfleet/runs"
	"orbitfleet/sim"
	"orbitfleet/web"
)

const usage = `orbitfleet: lease slim simulators to agents (every command takes --json)

  orbitfleet claim [--holder h] [--purpose p] [--device udid|name] [--runtime 26.4] [--ttl 30m] [--wait 10m]
  orbitfleet release <udid> [--holder h] [--force] [--shutdown]
  orbitfleet renew <udid>
  orbitfleet ls
  orbitfleet run start <udid> [--label l] [--app bundle.id] [--log-level info|debug] [--predicate p]
  orbitfleet run launch <run> [--app bundle.id] [--wait-for-debugger] [-- app args]
  orbitfleet run mark <run> "<text>"
  orbitfleet run shot <run> [name]
  orbitfleet run stop <run> --status passed|failed [--reason r]
  orbitfleet run show <run> | run logs <run> [--grep x]
  orbitfleet runs [--holder h] [--since 24h]
  orbitfleet debug <run> lldb [--open] | profile [--template t] [--time-limit 30s] | video start|stop
  orbitfleet ui [--print]                     open the browser UI (signed in)
  orbitfleet version                          this binary and the running daemon
  orbitfleet serve [--pool-prefix fleet-] [--max-booted n] [--profile ci.json] [--ttl 30m] [--port 7421]

Holder defaults to $ORBITFLEET_HOLDER. State: $ORBITFLEET_HOME, else ~/Library/Application Support/orbitfleet.
The first command starts the daemon if it is not running.`

func main() {
	if len(os.Args) < 2 || os.Args[1] == "help" || os.Args[1] == "-h" || os.Args[1] == "--help" {
		fmt.Println(usage)
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	cmd, args := os.Args[1], os.Args[2:]
	var err error
	switch cmd {
	case "serve":
		err = serve(ctx, args)
	case "claim", "release", "renew", "ls", "ui", "version":
		err = client(ctx, cmd, args)
	case "run", "runs":
		err = runCmd(ctx, cmd, args)
	case "debug":
		err = debugCmd(ctx, args)
	default:
		fmt.Fprintln(os.Stderr, usage)
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "orbitfleet:", err)
		os.Exit(1)
	}
}

func stateDir() (string, error) {
	dir := os.Getenv("ORBITFLEET_HOME")
	if dir == "" {
		cfg, err := os.UserConfigDir()
		if err != nil {
			return "", err
		}
		dir = filepath.Join(cfg, "orbitfleet")
	}
	return dir, os.MkdirAll(dir, 0o700)
}

func serve(ctx context.Context, args []string) error {
	ctx, stopServe := context.WithCancel(ctx) // a newer binary asks for this through /api/restart
	defer stopServe()
	go func() { // a stop must end the process: one under heavy load once ignored SIGTERM and leaked
		<-ctx.Done()
		time.Sleep(15 * time.Second)
		log.Print("shutdown took over 15 s; exiting")
		os.Exit(1)
	}()
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	prefix := fs.String("pool-prefix", "fleet-", "devices claimable without naming them")
	maxBooted := fs.Int("max-booted", defaultMaxBooted(), "booted simulators on this Mac before claims queue")
	profile := fs.String("profile", "", "simslim profile file (default: every category off)")
	ttl := fs.Duration("ttl", 30*time.Minute, "default lease length")
	port := fs.Int("port", 7421, "browser UI on 127.0.0.1 (0: off)")
	parse(fs, args)
	dir, err := stateDir()
	if err != nil {
		return err
	}
	release, err := lockDaemon(dir) // first: a second daemon must not load (and abort) this one's state
	if err != nil {
		return err
	}
	defer release()
	sims, err := sim.New(*profile, func(m string) { log.Print(m) })
	if err != nil {
		return err
	}
	leasesPath := filepath.Join(dir, "leases.json")
	var saved []fleet.Lease
	if b, err := os.ReadFile(leasesPath); err == nil {
		if err := json.Unmarshal(b, &saved); err != nil {
			return fmt.Errorf("%s: %w", leasesPath, err)
		}
	}
	f := fleet.New(sims, fleet.Config{
		PoolPrefix: *prefix, MaxBooted: *maxBooted, TTL: *ttl,
		Save: func(l []fleet.Lease) error { return writeAtomic(leasesPath, l) },
	}, saved)
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	rs, err := runs.New(sims, runs.Config{Dir: filepath.Join(dir, "runs"), CrashDir: filepath.Join(home, "Library/Logs/DiagnosticReports")})
	if err != nil {
		return err
	}
	go func() {
		t := time.NewTicker(15 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				for _, l := range f.Reap() {
					log.Printf("lease expired: %s (%s) held by %s", l.Name, l.UDID, l.Holder)
					rs.AbortUDID(ctx, l.UDID, "lease expired")
				}
			}
		}
	}()
	sock := filepath.Join(dir, "orbitfleet.sock")
	log.Printf("serving on %s (pool %q, max booted %d, ttl %s, %d leases restored)", sock, *prefix, *maxBooted, *ttl, len(saved))
	version, err := daemonVersion()
	if err != nil {
		return err
	}
	pidFile := filepath.Join(dir, "daemon.pid")
	os.WriteFile(pidFile, []byte(fmt.Sprint(version.PID)), 0o600)
	defer func() { // only ours: after a restart the next daemon may already have written its own
		if b, _ := os.ReadFile(pidFile); string(b) == fmt.Sprint(version.PID) {
			os.Remove(pidFile)
		}
	}()
	tools := web.Tools{Stats: sims.Stats, Thumb: sims.Thumb, OpenSimulator: sim.OpenSimulator, Reveal: sim.Reveal,
		OpenPath: sim.OpenPath, Version: version, Restart: restartIfIdle(f, rs, stopServe)}
	var token string
	if *port > 0 {
		if token, err = uiToken(dir); err != nil {
			return err
		}
		tools.LoginURL = fmt.Sprintf("http://127.0.0.1:%d/login?t=%s", *port, token)
	}
	h := web.Handler(f, rs, tools)
	if *port > 0 {
		go func() {
			addr := fmt.Sprintf("127.0.0.1:%d", *port)
			log.Printf("browser UI on http://%s (orbitfleet ui)", addr)
			if err := web.ServeTCP(ctx, web.Guard(h, token, *port), addr); err != nil {
				log.Printf("browser UI off: %v", err) // agents keep working over the socket
			}
		}()
	}
	err = web.Serve(ctx, h, sock)
	rs.AbortAll(context.Background(), "daemon stopped")
	return err
}

// uiToken is the browser UI's sign-in secret, kept in a 0600 file so a restart keeps the browser signed in.
func uiToken(dir string) (string, error) {
	path := filepath.Join(dir, "ui.token")
	if b, err := os.ReadFile(path); err == nil && len(b) >= 32 {
		return string(b), nil
	}
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	token := hex.EncodeToString(b)
	return token, os.WriteFile(path, []byte(token), 0o600)
}

// defaultMaxBooted is DESIGN.md D10: min(4, (RAM - 6 GB) / 1 GB). RAM allows ~16 slim sims, but the Mac contends on
// CPU and simctl long before that (load 677 with 11 booted, 2026-10-26).
func defaultMaxBooted() int {
	ram, err := sim.HostRAM()
	if err != nil {
		return 4
	}
	return max(1, min(4, int((ram>>30)-6)))
}

func writeAtomic(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func client(ctx context.Context, cmd string, args []string) error {
	fs := flag.NewFlagSet(cmd, flag.ExitOnError)
	asJSON := fs.Bool("json", false, "machine-readable output")
	holder := fs.String("holder", os.Getenv("ORBITFLEET_HOLDER"), "who holds the lease")
	purpose := fs.String("purpose", "", "claim: what the agent is testing")
	device := fs.String("device", "", "claim: udid or exact name")
	runtime := fs.String("runtime", "", "claim: OS version, e.g. 26.4")
	ttl := fs.Duration("ttl", 0, "claim: lease length (default: the daemon's)")
	wait := fs.Duration("wait", 0, "claim: queue this long when no simulator is free")
	force := fs.Bool("force", false, "release: someone else's lease")
	shutdown := fs.Bool("shutdown", false, "release: also shut the simulator down")
	printURL := fs.Bool("print", false, "ui: print the sign-in link instead of opening it")
	pos := parse(fs, args)
	want := 0
	if cmd == "release" || cmd == "renew" {
		want = 1 // the udid
	}
	if len(pos) != want {
		return fmt.Errorf("usage:\n%s", usage)
	}
	c, err := connect(ctx)
	if err != nil {
		return err
	}
	var out any
	switch cmd {
	case "claim":
		var l fleet.Lease
		err = c.Do(ctx, "POST", "/api/claim", fleet.ClaimRequest{Holder: *holder, Purpose: *purpose, Device: *device, Runtime: *runtime, TTL: *ttl, Wait: *wait}, &l)
		out = l
	case "release":
		err = c.Do(ctx, "POST", "/api/release", fleet.ReleaseRequest{UDID: pos[0], Holder: *holder, Force: *force, Shutdown: *shutdown}, nil)
		out = map[string]string{"released": pos[0]}
	case "renew":
		var l fleet.Lease
		err = c.Do(ctx, "POST", "/api/renew", fleet.ReleaseRequest{UDID: pos[0]}, &l)
		out = l
	case "ls":
		var ls []fleet.Entry
		err = c.Do(ctx, "GET", "/api/ls", nil, &ls)
		out = ls
	case "version":
		var d web.Version
		err = c.Do(ctx, "GET", "/api/version", nil, &d)
		self, mtime, _ := selfBinary()
		out = map[string]any{"cli": web.Version{Binary: self, Mtime: mtime, PID: os.Getpid()}, "daemon": d}
	case "ui":
		var u struct{ URL string }
		if err = c.Do(ctx, "GET", "/api/ui", nil, &u); err == nil && u.URL == "" {
			err = errors.New("the browser UI is off (daemon started with --port 0)")
		}
		if err != nil || *printURL || *asJSON {
			out = map[string]string{"url": u.URL}
			break
		}
		return exec.Command("open", u.URL).Run()
	}
	return emit(*asJSON, out, err, printHuman)
}

// emit prints a command's result: JSON (errors too, as {"error": ...}) or the human form. It returns err so the
// process exits non-zero on failure either way.
func emit(asJSON bool, out any, err error, human func(any)) error {
	if err != nil {
		var fe *fleet.Error
		if asJSON && errors.As(err, &fe) {
			json.NewEncoder(os.Stdout).Encode(map[string]any{"error": fe})
		}
		return err
	}
	if asJSON {
		if s, ok := out.(string); ok { // logs: already ndjson
			_, err := fmt.Print(s)
			return err
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(out)
	}
	human(out)
	return nil
}

func printHuman(v any) {
	switch v := v.(type) {
	case fleet.Lease:
		fmt.Printf("%s  %s  %s  held by %s until %s\n", v.Name, v.UDID, v.OS, v.Holder, v.Expires.Local().Format("15:04"))
	case []fleet.Entry:
		w := tabwriter.NewWriter(os.Stdout, 0, 4, 2, ' ', 0)
		fmt.Fprintln(w, "NAME\tSTATE\tOS\tHOLDER\tUNTIL\tUDID")
		for _, e := range v {
			holder, until := "-", "-"
			if e.Lease != nil {
				holder, until = e.Lease.Holder, e.Lease.Expires.Local().Format("15:04")
			}
			fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\t%s\n", e.Name, e.State, e.OS, holder, until, e.UDID)
		}
		w.Flush()
	case map[string]any: // version
		for _, k := range []string{"cli", "daemon"} {
			v := v[k].(web.Version)
			fmt.Printf("%-6s %s (built %s) pid %d %s\n", k, v.Binary, v.Mtime.Local().Format("Jan 2 15:04:05"), v.PID, strings.Join(v.Args, " "))
		}
	case map[string]string:
		if u, ok := v["url"]; ok {
			fmt.Println(u)
		} else {
			fmt.Println("released", v["released"])
		}
	}
}

// parse lets flags come before or after positional arguments (`release <udid> --force`) and returns the positionals.
func parse(fs *flag.FlagSet, args []string) []string {
	var pos []string
	for {
		fs.Parse(args)
		args = fs.Args()
		if len(args) == 0 {
			return pos
		}
		pos, args = append(pos, args[0]), args[1:]
	}
}
