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

	"github.com/BurntSushi/toml"
)

// flat is a next step's words and commands, for asserting on.
func flat(n NextStep) string {
	out := n.Title + "\n" + n.Text + "\n" + strings.Join(n.Cmds, "\n")
	for _, p := range n.Parts {
		out += "\n" + flat(p)
	}
	return out
}

// kinbaseWorld is the machine around the connect step: Doppler as signed in
// and as permitted as the test says, a kinbase whose `auth login` leaves a
// Google session and whose status answers with a fresh snapshot once signed
// in, and a Python that records the proof clock.
type kinbaseWorld struct {
	t          *testing.T
	env        *Env
	doppler    bool // installed
	signedIn   bool // `doppler me` succeeds
	access     bool // the secrets read
	loginFails bool
	ran        [][]string
	clock      bool
}

func newKinbaseWorld(t *testing.T, w kinbaseWorld) *kinbaseWorld {
	// Linux runners can set XDG roots outside the temporary Options.Home.
	// Each simulated machine needs its own session and proof-clock files.
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(t.TempDir(), "config"))
	t.Setenv("XDG_STATE_HOME", filepath.Join(t.TempDir(), "state"))
	w.t = t
	w.env = testEnv(t, kindexOptions())
	w.env.Interactive = true
	o := w.env.Opts
	bin := t.TempDir()
	tools := []string{"git", "python3.13"}
	if w.doppler {
		tools = append(tools, "doppler")
	}
	for _, name := range tools {
		must(t, os.WriteFile(filepath.Join(bin, name), []byte("#!/bin/sh\n"), 0o755))
	}
	writeTree(t, filepath.Dir(o.LocalBin("kinbase")), map[string]string{"kinbase": "#!/bin/sh\n"})
	must(t, os.Chmod(o.LocalBin("kinbase"), 0o755))
	w.env.searchDirs = []string{}
	w.env.lookPath = func(name string) (string, error) {
		if slices.Contains(tools, name) {
			return filepath.Join(bin, name), nil
		}
		return "", exec.ErrNotFound
	}
	w.env.run = w.run
	return &w
}

func (w *kinbaseWorld) run(_ context.Context, c *exec.Cmd) error {
	w.ran = append(w.ran, c.Args)
	args := c.Args[1:]
	dir := kinbaseDir(w.env.Opts)
	switch filepath.Base(c.Path) {
	case "doppler":
		switch {
		case args[0] == "me" && !w.signedIn, args[0] == "secrets" && !w.access:
			return errors.New("exit status 1")
		case args[0] == "secrets":
			fmt.Fprintf(c.Stdout, "value-of-%s\n", args[2])
		}
	case "kinbase":
		switch {
		case slices.Equal(args, []string{"auth", "login"}):
			if w.loginFails {
				return errors.New("exit status 1")
			}
			must(w.t, os.WriteFile(filepath.Join(dir, "oidc.json"), []byte(`{"session":1}`), 0o600))
		case slices.Contains(args, "status") && nonEmpty(filepath.Join(dir, "oidc.json")):
			fmt.Fprintf(c.Stdout, `{"company": {"refreshed_at": %q}}`, args[len(args)-1])
		}
	case "python3.13":
		if args[0] == "-c" && strings.Contains(args[1], "sqlite3") {
			switch args[len(args)-1] {
			case "check":
				if !w.clock {
					return errors.New("exit status 1")
				}
			case "record":
				w.clock = true
			}
		} else if strings.Contains(strings.Join(args, " "), "version_info") {
			fmt.Fprintln(c.Stdout, "3 13")
		}
	}
	return nil
}

func (w *kinbaseWorld) ranLogin() int {
	n := 0
	for _, a := range w.ran {
		if filepath.Base(a[0]) == "kinbase" && slices.Equal(a[1:], []string{"auth", "login"}) {
			n++
		}
	}
	return n
}

