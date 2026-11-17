package setup

import (
	"context"
	"fmt"
	"path/filepath"
)

// rustInstallStep makes sure a cargo that runs, new enough for everything the
// plan builds with it, is there before the step that needs one: kinbase at
// Rust 1.98+.
type rustInstallStep struct {
	// minor is the Rust 1.minor the plan needs, 0 for any.
	minor int
}

func (*rustInstallStep) ID() string    { return "rust.install" }
func (*rustInstallStep) Title() string { return "Install Rust (rustup)" }

// rustState is what this machine's Rust needs: nothing, a toolchain for a
// rustup that has none, a newer toolchain from its rustup, rustup itself, or
// something only the person can do.
type rustState int

const (
	rustReady rustState = iota
	rustNoToolchain
	rustOld
	rustMissing
	rustOldUnmanaged
)

// rustup is the rustup that manages cargo, if one does: it sits beside the
// cargo proxy it installed.
func rustupBeside(env *Env, cargo string) (string, bool) {
	return env.Have(filepath.Join(filepath.Dir(cargo), "rustup"))
}

func (s *rustInstallStep) state(ctx context.Context, env *Env) (rustState, string, string) {
	cargo, ok := env.Have("cargo")
	if !ok {
		return rustMissing, "", ""
	}
	rustup, managed := rustupBeside(env, cargo)
	if _, reason := cargoFor(ctx, env, s.minor); reason == "" {
		return rustReady, cargo, rustup
	} else if _, err := env.Output(ctx, cargo, "--version"); err != nil {
		if managed {
			return rustNoToolchain, cargo, rustup
		}
		return rustOldUnmanaged, cargo, reason
	} else if managed {
		return rustOld, cargo, rustup
	} else {
		return rustOldUnmanaged, cargo, reason
	}
}

func (s *rustInstallStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	st, cargo, extra := s.state(ctx, env)
	switch st {
	case rustReady:
		v, _ := env.Output(ctx, cargo, "--version")
		return StatusDone, v, nil
	case rustNoToolchain:
		return StatusMissing, "rustup has no toolchain yet; choosing stable", nil
	case rustOld:
		return StatusMissing, fmt.Sprintf("Rust 1.%d+ is needed; updating stable", s.minor), nil
	case rustMissing:
		return StatusMissing, "rustup.rs installs Rust into ~/.cargo", nil
	}
	return StatusBlocked, extra, nil
}

func (s *rustInstallStep) Apply(ctx context.Context, env *Env) error {
	st, _, extra := s.state(ctx, env)
	switch st {
	case rustReady:
		return nil
	case rustNoToolchain:
		if err := env.Run(ctx, env.Opts.Home, extra, "default", "stable"); err != nil {
			return err
		}
	case rustOld:
		if err := env.Run(ctx, env.Opts.Home, extra, "update", "stable"); err != nil {
			return err
		}
		if err := env.Run(ctx, env.Opts.Home, extra, "default", "stable"); err != nil {
			return err
		}
	case rustMissing:
		// The installer rust-lang.org documents, with the stable toolchain and
		// only what building needs. It leaves the person's shell files alone:
		// the steps that build find ~/.cargo/bin themselves.
		script := `tmp="${TMPDIR:-/tmp}"
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o "$tmp/rustup-init.sh"
sh "$tmp/rustup-init.sh" -y --no-modify-path --profile minimal --default-toolchain stable`
		if err := env.Shell(ctx, env.Opts.Home, script); err != nil {
			return err
		}
	default:
		return fmt.Errorf("%s", extra)
	}
	if _, reason := cargoFor(ctx, env, s.minor); reason != "" {
		return fmt.Errorf("Rust still is not ready after installing it: %s", reason)
	}
	return nil
}
