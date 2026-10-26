// Package web is orbitfleet's only HTTP code: the daemon's handler (leases here, runs in runs.go), its unix-socket
// listener, and the client the CLI uses. Browser access on 127.0.0.1 (with token and Host checks, DESIGN.md D6) arrives in P3.
package web

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"

	"orbitfleet/fleet"
	"orbitfleet/runs"
)

func Handler(f *fleet.Fleet, rs *runs.Runs, t Tools) http.Handler {
	mux := http.NewServeMux()
	runRoutes(mux, f, rs)
	uiRoutes(mux, f, rs, t)
	mux.HandleFunc("POST /api/claim", func(w http.ResponseWriter, r *http.Request) {
		var req fleet.ClaimRequest
		if decode(w, r, &req) {
			l, err := f.Claim(r.Context(), req)
			write(w, l, err)
		}
	})
	mux.HandleFunc("POST /api/release", func(w http.ResponseWriter, r *http.Request) {
		var req fleet.ReleaseRequest
		if !decode(w, r, &req) {
			return
		}
		err := f.Release(r.Context(), req)
		var fe *fleet.Error
		if err == nil || errors.As(err, &fe) && fe.Code == "shutdown" { // the lease is gone either way
			rs.AbortUDID(r.Context(), req.UDID, "lease released")
		}
		write(w, struct{}{}, err)
	})
	mux.HandleFunc("POST /api/renew", func(w http.ResponseWriter, r *http.Request) {
		var req fleet.ReleaseRequest
		if decode(w, r, &req) {
			l, err := f.Renew(req.UDID, req.Holder)
			write(w, l, err)
		}
	})
	mux.HandleFunc("GET /api/ls", func(w http.ResponseWriter, r *http.Request) {
		entries, err := f.List(r.Context())
		write(w, entries, err)
	})
	return mux
}

func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		write(w, nil, &fleet.Error{Code: "bad-request", Message: err.Error()})
		return false
	}
	return true
}

var statusOf = map[string]int{
	"bad-request": 400, "not-holder": 403, "no-device": 404, "not-leased": 404, "not-found": 404,
	"capacity": 409, "run-active": 409, "run-ended": 409, "busy": 409, "prepare": 502, "shutdown": 502,
}

func write(w http.ResponseWriter, v any, err error) {
	w.Header().Set("Content-Type", "application/json")
	if err != nil {
		var fe *fleet.Error
		if !errors.As(err, &fe) {
			fe = &fleet.Error{Code: "internal", Message: err.Error()}
		}
		w.WriteHeader(cmp.Or(statusOf[fe.Code], 500))
		v = map[string]any{"error": fe}
	}
	json.NewEncoder(w).Encode(v)
}

// Serve listens on a unix socket only its owner can open (0600), replacing a stale socket file but refusing to
// take over a live one. It returns nil when ctx ends.
func Serve(ctx context.Context, h http.Handler, socket string) error {
	if c, err := net.Dial("unix", socket); err == nil {
		c.Close()
		return fmt.Errorf("orbitfleet already serving on %s", socket)
	}
	os.Remove(socket)
	ln, err := net.Listen("unix", socket)
	if err != nil {
		return err
	}
	if err := os.Chmod(socket, 0o600); err != nil {
		ln.Close()
		return err
	}
	return serve(ctx, h, ln)
}

type Client struct{ Socket string }

// Do sends in as JSON and decodes the reply into out (nil to discard). An API error comes back as *fleet.Error.
func (c Client) Do(ctx context.Context, method, path string, in, out any) error {
	var body bytes.Buffer
	if in != nil {
		if err := json.NewEncoder(&body).Encode(in); err != nil {
			return err
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, "http://orbitfleet"+path, &body)
	if err != nil {
		return err
	}
	hc := http.Client{Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", c.Socket)
	}}}
	resp, err := hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		var e struct{ Error *fleet.Error }
		if json.NewDecoder(resp.Body).Decode(&e) != nil || e.Error == nil {
			return fmt.Errorf("%s %s: HTTP %d", method, path, resp.StatusCode)
		}
		return e.Error
	}
	switch out := out.(type) {
	case nil:
		return nil
	case *string: // a text body, e.g. logs
		b, err := io.ReadAll(resp.Body)
		*out = string(b)
		return err
	default:
		return json.NewDecoder(resp.Body).Decode(out)
	}
}
