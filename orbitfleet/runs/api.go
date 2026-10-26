package runs

import "time"

// The JSON contract for runs (DESIGN.md D9). Changing a field is a breaking change.

type Run struct {
	ID        string     `json:"id"`
	UDID      string     `json:"udid"`
	Holder    string     `json:"holder"`
	Label     string     `json:"label,omitempty"`
	App       string     `json:"app,omitempty"` // bundle id; crash reports are matched on it
	PID       int        `json:"pid,omitempty"` // host pid of the last launch (simulator apps are host processes)
	Status    string     `json:"status"`        // running | passed | failed | aborted
	Reason    string     `json:"reason,omitempty"`
	Started   time.Time  `json:"started"`
	Ended     *time.Time `json:"ended,omitempty"`
	Dir       string     `json:"dir"`
	Artifacts []Artifact `json:"artifacts"`
	Warnings  []string   `json:"warnings,omitempty"`  // a capture step that failed at stop; the run still ended
	Recording bool       `json:"recording,omitempty"` // a screen recording is running (debug video start)
}

// Artifact paths are absolute. App output lives in the simulator's data dir while the run is active and moves into
// Dir when it stops (simctl can only write there).
type Artifact struct {
	Kind  string `json:"kind"` // device-log | app-out | app-err | shot | crash | trace | video
	Path  string `json:"path"`
	Bytes int64  `json:"bytes,omitempty"`
	Note  string `json:"note,omitempty"` // e.g. a profile taken with an injected dylib
}

// Debug is what `debug lldb` returns: the process and the command that attaches to it.
type Debug struct {
	PID     int    `json:"pid"`
	Command string `json:"command"`
}

// Event is one line of <run>/events.ndjson: the run's timeline.
type Event struct {
	T    time.Time `json:"t"`
	Kind string    `json:"kind"` // start | launch | mark | shot | debug | crash | stop
	Text string    `json:"text"`
}

type StartRequest struct {
	UDID      string `json:"udid"`
	Holder    string `json:"holder"`
	Label     string `json:"label,omitempty"`
	App       string `json:"app,omitempty"`
	LogLevel  string `json:"logLevel,omitempty"`  // info (default) | debug | default
	Predicate string `json:"predicate,omitempty"` // passed to `log stream --predicate`
}

type LaunchRequest struct {
	App             string   `json:"app,omitempty"` // default: the run's app
	WaitForDebugger bool     `json:"waitForDebugger,omitempty"`
	Args            []string `json:"args,omitempty"`
}

type StopRequest struct {
	Status string `json:"status"` // passed | failed
	Reason string `json:"reason,omitempty"`
}
