package setup

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/BurntSushi/toml"
)

// Connecting kinbase to the production Company store, as tooling-devshell's
// wan-kinbase-setup does: the store sits behind the kinbase.wander.com
// gateway and Google sign-in. Read the shared facts token and the Google
// client's secret from Doppler, write the [company] section of the person's
// own kinbase config, sign in, prove it with a fresh signed snapshot, and
// record the proof clock that kindex and `kinbase project` replay.

// The gateway, its Google Desktop client and the store's root public key are
// public; the facts token and the client secret come from Doppler.
const (
	kinbaseURL      = "https://kinbase.wander.com"
	kinbaseClientID = "290487036515-vmlvfn9qujbd719dl53622knkqcqbmji.apps.googleusercontent.com"
	kinbaseRootKey  = "8d669084ee4d4dbdbe2c9f2bb94250382a5cabc33d24b961cef0a6d8f3963dd5"

	dopplerProject = "devshell"
	dopplerConfig  = "agent-tooling"
	factsTokenName = "KINBASE_FACTS_TOKEN_PRODUCTION"
	oidcSecretName = "KINBASE_OIDC_CLIENT_SECRET_PRODUCTION"
)

// kinbaseDir is where kinbase reads its config, beside the secrets and the
// Google session it keeps there.
func kinbaseDir(o Options) string {
	if v := os.Getenv("XDG_CONFIG_HOME"); v != "" {
		return filepath.Join(v, "kinbase")
	}
	return filepath.Join(o.Home, ".config", "kinbase")
}

func personalRoot(o Options) string {
	if v := os.Getenv("XDG_STATE_HOME"); v != "" {
		return filepath.Join(v, "kinbase-personal")
	}
	return filepath.Join(o.Home, ".local", "state", "kinbase-personal")
}

// companySection is the [company] wan-kinbase-setup writes for production.
func companySection(o Options) string {
	dir := kinbaseDir(o)
	return fmt.Sprintf(`[company]
url = %q
allow_non_loopback = true
facts_token_file = %q
root_public_key_file = %q
cache_root = %q
oidc_issuer = "https://accounts.google.com"
oidc_client_id = %q
snapshot_timeout_seconds = 120
oidc_client_secret_file = %q`, kinbaseURL, filepath.Join(dir, "facts.token"), filepath.Join(dir, "root-public.key"),
		filepath.Join(dir, "cache-production"), kinbaseClientID, filepath.Join(dir, "oidc-client.secret"))
}

// kinbaseBin is the kinbase this machine runs: the kindex component's, else
// the devshell's.
func kinbaseBin(o Options) string {
	if kindexReady(o) {
		return o.LocalBin("kinbase")
	}
	return o.DevshellBin("kinbase")
}

// The Doppler steps only the person can do.
var (
	partDopplerLogin = NextStep{Title: "Sign in to Doppler", Text: "It opens a browser.", Cmds: []string{"doppler login"}}
	partDopplerAsk   = NextStep{
		Title: "Get access to Doppler devshell/agent-tooling, if you don't have it yet",
		Text:  "Ask Alex or Shane in Slack. Kinbase's shared token and Google client secret live there.",
	}
	partConnect = NextStep{
		Title: "Connect",
		Text:  "A Google sign-in tab opens: pick your wander.com account. It waits up to 5 minutes, then checks the connection with a fresh signed snapshot. Nothing else is reinstalled.",
		Cmds:  []string{"~/.local/bin/factory-setup --components kindex"},
	}
)

func dopplerInstall() NextStep {
	step := NextStep{Title: "Install the Doppler CLI"}
	if runtime.GOOS == "darwin" {
		step.Cmds = []string{"brew install dopplerhq/cli/doppler"}
		return step
	}
	sudo := "sudo "
	if os.Geteuid() == 0 {
		sudo = ""
	}
	step.Cmds = []string{`curl -Ls --tlsv1.2 --proto "=https" --retry 3 https://cli.doppler.com/install.sh | ` + sudo + "sh"}
	return step
}

// connectNextStep is what to do for a machine that cannot connect yet.
func connectNextStep(why string, parts ...NextStep) NextStep {
	return NextStep{
		Title: "Connect Kinbase",
		Text:  "Kinbase is Wander's company knowledge store; Claude Code and Codex read it through kindex. " + why,
		Parts: append(parts, partConnect),
	}
}

type kinbaseConnectStep struct {
	// next is what Check found left to do when it skipped.
	next NextStep
}

func (*kinbaseConnectStep) ID() string    { return "kinbase.connect" }
func (*kinbaseConnectStep) Title() string { return "Connect to Kinbase (production)" }

