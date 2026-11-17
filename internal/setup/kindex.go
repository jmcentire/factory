package setup

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Kindex and Kinbase without Nix. The devshell builds both in its flake, along
// with Nix itself and dozens of other tools; this component installs just the
// two, at the commits the devshell pins, so a machine can have the Factory and
// Kinbase and nothing more.

// Keep these at the pins in wandercom/tooling-devshell's flake.nix (its
// kindex and kinbase packages), so both routes run the same code.
const (
	PinnedKindexRev  = "75dde5adcd6b11759427e339009327b8d574fb30" // kindex 0.50.1 (the commit its v0.50.1 tag, which the flake pins, points at)
	PinnedKinbaseRev = "c9267aee54dffa6f907be304bc70fbbe73ea93b3" // kinbase 0.1.2, the flake's kinbase input

	kindexRepoURL  = "https://github.com/wandercom/kindex"
	kinbaseRepoURL = "https://github.com/wandercom/kinbase"

	// kinbase's workspace declares rust-version 1.98.0.
	kinbaseRustMinor = 98
	// The extras that give kindex the dependency set the devshell builds it
	// with: the Kinbase verifier, the MCP server, and the optional LLM,
	// vector and reminder features.
	kindexExtras = "kinbase,mcp,llm,vectors,reminders"
	// kindexMarker and kinbaseMarker identify the wrappers this component
	// writes.
	kindexMarker  = "# factory-setup: kindex wrapper"
	kinbaseMarker = "# factory-setup: kinbase wrapper"
)

// kindexCommands are kindex's entry points, each wrapped into ~/.local/bin.
var kindexCommands = []string{"kin", "kin-mcp", "kindex-lite"}

// LocalBin is ~/.local/bin, where this component puts its commands, as
// install.sh does factory-setup itself.
func (o Options) LocalBin(name string) string { return filepath.Join(o.Home, ".local", "bin", name) }

// KinbaseRoot is where cargo installs the kinbase binary itself; ~/.local/bin
// gets the wrapper that runs it.
func (o Options) KinbaseRoot() string {
	return filepath.Join(o.Home, ".local", "share", "wander", "kinbase")
}

// KinbaseRaw is the kinbase binary, under its cargo root.
func (o Options) KinbaseRaw() string { return filepath.Join(o.KinbaseRoot(), "bin", "kinbase") }

// KindexVenv is the virtualenv kindex is installed into.
func (o Options) KindexVenv() string {
	return filepath.Join(o.Home, ".local", "share", "wander", "kindex")
}

func kindexReady(o Options) bool {
	return o.Has(ComponentKindex) || FileContains(o.LocalBin("kin-mcp"), kindexMarker)
}

// cargoFor finds a cargo that runs, at Rust 1.minor or newer (0 for any),
// or says what to do about it.
func cargoFor(ctx context.Context, env *Env, minor int) (string, string) {
	cargo, ok := env.Have("cargo")
	if !ok {
		return "", "cargo not found (" + osHint("rust") + ")"
	}
	// rustup's cargo is a proxy that exists before any Rust does: until a
	// default toolchain is chosen it only errors.
	v, err := env.Output(ctx, cargo, "--version")
	if err != nil {
		rustup := filepath.Join(filepath.Dir(cargo), "rustup")
		if _, ok := env.Have(rustup); !ok {
			rustup = "rustup"
		}
		return "", cargo + " does not run; choose a Rust toolchain: \"" + rustup + "\" default stable"
	}
	var major, have int
	if _, err := fmt.Sscanf(v, "cargo %d.%d", &major, &have); err == nil && major == 1 && have < minor {
		return "", fmt.Sprintf("Rust 1.%d+ is needed, %s is %s (rustup update stable, or brew upgrade rust)", minor, cargo, strings.Fields(v)[1])
	}
	return cargo, ""
}

// kinbaseInstallStep builds the kinbase CLI with cargo and puts it in
// ~/.local/bin.
type kinbaseInstallStep struct{}

func (kinbaseInstallStep) ID() string    { return "kinbase.install" }
func (kinbaseInstallStep) Title() string { return "Install kinbase (built with cargo, no Nix)" }

// shq quotes s for a POSIX shell.
func shq(s string) string { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }

// kinbaseWrapper runs the kinbase binary itself. Kinbase is reached through
// the kinbase.wander.com gateway, so nothing has to be opened first; the
// wrapper is here so the binary keeps its own cargo root, and so a later
// install can tell the command in ~/.local/bin is its own.
func kinbaseWrapper(o Options) string {
	return fmt.Sprintf("#!/bin/sh\n%s (kinbase %s)\nexec %s \"$@\"\n", kinbaseMarker, shortRef(PinnedKinbaseRev), shq(o.KinbaseRaw()))
}

