// Package fleet hands out simulator leases: one holder per device, a TTL renewed by use, a cap on booted devices.
// It never runs a process itself; Sims does (package sim, or a fake in tests).
package fleet

import (
	"cmp"
	"context"
	"fmt"
	"slices"
	"strings"
	"sync"
	"time"
)

type Sims interface {
	List(ctx context.Context) ([]Device, error)
	// Prepare boots the device slim per the profile and clears what a previous holder left (argent's
	// DYLD_INSERT_LIBRARIES). Every new holder comes through here, however the last lease ended. A warning is
	// something the holder should know but that does not stop the claim.
	Prepare(ctx context.Context, udid string) (warning string, err error)
	Shutdown(ctx context.Context, udid string) error
}

type Config struct {
	PoolPrefix string           // devices claimable without naming them
	MaxBooted  int              // booted simulators on this Mac, pool or not, before claims queue
	TTL        time.Duration    // default lease length, renewed by every command naming the device
	Now        func() time.Time // lease clock; waits use the wall clock
	Save       func([]Lease) error
}

type Fleet struct {
	sims    Sims
	cfg     Config
	mu      sync.Mutex
	leases  map[string]*Lease
	changed chan struct{} // closed and replaced whenever a device frees up
	waiting int
}

func New(s Sims, cfg Config, leases []Lease) *Fleet {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	f := &Fleet{sims: s, cfg: cfg, leases: map[string]*Lease{}, changed: make(chan struct{})}
	for _, l := range leases {
		f.leases[l.UDID] = &l
	}
	return f
}

func (f *Fleet) Claim(ctx context.Context, req ClaimRequest) (Lease, error) {
	if strings.TrimSpace(req.Holder) == "" {
		return Lease{}, &Error{"bad-request", "holder is required (--holder or ORBITFLEET_HOLDER)"}
	}
	deadline := time.Now().Add(req.Wait)
	for {
		devs, err := f.sims.List(ctx)
		if err != nil {
			return Lease{}, err
		}
		f.mu.Lock()
		l, rerr := f.reserve(devs, req)
		changed := f.changed
		f.mu.Unlock()
		if rerr == nil {
			return f.prepare(ctx, l)
		}
		if rerr.Code != "capacity" || time.Until(deadline) <= 0 {
			return Lease{}, rerr
		}
		if err := f.wait(ctx, changed, deadline); err != nil {
			return Lease{}, err
		}
	}
}

// wait blocks until a device frees up or the deadline passes, counted in Overview.Waiting meanwhile.
func (f *Fleet) wait(ctx context.Context, changed chan struct{}, deadline time.Time) error {
	f.mu.Lock()
	f.waiting++
	f.mu.Unlock()
	defer func() { f.mu.Lock(); f.waiting--; f.mu.Unlock() }()
	select {
	case <-changed:
	case <-time.After(time.Until(deadline)):
	case <-ctx.Done():
		return ctx.Err()
	}
	return nil
}

// reserve picks a device and records the lease before Prepare runs, so concurrent claims never share one.
// Order: booted before shutdown. A shutdown device only when the Mac has room to boot it. Caller holds mu.
func (f *Fleet) reserve(devs []Device, req ClaimRequest) (Lease, *Error) {
	booted := 0
	var match []Device
	for _, d := range devs {
		if d.State == "Booted" || f.leases[d.UDID] != nil {
			booted++ // a leased device is booted or about to be
		}
		named := req.Device != "" && (d.UDID == req.Device || d.Name == req.Device)
		pool := req.Device == "" && strings.HasPrefix(d.Name, f.cfg.PoolPrefix)
		if (named || pool) && strings.Contains(d.OS, req.Runtime) {
			match = append(match, d)
		}
	}
	if len(match) == 0 {
		return Lease{}, &Error{"no-device", fmt.Sprintf("no simulator matches (device %q, runtime %q, pool prefix %q)", req.Device, req.Runtime, f.cfg.PoolPrefix)}
	}
	slices.SortStableFunc(match, func(a, b Device) int { return rank(a) - rank(b) })
	for _, d := range match {
		if f.leases[d.UDID] != nil || (d.State != "Booted" && booted >= f.cfg.MaxBooted) {
			continue
		}
		now := f.cfg.Now()
		ttl := cmp.Or(req.TTL, f.cfg.TTL)
		l := Lease{UDID: d.UDID, Name: d.Name, OS: d.OS, Holder: req.Holder, Purpose: req.Purpose, Since: now, Expires: now.Add(ttl), TTL: ttl}
		f.leases[d.UDID] = &l
		f.save()
		return l, nil
	}
	return Lease{}, &Error{"capacity", fmt.Sprintf("%d matching simulators all leased or no room to boot one (%d of %d booted)", len(match), booted, f.cfg.MaxBooted)}
}

