package web

import (
	"context"
	"crypto/subtle"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strings"
	"time"
)

// ServeTCP serves the browser UI on a loopback IP only (DESIGN.md D6: zero inbound ports). Anything else, a hostname
// included, is refused before listening.
func ServeTCP(ctx context.Context, h http.Handler, addr string) error {
	host, _, err := net.SplitHostPort(addr)
	if ip := net.ParseIP(host); err != nil || ip == nil || !ip.IsLoopback() {
		return fmt.Errorf("refusing to listen on %q: the UI binds a loopback IP only", addr)
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	return serve(ctx, h, ln)
}

func serve(ctx context.Context, h http.Handler, ln net.Listener) error {
	srv := &http.Server{Handler: h, ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shut, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		srv.Shutdown(shut)
	}()
	if err := srv.Serve(ln); !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

// Guard admits a browser to the UI. Any local process and any web page can reach a loopback port, so:
//   - the Host header must name this port on 127.0.0.1, localhost or ::1 (a rebound DNS name fails here);
//   - /login?t=<token> sets an HttpOnly, SameSite=Strict cookie, which every other request must carry
//     (`orbitfleet ui` opens that link; the token lives in a 0600 file);
//   - a write must also send X-Orbitfleet: 1, a header a cross-site form cannot set.
func Guard(h http.Handler, token string, port int) http.Handler {
	hosts := map[string]bool{}
	for _, host := range []string{"127.0.0.1", "localhost", "[::1]"} {
		hosts[fmt.Sprintf("%s:%d", host, port)] = true
	}
	same := func(a string) bool { return subtle.ConstantTimeCompare([]byte(a), []byte(token)) == 1 }
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !hosts[r.Host] {
			http.Error(w, "forbidden host", http.StatusForbidden)
			return
		}
		if r.URL.Path == "/login" {
			if !same(r.URL.Query().Get("t")) {
				http.Error(w, "bad token: run `orbitfleet ui`", http.StatusUnauthorized)
				return
			}
			http.SetCookie(w, &http.Cookie{Name: "orbitfleet", Value: token, Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode})
			next := r.URL.Query().Get("next")
			if !strings.HasPrefix(next, "/") || strings.HasPrefix(next, "//") || strings.HasPrefix(next, `/\`) {
				next = "/" // local paths only: no open redirect
			}
			w.Header().Set("Location", next)
			w.WriteHeader(http.StatusFound)
			return
		}
		if c, err := r.Cookie("orbitfleet"); err != nil || !same(c.Value) {
			http.Error(w, "not signed in: run `orbitfleet ui`", http.StatusUnauthorized)
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead && r.Header.Get("X-Orbitfleet") != "1" {
			http.Error(w, "missing X-Orbitfleet header", http.StatusForbidden)
			return
		}
		h.ServeHTTP(w, r)
	})
}