// kinbaseWrappers are the files this step writes into ~/.local/bin.
func kinbaseWrappers(env *Env) map[string]string {
	return map[string]string{env.Opts.LocalBin("kinbase"): kinbaseWrapper(env.Opts)}
}

// retiredKinbase are the commands earlier installs wrote that kinbase no
// longer needs: kinbase-raw, beside the staging tunnel's wrapper.
func retiredKinbase(o Options) []string {
	var found []string
	if p := o.LocalBin("kinbase-raw"); FileContains(p, kinbaseMarker) {
		found = append(found, p)
	}
	return found
}

// v019Kinbase is a kinbase that factory-setup v0.1.9 installed with cargo
// straight into ~/.local/bin, where the wrapper now goes.
func v019Kinbase(o Options) bool {
	return FileContains(filepath.Join(o.Home, ".local", ".crates.toml"), "kinbase?rev=")
}

func kinbaseAtPin(ctx context.Context, env *Env) bool {
	o := env.Opts
	if !FileContains(filepath.Join(o.KinbaseRoot(), ".crates.toml"), "kinbase?rev="+PinnedKinbaseRev) {
		return false
	}
	_, err := env.Output(ctx, o.KinbaseRaw(), "--version")
	return err == nil
}

func (kinbaseInstallStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	o := env.Opts
	for path := range kinbaseWrappers(env) {
		if (Exists(path) || isSymlink(path)) && !FileContains(path, kinbaseMarker) && !(v019Kinbase(o) && path == o.LocalBin("kinbase")) {
			return StatusBlocked, path + " was not installed by factory-setup; move it aside to install kinbase there", nil
		}
	}
	if kinbaseAtPin(ctx, env) {
		for path, body := range kinbaseWrappers(env) {
			if b, err := os.ReadFile(path); err != nil || string(b) != body {
				return StatusMissing, "writing the kinbase command into " + filepath.Dir(path), nil
			}
		}
		if len(retiredKinbase(o)) > 0 {
			return StatusMissing, "retiring the staging tunnel's kinbase-raw", nil
		}
		return StatusDone, "kinbase @ " + shortRef(PinnedKinbaseRev), nil
	}
	if _, reason := cargoFor(ctx, env, kinbaseRustMinor); reason != "" {
		if env.DryRun {
			// The Rust step before this one installs it in a real run.
			return StatusMissing, "built once Rust is installed", nil
		}
		return StatusBlocked, reason, nil
	}
	return StatusMissing, "", nil
}

func (kinbaseInstallStep) Apply(ctx context.Context, env *Env) error {
	o := env.Opts
	if !kinbaseAtPin(ctx, env) {
		cargo, reason := cargoFor(ctx, env, kinbaseRustMinor)
		if reason != "" {
			return fmt.Errorf("%s", reason)
		}
		if v019Kinbase(o) {
			if err := env.Run(ctx, o.Home, cargo, "uninstall", "--root", filepath.Join(o.Home, ".local"), "kinbase"); err != nil {
				return err
			}
		}
		if err := env.Run(ctx, o.Home, cargo, "install", "--locked", "--force",
			"--git", kinbaseRepoURL, "--rev", PinnedKinbaseRev,
			"--root", o.KinbaseRoot(), "kinbase"); err != nil {
			return err
		}
	}
	for path, body := range kinbaseWrappers(env) {
		if err := env.WriteFile(path, []byte(body), 0o755); err != nil {
			return err
		}
	}
	// Written by an earlier install and wholly its own.
	for _, p := range retiredKinbase(o) {
		env.Log("remove %s", p)
		if err := os.Remove(p); err != nil {
			return err
		}
	}
	return nil
}

// kindexInstallStep installs kindex into its own virtualenv and wraps its
// commands into ~/.local/bin.
type kindexInstallStep struct{}

func (kindexInstallStep) ID() string    { return "kindex.install" }
func (kindexInstallStep) Title() string { return "Install kindex (Python, no Nix)" }

// venvHealthy reports whether kindex's virtualenv is one: its Python runs, and
// runs as the virtualenv's rather than the interpreter it came from. Without
// its pyvenv.cfg, or once Homebrew has replaced the Python it links to, it is
// not, and pip run through it installs into (or is refused by) the system's.
func venvHealthy(ctx context.Context, env *Env) bool {
	py := filepath.Join(env.Opts.KindexVenv(), "bin", "python")
	if !Exists(py) || !Exists(filepath.Join(env.Opts.KindexVenv(), "pyvenv.cfg")) {
		return false
	}
	_, err := env.Output(ctx, py, "-c", "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)")
	return err == nil
}

