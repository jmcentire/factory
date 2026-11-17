package setup

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// The installer encodes assumptions about two repositories it does not own:
// which files exist, which Make targets exist, which binaries the devshell
// flake produces, and which location names the Factory prompts cite. When an
// upstream change breaks one of those assumptions the correct behaviour is to
// stop and say exactly which assumption broke and where in this code it
// lives, rather than half-configure a machine. repoContract is that list.
type repoContract struct {
	name string
	dir  func(Options) string
	// requiredFiles must exist relative to dir.
	requiredFiles []string
	// requiredMakeTargets must be defined in dir/Makefile.
	requiredMakeTargets []string
	// requiredSubstrings maps a relative file to text it must contain; used
	// where the installer depends on an interface (a flag, a path, a key).
	requiredSubstrings map[string][]string
	// warnSubstrings maps a relative file to text the installer expects but
	// can live without; absence is reported as a warning, not a block.
	warnSubstrings map[string][]string
	// where points the reader at the code to update when the contract fails.
	where string
}

func devshellContract() repoContract {
	return repoContract{
		name: "tooling-devshell",
		dir:  Options.DevshellDir,
		requiredFiles: []string{
			"flake.nix", "flake.lock", "nix-bootstrap.sh", ".envrc",
		},
		requiredSubstrings: map[string][]string{
			// The build step targets .#default and agent config points at
			// these package binaries by name.
			"flake.nix": {"default = pkgs.buildEnv", "signet-eval", "kindex", "constrain", "pact-agents", "transmogrifier"},
			// nix-bootstrap.sh installs direnv and nix-direnv and marks the
			// direnvrc loader devshellBootstrapStep checks for.
			"nix-bootstrap.sh": {"nixpkgs#nix-direnv", nixDirenvMarker},
		},
		where: "internal/setup/devshell.go and devshellMCPServers in internal/setup/agents.go",
	}
}

func factoryContract() repoContract {
	files := []string{"Makefile", "pyproject.toml", "prompts/README.md",
		// The /factory procedure runs or names these.
		"harness/factory.sh", "harness/run_record.py", "harness/model_settings.py",
		"harness/endgame.sh", "harness/status.sh",
		"harness/tmux_lane.sh", "harness/tmux_lane_message.sh", "harness/role_inputs.py",
		"docs/SOFTWARE-FACTORY.md"}
	for _, p := range FactoryPrompts {
		if p.File != "" {
			files = append(files, p.File)
		}
	}
	return repoContract{
		name:                "factory",
		dir:                 Options.FactoryDir,
		requiredFiles:       files,
		requiredMakeTargets: []string{"dev", "venv"},
		requiredSubstrings: map[string][]string{
			// factoryVenvStep looks for the venv and stamp the Makefile creates.
			"Makefile": {"VENV ?= .venv", "VENV_STAMP := $(VENV)/.factory-deps"},
			// The /factory skill starts runs with factory.sh --repo and a kickoff
			// Validator; the launcher reads each seat's FACTORY_<ROLE>_* variables.
			"harness/factory.sh":        {"--repo", "run_record.py"},
			"harness/run_record.py":     {`"kickoff"`, "BRIEF.md"},
			"harness/model_settings.py": {`f"FACTORY_{role.upper()}_{field.upper()}"`},
			"harness/endgame.sh":        {"validate_candidate.py"},
			"harness/tmux_lane.sh":      {"cursor-agent", "--model"},
		},
		warnSubstrings: map[string][]string{
			// promptLoader binds this name to the checkout. If upstream
			// renames it the loaders are stale, not broken.
			"prompts/README.md": {"$FACTORY_HOME"},
		},
		where: "FactoryPrompts in internal/setup/factory.go, factory_skill.go and seats.go",
	}
}

var makeTargetRE = regexp.MustCompile(`(?m)^([A-Za-z0-9_.-]+):`)

