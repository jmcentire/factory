package setup

import (
	"context"
	"path/filepath"
)

// policyRulesStep initialises signet-eval's system policy (locked
// self-protection plus upstream defaults) and installs Wander's user rules.
// Per-agent hook wiring lives in the agent steps; this owns the policy files.
type policyRulesStep struct{}

func (policyRulesStep) ID() string    { return "policy.rules" }
func (policyRulesStep) Title() string { return "Install the tool-call policy (signet-eval)" }

func signetDir(o Options) string    { return filepath.Join(o.Home, ".signet") }
func signetPolicy(o Options) string { return filepath.Join(signetDir(o), "policy.yaml") }
func signetRules(o Options) string  { return filepath.Join(signetDir(o), "rules.yaml") }
func signetBinary(env *Env) (string, bool) {
	if p := env.Opts.DevshellBin("signet-eval"); Exists(p) {
		return p, true
	}
	return env.Have("signet-eval")
}

const wanderRulesMarker = "# wander-rules-v1"

// wanderRules layer between signet's locked rules and its unlocked defaults.
// They mirror chiron's repo rules generalised for any Wander checkout.
const wanderRules = wanderRulesMarker + `
# Installed by factory-setup. Edit freely; re-running the installer leaves this
# file alone once the marker on line 1 is present.
#
# Evaluation order: locked self-protection -> these rules -> upstream defaults.
# First match wins. Conditions on one rule are AND'd.

- name: ask_push_protected_branch
  tool_pattern: "^Bash$"
  conditions:
    - "matches(command, 'git\\s+push\\b')"
    - "matches(command, '\\b(dev|main|master)\\b')"
  action: ASK
  reason: "Pushing to a protected branch directly. Wander changes land via PR."
  alternative: "Push a feature branch and open a PR with 'gh pr create'."

- name: ask_pr_merge
  tool_pattern: "^Bash$"
  conditions:
    - "matches(command, '\\bgh\\s+pr\\s+merge\\b')"
  action: ASK
  reason: "Merging a PR is a human decision."

- name: deny_history_rewrite
  tool_pattern: "^Bash$"
  conditions:
    - "matches(command, 'git\\s+(reset\\s+--hard|clean\\s+-[a-zA-Z]*f|checkout\\s+--\\s+\\.|restore\\s+\\.|branch\\s+-D)')"
  action: DENY
  reason: "Destructive git operation on the working tree or branches."
  alternative: "Use 'git stash', 'git restore <file>' for a single file, or ask the user."

- name: deny_sudo
  tool_pattern: "^Bash$"
  conditions:
    - "matches(command, '\\bsudo\\b')"
  action: DENY
  reason: "Privilege escalation is reserved for the human."

- name: ask_ci_workflow_edit
  tool_pattern: "^(Write|Edit|NotebookEdit)$"
  conditions:
    - "matches(file_path, '(^|/)\\.github/workflows/')"
  action: ASK
  reason: "CI workflow changes run with repo secrets; confirm before editing."

- name: ask_network_install
  tool_pattern: "^Bash$"
  conditions:
    - "matches(command, '\\b(brew|npm|pnpm|yarn|pip3?|cargo|nix\\s+profile)\\s+(install|add)\\b')"
  action: ASK
  reason: "Installing packages changes the dev machine."
`

func (policyRulesStep) Check(_ context.Context, env *Env) (Status, string, error) {
	if _, ok := signetBinary(env); !ok {
		if env.Opts.Has(ComponentDevshell) {
			// Built earlier in this run; in dry-run it does not exist yet.
			if env.DryRun {
				return StatusMissing, "", nil
			}
		}
		return StatusBlocked, "signet-eval not found; select the devshell component or install it", nil
	}
	if Exists(signetPolicy(env.Opts)) && FileContains(signetRules(env.Opts), wanderRulesMarker) {
		return StatusDone, signetDir(env.Opts), nil
	}
	return StatusMissing, "", nil
}

func (policyRulesStep) Apply(ctx context.Context, env *Env) error {
	bin, _ := signetBinary(env)
	if bin == "" {
		bin = "signet-eval"
	}
	if !Exists(signetPolicy(env.Opts)) {
		if err := env.Run(ctx, env.Opts.Home, bin, "init"); err != nil {
			return err
		}
	}
	if !FileContains(signetRules(env.Opts), wanderRulesMarker) {
		if Exists(signetRules(env.Opts)) {
			// Preserve a hand-written rules file: prepend ours by appending
			// a marker-led block instead of overwriting.
			_, err := env.AppendLineOnce(signetRules(env.Opts), wanderRulesMarker, wanderRules[len(wanderRulesMarker)+1:])
			return err
		}
		return env.WriteFile(signetRules(env.Opts), []byte(wanderRules), 0o644)
	}
	return nil
}
