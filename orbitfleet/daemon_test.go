package main

import "testing"

// One daemon per state dir: a second serve must fail before touching any state (its runs.New would abort the
// first daemon's running runs), and the CLI must see the first one as running and wait instead of starting another.
func TestOneDaemonPerStateDir(t *testing.T) {
	dir := t.TempDir()
	if daemonRunning(dir) {
		t.Fatal("no daemon yet")
	}
	release, err := lockDaemon(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := lockDaemon(dir); err == nil {
		t.Fatal("a second daemon got the lock")
	}
	if !daemonRunning(dir) {
		t.Fatal("the CLI must see the starting daemon")
	}
	release()
	if daemonRunning(dir) {
		t.Fatal("lock still held after release")
	}
}