func TestConnectingWaitsForWhatOnlyThePersonCanDo(t *testing.T) {
	rerun := "~/.local/bin/factory-setup --components kindex"
	for _, c := range []struct {
		name  string
		world kinbaseWorld
		tty   bool
		want  []string
		not   []string
	}{
		{"no terminal", kinbaseWorld{doppler: true, signedIn: true, access: true}, false, []string{"browser", rerun}, nil},
		{"no Doppler", kinbaseWorld{}, true, []string{"Install the Doppler CLI", "doppler login", "Alex or Shane in Slack", rerun}, nil},
		{"Doppler signed out", kinbaseWorld{doppler: true}, true, []string{"doppler login", "Alex or Shane in Slack", rerun}, []string{"Install the Doppler CLI"}},
		{"no access", kinbaseWorld{doppler: true, signedIn: true}, true, []string{"Alex or Shane in Slack", "devshell/agent-tooling", rerun}, []string{"doppler login", "Install the Doppler CLI"}},
	} {
		w := newKinbaseWorld(t, c.world)
		w.env.Interactive = c.tty
		s := &kinbaseConnectStep{}
		st, _, _ := s.Check(context.Background(), w.env)
		got := flat(s.NextStep())
		if st != StatusSkipped {
			t.Errorf("%s: expected skipped, got %v", c.name, st)
		}
		for _, want := range c.want {
			if !strings.Contains(got, want) {
				t.Errorf("%s: lacks %q:\n%s", c.name, want, got)
			}
		}
		for _, not := range c.not {
			if strings.Contains(got, not) {
				t.Errorf("%s: should not say %q:\n%s", c.name, not, got)
			}
		}
		if w.ranLogin() != 0 {
			t.Errorf("%s: signed in while checking", c.name)
		}
	}
	w := newKinbaseWorld(t, kinbaseWorld{doppler: true, signedIn: true, access: true})
	if st, detail, _ := (&kinbaseConnectStep{}).Check(context.Background(), w.env); st != StatusMissing {
		t.Fatalf("with everything in place: %v %q", st, detail)
	}
}

func TestConnectingWritesTheProductionProfileSignsInAndProvesIt(t *testing.T) {
	w := newKinbaseWorld(t, kinbaseWorld{doppler: true, signedIn: true, access: true})
	o := w.env.Opts
	must(t, (&kinbaseConnectStep{}).Apply(context.Background(), w.env))
	dir := kinbaseDir(o)
	for file, want := range map[string]string{
		"facts.token":        "value-of-" + factsTokenName,
		"oidc-client.secret": "value-of-" + oidcSecretName,
		"root-public.key":    kinbaseRootKey,
	} {
		b, err := os.ReadFile(filepath.Join(dir, file))
		must(t, err)
		if string(b) != want {
			t.Errorf("%s = %q, want %q", file, b, want)
		}
		// kinbase refuses key material readable by group or other.
		if st, _ := os.Stat(filepath.Join(dir, file)); st.Mode().Perm()&0o077 != 0 {
			t.Errorf("%s is mode %v", file, st.Mode().Perm())
		}
	}
	var cfg struct {
		Personal struct {
			DataRoot string `toml:"data_root"`
		} `toml:"personal"`
		Company map[string]any `toml:"company"`
	}
	_, err := toml.DecodeFile(filepath.Join(dir, "config.toml"), &cfg)
	must(t, err)
	if cfg.Personal.DataRoot != personalRoot(o) || cfg.Company["url"] != kinbaseURL || cfg.Company["oidc_client_id"] != kinbaseClientID ||
		cfg.Company["oidc_client_secret_file"] != filepath.Join(dir, "oidc-client.secret") || cfg.Company["snapshot_timeout_seconds"] != int64(120) {
		t.Fatalf("config.toml: %+v", cfg)
	}
	if w.ranLogin() != 1 || !w.clock {
		t.Fatalf("signed in %d times, proof clock recorded %v", w.ranLogin(), w.clock)
	}
	if st, detail, _ := (&kinbaseConnectStep{}).Check(context.Background(), w.env); st != StatusDone {
		t.Fatalf("expected connected, got %v %q", st, detail)
	}
}