func kindexStamp(o Options) string { return filepath.Join(o.KindexVenv(), ".factory-setup-rev") }

// kindexWrapper runs a kindex command from the virtualenv with ~/.local/bin
// first on its PATH: kindex finds kinbase with shutil.which, and an agent may
// start the MCP server with a PATH that lacks it. The devshell's wrapper does
// the same.
func kindexWrapper(o Options, name string) string {
	return fmt.Sprintf("#!/bin/sh\n%s (kindex %s)\nPATH=%s:\"$PATH\" exec %s \"$@\"\n",
		kindexMarker, shortRef(PinnedKindexRev), shq(filepath.Dir(o.LocalBin(name))), shq(filepath.Join(o.KindexVenv(), "bin", name)))
}

// olderKindex are the kindex commands in ~/.local/bin this component did not
// write: an older kindex, from pipx, uv or pip.
func olderKindex(o Options) []string {
	var found []string
	for _, name := range kindexCommands {
		if p := o.LocalBin(name); (Exists(p) || isSymlink(p)) && !FileContains(p, kindexMarker) {
			found = append(found, p)
		}
	}
	return found
}

// replacedDir keeps what the installer moved out of the way, off PATH.
func (o Options) replacedDir() string {
	return filepath.Join(o.Home, ".local", "share", "wander", "replaced")
}

// moveAside puts path in the replaced directory under a name not yet taken
// there, so nothing is lost and nothing stray is left on PATH.
func moveAside(env *Env, path string) error {
	dir := env.Opts.replacedDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	dest := filepath.Join(dir, filepath.Base(path))
	for i := 1; Exists(dest) || isSymlink(dest); i++ {
		dest = filepath.Join(dir, fmt.Sprintf("%s.%d", filepath.Base(path), i))
	}
	env.Log("move %s -> %s", path, dest)
	return os.Rename(path, dest)
}

func isSymlink(p string) bool {
	st, err := os.Lstat(p)
	return err == nil && st.Mode()&os.ModeSymlink != 0
}

func (s kindexInstallStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	o := env.Opts
	older := olderKindex(o)
	done := len(older) == 0 && FileContains(kindexStamp(o), PinnedKindexRev) && venvHealthy(ctx, env)
	for _, name := range kindexCommands {
		if b, err := os.ReadFile(o.LocalBin(name)); err != nil || string(b) != kindexWrapper(o, name) {
			done = false
		}
	}
	if done {
		return StatusDone, "kindex " + shortRef(PinnedKindexRev) + " in " + o.KindexVenv(), nil
	}
	if py, tooOld := findPython(ctx, env); py == "" {
		return StatusBlocked, pythonBlocked(tooOld), nil
	}
	if len(older) > 0 {
		return StatusMissing, "replacing an older kindex (" + strings.Join(older, ", ") + "); it moves to " + o.replacedDir(), nil
	}
	return StatusMissing, "", nil
}

func (s kindexInstallStep) Apply(ctx context.Context, env *Env) error {
	o := env.Opts
	py, tooOld := findPython(ctx, env)
	if py == "" {
		return fmt.Errorf("%s", pythonBlocked(tooOld))
	}
	venvPy := filepath.Join(o.KindexVenv(), "bin", "python")
	if !venvHealthy(ctx, env) {
		// --clear rebuilds one left half there, or by a Python since upgraded.
		if err := env.Run(ctx, o.Home, py, "-m", "venv", "--clear", o.KindexVenv()); err != nil {
			return err
		}
	}
	spec := "kindex[" + kindexExtras + "] @ git+" + kindexRepoURL + "@" + PinnedKindexRev
	if err := env.Run(ctx, o.Home, venvPy, "-m", "pip", "install", "--quiet", "--force-reinstall", spec); err != nil {
		return err
	}
	// An older kindex's commands make way for this one's, kept rather than
	// deleted.
	for _, p := range olderKindex(o) {
		if err := moveAside(env, p); err != nil {
			return err
		}
	}
	for _, name := range kindexCommands {
		if err := env.WriteFile(o.LocalBin(name), []byte(kindexWrapper(o, name)), 0o755); err != nil {
			return err
		}
	}
	return env.WriteFile(kindexStamp(o), []byte(PinnedKindexRev+"\n"), 0o644)
}
