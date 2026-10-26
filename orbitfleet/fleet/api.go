package fleet

import "time"

// The JSON contract between the CLI, the daemon and the UI (DESIGN.md D9). Changing a field is a breaking change.

type Device struct {
	UDID  string `json:"udid"`
	Name  string `json:"name"`
	State string `json:"state"` // "Booted" or "Shutdown"
	OS    string `json:"os"`    // "iOS 26.4"
}

type Lease struct {
	UDID    string        `json:"udid"`
	Name    string        `json:"name"`
	OS      string        `json:"os"`
	Holder  string        `json:"holder"`
	Purpose string        `json:"purpose,omitempty"`
	Since   time.Time     `json:"since"`
	Expires time.Time     `json:"expires"`
	TTL     time.Duration `json:"ttl"`               // nanoseconds; each renew sets Expires = now + TTL
	Ready   bool          `json:"ready"`             // false while the claim boots and slims the device
	Warning string        `json:"warning,omitempty"` // e.g. an attached argent server re-injected its dylib
}

type ClaimRequest struct {
	Holder  string        `json:"holder"`
	Purpose string        `json:"purpose,omitempty"`
	Device  string        `json:"device,omitempty"`  // udid or exact name; may be outside the pool
	Runtime string        `json:"runtime,omitempty"` // substring of Device.OS: "26.4", "iOS 27"
	TTL     time.Duration `json:"ttl,omitempty"`
	Wait    time.Duration `json:"wait,omitempty"` // how long to queue when every match is leased or the Mac is full
}

type ReleaseRequest struct {
	UDID     string `json:"udid"`
	Holder   string `json:"holder"`
	Force    bool   `json:"force,omitempty"`
	Shutdown bool   `json:"shutdown,omitempty"`
}

// Entry is one row of `orbitfleet ls`: a pool device, or any leased device.
type Entry struct {
	Device
	Lease *Lease `json:"lease,omitempty"`
}

// Overview is the fleet as the UI shows it: every pool or leased device plus the Mac-wide capacity numbers.
type Overview struct {
	Entries   []Entry `json:"entries"`
	Booted    int     `json:"booted"` // booted simulators on this Mac, pool or not
	MaxBooted int     `json:"maxBooted"`
	Waiting   int     `json:"waiting"` // claims queued with --wait
}

// Stat is one booted simulator's footprint (simslim's measure) and slim state.
type Stat struct {
	Bytes           int64 `json:"bytes"` // summed phys_footprint of the simulator's processes
	ManagedDisabled int   `json:"managedDisabled"`
	ManagedTotal    int   `json:"managedTotal"`
}

// Error codes: bad-request, no-device, capacity, not-leased, not-holder, prepare, shutdown.
type Error struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }
