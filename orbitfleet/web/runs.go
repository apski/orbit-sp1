package web

import (
	"bufio"
	"net/http"
	"strings"
	"time"

	"orbitfleet/fleet"
	"orbitfleet/runs"
)

func runRoutes(mux *http.ServeMux, f *fleet.Fleet, r *runs.Runs) {
	mux.HandleFunc("POST /api/runs", func(w http.ResponseWriter, req *http.Request) {
		var in runs.StartRequest
		if !decode(w, req, &in) {
			return
		}
		if _, err := f.Renew(in.UDID, in.Holder); err != nil { // only the holder starts a run on its sim
			write(w, nil, err)
			return
		}
		run, err := r.Start(req.Context(), in)
		write(w, run, err)
	})
	mux.HandleFunc("GET /api/runs", func(w http.ResponseWriter, req *http.Request) {
		var since time.Time
		if s := req.URL.Query().Get("since"); s != "" {
			d, err := time.ParseDuration(s)
			if err != nil {
				write(w, nil, &fleet.Error{Code: "bad-request", Message: "since: " + err.Error()})
				return
			}
			since = time.Now().Add(-d)
		}
		list, err := r.List(req.URL.Query().Get("holder"), since)
		write(w, list, err)
	})
	mux.HandleFunc("GET /api/runs/{id}", func(w http.ResponseWriter, req *http.Request) {
		run, err := r.Get(req.PathValue("id"))
		write(w, run, err)
	})
	mux.HandleFunc("POST /api/runs/{id}/launch", func(w http.ResponseWriter, req *http.Request) {
		var in runs.LaunchRequest
		if decode(w, req, &in) {
			run, err := r.Launch(req.Context(), touch(f, r, req), in)
			write(w, run, err)
		}
	})
	mux.HandleFunc("POST /api/runs/{id}/mark", func(w http.ResponseWriter, req *http.Request) {
		var in struct{ Text string }
		if decode(w, req, &in) {
			ev, err := r.Mark(touch(f, r, req), in.Text)
			write(w, ev, err)
		}
	})
	mux.HandleFunc("POST /api/runs/{id}/shot", func(w http.ResponseWriter, req *http.Request) {
		var in struct{ Name string }
		if decode(w, req, &in) {
			art, err := r.Shot(req.Context(), touch(f, r, req), in.Name)
			write(w, art, err)
		}
	})
	mux.HandleFunc("POST /api/runs/{id}/stop", func(w http.ResponseWriter, req *http.Request) {
		var in runs.StopRequest
		if decode(w, req, &in) {
			run, err := r.Stop(req.Context(), touch(f, r, req), in)
			write(w, run, err)
		}
	})
	mux.HandleFunc("POST /api/runs/{id}/debug/lldb", func(w http.ResponseWriter, req *http.Request) {
		var in struct{ Open bool }
		if decode(w, req, &in) {
			d, err := r.LLDB(req.Context(), touch(f, r, req), in.Open)
			write(w, d, err)
		}
	})
	mux.HandleFunc("POST /api/runs/{id}/debug/profile", func(w http.ResponseWriter, req *http.Request) {
		var in struct {
			Template string
			Limit    time.Duration
		}
		if decode(w, req, &in) {
			a, err := r.Profile(req.Context(), touch(f, r, req), in.Template, in.Limit)
			write(w, a, err)
		}
	})
	mux.HandleFunc("POST /api/runs/{id}/debug/video", func(w http.ResponseWriter, req *http.Request) {
		var in struct{ Action string }
		if !decode(w, req, &in) {
			return
		}
		if in.Action != "start" && in.Action != "stop" {
			write(w, nil, &fleet.Error{Code: "bad-request", Message: "action: start or stop"})
			return
		}
		a, err := r.Video(req.Context(), touch(f, r, req), in.Action == "start")
		write(w, a, err)
	})
	// Logs streams the device log as ndjson lines, those containing ?grep= only.
	mux.HandleFunc("GET /api/runs/{id}/logs", func(w http.ResponseWriter, req *http.Request) {
		rc, err := r.Logs(req.PathValue("id"))
		if err != nil {
			write(w, nil, err)
			return
		}
		defer rc.Close()
		grep := req.URL.Query().Get("grep")
		w.Header().Set("Content-Type", "application/x-ndjson")
		sc := bufio.NewScanner(rc)
		sc.Buffer(make([]byte, 64*1024), 4*1024*1024)
		for sc.Scan() {
			if line := sc.Text(); strings.Contains(line, grep) {
				w.Write(append(sc.Bytes(), '\n'))
			}
		}
	})
}

// touch returns the run id from the path and renews the lease of the run's simulator: any command that names a
// device, directly or through its run, keeps the lease alive (DESIGN.md D5).
func touch(f *fleet.Fleet, r *runs.Runs, req *http.Request) string {
	id := req.PathValue("id")
	if run, err := r.Get(id); err == nil {
		f.Renew(run.UDID, "")
	}
	return id
}
