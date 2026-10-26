package fleet

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
)

type fakeSims struct {
	mu         sync.Mutex
	devs       []Device
	prepared   []string
	shut       []string
	delay      time.Duration
	prepareErr error
	warning    string
}

func (s *fakeSims) List(ctx context.Context) ([]Device, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Device(nil), s.devs...), nil
}

func (s *fakeSims) Prepare(ctx context.Context, udid string) (string, error) {
	time.Sleep(s.delay)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.prepareErr != nil {
		return "", s.prepareErr
	}
	s.prepared = append(s.prepared, udid)
	s.set(udid, "Booted")
	return s.warning, nil
}

func (s *fakeSims) Shutdown(ctx context.Context, udid string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.shut = append(s.shut, udid)
	s.set(udid, "Shutdown")
	return nil
}

func (s *fakeSims) set(udid, state string) {
	for i := range s.devs {
		if s.devs[i].UDID == udid {
			s.devs[i].State = state
		}
	}
}

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *clock) add(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

func dev(name, state string) Device {
	return Device{UDID: "UDID-" + name, Name: name, State: state, OS: "iOS 26.4"}
}

func newFleet(s *fakeSims, max int) (*Fleet, *clock) {
	c := &clock{t: time.Date(2026, 10, 25, 10, 0, 0, 0, time.UTC)}
	return New(s, Config{PoolPrefix: "fleet-", MaxBooted: max, TTL: 30 * time.Minute, Now: c.now}, nil), c
}

func code(err error) string {
	var e *Error
	if errors.As(err, &e) {
		return e.Code
	}
	return fmt.Sprint(err)
}

var ctx = context.Background()

func TestClaimIsExclusive(t *testing.T) {
	s := &fakeSims{delay: 5 * time.Millisecond, devs: []Device{
		dev("fleet-01", "Booted"), dev("fleet-02", "Booted"), dev("fleet-03", "Booted"), dev("NavCore", "Booted"),
	}}
	f, _ := newFleet(s, 16)
	var mu sync.Mutex
	got := map[string]string{}
	errs := map[string]int{}
	var wg sync.WaitGroup
	for i := range 50 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			l, err := f.Claim(ctx, ClaimRequest{Holder: fmt.Sprintf("agent-%d", i)})
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				errs[code(err)]++
				return
			}
			if prev, dup := got[l.UDID]; dup {
				t.Errorf("%s held by %s and %s", l.UDID, prev, l.Holder)
			}
			got[l.UDID] = l.Holder
		}()
	}
	wg.Wait()
	if len(got) != 3 || errs["capacity"] != 47 {
		t.Fatalf("leases %v, errors %v; want 3 leases and 47 capacity", got, errs)
	}
	if _, ok := got["UDID-NavCore"]; ok {
		t.Fatal("leased a device outside the pool")
	}
}

func TestClaimPrefersBootedThenRespectsCapacity(t *testing.T) {
	s := &fakeSims{devs: []Device{dev("fleet-01", "Shutdown"), dev("fleet-02", "Booted"), dev("NavCore", "Booted")}}
	f, _ := newFleet(s, 2)
	l, err := f.Claim(ctx, ClaimRequest{Holder: "a"})
	if err != nil || l.Name != "fleet-02" || !l.Ready {
		t.Fatalf("first claim = %+v, %v; want ready fleet-02", l, err)
	}
	// fleet-02 and NavCore are booted: booting fleet-01 would exceed MaxBooted 2.
	if _, err := f.Claim(ctx, ClaimRequest{Holder: "b"}); code(err) != "capacity" {
		t.Fatalf("second claim err = %v; want capacity", err)
	}
	f.cfg.MaxBooted = 3
	l, err = f.Claim(ctx, ClaimRequest{Holder: "b"})
	if err != nil || l.Name != "fleet-01" {
		t.Fatalf("third claim = %+v, %v; want fleet-01", l, err)
	}
	if want := []string{"UDID-fleet-02", "UDID-fleet-01"}; fmt.Sprint(s.prepared) != fmt.Sprint(want) {
		t.Fatalf("prepared %v; want %v (every claim prepares: slim + clean)", s.prepared, want)
	}
}

func TestClaimFilters(t *testing.T) {
	s := &fakeSims{devs: []Device{dev("fleet-01", "Booted"), dev("NavCore", "Booted")}}
	f, _ := newFleet(s, 16)
	if _, err := f.Claim(ctx, ClaimRequest{Holder: "a", Runtime: "27"}); code(err) != "no-device" {
		t.Fatalf("runtime 27: %v; want no-device", err)
	}
	if _, err := f.Claim(ctx, ClaimRequest{Holder: "a", Device: "missing"}); code(err) != "no-device" {
		t.Fatalf("missing device: %v; want no-device", err)
	}
	if _, err := f.Claim(ctx, ClaimRequest{}); code(err) != "bad-request" {
		t.Fatalf("no holder: %v; want bad-request", err)
	}
	l, err := f.Claim(ctx, ClaimRequest{Holder: "a", Device: "NavCore", Runtime: "26.4"})
	if err != nil || l.Name != "NavCore" {
		t.Fatalf("named device outside the pool = %+v, %v; a named device is allowed", l, err)
	}
	if l, err := f.Claim(ctx, ClaimRequest{Holder: "b", Device: "UDID-fleet-01"}); err != nil || l.Name != "fleet-01" {
		t.Fatalf("claim by udid = %+v, %v", l, err)
	}
}