// NextStep is what to do about a skipped Check.
func (s *kinbaseConnectStep) NextStep() NextStep { return s.next }

func (s *kinbaseConnectStep) skip(next NextStep) (Status, string, error) {
	s.next = next
	return StatusSkipped, "not connected yet: " + next.Text, nil
}

// dopplerBin is the Doppler CLI on PATH, or the devshell's.
func dopplerBin(env *Env) (string, bool) {
	if p, ok := env.Have("doppler"); ok {
		return p, true
	}
	return env.Have(env.Opts.DevshellBin("doppler"))
}

func dopplerSecret(ctx context.Context, env *Env, doppler, name string) (string, error) {
	return env.Output(ctx, doppler, "secrets", "get", name, "--plain", "--project", dopplerProject, "--config", dopplerConfig)
}

// companyURL is the url of the [company] section in config, "" when the file
// has none, and an error when it is not TOML.
func companyURL(config string) (string, error) {
	b, err := os.ReadFile(config)
	if os.IsNotExist(err) {
		return "", nil
	} else if err != nil {
		return "", err
	}
	var c struct {
		Company *struct {
			URL string `toml:"url"`
		} `toml:"company"`
	}
	if _, err := toml.Decode(string(b), &c); err != nil {
		return "", fmt.Errorf("%s is not valid TOML: %w", config, err)
	}
	if c.Company == nil {
		return "", nil
	}
	return c.Company.URL, nil
}

// connected reports whether this machine already reads production: the
// config points at the gateway, the secrets and the Google session are in
// place, and the proof clock is recorded. A sign-in made with the devshell's
// own wan-kinbase-setup counts.
func connected(ctx context.Context, env *Env) bool {
	dir := kinbaseDir(env.Opts)
	if url, err := companyURL(filepath.Join(dir, "config.toml")); err != nil || url != kinbaseURL {
		return false
	}
	for _, f := range []string{"facts.token", "oidc-client.secret", "oidc.json"} {
		if !nonEmpty(filepath.Join(dir, f)) {
			return false
		}
	}
	return proofClock(ctx, env, "check") == nil
}

func (s *kinbaseConnectStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	if connected(ctx, env) {
		return StatusDone, "connected to " + kinbaseURL, nil
	}
	if !env.Interactive {
		return s.skip(connectNextStep("Signing in opens a browser tab, and this run has no terminal to open it from.",
			NextStep{Title: "Run the connect from a terminal on a machine with a browser"}))
	}
	doppler, ok := dopplerBin(env)
	if !ok {
		return s.skip(connectNextStep("It reads two values from Doppler, and the Doppler CLI is not installed.",
			partDopplerAsk, dopplerInstall(), partDopplerLogin))
	}
	if _, err := env.Output(ctx, doppler, "me"); err != nil {
		return s.skip(connectNextStep("It reads two values from Doppler, and Doppler is not signed in.",
			partDopplerAsk, partDopplerLogin))
	}
	for _, name := range []string{factsTokenName, oidcSecretName} {
		if v, err := dopplerSecret(ctx, env, doppler, name); err != nil || strings.TrimSpace(v) == "" {
			return s.skip(connectNextStep("Doppler is signed in, but cannot read "+name+" from devshell/agent-tooling.",
				NextStep{Title: "Get access to Doppler devshell/agent-tooling", Text: "Ask Alex or Shane in Slack. Kinbase's shared token and Google client secret live there."}))
		}
	}
	return StatusMissing, "", nil
}

func (*kinbaseConnectStep) Apply(ctx context.Context, env *Env) error {
	o := env.Opts
	doppler, ok := dopplerBin(env)
	if !ok {
		return fmt.Errorf("the Doppler CLI is not installed")
	}
	secrets := map[string]string{"root-public.key": kinbaseRootKey}
	for file, name := range map[string]string{"facts.token": factsTokenName, "oidc-client.secret": oidcSecretName} {
		v, err := dopplerSecret(ctx, env, doppler, name)
		if err != nil || strings.TrimSpace(v) == "" {
			return fmt.Errorf("cannot read %s from Doppler %s/%s (doppler login, or ask for access)", name, dopplerProject, dopplerConfig)
		}
		secrets[file] = strings.TrimSpace(v)
	}
	dir := kinbaseDir(o)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	// kinbase refuses a secret another user can read.
	for file, value := range secrets {
		if err := env.WriteFile(filepath.Join(dir, file), []byte(value), 0o600); err != nil {
			return err
		}
	}
	if err := writeCompany(env); err != nil {
		return err
	}

	kinbase := kinbaseBin(o)
	// A Google session from an earlier sign-in is tried before a new one.
	if !nonEmpty(filepath.Join(dir, "oidc.json")) || !freshSnapshot(ctx, env, kinbase) {
		if err := env.Run(ctx, o.Home, kinbase, "auth", "login"); err != nil {
			return fmt.Errorf("Google sign-in did not complete; run it again, it keeps the config and only redoes the sign-in (%w)", err)
		}
		if !freshSnapshot(ctx, env, kinbase) {
			return fmt.Errorf("the store did not return a fresh signed snapshot; run it once more, and if it repeats, send Shane the output")
		}
	}
	return proofClock(ctx, env, "record")
}

