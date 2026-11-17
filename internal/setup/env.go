package setup

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Env is the execution environment steps run against. Tests substitute a
// temporary home and a recording Exec.
type Env struct {
	Opts   Options
	DryRun bool
	// Stdout and Stderr receive output of interactive commands (installers
	// that prompt for sudo). Non-interactive commands are captured.
	Stdout io.Writer
	Stderr io.Writer
	// Log receives one line per notable action.
	Log func(format string, args ...any)
	// Interactive is set when a person is at a terminal: a step that needs a
	// browser sign-in only runs then.
	Interactive bool
	// lookPath, run and searchDirs are indirections for tests; searchDirs,
	// when set, replaces Have's fallback directories.
	lookPath   func(string) (string, error)
	run        func(ctx context.Context, c *exec.Cmd) error
	searchDirs []string
}

// NewEnv builds an Env for real execution.
func NewEnv(o Options) *Env {
	return &Env{
		Opts:     o,
		DryRun:   o.DryRun,
		Stdout:   os.Stdout,
		Stderr:   os.Stderr,
		Log:      func(string, ...any) {},
		lookPath: exec.LookPath,
		run:      func(_ context.Context, c *exec.Cmd) error { return c.Run() },
	}
}

// Have reports whether a binary is on PATH or at a known nix location. An
// absolute path is accepted as-is when it is executable.
func (e *Env) Have(name string) (string, bool) {
	if filepath.IsAbs(name) {
		if st, err := os.Stat(name); err == nil && st.Mode()&0o111 != 0 && !st.IsDir() {
			return name, true
		}
		return "", false
	}
	if p, err := e.lookPath(name); err == nil {
		return p, true
	}
	dirs := e.searchDirs
	if dirs == nil {
		dirs = append([]string{
			"/nix/var/nix/profiles/default/bin",
			filepath.Join(e.Opts.Home, ".nix-profile", "bin"),
			"/opt/homebrew/bin",
			"/usr/local/bin",
			filepath.Join(e.Opts.Home, ".local", "bin"),
		}, rustDirs(e.Opts.Home)...)
	}
	for _, dir := range dirs {
		p := filepath.Join(dir, name)
		if st, err := os.Stat(p); err == nil && st.Mode()&0o111 != 0 {
			return p, true
		}
	}
	return "", false
}

// Output runs a command and returns its trimmed stdout. Never mutating; safe
// in dry-run.
func (e *Env) Output(ctx context.Context, name string, args ...string) (string, error) {
	return e.OutputIn(ctx, "", name, args...)
}

// OutputIn is Output run in dir.
func (e *Env) OutputIn(ctx context.Context, dir, name string, args ...string) (string, error) {
	path, ok := e.Have(name)
	if !ok {
		return "", fmt.Errorf("%s: not found", name)
	}
	var out, errb bytes.Buffer
	c := exec.CommandContext(ctx, path, args...)
	c.Dir = dir
	c.Env = e.childEnv()
	c.Stdout, c.Stderr = &out, &errb
	// A child left holding the pipes must not outlive ctx's deadline.
	c.WaitDelay = 2 * time.Second
	// ErrWaitDelay: the command succeeded, a child it left kept the pipes.
	if err := e.run(ctx, c); err != nil && !errors.Is(err, exec.ErrWaitDelay) {
		return "", fmt.Errorf("%s %s: %w: %s", name, strings.Join(args, " "), err, strings.TrimSpace(errb.String()))
	}
	return strings.TrimSpace(out.String()), nil
}

// rustDirs are where rustup puts cargo: ~/.cargo/bin from rustup.rs, and the
// bin of Homebrew's keg-only rustup, which is not linked onto PATH.
func rustDirs(home string) []string {
	return []string{
		filepath.Join(home, ".cargo", "bin"),
		"/opt/homebrew/opt/rustup/bin",
		"/usr/local/opt/rustup/bin",
	}
}

// childEnv is the environment for commands the installer runs. A freshly
// installed Nix is not on this process's PATH until a new login shell, so
// scripts that call `nix` (nix-bootstrap.sh, direnv) would fail on the very
// run that installed it. Prepend the standard profile locations.
func (e *Env) childEnv() []string {
	extra := append([]string{
		"/nix/var/nix/profiles/default/bin",
		filepath.Join(e.Opts.Home, ".nix-profile", "bin"),
		filepath.Join(e.Opts.Home, ".local", "bin"),
	}, rustDirs(e.Opts.Home)...)
	cur := os.Getenv("PATH")
	var add []string
	for _, d := range extra {
		if !strings.Contains(":"+cur+":", ":"+d+":") && IsDir(d) {
			add = append(add, d)
		}
	}
	env := os.Environ()
	if len(add) == 0 {
		return env
	}
	out := make([]string, 0, len(env)+1)
	for _, kv := range env {
		if !strings.HasPrefix(kv, "PATH=") {
			out = append(out, kv)
		}
	}
	return append(out, "PATH="+strings.Join(add, ":")+":"+cur)
}

