package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/url"
	"os"
	"slices"
	"strings"
	"text/tabwriter"
	"time"

	"orbitfleet/runs"
)

// runCmd handles `run <sub> ...` and `runs`. Arguments after `--` go to the app on `run launch`.
func runCmd(ctx context.Context, cmd string, args []string) error {
	sub := "list"
	if cmd == "run" {
		if len(args) == 0 {
			return fmt.Errorf("usage:\n%s", usage)
		}
		sub, args = args[0], args[1:]
	}
	var appArgs []string
	if i := slices.Index(args, "--"); i >= 0 {
		args, appArgs = args[:i], args[i+1:]
	}
	fs := flag.NewFlagSet("run "+sub, flag.ExitOnError)
	asJSON := fs.Bool("json", false, "machine-readable output")
	holder := fs.String("holder", os.Getenv("ORBITFLEET_HOLDER"), "start: who holds the simulator; runs: filter")
	label := fs.String("label", "", "start: what this run tests")
	app := fs.String("app", "", "start, launch: bundle id")
	level := fs.String("log-level", "info", "start: device log level (info, debug, default)")
	predicate := fs.String("predicate", "", "start: log stream predicate")
	wait := fs.Bool("wait-for-debugger", false, "launch: start suspended until a debugger attaches")
	status := fs.String("status", "", "stop: passed or failed")
	reason := fs.String("reason", "", "stop: why")
	grep := fs.String("grep", "", "logs: only lines containing this")
	since := fs.Duration("since", 0, "runs: started within this long, e.g. 24h")
	pos := parse(fs, args)
	minArgs := map[string]int{"list": 0, "start": 1, "launch": 1, "mark": 2, "shot": 1, "stop": 1, "show": 1, "logs": 1}
	if n, ok := minArgs[sub]; !ok || len(pos) < n {
		return fmt.Errorf("usage:\n%s", usage)
	}
	c, err := connect(ctx)
	if err != nil {
		return err
	}
	var out any
	path := func(p string) string { return "/api/runs/" + url.PathEscape(pos[0]) + p }
	switch sub {
	case "start":
		var r runs.Run
		err = c.Do(ctx, "POST", "/api/runs", runs.StartRequest{UDID: pos[0], Holder: *holder, Label: *label, App: *app, LogLevel: *level, Predicate: *predicate}, &r)
		out = r
	case "launch":
		var r runs.Run
		err = c.Do(ctx, "POST", path("/launch"), runs.LaunchRequest{App: *app, WaitForDebugger: *wait, Args: appArgs}, &r)
		out = r
	case "mark":
		var e runs.Event
		err = c.Do(ctx, "POST", path("/mark"), map[string]string{"text": strings.Join(pos[1:], " ")}, &e)
		out = e
	case "shot":
		var a runs.Artifact
		err = c.Do(ctx, "POST", path("/shot"), map[string]string{"name": strings.Join(pos[1:], " ")}, &a)
		out = a
	case "stop":
		var r runs.Run
		err = c.Do(ctx, "POST", path("/stop"), runs.StopRequest{Status: *status, Reason: *reason}, &r)
		out = r
	case "show":
		var r runs.Run
		err = c.Do(ctx, "GET", path(""), nil, &r)
		out = r
	case "logs":
		var s string
		err = c.Do(ctx, "GET", path("/logs?grep="+url.QueryEscape(*grep)), nil, &s)
		out = s
	case "list":
		q := url.Values{"holder": {*holder}}
		if *since > 0 {
			q.Set("since", since.String())
		}
		var rs []runs.Run
		err = c.Do(ctx, "GET", "/api/runs?"+q.Encode(), nil, &rs)
		out = rs
	}
	return emit(*asJSON, out, err, printRunHuman)
}

func printRunHuman(v any) {
	switch v := v.(type) {
	case runs.Run:
		fmt.Printf("%s  %s  %s  %s\n", v.ID, v.Status, v.Holder, strings.TrimSpace(v.Label+" "+v.Reason))
		for _, a := range v.Artifacts {
			fmt.Printf("  %-10s %s (%d KB)\n", a.Kind, a.Path, a.Bytes/1024)
		}
		for _, w := range v.Warnings {
			fmt.Println("  warning:", w)
		}
	case runs.Event:
		fmt.Printf("%s %s\n", v.Kind, v.Text)
	case runs.Artifact:
		fmt.Println(v.Path)
	case []runs.Run:
		w := tabwriter.NewWriter(os.Stdout, 0, 4, 2, ' ', 0)
		fmt.Fprintln(w, "RUN\tSTATUS\tHOLDER\tLABEL\tSTARTED\tTOOK")
		for _, r := range v {
			took := "-"
			if r.Ended != nil {
				took = r.Ended.Sub(r.Started).Round(time.Second).String()
			}
			fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\t%s\n", r.ID, r.Status, r.Holder, r.Label, r.Started.Local().Format("Jan 2 15:04"), took)
		}
		w.Flush()
	case string: // device log ndjson -> "15:04:05.000 Error Process: message"
		for line := range strings.Lines(v) {
			var e struct{ Timestamp, MessageType, ProcessImagePath, EventMessage string }
			if json.Unmarshal([]byte(line), &e) != nil {
				continue // log stream's "Filtering the log data..." header
			}
			ts := e.Timestamp
			if len(ts) >= 23 {
				ts = ts[11:23]
			}
			proc := e.ProcessImagePath[strings.LastIndex(e.ProcessImagePath, "/")+1:]
			fmt.Printf("%s %-7s %s: %s\n", ts, e.MessageType, proc, e.EventMessage)
		}
	}
}

// debugCmd handles `debug <run> lldb [--open] | profile [--template t] [--time-limit d] | video start|stop`.
func debugCmd(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("debug", flag.ExitOnError)
	asJSON := fs.Bool("json", false, "machine-readable output")
	open := fs.Bool("open", false, "lldb: run it in a new Terminal window")
	template := fs.String("template", "", "profile: Instruments template (default Time Profiler)")
	limit := fs.Duration("time-limit", 0, "profile: how long to record (default 30s, at most 10m)")
	pos := parse(fs, args)
	if len(pos) < 2 || (pos[1] == "video" && (len(pos) != 3 || (pos[2] != "start" && pos[2] != "stop"))) {
		return fmt.Errorf("usage:\n%s", usage)
	}
	c, err := connect(ctx)
	if err != nil {
		return err
	}
	path := "/api/runs/" + url.PathEscape(pos[0]) + "/debug/" + pos[1]
	var out any
	switch pos[1] {
	case "lldb":
		var d runs.Debug
		err = c.Do(ctx, "POST", path, map[string]bool{"open": *open}, &d)
		out = d
	case "profile":
		var a runs.Artifact
		err = c.Do(ctx, "POST", path, map[string]any{"template": *template, "limit": *limit}, &a)
		out = a
	case "video":
		var a runs.Artifact
		err = c.Do(ctx, "POST", path, map[string]string{"action": pos[2]}, &a)
		out = a
	default:
		return fmt.Errorf("debug %s: use lldb, profile or video", pos[1])
	}
	return emit(*asJSON, out, err, func(v any) {
		switch v := v.(type) {
		case runs.Debug:
			fmt.Println(v.Command)
		case runs.Artifact:
			fmt.Println(v.Path)
			if v.Note != "" {
				fmt.Println("note:", v.Note)
			}
		}
	})
}