// writeCompany puts the production [company] in the person's kinbase config,
// keeping everything else. A [company] pointing anywhere else (an old
// staging tunnel, say) is replaced, the file backed up first to
// config.toml.before-prod; one already pointing at production is kept.
func writeCompany(env *Env) error {
	o := env.Opts
	config := filepath.Join(kinbaseDir(o), "config.toml")
	url, err := companyURL(config)
	if err != nil {
		return err
	}
	if url == kinbaseURL {
		return nil
	}
	cur, err := os.ReadFile(config)
	if os.IsNotExist(err) {
		body := fmt.Sprintf("schema_version = \"1\"\n\n[personal]\ndata_root = %q\n\n%s\n", personalRoot(o), companySection(o))
		return env.WriteFile(config, []byte(body), 0o600)
	} else if err != nil {
		return err
	}
	text := string(cur)
	if url != "" {
		env.Log("backup %s -> %s.before-prod", config, config)
		if err := os.WriteFile(config+".before-prod", cur, 0o600); err != nil {
			return err
		}
		text = withoutTable(text, "company")
	}
	return env.WriteFile(config, []byte(strings.TrimRight(text, "\n")+"\n\n"+companySection(o)+"\n"), 0o600)
}

// withoutTable is TOML text without the [name] table: its header line and
// everything up to the next table header.
func withoutTable(text, name string) string {
	var out []string
	in := false
	for _, line := range strings.SplitAfter(text, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") {
			in = trimmed == "["+name+"]"
		}
		if !in {
			out = append(out, line)
		}
	}
	return strings.Join(out, "")
}

// freshSnapshot asks the store for a snapshot stamped with this moment: a
// cached answer is not proof. status refuses to run outside a repository, so
// it gets a scratch one.
func freshSnapshot(ctx context.Context, env *Env, kinbase string) bool {
	repo, err := os.MkdirTemp("", "kinbase-check-*")
	if err != nil {
		return false
	}
	defer os.RemoveAll(repo)
	if _, err := env.Output(ctx, "git", "-C", repo, "init", "-q"); err != nil {
		return false
	}
	asOf := time.Now().UTC().Format("2006-01-02T15:04:05") + ".000Z"
	var got struct {
		Company struct {
			RefreshedAt string `json:"refreshed_at"`
		} `json:"company"`
	}
	return json.Unmarshal([]byte(statusJSON(ctx, env, repo, kinbase, asOf)), &got) == nil && got.Company.RefreshedAt == asOf
}

// The proof clock kindex and `kinbase project` replay. A new profile has
// none, and `kinbase repo init` (which records one) needs a repository
// certificate, so it is recorded the way wan-kinbase-setup does.
const proofClockScript = `import datetime, sqlite3, sys, tomllib
root = tomllib.load(open(sys.argv[1], "rb"))["personal"]["data_root"]
db = sqlite3.connect(f"{root}/kinbase-personal.sqlite3")
if sys.argv[2] == "check":
    row = db.execute("SELECT value FROM meta WHERE key = 'proof-clock:default'").fetchone()
    sys.exit(0 if row else 1)
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('proof-clock:default', ?)", (now,))
db.commit()
`

func proofClock(ctx context.Context, env *Env, mode string) error {
	py, _ := findPython(ctx, env)
	if py == "" {
		return fmt.Errorf("python 3.12+ not found")
	}
	_, err := env.Output(ctx, py, "-c", proofClockScript, filepath.Join(kinbaseDir(env.Opts), "config.toml"), mode)
	return err
}

// statusJSON is what `kinbase --json status` prints, exit status aside: it
// reports a refusal in the document as well.
func statusJSON(ctx context.Context, env *Env, dir, kinbase, asOf string) string {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()
	var out bytes.Buffer
	c := exec.CommandContext(ctx, kinbase, "--json", "status", "--as-of", asOf)
	c.Dir = dir
	c.Env = env.childEnv()
	c.Stdout = &out
	_ = env.run(ctx, c)
	return strings.TrimSpace(out.String())
}

func nonEmpty(path string) bool {
	st, err := os.Stat(path)
	return err == nil && st.Size() > 0
}