// verify returns blocking problems and non-blocking warnings.
func (c repoContract) verify(o Options) (problems, warnings []string) {
	dir := c.dir(o)
	for _, f := range c.requiredFiles {
		if !Exists(filepath.Join(dir, f)) {
			problems = append(problems, "missing "+f)
		}
	}
	if len(c.requiredMakeTargets) > 0 {
		b, err := os.ReadFile(filepath.Join(dir, "Makefile"))
		if err == nil {
			have := map[string]bool{}
			for _, m := range makeTargetRE.FindAllStringSubmatch(string(b), -1) {
				have[m[1]] = true
			}
			for _, t := range c.requiredMakeTargets {
				if !have[t] {
					problems = append(problems, "Makefile lacks target "+t)
				}
			}
		}
	}
	for f, needles := range c.requiredSubstrings {
		for _, n := range needles {
			if !FileContains(filepath.Join(dir, f), n) {
				problems = append(problems, fmt.Sprintf("%s no longer contains %q", f, n))
			}
		}
	}
	for f, needles := range c.warnSubstrings {
		for _, n := range needles {
			if Exists(filepath.Join(dir, f)) && !FileContains(filepath.Join(dir, f), n) {
				warnings = append(warnings, fmt.Sprintf("%s no longer cites %q", f, n))
			}
		}
	}
	return problems, warnings
}

// contractStep runs a repoContract after its clone and blocks on failure.
type contractStep struct{ c repoContract }

func (s *contractStep) ID() string { return s.c.name + ".contract" }
func (s *contractStep) Title() string {
	return "Verify " + s.c.name + " still matches what the installer expects"
}

func (s *contractStep) Check(_ context.Context, env *Env) (Status, string, error) {
	if !IsDir(s.c.dir(env.Opts)) {
		if env.DryRun {
			return StatusMissing, "checked after clone", nil
		}
		return StatusBlocked, s.c.dir(env.Opts) + " is not present", nil
	}
	problems, warnings := s.c.verify(env.Opts)
	if len(problems) > 0 {
		msg := fmt.Sprintf("%s changed upstream in a way this installer does not understand:\n", s.c.name)
		for _, p := range problems {
			msg += "      - " + p + "\n"
		}
		msg += "    Update " + s.c.where + " for the new layout, or pin an older revision with --" + s.c.name + "-ref."
		return StatusBlocked, msg, nil
	}
	detail := "ok"
	if len(warnings) > 0 {
		detail = "warnings: " + strings.Join(warnings, "; ")
	}
	return StatusDone, detail, nil
}

func (s *contractStep) Apply(context.Context, *Env) error {
	// Check never returns Missing outside dry-run, so nothing to do.
	return nil
}

// devshellBinariesStep confirms the built environment actually exposes the
// binaries agent configuration will reference. A flake refactor that renames
// or drops one would otherwise leave dangling MCP entries.
type devshellBinariesStep struct{}

func (devshellBinariesStep) ID() string { return "devshell.binaries" }
func (devshellBinariesStep) Title() string {
	return "Verify the built devshell provides the expected binaries"
}

func (devshellBinariesStep) Check(_ context.Context, env *Env) (Status, string, error) {
	o := env.Opts
	if !IsDir(filepath.Join(o.DevshellLink(), "bin")) {
		if env.DryRun {
			return StatusMissing, "checked after build", nil
		}
		return StatusBlocked, o.DevshellLink() + "/bin is not present", nil
	}
	var missing []string
	for _, s := range devshellMCPServers(o) {
		if !Exists(s.Command) {
			missing = append(missing, filepath.Base(s.Command))
		}
	}
	for _, extra := range []string{"kin", "constrain", "pact", "advocate", "transmogrify", "sim", "meditate", "gum", "doppler"} {
		if !Exists(o.DevshellBin(extra)) {
			missing = append(missing, extra)
		}
	}
	if len(missing) > 0 {
		return StatusBlocked, "the devshell build lacks: " + strings.Join(missing, ", ") +
			". The flake's package set changed; update devshellMCPServers in internal/setup/agents.go or pin with --tooling-devshell-ref.", nil
	}
	return StatusDone, "", nil
}

func (devshellBinariesStep) Apply(context.Context, *Env) error { return nil }