func (f *Fleet) prepare(ctx context.Context, l Lease) (Lease, error) {
	warning, err := f.sims.Prepare(ctx, l.UDID)
	f.mu.Lock()
	defer f.mu.Unlock()
	if err != nil {
		f.free(l.UDID)
		return Lease{}, &Error{"prepare", err.Error()}
	}
	cur := f.leases[l.UDID]
	if cur == nil || cur.Holder != l.Holder || !cur.Since.Equal(l.Since) {
		return Lease{}, &Error{"not-leased", "lease was released while the device was being prepared"}
	}
	cur.Ready, cur.Warning = true, warning
	f.save()
	return *cur, nil
}

// Renew extends a lease by its TTL. A non-empty holder must match (run start checks ownership this way); the UI's
// Extend and commands that name a run pass "".
func (f *Fleet) Renew(udid, holder string) (Lease, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	l := f.leases[udid]
	if l == nil {
		return Lease{}, &Error{"not-leased", udid + " has no lease"}
	}
	if holder != "" && holder != l.Holder {
		return Lease{}, &Error{"not-holder", fmt.Sprintf("%s is held by %s", udid, l.Holder)}
	}
	l.Expires = f.cfg.Now().Add(l.TTL)
	f.save()
	return *l, nil
}

func (f *Fleet) Release(ctx context.Context, req ReleaseRequest) error {
	f.mu.Lock()
	l := f.leases[req.UDID]
	switch {
	case l == nil:
		f.mu.Unlock()
		return &Error{"not-leased", req.UDID + " has no lease"}
	case l.Holder != req.Holder && !req.Force:
		f.mu.Unlock()
		return &Error{"not-holder", fmt.Sprintf("%s is held by %s (use --force)", req.UDID, l.Holder)}
	}
	f.free(req.UDID)
	f.mu.Unlock()
	if req.Shutdown {
		if err := f.sims.Shutdown(ctx, req.UDID); err != nil {
			return &Error{"shutdown", "released, but shutdown failed: " + err.Error()}
		}
	}
	return nil
}

// Reap ends expired leases and returns them.
func (f *Fleet) Reap() []Lease {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []Lease
	now := f.cfg.Now()
	for udid, l := range f.leases {
		if !now.Before(l.Expires) {
			out = append(out, *l)
			f.free(udid)
		}
	}
	return out
}

// List returns every pool device plus any leased device outside the pool, sorted by name.
func (f *Fleet) List(ctx context.Context) ([]Entry, error) {
	o, err := f.Overview(ctx)
	return o.Entries, err
}

func (f *Fleet) Overview(ctx context.Context) (Overview, error) {
	devs, err := f.sims.List(ctx)
	if err != nil {
		return Overview{}, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	o := Overview{MaxBooted: f.cfg.MaxBooted, Waiting: f.waiting}
	for _, d := range devs {
		if d.State == "Booted" || f.leases[d.UDID] != nil {
			o.Booted++
		}
	}
	o.Entries = f.entries(devs)
	return o, nil
}

// entries is List's rows. Caller holds mu.
func (f *Fleet) entries(devs []Device) []Entry {
	var out []Entry
	for _, d := range devs {
		e := Entry{Device: d}
		if l := f.leases[d.UDID]; l != nil {
			cp := *l
			e.Lease = &cp
		} else if !strings.HasPrefix(d.Name, f.cfg.PoolPrefix) {
			continue
		}
		out = append(out, e)
	}
	slices.SortFunc(out, func(a, b Entry) int { return cmp.Compare(a.Name, b.Name) })
	return out
}

func rank(d Device) int {
	if d.State == "Booted" {
		return 0
	}
	return 1
}

// free drops a lease and wakes waiting claims. Caller holds mu.
func (f *Fleet) free(udid string) {
	delete(f.leases, udid)
	close(f.changed)
	f.changed = make(chan struct{})
	f.save()
}

// save persists the leases. Caller holds mu. A failed save is not fatal: the in-memory leases stay correct.
func (f *Fleet) save() {
	if f.cfg.Save == nil {
		return
	}
	out := make([]Lease, 0, len(f.leases))
	for _, l := range f.leases {
		out = append(out, *l)
	}
	slices.SortFunc(out, func(a, b Lease) int { return cmp.Compare(a.UDID, b.UDID) })
	_ = f.cfg.Save(out)
}
