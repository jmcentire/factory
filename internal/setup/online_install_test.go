package setup

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// This opt-in test uses real GitHub transport, Python package downloads and
// installer steps. Ordinary unit tests remain independent of network access.
func TestOnlineFactoryInstallation(t *testing.T) {
	if os.Getenv("FACTORY_ONLINE_INSTALL_TEST") != "1" {
		t.Skip("requires an online runner with GitHub authentication and Python 3.14")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	o := DefaultOptions()
	root := t.TempDir()
	o.Home = filepath.Join(root, "profile")
	o.CodeDir = filepath.Join(root, "Code")
	o.Components = []Component{ComponentFactory}
	must(t, os.MkdirAll(o.Home, 0o755))
	plan, err := BuildPlan(o)
	must(t, err)
	env := NewEnv(o)
	env.Log = t.Logf
	runner := Runner{Env: env}
	first, err := runner.Run(ctx, plan)
	must(t, err)
	for _, result := range first {
		if result.Status != StatusDone {
			t.Fatalf("incomplete step: %s (%s)", result.Step.ID(), result.Detail)
		}
	}
	python := filepath.Join(o.FactoryDir(), ".venv", "bin", "python")
	version, err := env.Output(ctx, python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
	must(t, err)
	if version != "3.14" {
		t.Fatalf("expected fresh Python 3.14 install, got %s", version)
	}
	bindir := filepath.Join(root, "bin")
	commands := filepath.Join(root, "commands")
	must(t, env.Run(ctx, o.FactoryDir(), python, "scripts/install.py", "install", "--bindir", bindir, "--commands-dir", commands))
	help, err := env.Output(ctx, filepath.Join(bindir, "factory"), "run", "--help")
	must(t, err)
	if !strings.Contains(help, "usage:") {
		t.Fatalf("installed command did not start: %s", help)
	}
	second, err := runner.Run(ctx, plan)
	must(t, err)
	for _, result := range second {
		if result.Status != StatusDone || result.Applied {
			t.Fatalf("second install changed step: %s", result.Step.ID())
		}
	}
}