// Run executes a mutating command with the user's terminal attached, so
// installers can prompt for sudo. Skipped in dry-run.
func (e *Env) Run(ctx context.Context, dir string, name string, args ...string) error {
	e.Log("$ %s %s", name, strings.Join(args, " "))
	if e.DryRun {
		return nil
	}
	path, ok := e.Have(name)
	if !ok {
		return fmt.Errorf("%s: not found", name)
	}
	c := exec.CommandContext(ctx, path, args...)
	c.Dir = dir
	c.Env = e.childEnv()
	c.Stdin, c.Stdout, c.Stderr = os.Stdin, e.Stdout, e.Stderr
	return e.run(ctx, c)
}

// Shell runs a script through bash with the terminal attached.
func (e *Env) Shell(ctx context.Context, dir string, script string) error {
	return e.Run(ctx, dir, "bash", "-euo", "pipefail", "-c", script)
}

// BackupSuffix is appended to a pre-existing file's name before the installer
// changes it, so a bad edit is one `mv` away from undone.
const BackupSuffix = ".factory-setup.bak"

// backup copies an existing regular file to path+BackupSuffix when its content
// differs from what is about to be written. Files the installer created itself
// on a previous run are backed up too; the cost is one small file.
func (e *Env) backup(path string, next []byte) error {
	cur, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	if bytes.Equal(cur, next) {
		return nil
	}
	// A wrapper factory-setup wrote is wholly its own: a backup would only be
	// a stray command beside it in ~/.local/bin.
	if bytes.Contains(cur, []byte(kindexMarker)) || bytes.Contains(cur, []byte(kinbaseMarker)) {
		return nil
	}
	st, err := os.Stat(path)
	if err != nil {
		return err
	}
	e.Log("backup %s -> %s", path, path+BackupSuffix)
	return os.WriteFile(path+BackupSuffix, cur, st.Mode().Perm())
}

// WriteFile writes content atomically, creating parent directories and
// backing up a pre-existing file first. Skipped in dry-run. It refuses to
// follow symlinks at the destination.
func (e *Env) WriteFile(path string, content []byte, mode os.FileMode) error {
	e.Log("write %s", path)
	if e.DryRun {
		return nil
	}
	if st, err := os.Lstat(path); err == nil && st.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("%s is a symlink; refusing to write through it", path)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if err := e.backup(path, content); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".factory-setup-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(content); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(mode); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

// WriteConfigFile writes a user-edited configuration file (an agent's
// settings, MCP, hooks or memory file) as WriteFile does, except that a
// symlink at path is followed and its target is written. People keep these
// files in a dotfiles repository and link them into place; replacing the link
// with a plain file would silently detach it from that repository.
//
// A link whose target does not exist is an error rather than a file created
// wherever the link happens to point. Files the installer owns outright keep
// WriteFile's refusal, because a link there usually points into a virtualenv
// or the read-only Nix store.
func (e *Env) WriteConfigFile(path string, content []byte, mode os.FileMode) error {
	target, err := linkTarget(path)
	if err != nil {
		return err
	}
	if target == path {
		return e.WriteFile(path, content, mode)
	}
	e.Log("%s links to %s", path, target)
	if err := e.WriteFile(target, content, mode); err != nil {
		return fmt.Errorf("%s links to %s, which could not be written: %w", path, target, err)
	}
	return nil
}

// linkTarget returns the file that path finally points to, or path itself when it
// is not a symlink or does not exist yet.
func linkTarget(path string) (string, error) {
	st, err := os.Lstat(path)
	if err != nil {
		if os.IsNotExist(err) {
			return path, nil
		}
		return "", err
	}
	if st.Mode()&os.ModeSymlink == 0 {
		return path, nil
	}
	target, err := filepath.EvalSymlinks(path)
	if err != nil {
		return "", fmt.Errorf("%s is a symlink that does not resolve; fix or remove it before the installer edits it: %w", path, err)
	}
	return target, nil
}

// FileContains reports whether path exists and contains needle.
func FileContains(path, needle string) bool {
	b, err := os.ReadFile(path)
	return err == nil && strings.Contains(string(b), needle)
}

// AppendLineOnce appends a marked block to a file if the marker is absent.
func (e *Env) AppendLineOnce(path, marker, block string) (bool, error) {
	if FileContains(path, marker) {
		return false, nil
	}
	e.Log("append to %s", path)
	if e.DryRun {
		return true, nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return false, err
	}
	if cur, err := os.ReadFile(path); err == nil {
		if err := e.backup(path, append(append([]byte{}, cur...), []byte("\n"+marker+"\n"+block+"\n")...)); err != nil {
			return false, err
		}
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return false, err
	}
	defer f.Close()
	if _, err := fmt.Fprintf(f, "\n%s\n%s\n", marker, block); err != nil {
		return false, err
	}
	return true, nil
}

// IsDir reports whether path is an existing directory.
func IsDir(path string) bool {
	st, err := os.Stat(path)
	return err == nil && st.IsDir()
}

// Exists reports whether path exists.
func Exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

var errNeedsTerminal = errors.New("needs an interactive terminal")
