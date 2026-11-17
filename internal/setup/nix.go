package setup

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
)

// osHint returns the platform's way to get the basic build tools.
func osHint(what string) string {
	switch runtime.GOOS {
	case "darwin":
		switch what {
		case "basics":
			return "xcode-select --install"
		case "python":
			return "brew install python@3.13"
		case "make":
			return "xcode-select --install"
		case "kubectl":
			return "brew install kubectl"
		case "gcloud":
			return "brew install --cask google-cloud-sdk"
		case "rust":
			// Homebrew's rustup is keg-only and no longer ships rustup-init:
			// rustup and its cargo proxy land in $(brew --prefix rustup)/bin,
			// and install no Rust until a default toolchain is chosen.
			return "brew install rustup && \"$(brew --prefix rustup)/bin/rustup\" default stable   (or https://rustup.rs)"
		}
	case "linux":
		switch what {
		case "basics":
			return "sudo apt install git curl bash   (or your distro's equivalent)"
		case "python":
			return "sudo apt install python3 python3-venv   (3.12+; or pyenv)"
		case "make":
			return "sudo apt install make"
		case "rust":
			return "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   (https://rustup.rs)"
		case "kubectl":
			return "gcloud components install kubectl   (or https://kubernetes.io/docs/tasks/tools/)"
		case "gcloud":
			return "https://cloud.google.com/sdk/docs/install"
		}
	}
	return "install " + what
}

// nixInstallStep installs Determinate Nix, the flavour tooling-devshell's
// bootstrap script requires. An upstream Nix already present is reported as
// blocked with removal guidance rather than installed over.
type nixInstallStep struct{}

func (nixInstallStep) ID() string    { return "nix.install" }
func (nixInstallStep) Title() string { return "Install Determinate Nix" }

// NixFlavor classifies an installed nix binary.
type NixFlavor int

const (
	NixAbsent NixFlavor = iota
	NixDeterminate
	NixUpstream
)

func detectNix(ctx context.Context, env *Env) (NixFlavor, string) {
	if _, ok := env.Have("nix"); !ok {
		return NixAbsent, ""
	}
	v, err := env.Output(ctx, "nix", "--version")
	if err != nil {
		return NixAbsent, ""
	}
	if strings.Contains(v, "Determinate") {
		return NixDeterminate, v
	}
	return NixUpstream, v
}

func (nixInstallStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	switch flavor, v := detectNix(ctx, env); flavor {
	case NixDeterminate:
		return StatusDone, v, nil
	case NixUpstream:
		uninstall := "https://nix.dev/manual/nix/latest/installation/uninstall#macos"
		if runtime.GOOS == "linux" {
			uninstall = "https://nix.dev/manual/nix/latest/installation/uninstall#linux"
		}
		hint := "upstream Nix is installed (" + v + "); tooling-devshell requires Determinate Nix. " +
			"Uninstall first (" + uninstall + ") " +
			"or run /nix/nix-installer uninstall if it was installed with nix-installer, then re-run."
		return StatusBlocked, hint, nil
	default:
		return StatusMissing, "", nil
	}
}

func (nixInstallStep) Apply(ctx context.Context, env *Env) error {
	// Determinate's documented installer. --determinate selects Determinate
	// Nix rather than upstream; --no-confirm keeps it scriptable. It still
	// prompts for sudo through the attached terminal.
	//
	// FACTORY_SETUP_NIX_INSTALLER_ARGS names a planner and its flags for
	// unusual hosts, e.g. "linux --init none" inside a container without
	// systemd. It must follow `install` directly; the installer re-executes
	// itself through sudo, so NIX_INSTALLER_* environment variables are lost.
	extra := os.Getenv("FACTORY_SETUP_NIX_INSTALLER_ARGS")
	script := `tmp="${TMPDIR:-/tmp}"
curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix -o "$tmp/nix-installer.sh"
sh "$tmp/nix-installer.sh" install ` + extra + ` --determinate --no-confirm`
	if err := env.Shell(ctx, env.Opts.Home, script); err != nil {
		return err
	}
	// The daemon profile is not in this process's PATH yet; later steps use
	// Env.Have's fallback locations.
	return nil
}

// nixFlakesStep makes sure the nix-command and flakes features are on for the
// user. Determinate enables them system-wide; this covers upstream-style
// configs and is harmless when redundant.
type nixFlakesStep struct{}

func (nixFlakesStep) ID() string    { return "nix.flakes" }
func (nixFlakesStep) Title() string { return "Enable nix-command and flakes for this user" }

func (s nixFlakesStep) confPath(env *Env) string {
	return filepath.Join(env.Opts.Home, ".config", "nix", "nix.conf")
}