// Step 4 of the instructions: a [company] pointing anywhere else (an old
// staging tunnel) is backed up and replaced; the rest of the file stays.
func TestAForeignCompanyIsBackedUpAndReplaced(t *testing.T) {
	w := newKinbaseWorld(t, kinbaseWorld{doppler: true, signedIn: true, access: true})
	dir := kinbaseDir(w.env.Opts)
	old := "schema_version = \"1\"\n\n[personal]\ndata_root = \"/mine\"\n\n[company]\nurl = \"http://127.0.0.1:8500\"\nfacts_token_file = \"/old/facts.token\"\n\n[hosts]\nclaude_version = \">=0.0.0\"\n"
	writeTree(t, dir, map[string]string{"config.toml": old})
	must(t, (&kinbaseConnectStep{}).Apply(context.Background(), w.env))
	if b, _ := os.ReadFile(filepath.Join(dir, "config.toml.before-prod")); string(b) != old {
		t.Fatalf("the old config was not kept: %q", b)
	}
	b, _ := os.ReadFile(filepath.Join(dir, "config.toml"))
	got := string(b)
	if strings.Count(got, "[company]") != 1 || strings.Contains(got, "127.0.0.1") || !strings.Contains(got, `data_root = "/mine"`) ||
		!strings.Contains(got, "[hosts]") || !strings.Contains(got, kinbaseURL) {
		t.Fatalf("config.toml:\n%s", got)
	}
}

// A Google session from an earlier sign-in (the devshell's wan-kinbase-setup,
// say) that still works is used, not replaced by a new tab.
func TestAnEarlierSignInIsUsed(t *testing.T) {
	w := newKinbaseWorld(t, kinbaseWorld{doppler: true, signedIn: true, access: true})
	writeTree(t, kinbaseDir(w.env.Opts), map[string]string{"oidc.json": `{"session":0}`})
	must(t, (&kinbaseConnectStep{}).Apply(context.Background(), w.env))
	if w.ranLogin() != 0 {
		t.Fatalf("signed in again over a working session")
	}
}

func TestASignInThatDidNotCompleteIsNotConnected(t *testing.T) {
	w := newKinbaseWorld(t, kinbaseWorld{doppler: true, signedIn: true, access: true, loginFails: true})
	err := (&kinbaseConnectStep{}).Apply(context.Background(), w.env)
	if err == nil || !strings.Contains(err.Error(), "Google sign-in did not complete") {
		t.Fatalf("expected the sign-in to fail, got %v", err)
	}
	// Even with the proof clock recorded, no Google session is no connection.
	w.clock = true
	if st, _, _ := (&kinbaseConnectStep{}).Check(context.Background(), w.env); st == StatusDone {
		t.Fatal("a profile without a sign-in reads as connected")
	}
}

func TestWithoutTableKeepsTheRest(t *testing.T) {
	in := "a = 1\n[personal]\nx = 1\n[company]\nurl = \"u\"\n\n[hosts]\ny = 2\n"
	if got := withoutTable(in, "company"); got != "a = 1\n[personal]\nx = 1\n[hosts]\ny = 2\n" {
		t.Fatalf("got %q", got)
	}
}

// The runner hands the summary what to do about a skipped step.
func TestASkippedConnectCarriesItsNextStep(t *testing.T) {
	w := newKinbaseWorld(t, kinbaseWorld{})
	outcomes, err := (&Runner{Env: w.env}).Run(context.Background(), &Plan{Options: w.env.Opts, Steps: []Step{&kinbaseConnectStep{}}})
	if err != nil || len(outcomes) != 1 || outcomes[0].Next == nil || outcomes[0].Next.Title != "Connect Kinbase" {
		t.Fatalf("no next step on the skipped connect: %v %+v", err, outcomes)
	}
}

type fixedStep struct {
	id     string
	status Status
	ran    *[]string
}

func (s fixedStep) ID() string    { return s.id }
func (s fixedStep) Title() string { return s.id }
func (s fixedStep) Check(context.Context, *Env) (Status, string, error) {
	*s.ran = append(*s.ran, s.id)
	return s.status, "", nil
}
func (s fixedStep) Apply(context.Context, *Env) error { return nil }

func TestASkippedStepDoesNotStopTheRun(t *testing.T) {
	env := testEnv(t, kindexOptions())
	var ran []string
	p := &Plan{Options: env.Opts, Steps: []Step{
		fixedStep{"first", StatusSkipped, &ran}, fixedStep{"second", StatusDone, &ran},
	}}
	outcomes, err := (&Runner{Env: env}).Run(context.Background(), p)
	if err != nil || len(outcomes) != 2 || !slices.Equal(ran, []string{"first", "second"}) {
		t.Fatalf("run stopped at the skipped step: err %v, ran %v", err, ran)
	}
}
