package setup

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// fakeRust is a machine's Rust: where cargo is, whether rustup sits beside
// it, and the version cargo reports ("" when it does not run). Commands it
// runs are recorded; rustup default/update and the rustup.rs installer
// change what cargo reports, as they would.
type fakeRust struct {
	t       *testing.T
	env     *Env
	dir     string // where cargo and rustup are; "" for no cargo
	rustup  bool
	version string
	ran     [][]string
}

func newFakeRust(t *testing.T, env *Env, withCargo, rustup bool, version string) *fakeRust {
	f := &fakeRust{t: t, env: env, rustup: rustup, version: version}
	// Only ~/.cargo/bin is searched: the host's own cargo stays out of it.
	env.searchDirs = []string{filepath.Join(env.Opts.Home, ".cargo", "bin")}
	if withCargo {
		f.dir = t.TempDir()
		f.put("cargo")
		if rustup {
			f.put("rustup")
		}
	}
	env.lookPath = func(name string) (string, error) {
		if f.dir != "" && (name == "cargo" || name == "rustup" && f.rustup) {
			return filepath.Join(f.dir, name), nil
		}
		if name == "bash" {
			return "/bin/bash", nil
		}
		return "", exec.ErrNotFound
	}
	env.run = f.run
	return f
}

func (f *fakeRust) put(name string) {
	must(f.t, os.MkdirAll(f.dir, 0o755))
	must(f.t, os.WriteFile(filepath.Join(f.dir, name), []byte("#!/bin/sh\n"), 0o755))
}

func (f *fakeRust) run(_ context.Context, c *exec.Cmd) error {
	f.ran = append(f.ran, c.Args)
	args := strings.Join(c.Args[1:], " ")
	switch base := filepath.Base(c.Path); {
	case base == "cargo":
		if f.version == "" {
			return errors.New("rustup could not choose a version of cargo to run")
		}
		fmt.Fprintln(c.Stdout, "cargo "+f.version+" (abc 2026-10-01)")
	case base == "rustup" && (args == "default stable" || args == "update stable"):
		f.version = "1.99.0"
	case base == "bash" && strings.Contains(args, "https://sh.rustup.rs"):
		// rustup.rs puts cargo and rustup in ~/.cargo/bin.
		f.dir, f.rustup, f.version = filepath.Join(f.env.Opts.Home, ".cargo", "bin"), true, "1.99.0"
		f.put("cargo")
		f.put("rustup")
	}
	return nil
}

func rustReadyAfter(t *testing.T, env *Env, s *rustInstallStep) {
	t.Helper()
	must(t, s.Apply(context.Background(), env))
	if st, detail, _ := s.Check(context.Background(), env); st != StatusDone {
		t.Fatalf("Rust not ready after apply: %v %q", st, detail)
	}
}

func TestRustIsInstalledWhenThereIsNone(t *testing.T) {
	env := testEnv(t, kindexOptions())
	f := newFakeRust(t, env, false, false, "")
	s := &rustInstallStep{minor: kinbaseRustMinor}
	if st, detail, _ := s.Check(context.Background(), env); st != StatusMissing || !strings.Contains(detail, "rustup.rs") {
		t.Fatalf("expected rustup.rs to install it, got %v %q", st, detail)
	}
	rustReadyAfter(t, env, s)
	script := strings.Join(f.ran[0], " ")
	for _, want := range []string{"https://sh.rustup.rs", "-y", "--no-modify-path", "--default-toolchain stable"} {
		if !strings.Contains(script, want) {
			t.Errorf("the installer ran without %q: %s", want, script)
		}
	}
}

// Homebrew's rustup: a cargo proxy that only errors until a toolchain is
// chosen.
func TestARustupWithoutAToolchainGetsStable(t *testing.T) {
	env := testEnv(t, kindexOptions())
	f := newFakeRust(t, env, true, true, "")
	s := &rustInstallStep{}
	if st, detail, _ := s.Check(context.Background(), env); st != StatusMissing || !strings.Contains(detail, "toolchain") {
		t.Fatalf("expected a toolchain to be chosen, got %v %q", st, detail)
	}
	rustReadyAfter(t, env, s)
	if !slices.ContainsFunc(f.ran, func(a []string) bool {
		return strings.HasSuffix(a[0], "rustup") && strings.Join(a[1:], " ") == "default stable"
	}) {
		t.Fatalf("rustup default stable did not run: %q", f.ran)
	}
}

func TestAnOldRustupToolchainIsUpdated(t *testing.T) {
	env := testEnv(t, kindexOptions())
	f := newFakeRust(t, env, true, true, "1.97.0")
	s := &rustInstallStep{minor: kinbaseRustMinor}
	if st, _, _ := s.Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("expected an update, got %v", st)
	}
	rustReadyAfter(t, env, s)
	if !slices.ContainsFunc(f.ran, func(a []string) bool { return strings.Join(a[1:], " ") == "update stable" }) {
		t.Fatalf("rustup update stable did not run: %q", f.ran)
	}
}

// A Rust that rustup does not manage (Homebrew's rust formula, a distro's)
// is not the installer's to replace.
func TestAnOldRustWithoutRustupIsLeftToThePerson(t *testing.T) {
	env := testEnv(t, kindexOptions())
	newFakeRust(t, env, true, false, "1.97.0")
	st, detail, _ := (&rustInstallStep{minor: kinbaseRustMinor}).Check(context.Background(), env)
	if st != StatusBlocked || !strings.Contains(detail, "Rust 1.98+") {
		t.Fatalf("expected blocked naming the version, got %v %q", st, detail)
	}
}

func TestRustComesOnceBeforeEveryCargoBuild(t *testing.T) {
	for _, c := range []struct {
		components []Component
		minor      int
	}{
		{[]Component{ComponentKindex}, kinbaseRustMinor},
		{[]Component{ComponentFactory, ComponentKindex, ComponentAgents}, kinbaseRustMinor},
	} {
		o := kindexOptions()
		o.Components = c.components
		env := testEnv(t, o)
		p, err := BuildPlan(env.Opts)
		must(t, err)
		var ids []string
		var rust *rustInstallStep
		for _, s := range p.Steps {
			ids = append(ids, s.ID())
			if r, ok := s.(*rustInstallStep); ok {
				rust = r
			}
		}
		at := slices.Index(ids, "rust.install")
		if at < 0 || slices.Index(ids[at+1:], "rust.install") >= 0 || rust.minor != c.minor {
			t.Fatalf("%v: want rust.install once at 1.%d: %v", c.components, c.minor, ids)
		}
		for _, needs := range []string{"kinbase.install"} {
			if i := slices.Index(ids, needs); i >= 0 && i < at {
				t.Fatalf("%v: %s comes before rust.install: %v", c.components, needs, ids)
			}
		}
	}
}
