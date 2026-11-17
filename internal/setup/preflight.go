package setup

import (
	"context"
	"runtime"
	"slices"
	"strings"
)

// preflightStep checks, before anything is installed, every tool the selected
// components build with, and reports all that are missing at once with the
// command that installs them on this OS. Without it a missing C compiler
// surfaced only when kinbase failed to link, and Python and make one rerun
// each.
type preflightStep struct {
	next NextStep
}

func (*preflightStep) ID() string    { return "preflight" }
func (*preflightStep) Title() string { return "Check the tools this install needs" }

// NextStep is what to install when Check finds something missing.
func (s *preflightStep) NextStep() NextStep { return s.next }

// toolGap is one missing tool and what provides it on each kind of machine.
type toolGap struct {
	name     string   // as the person reads it
	macCmd   string   // the macOS command that installs it
	apt, dnf []string // the Linux packages that provide it
}

func (s *preflightStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		return StatusBlocked, "factory-setup supports macOS and Linux", nil
	}
	gaps := s.gaps(ctx, env, runtime.GOOS)
	if len(gaps) == 0 {
		return StatusDone, runtime.GOOS + "/" + runtime.GOARCH, nil
	}
	s.next = installGaps(env, gaps, runtime.GOOS)
	return StatusBlocked, "missing " + strings.Join(gapNames(gaps), ", ") + " (" + strings.Join(s.next.Cmds, "; ") + ")", nil
}

func (*preflightStep) Apply(context.Context, *Env) error { return nil }

func gapNames(gaps []toolGap) []string {
	var names []string
	for _, g := range gaps {
		names = append(names, g.name)
	}
	return names
}

// installGaps is the next step that installs every gap at once: the macOS
// commands, or one apt or dnf line.
func installGaps(env *Env, gaps []toolGap, goos string) NextStep {
	var cmds, apt, dnf []string
	for _, g := range gaps {
		if g.macCmd != "" && !slices.Contains(cmds, g.macCmd) {
			cmds = append(cmds, g.macCmd)
		}
		apt, dnf = appendNew(apt, g.apt...), appendNew(dnf, g.dnf...)
	}
	next := NextStep{Title: "Install the tools this machine is missing", Text: "Missing: " + listed(gapNames(gaps)) + "."}
	if goos == "darwin" {
		next.Cmds = cmds
	} else if cmd := linuxInstall(env, apt, dnf); cmd != "" {
		next.Cmds = []string{cmd}
	} else {
		next.Text += " Install them with your package manager."
	}
	return next
}

func (s *preflightStep) gaps(ctx context.Context, env *Env, goos string) []toolGap {
	o := env.Opts
	// kinbase links with a C compiler, the Factory's runtime is built by
	// its Makefile (make comes with the Command Line Tools on macOS), and
	// the Factory and kindex each get a virtualenv.
	builds := o.Has(ComponentFactory) || o.Has(ComponentKindex)
	var gaps []toolGap
	for _, tool := range []string{"git", "curl", "bash"} {
		if _, ok := env.Have(tool); !ok {
			gaps = append(gaps, toolGap{name: tool, macCmd: "xcode-select --install", apt: []string{tool}, dnf: []string{tool}})
		}
	}
	if goos == "darwin" {
		// cc and make are stubs until the Command Line Tools are in: running
		// one opens an install dialog instead of building.
		if builds {
			if _, err := env.Output(ctx, "xcode-select", "-p"); err != nil {
				gaps = append(gaps, toolGap{name: "the Xcode Command Line Tools (a C compiler and make)", macCmd: "xcode-select --install"})
			}
		}
	} else if builds {
		if !haveAny(env, "cc", "gcc", "clang") && o.Has(ComponentKindex) {
			gaps = append(gaps, toolGap{name: "a C compiler", apt: []string{"build-essential"}, dnf: []string{"gcc"}})
		}
		if _, ok := env.Have("make"); !ok && o.Has(ComponentFactory) {
			gaps = append(gaps, toolGap{name: "make", apt: []string{"build-essential"}, dnf: []string{"make"}})
		}
	}
	if builds {
		py, tooOld := findPython(ctx, env)
		if py == "" {
			name := "Python 3.12 or newer"
			if len(tooOld) > 0 {
				name += " (found " + strings.Join(tooOld, ", ") + ")"
			}
			gaps = append(gaps, toolGap{name: name, macCmd: "brew install python@3.13", apt: []string{"python3", "python3-venv"}, dnf: []string{"python3"}})
		} else if _, err := env.Output(ctx, py, "-c", "import venv, ensurepip"); err != nil {
			// Debian and Ubuntu split venv's pip bootstrap into its own
			// package, named for the Python version.
			v, _ := env.Output(ctx, py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])")
			gaps = append(gaps, toolGap{name: "the venv module for " + py, apt: []string{"python" + v + "-venv"}, dnf: []string{"python3"}})
		}
	}
	return gaps
}

func haveAny(env *Env, names ...string) bool {
	for _, n := range names {
		if _, ok := env.Have(n); ok {
			return true
		}
	}
	return false
}

func appendNew(list []string, items ...string) []string {
	for _, it := range items {
		if !slices.Contains(list, it) {
			list = append(list, it)
		}
	}
	return list
}

// listed joins names the way a sentence does: "a, b and c".
func listed(names []string) string {
	if len(names) < 2 {
		return strings.Join(names, "")
	}
	return strings.Join(names[:len(names)-1], ", ") + " and " + names[len(names)-1]
}
