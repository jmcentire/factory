package setup

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Critical data-preservation invariant: unknown liveness must never authorize
// destructive cleanup. These implementation-authored checks are not independent
// acceptance; do not casually weaken them when changing client or record handling.
func TestRunCheckoutLivenessPreservesWork(t *testing.T) {
	for _, tc := range []struct {
		name, record, message string
		status                int
		missingClient, keep   bool
	}{
		{"live selected client", `{"tmux_socket":"fixture","tmux_tmpdir":"/fixture/original"}`, "", 0, false, true},
		{"missing client", `{"tmux_socket":"fixture","tmux_tmpdir":"/fixture/original"}`, "", 0, true, true},
		{"client failure", `{"tmux_socket":"fixture","tmux_tmpdir":"/fixture/original"}`, "permission denied", 2, false, true},
		{"unknown transport", `{"tmux_socket":"fixture","tmux_tmpdir":"/fixture/original"}`, "transport unavailable", 1, false, true},
		{"legacy socket name only", `{"tmux_socket":"fixture"}`, "", 0, false, true},
		{"missing record", "", "", 0, false, true},
		{"malformed record", `not JSON "tmux_socket":"fixture"`, "no server running on fixture", 1, false, true},
		{"confirmed inactive", `{"tmux_socket":"fixture","tmux_tmpdir":"/fixture/original"}`, "no server running on fixture", 1, false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dir, err := filepath.EvalSymlinks(t.TempDir())
			must(t, err)
			target := filepath.Join(dir, "target")
			must(t, os.Mkdir(target, 0o755))
			out, err := exec.Command("git", "init", "-q", target).CombinedOutput()
			if err != nil {
				t.Fatalf("git init: %v %s", err, out)
			}
			script := filepath.Join(dir, "run-checkout.sh")
			must(t, os.WriteFile(script, runCheckoutScript, 0o755))
			// Metadata parsing must not import code from the caller's repository.
			must(t, os.WriteFile(filepath.Join(dir, "json.py"), []byte("from pathlib import Path\nPath('json-imported').touch()\nraise RuntimeError('untrusted module')\n"), 0o644))
			t.Setenv("PYTHONPATH", dir)
			run := filepath.Join(target, ".factory", "runs", "probe")
			bulk := filepath.Join(run, "validation", "work")
			must(t, os.MkdirAll(filepath.Dir(bulk), 0o755))
			must(t, os.WriteFile(bulk, []byte("valuable work"), 0o644))
			if tc.record != "" {
				must(t, os.WriteFile(filepath.Join(run, "harness.json"), []byte(tc.record), 0o644))
			}
			bin := filepath.Join(dir, "clients")
			must(t, os.Mkdir(bin, 0o755))
			// An unrelated PATH client claims inactivity. It must not override the selection.
			must(t, os.WriteFile(filepath.Join(bin, "tmux"), []byte("#!/bin/sh\necho 'no server running on unrelated' >&2\nexit 1\n"), 0o755))
			selected := filepath.Join(bin, "selected client")
			if !tc.missingClient {
				body := fmt.Sprintf("#!/bin/sh\nprintf '%%s\\n' \"$@\" > \"$FACTORY_TMUX_LOG\"\nprintf '%%s\\n' '%s' >&2\nexit %d\n", tc.message, tc.status)
				must(t, os.WriteFile(selected, []byte(body), 0o755))
			}
			t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
			// A relative path containing a space also checks caller-directory selection.
			t.Setenv("FACTORY_TMUX", filepath.Join("clients", "selected client"))
			log := filepath.Join(dir, "client-args")
			t.Setenv("FACTORY_TMUX_LOG", log)
			t.Setenv("TMUX_TMPDIR", filepath.Join(dir, "different socket namespace"))
			invoke := func(command string) ([]byte, error) {
				cmd := exec.Command("bash", script, command, target, "probe")
				if command == "list" {
					cmd = exec.Command("bash", script, command, target)
				}
				cmd.Dir = dir
				return cmd.CombinedOutput()
			}
			listed, err := invoke("list")
			if err != nil {
				t.Fatalf("list: %v %s", err, listed)
			}
			cleaned, err := invoke("clean")
			_, statErr := os.Stat(bulk)
			if tc.keep && statErr != nil {
				t.Fatalf("cleanup lost work: %v", statErr)
			}
			if tc.keep {
				content, err := os.ReadFile(bulk)
				must(t, err)
				if string(content) != "valuable work" {
					t.Fatal("cleanup changed retained work")
				}
			}
			if _, err := os.Stat(filepath.Join(dir, "json-imported")); !os.IsNotExist(err) {
				t.Fatal("metadata parsing executed repository code")
			}
			if !tc.keep && !os.IsNotExist(statErr) {
				t.Fatalf("confirmed-inactive work was not cleaned: %v", statErr)
			}
			if (err != nil) != tc.keep {
				t.Fatalf("clean: %v %s", err, cleaned)
			}
			if strings.Contains(string(listed), "keep:") != tc.keep {
				t.Fatalf("incorrect list disposition: %s", listed)
			}
			if !tc.missingClient && strings.Contains(tc.record, "tmux_tmpdir") {
				args, err := os.ReadFile(log)
				must(t, err)
				if string(args) != fmt.Sprintf("-S\n/fixture/original/tmux-%d/fixture\nhas-session\n-t\n=probe\n", os.Getuid()) {
					t.Fatalf("wrong run addressing: %q", args)
				}
			}
		})
	}
}