func TestExpiryAndRenew(t *testing.T) {
	s := &fakeSims{devs: []Device{dev("fleet-01", "Booted")}}
	f, c := newFleet(s, 16)
	l, _ := f.Claim(ctx, ClaimRequest{Holder: "a"})
	c.add(29 * time.Minute)
	if r := f.Reap(); len(r) != 0 {
		t.Fatalf("reaped %v before expiry", r)
	}
	if _, err := f.Renew(l.UDID, "b"); code(err) != "not-holder" {
		t.Fatalf("renew by b: %v; want not-holder", err)
	}
	if _, err := f.Renew(l.UDID, ""); err != nil {
		t.Fatal(err)
	}
	c.add(29 * time.Minute)
	if r := f.Reap(); len(r) != 0 {
		t.Fatalf("reaped %v after renew", r)
	}
	c.add(2 * time.Minute)
	if r := f.Reap(); len(r) != 1 || r[0].Holder != "a" {
		t.Fatalf("reaped %v; want a's lease", r)
	}
	if _, err := f.Renew(l.UDID, "a"); code(err) != "not-leased" {
		t.Fatalf("renew after expiry: %v; want not-leased", err)
	}
	if l, err := f.Claim(ctx, ClaimRequest{Holder: "b"}); err != nil || l.Holder != "b" {
		t.Fatalf("claim after expiry = %+v, %v", l, err)
	}
}

func TestRelease(t *testing.T) {
	s := &fakeSims{devs: []Device{dev("fleet-01", "Booted")}}
	f, _ := newFleet(s, 16)
	l, _ := f.Claim(ctx, ClaimRequest{Holder: "a"})
	if err := f.Release(ctx, ReleaseRequest{UDID: l.UDID, Holder: "b"}); code(err) != "not-holder" {
		t.Fatalf("release by b: %v; want not-holder", err)
	}
	if err := f.Release(ctx, ReleaseRequest{UDID: l.UDID, Holder: "b", Force: true, Shutdown: true}); err != nil {
		t.Fatal(err)
	}
	if len(s.shut) != 1 {
		t.Fatalf("shutdown calls %v; want 1", s.shut)
	}
	if err := f.Release(ctx, ReleaseRequest{UDID: l.UDID, Holder: "a"}); code(err) != "not-leased" {
		t.Fatalf("second release: %v; want not-leased", err)
	}
}

func TestWaitGetsReleasedDevice(t *testing.T) {
	s := &fakeSims{devs: []Device{dev("fleet-01", "Booted")}}
	f, _ := newFleet(s, 16)
	l, _ := f.Claim(ctx, ClaimRequest{Holder: "a"})
	done := make(chan Lease)
	go func() {
		l, err := f.Claim(ctx, ClaimRequest{Holder: "b", Wait: 2 * time.Second})
		if err != nil {
			t.Error(err)
		}
		done <- l
	}()
	time.Sleep(50 * time.Millisecond)
	if err := f.Release(ctx, ReleaseRequest{UDID: l.UDID, Holder: "a"}); err != nil {
		t.Fatal(err)
	}
	select {
	case got := <-done:
		if got.Holder != "b" {
			t.Fatalf("waiter got %+v", got)
		}
	case <-time.After(time.Second):
		t.Fatal("waiter not woken by release")
	}
	start := time.Now()
	if _, err := f.Claim(ctx, ClaimRequest{Holder: "c", Wait: 50 * time.Millisecond}); code(err) != "capacity" {
		t.Fatalf("timed-out wait: %v; want capacity", err)
	}
	if d := time.Since(start); d < 50*time.Millisecond {
		t.Fatalf("returned after %v; should wait 50ms", d)
	}
}

func TestPrepareFailureFreesDevice(t *testing.T) {
	s := &fakeSims{devs: []Device{dev("fleet-01", "Booted")}, prepareErr: errors.New("boot timed out")}
	f, _ := newFleet(s, 16)
	if _, err := f.Claim(ctx, ClaimRequest{Holder: "a"}); code(err) != "prepare" || !strings.Contains(err.Error(), "boot timed out") {
		t.Fatalf("err = %v; want prepare: boot timed out", err)
	}
	s.prepareErr, s.warning = nil, "argent re-injected"
	l, err := f.Claim(ctx, ClaimRequest{Holder: "b"})
	if err != nil {
		t.Fatalf("device still reserved after a failed prepare: %v", err)
	}
	if l.Warning != "argent re-injected" {
		t.Fatalf("warning = %q; a prepare warning must reach the holder", l.Warning)
	}
}

func TestLeasesPersist(t *testing.T) {
	s := &fakeSims{devs: []Device{dev("fleet-01", "Booted"), dev("fleet-02", "Booted")}}
	var saved []Lease
	c := &clock{t: time.Date(2026, 10, 25, 10, 0, 0, 0, time.UTC)}
	cfg := Config{PoolPrefix: "fleet-", MaxBooted: 16, TTL: 30 * time.Minute, Now: c.now, Save: func(l []Lease) error { saved = l; return nil }}
	f := New(s, cfg, nil)
	l, _ := f.Claim(ctx, ClaimRequest{Holder: "a"})
	if len(saved) != 1 || saved[0].UDID != l.UDID || !saved[0].Ready {
		t.Fatalf("saved %+v after claim", saved)
	}
	restarted := New(s, cfg, saved)
	got, err := restarted.Claim(ctx, ClaimRequest{Holder: "b"})
	if err != nil || got.UDID == l.UDID {
		t.Fatalf("after restart b got %+v, %v; a's lease must survive", got, err)
	}
	entries, err := restarted.List(ctx)
	if err != nil || len(entries) != 2 || entries[0].Lease == nil || entries[0].Lease.Holder != "a" {
		t.Fatalf("list = %+v, %v", entries, err)
	}
}