func (s nixFlakesStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	if flavor, _ := detectNix(ctx, env); flavor == NixDeterminate {
		return StatusDone, "Determinate Nix enables flakes system-wide", nil
	}
	if FileContains(s.confPath(env), "flakes") {
		return StatusDone, s.confPath(env), nil
	}
	return StatusMissing, "", nil
}

func (s nixFlakesStep) Apply(_ context.Context, env *Env) error {
	_, err := env.AppendLineOnce(s.confPath(env), "# factory-setup: flakes", "experimental-features = nix-command flakes")
	return err
}

// cloneStep clones a repository and checks out a pinned revision. An existing
// checkout is accepted only when it tracks the same origin, is clean, and is
// at the pinned revision; anything else blocks with the reason, because a
// checkout the installer did not produce is the user's to reconcile.
type cloneStep struct {
	id, title, repo, ref, dest string
}

func (c *cloneStep) ID() string    { return c.id }
func (c *cloneStep) Title() string { return c.title + " @ " + shortRef(c.ref) }

var shaRE = regexp.MustCompile(`^[0-9a-f]{7,40}$`)

func shortRef(ref string) string {
	if shaRE.MatchString(ref) && len(ref) > 12 {
		return ref[:12]
	}
	return ref
}

func (c *cloneStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	if !IsDir(filepath.Join(c.dest, ".git")) {
		if Exists(c.dest) {
			return StatusBlocked, c.dest + " exists but is not a git checkout", nil
		}
		return StatusMissing, "", nil
	}
	origin, err := env.Output(ctx, "git", "-C", c.dest, "remote", "get-url", "origin")
	if err != nil {
		return StatusBlocked, "cannot read origin of " + c.dest, nil
	}
	if normalizeRepo(origin) != normalizeRepo(c.repo) {
		return StatusBlocked, fmt.Sprintf("%s tracks %s, expected %s", c.dest, origin, c.repo), nil
	}
	if dirty, _ := env.Output(ctx, "git", "-C", c.dest, "status", "--porcelain"); dirty != "" {
		return StatusBlocked, c.dest + " has uncommitted changes; commit or stash them before the installer touches it", nil
	}
	head, err := env.Output(ctx, "git", "-C", c.dest, "rev-parse", "HEAD")
	if err != nil {
		return StatusBlocked, "cannot read HEAD of " + c.dest, nil
	}
	if shaRE.MatchString(c.ref) {
		if strings.HasPrefix(head, c.ref) {
			return StatusDone, shortRef(head), nil
		}
		// The pin is a minimum: a checkout already past it (an engineer on dev,
		// kept current by `wander-tooling sync`) is left where it is, not moved back.
		if _, err := env.Output(ctx, "git", "-C", c.dest, "merge-base", "--is-ancestor", c.ref, "HEAD"); err == nil {
			return StatusDone, shortRef(head) + ", includes the pin " + shortRef(c.ref), nil
		}
		return StatusMissing, fmt.Sprintf("at %s, pinned %s; will fetch and check out the pin", shortRef(head), shortRef(c.ref)), nil
	}
	// A branch or tag was requested explicitly; accept whatever it resolves to.
	return StatusDone, shortRef(head) + " (" + c.ref + ")", nil
}

func (c *cloneStep) Apply(ctx context.Context, env *Env) error {
	if !IsDir(filepath.Join(c.dest, ".git")) {
		if err := os.MkdirAll(filepath.Dir(c.dest), 0o755); err != nil && !env.DryRun {
			return err
		}
		if err := env.Run(ctx, "", "git", "clone", c.repo, c.dest); err != nil {
			return err
		}
	} else if err := env.Run(ctx, c.dest, "git", "fetch", "--quiet", "origin"); err != nil {
		return err
	}
	// Detached checkout of the pin. For a branch name this still lands on the
	// remote-tracking tip rather than a possibly stale local branch.
	target := c.ref
	if !shaRE.MatchString(c.ref) {
		target = "origin/" + c.ref
	}
	if err := env.Run(ctx, c.dest, "git", "checkout", "--quiet", "--detach", target); err != nil {
		return fmt.Errorf("checkout %s: %w (does the revision exist upstream?)", c.ref, err)
	}
	return nil
}

func normalizeRepo(u string) string {
	u = strings.TrimSpace(u)
	u = strings.TrimSuffix(u, ".git")
	u = strings.TrimPrefix(u, "https://")
	u = strings.TrimPrefix(u, "ssh://")
	u = strings.TrimPrefix(u, "git@")
	u = strings.Replace(u, "github.com:", "github.com/", 1)
	return strings.ToLower(u)
}
