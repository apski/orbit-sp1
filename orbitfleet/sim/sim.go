// Package sim is orbitfleet's only adapter to simulators: simslim (in-process) and xcrun (DESIGN.md D3).
package sim

import (
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/mobai-app/simslim"

	"orbitfleet/fleet"
)

type Sims struct {
	SlimProfile simslim.Profile
	Log         func(string)
	sets        sync.Map // udid -> device set, learned from List: per-device calls skip a full `simctl list`
}

// set returns the device set of udid, from List's cache or a lookup.
func (s *Sims) set(ctx context.Context, udid string) (string, error) {
	if v, ok := s.sets.Load(udid); ok {
		return v.(string), nil
	}
	d, err := simslim.FindDevice(ctx, udid, "")
	if err != nil {
		return "", err
	}
	s.sets.Store(udid, d.Set)
	return d.Set, nil
}

// New loads a simslim profile file; an empty path is simslim's default (every category off).
func New(profilePath string, log func(string)) (*Sims, error) {
	p, err := simslim.BuildProfile(profilePath, "", "")
	if err != nil {
		return nil, err
	}
	return &Sims{SlimProfile: p, Log: log}, nil
}

func (s *Sims) List(ctx context.Context) ([]fleet.Device, error) {
	ds, err := simslim.ListDevices(ctx)
	if err != nil {
		return nil, err
	}
	out := make([]fleet.Device, len(ds))
	for i, d := range ds {
		s.sets.Store(d.UDID, d.Set)
		out[i] = fleet.Device{UDID: d.UDID, Name: d.Name, State: d.State, OS: d.PlatformName() + " " + d.OSVersion}
	}
	return out, nil
}

// Prepare boots the device slim (a single read when it already matches the profile), then clears argent's
// DYLD_INSERT_LIBRARIES from the simulator's launchd so the new holder starts clean (orbit-SP1 HARNESS rule 2).
// An argent tool-server attached to the device (any session's) re-sets it at once; that is a warning, not a
// failure: the holder will usually run argent anyway, and only timing runs need it gone.
func (s *Sims) Prepare(ctx context.Context, udid string) (string, error) {
	d, err := simslim.FindDevice(ctx, udid, "")
	if err != nil {
		return "", err
	}
	report := func(msg string) { s.log(d.Name + ": " + msg) }
	if d.SupportsPersistentOverrides() {
		_, err = simslim.EnableSlim(ctx, d.Set, udid, s.SlimProfile, report)
	} else {
		_, err = simslim.EnableSlimNoReboot(ctx, d.Set, udid, s.SlimProfile, report)
	}
	if err != nil {
		return "", err
	}
	for range 3 {
		if _, err := simctl(ctx, d.Set, "spawn", udid, "launchctl", "unsetenv", "DYLD_INSERT_LIBRARIES"); err != nil {
			return "", err
		}
		v, err := simctl(ctx, d.Set, "spawn", udid, "launchctl", "getenv", "DYLD_INSERT_LIBRARIES")
		if err != nil {
			return "", err
		}
		if v == "" { // getenv prints nothing (exit 0) when the variable is unset
			return "", nil
		}
		s.log(d.Name + ": DYLD_INSERT_LIBRARIES still set: " + v)
		time.Sleep(time.Second)
	}
	return "DYLD_INSERT_LIBRARIES keeps coming back (an argent tool-server is attached to this simulator); timings here are not clean", nil
}

func (s *Sims) log(msg string) {
	if s.Log != nil {
		s.Log(msg)
	}
}

func (s *Sims) Shutdown(ctx context.Context, udid string) error {
	d, err := simslim.FindDevice(ctx, udid, "")
	if err != nil {
		return err
	}
	return simslim.Shutdown(ctx, d.Set, udid)
}

// HostRAM is the Mac's physical memory in bytes.
func HostRAM() (int64, error) {
	out, err := exec.Command("sysctl", "-n", "hw.memsize").Output()
	if err != nil {
		return 0, err
	}
	return strconv.ParseInt(strings.TrimSpace(string(out)), 10, 64)
}

// simctlArgs prefixes a simctl subcommand with the device's set ("default" needs no --set).
func simctlArgs(set string, args ...string) []string {
	pre := []string{"simctl"}
	if set != "" && set != "default" {
		pre = append(pre, "--set", set)
	}
	return append(pre, args...)
}

// simctl runs `xcrun simctl` in the device's set and returns trimmed stdout.
func simctl(ctx context.Context, set string, args ...string) (string, error) {
	out, err := exec.CommandContext(ctx, "xcrun", simctlArgs(set, args...)...).Output()
	if err != nil {
		return "", fmt.Errorf("xcrun simctl %s: %w", strings.Join(args, " "), err)
	}
	return strings.TrimSpace(string(out)), nil
}
