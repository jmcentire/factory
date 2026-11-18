package setup

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// devshellBootstrapStep runs tooling-devshell's own nix-bootstrap.sh, which
// marks the user trusted, restarts the daemon, installs direnv and nix-direnv
// system-wide, makes the user's direnvrc source nix-direnv (the devshell's
// .envrc calls its helpers), and removes flox. It needs sudo and Determinate
// Nix.
type devshellBootstrapStep struct{}

func (devshellBootstrapStep) ID() string { return "devshell.bootstrap" }
func (devshellBootstrapStep) Title() string {
	return "Run nix-bootstrap.sh (direnv, nix-direnv, trusted user)"
}

// nixDirenvMarker is the comment nix-bootstrap.sh writes above the loader it
// adds to the direnvrc, and the test it uses to skip that edit on re-run.
const nixDirenvMarker = "tooling-devshell: source nix-direnv"

// nixDirenvRC is the file nix-bootstrap.sh's loader sources.
const nixDirenvRC = "/nix/var/nix/profiles/default/share/nix-direnv/direnvrc"

// direnvRC resolves the rc file direnv reads, the way nix-bootstrap.sh does:
// $DIRENV_CONFIG or ${XDG_CONFIG_HOME:-~/.config}/direnv, then its direnvrc,
// falling back to a legacy ~/.direnvrc when only that exists.
func direnvRC(home string) string {
	dir := os.Getenv("DIRENV_CONFIG")
	if dir == "" {
		base := os.Getenv("XDG_CONFIG_HOME")
		if base == "" {
			base = filepath.Join(home, ".config")
		}
		dir = filepath.Join(base, "direnv")
	}
	rc := filepath.Join(dir, "direnvrc")
	if legacy := filepath.Join(home, ".direnvrc"); !Exists(rc) && Exists(legacy) {
		return legacy
	}
	return rc
}

func (devshellBootstrapStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	if flavor, _ := detectNix(ctx, env); flavor != NixDeterminate {
		return StatusBlocked, "Determinate Nix is required by nix-bootstrap.sh", nil
	}
	_, haveDirenv := env.Have("direnv")
	rc := direnvRC(env.Opts.Home)
	if haveDirenv && Exists(nixDirenvRC) && FileContains(rc, nixDirenvMarker) {
		return StatusDone, "direnv and nix-direnv present; " + rc + " sources nix-direnv", nil
	}
	return StatusMissing, "", nil
}

func (devshellBootstrapStep) Apply(ctx context.Context, env *Env) error {
	return env.Run(ctx, env.Opts.DevshellDir(), "bash", "./nix-bootstrap.sh")
}

// direnvHookStep adds the direnv hook to the user's shell rc so entering a
// Wander repo activates its devshell.
type direnvHookStep struct{}

func (direnvHookStep) ID() string    { return "devshell.direnv-hook" }
func (direnvHookStep) Title() string { return "Add the direnv hook to your shell" }

// shellRC picks the rc file and hook line for the login shell.
func shellRC(home string) (path, line string) {
	sh := filepath.Base(os.Getenv("SHELL"))
	switch sh {
	case "bash":
		return filepath.Join(home, ".bashrc"), `eval "$(direnv hook bash)"`
	case "fish":
		return filepath.Join(home, ".config", "fish", "config.fish"), `direnv hook fish | source`
	default:
		return filepath.Join(home, ".zshrc"), `eval "$(direnv hook zsh)"`
	}
}

const direnvMarker = "# factory-setup: direnv"

func (direnvHookStep) Check(_ context.Context, env *Env) (Status, string, error) {
	rc, line := shellRC(env.Opts.Home)
	if FileContains(rc, "direnv hook") || FileContains(rc, line) {
		return StatusDone, rc, nil
	}
	return StatusMissing, "", nil
}

func (direnvHookStep) Apply(_ context.Context, env *Env) error {
	rc, line := shellRC(env.Opts.Home)
	_, err := env.AppendLineOnce(rc, direnvMarker, line)
	return err
}

// devshellBuildStep builds the flake's default environment and keeps a stable
// out-link that agent configuration points at. It also allows direnv in the
// devshell checkout so the environment activates there.
type devshellBuildStep struct{}

func (devshellBuildStep) ID() string    { return "devshell.build" }
func (devshellBuildStep) Title() string { return "Build the tooling-devshell environment" }

func (devshellBuildStep) Check(_ context.Context, env *Env) (Status, string, error) {
	if _, ok := env.Have("nix"); !ok {
		return StatusBlocked, "nix not found", nil
	}
	link := env.Opts.DevshellLink()
	if target, err := os.Readlink(link); err == nil && IsDir(filepath.Join(target, "bin")) {
		// Rebuild when the checkout moved past the built revision.
		return StatusMissing, "rebuild against current checkout", nil
	}
	return StatusMissing, "", nil
}

func (devshellBuildStep) Apply(ctx context.Context, env *Env) error {
	o := env.Opts
	if err := os.MkdirAll(o.StateDir(), 0o755); err != nil && !env.DryRun {
		return err
	}
	// Private inputs (the flake reads github: sources) authenticate through
	// the gh token when one is available; public sources need nothing.
	args := []string{"build", ".#default", "--out-link", o.DevshellLink()}
	if tok, err := env.Output(ctx, "gh", "auth", "token"); err == nil && tok != "" {
		args = append(args, "--option", "access-tokens", "github.com="+tok)
	}
	if err := env.Run(ctx, o.DevshellDir(), "nix", args...); err != nil {
		return err
	}
	if _, ok := env.Have("direnv"); ok {
		if err := env.Run(ctx, o.DevshellDir(), "direnv", "allow", "."); err != nil {
			return fmt.Errorf("direnv allow: %w", err)
		}
	}
	return nil
}

// devshellProfileStep installs the built environment into the user's nix
// profile so the tools are on PATH everywhere, not only under direnv.
type devshellProfileStep struct{}

func (devshellProfileStep) ID() string { return "devshell.profile" }
func (devshellProfileStep) Title() string {
	return "Add the devshell to your nix profile (global PATH)"
}

func (devshellProfileStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	out, err := env.Output(ctx, "nix", "profile", "list")
	if err != nil {
		return StatusMissing, "", nil
	}
	if strings.Contains(out, "tooling-devshell-env") {
		return StatusDone, "", nil
	}
	return StatusMissing, "", nil
}

func (devshellProfileStep) Apply(ctx context.Context, env *Env) error {
	return env.Run(ctx, env.Opts.DevshellDir(), "nix", "profile", "install", ".#default")
}
