package setup

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"

	"github.com/BurntSushi/toml"
)

// AgentID names a supported coding agent.
type AgentID string

const (
	AgentClaude      AgentID = "claude-code"
	AgentCodex       AgentID = "codex"
	AgentOpenCode    AgentID = "opencode"
	AgentPrime       AgentID = "prime-agent"
	AgentPi          AgentID = "pi"
	AgentAntigravity AgentID = "antigravity"
)

// Agent describes how one coding agent is detected, installed, and wired to
// the devshell MCP servers, the Factory prompts, and the policy hook.
//
// Paths follow each agent's documentation and source as of October 2026:
//   - Claude Code: ~/.claude/commands/*.md, ~/.claude.json mcpServers, ~/.claude/settings.json hooks
//     (under $CLAUDE_CONFIG_DIR when set)
//   - Codex:       ~/.codex/skills/<name>/SKILL.md, ~/.codex/config.toml [mcp_servers.*], ~/.codex/hooks.json
//     (under $CODEX_HOME when set)
//   - OpenCode:    ~/.config/opencode/commands/*.md, opencode.json "mcp", plugins/*.js
//   - prime-agent: ~/.prime/agent/prompts/*.md, settings.json mcpServers, extensions/*.ts
//   - pi:          ~/.pi/agent/prompts/*.md, mcp.json mcpServers, extensions/*.ts
//   - Antigravity: ~/.gemini/antigravity-cli/skills/<name>/SKILL.md, ~/.gemini/config/mcp_config.json and hooks.json
//
// A re-run removes the Factory workflow section an earlier release wrote into
// each agent's global memory file (memoryPath): CLAUDE.md, Codex's AGENTS.md,
// OpenCode's, pi's and prime-agent's AGENTS.md, and ~/.gemini/GEMINI.md.
type Agent struct {
	ID      AgentID
	Name    string
	Binary  string
	Install string // shell one-liner shown to the user and run on request
	// configure writes every config file. It must be idempotent.
	configure func(ctx context.Context, env *Env, a *Agent) error
	// configured reports whether the essential files are already present.
	configured func(env *Env, a *Agent) bool
}

var agents = []*Agent{
	{ID: AgentClaude, Name: "Claude Code", Binary: "claude",
		Install:   `curl -fsSL https://claude.ai/install.sh | bash`,
		configure: configureClaude, configured: configuredClaude},
	{ID: AgentCodex, Name: "OpenAI Codex CLI", Binary: "codex",
		Install:   `npm install -g @openai/codex`,
		configure: configureCodex, configured: configuredCodex},
	{ID: AgentOpenCode, Name: "OpenCode", Binary: "opencode",
		// The upstream installer works on macOS and Linux; Homebrew's tap is
		// macOS-only, so the installer picks the portable form.
		Install:   `curl -fsSL https://opencode.ai/install | bash`,
		configure: configureOpenCode, configured: configuredOpenCode},
	{ID: AgentPrime, Name: "prime-agent (Prime Intellect)", Binary: "prime-agent",
		Install:   `curl --proto '=https' --proto-redir '=https' -fsSL https://app.primeintellect.ai/prime-agent/install.sh | sh`,
		configure: configurePrime, configured: configuredPrime},
	{ID: AgentPi, Name: "pi coding agent", Binary: "pi",
		Install:   `npm install -g --ignore-scripts @earendil-works/pi-coding-agent`,
		configure: configurePi, configured: configuredPi},
	{ID: AgentAntigravity, Name: "Google Antigravity CLI (agy)", Binary: "agy",
		Install:   `curl -fsSL https://antigravity.google/cli/install.sh | bash`,
		configure: configureAntigravity, configured: configuredAntigravity},
}

// Agents returns the supported agents in menu order.
func Agents() []*Agent { return agents }

func agentByID(id AgentID) (*Agent, bool) {
	for _, a := range agents {
		if a.ID == id {
			return a, true
		}
	}
	return nil, false
}

// DetectAgents returns the IDs of agents already installed, for preselection.
func DetectAgents(env *Env) []AgentID {
	var out []AgentID
	for _, a := range agents {
		if _, ok := env.Have(a.Binary); ok {
			out = append(out, a.ID)
		}
	}
	return out
}

// agentInstallStep installs the agent binary when absent, and updates one that
// falls short of the pinned Factory's lane contract.
type agentInstallStep struct {
	agent *Agent
	// stale is why a copy no known tool can update falls short, set by Check.
	stale string
}

func (s *agentInstallStep) ID() string    { return "agent." + string(s.agent.ID) + ".install" }
func (s *agentInstallStep) Title() string { return "Install " + s.agent.Name }

func (s *agentInstallStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	if p, ok := env.Have(s.agent.Binary); ok {
		if why := agentShortfall(ctx, env, s.agent); why != "" {
			// A copy setup cannot update (a standalone installer, a bun, pnpm or
			// volta shim) is left to the person; the rest of the install goes on.
			if _, err := env.Output(ctx, "bash", "-c", agentUpdatable(s.agent.Binary)); err != nil {
				s.stale = why
				return StatusSkipped, why + "; setup cannot update it", nil
			}
			return StatusMissing, why, nil
		}
		return StatusDone, p, nil
	}
	// npm comes with Node.js, which a fresh machine lacks; the rest of the
	// install goes on without this agent.
	if s.needsNode(env) {
		return StatusSkipped, "needs npm, from Node.js", nil
	}
	return StatusMissing, s.agent.Install, nil
}

func (s *agentInstallStep) needsNode(env *Env) bool {
	_, ok := env.Have("npm")
	return strings.HasPrefix(s.agent.Install, "npm ") && !ok
}

func (s *agentInstallStep) NextStep() NextStep {
	if s.stale != "" {
		return NextStep{
			Title: "Update " + s.agent.Name,
			Text:  s.stale + ". Update it with the tool that installed it, or put a current " + s.agent.Binary + " first on PATH.",
			Cmds:  []string{s.agent.Install},
		}
	}
	return NextStep{
		Title: "Install " + s.agent.Name,
		Text:  "It installs with npm, which comes with Node.js: install Node.js first (https://nodejs.org).",
		Cmds:  []string{s.agent.Install},
	}
}

func (s *agentInstallStep) Apply(ctx context.Context, env *Env) error {
	if _, ok := env.Have(s.agent.Binary); ok && agentShortfall(ctx, env, s.agent) != "" {
		if err := env.Shell(ctx, env.Opts.Home, agentUpdate(s.agent.Binary)); err != nil {
			return err
		}
		if why := agentShortfall(ctx, env, s.agent); why != "" {
			return fmt.Errorf("%s still falls short of the Factory after updating (%s); update it by hand", s.agent.Binary, why)
		}
		return nil
	}
	return env.Shell(ctx, env.Opts.Home, s.agent.Install)
}

// agentConfigureStep writes MCP servers, Factory slash commands, and the
// policy hook for one agent.
type agentConfigureStep struct{ agent *Agent }

func (s *agentConfigureStep) ID() string    { return "agent." + string(s.agent.ID) + ".configure" }
func (s *agentConfigureStep) Title() string { return "Configure " + s.agent.Name }

func (s *agentConfigureStep) Check(_ context.Context, env *Env) (Status, string, error) {
	if s.agent.configured(env, s.agent) {
		return StatusDone, "", nil
	}
	return StatusMissing, "", nil
}

func (s *agentConfigureStep) Apply(ctx context.Context, env *Env) error {
	return s.agent.configure(ctx, env, s.agent)
}

// ---- shared pieces -------------------------------------------------------

// MCPServer is a stdio MCP server from the devshell.
type MCPServer struct {
	Name    string
	Command string
	Args    []string
}

// devshellMCPServers lists the MCP servers the devshell provides. Commands are
// absolute paths under the stable devshell link so they survive rebuilds.
func devshellMCPServers(o Options) []MCPServer {
	return []MCPServer{
		{"kindex", o.DevshellBin("kin-mcp"), nil},
		{"constrain", o.DevshellBin("constrain-mcp"), nil},
		{"pact", o.DevshellBin("pact-mcp"), nil},
		{"transmogrifier", o.DevshellBin("transmog-mcp"), nil},
		{"signet", o.DevshellBin("signet-eval"), []string{"serve"}},
	}
}

// mcpServers are the MCP servers to register with an agent: the devshell's
// when it is installed, otherwise kindex alone when the kindex component put
// it in ~/.local/bin, otherwise none.
func mcpServers(o Options) []MCPServer {
	var servers []MCPServer
	if devshellReady(o) {
		servers = devshellMCPServers(o)
	}
	if kindexReady(o) {
		// The kindex component's kindex is at the devshell's newest pin, the
		// one with the Kinbase tools, so it takes the kindex server even
		// beside a devshell whose own kindex is older.
		lean := MCPServer{"kindex", o.LocalBin("kin-mcp"), nil}
		if i := slices.IndexFunc(servers, func(s MCPServer) bool { return s.Name == "kindex" }); i >= 0 {
			servers[i] = lean
		} else {
			servers = append(servers, lean)
		}
	}
	return servers
}

// kindexCommand is the command an agent's kindex MCP server should run, or
// "" when no kindex is installed.
func kindexCommand(o Options) string {
	for _, s := range mcpServers(o) {
		if s.Name == "kindex" {
			return s.Command
		}
	}
	return ""
}

// kindexWired reports whether an agent's MCP servers run this kindex: an
// entry for an older kindex, from an earlier install or another one, is not.
func kindexWired(servers map[string]any, o Options) bool {
	entry := subMap(servers, "kindex")
	switch cmd := entry["command"].(type) {
	case string:
		return cmd == kindexCommand(o)
	case []any: // OpenCode's: the command and its arguments in one list
		return len(cmd) > 0 && cmd[0] == kindexCommand(o)
	}
	return false
}

// policyHookCommand is the PreToolUse command for agents that speak the
// Claude-style hook protocol (Claude Code, Codex, Antigravity).
func policyHookCommand(o Options, adapter string) string {
	cmd := o.DevshellBin("signet-eval")
	if adapter != "" {
		cmd += " --adapter " + adapter
	}
	return cmd
}

// readJSON loads a JSON object file, returning an empty map when absent.
func readJSON(path string) (map[string]any, error) {
	m := map[string]any{}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return m, nil
		}
		return nil, err
	}
	if len(strings.TrimSpace(string(b))) == 0 {
		return m, nil
	}
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return m, nil
}

// mergeJSON edits a JSON object file in place through fn, preserving keys the
// installer does not own.
func (e *Env) mergeJSON(path string, fn func(m map[string]any)) error {
	m, err := readJSON(path)
	if err != nil {
		return err
	}
	fn(m)
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	return e.WriteConfigFile(path, append(b, '\n'), 0o600)
}

func subMap(m map[string]any, key string) map[string]any {
	if v, ok := m[key].(map[string]any); ok {
		return v
	}
	v := map[string]any{}
	m[key] = v
	return v
}

// addHook appends a Claude-style hook entry to hooks.<event> unless a hook
// with the same command is already present.
func addHook(m map[string]any, event, matcher, command string, timeout int) {
	hooks := subMap(m, "hooks")
	list, _ := hooks[event].([]any)
	for _, item := range list {
		if entry, ok := item.(map[string]any); ok {
			if inner, ok := entry["hooks"].([]any); ok {
				for _, h := range inner {
					if hm, ok := h.(map[string]any); ok && hm["command"] == command {
						return
					}
				}
			}
		}
	}
	hook := map[string]any{"type": "command", "command": command}
	if timeout > 0 {
		hook["timeout"] = timeout
	}
	hooks[event] = append(list, map[string]any{
		"matcher": matcher,
		"hooks":   []any{hook},
	})
}

func hasHook(m map[string]any, event, command string) bool {
	hooks, _ := m["hooks"].(map[string]any)
	list, _ := hooks[event].([]any)
	for _, item := range list {
		entry, _ := item.(map[string]any)
		inner, _ := entry["hooks"].([]any)
		for _, h := range inner {
			if hm, ok := h.(map[string]any); ok && hm["command"] == command {
				return true
			}
		}
	}
	return false
}

// yamlString renders s as a double-quoted YAML scalar. Descriptions contain
// ": ", which a plain scalar cannot hold, so a strict YAML parser rejects the
// whole frontmatter. A JSON string is a valid YAML one.
func yamlString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

// seatHost is the seat name a /factory loader gives its agent: the agent's own
// binary when that can hold the Validator seat, else "".
func seatHost(a *Agent) string {
	if a != nil && slices.Contains(validatorAgents, a.Binary) {
		return a.Binary
	}
	return ""
}

// loaderNamesHost reports whether the agent's /factory loader states its seat,
// so a loader written before the line existed is rewritten on upgrade.
func loaderNamesHost(o Options, a *Agent) bool {
	host := seatHost(a)
	return host == "" || FileContains(factoryLoaderPath(o, a.ID), "You run this as the Validator seat: `"+host+"`.")
}

// writePromptFiles writes one markdown loader per Factory prompt into dir,
// with a YAML frontmatter description. argToken is the agent's argument
// placeholder.
func writePromptFiles(env *Env, dir, argToken, host string) error {
	for _, p := range FactoryPrompts {
		body := "---\ndescription: " + yamlString(p.Description) + "\n---\n\n" +
			promptLoader(p, env.Opts, argToken, host)
		if err := env.WriteFile(filepath.Join(dir, p.Name+".md"), []byte(body), 0o644); err != nil {
			return err
		}
	}
	return nil
}

func promptsPresent(dir string) bool {
	for _, p := range FactoryPrompts {
		if !Exists(filepath.Join(dir, p.Name+".md")) {
			return false
		}
	}
	return true
}

// writeSkillDirs writes one <dir>/<name>/SKILL.md loader per Factory prompt,
// for agents whose user-invoked commands are skills. Skills take no argument
// placeholder, so the loader refers to the user's request.
func writeSkillDirs(env *Env, dir, host string) error {
	for _, p := range FactoryPrompts {
		body := "---\nname: " + p.Name + "\ndescription: " + yamlString(p.Description) + "\n---\n\n" +
			promptLoader(p, env.Opts, "the user's request", host)
		if err := env.WriteFile(filepath.Join(dir, p.Name, "SKILL.md"), []byte(body), 0o644); err != nil {
			return err
		}
	}
	return nil
}

func skillDirsPresent(dir string) bool {
	for _, p := range FactoryPrompts {
		if !Exists(filepath.Join(dir, p.Name, "SKILL.md")) {
			return false
		}
	}
	return true
}

// ---- Claude Code ----------------------------------------------------------

// claudePaths honours CLAUDE_CONFIG_DIR: when set, Claude Code keeps
// commands, settings.json and its .claude.json inside that directory instead
// of ~/.claude and ~/.claude.json.
func claudePaths(o Options) (commands, settings, mcp string) {
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		dir = absDir(dir)
		return filepath.Join(dir, "commands"),
			filepath.Join(dir, "settings.json"),
			filepath.Join(dir, ".claude.json")
	}
	return filepath.Join(o.Home, ".claude", "commands"),
		filepath.Join(o.Home, ".claude", "settings.json"),
		filepath.Join(o.Home, ".claude.json")
}

func configureClaude(_ context.Context, env *Env, a *Agent) error {
	o := env.Opts
	commands, settings, mcp := claudePaths(o)
	if factoryReady(o) {
		if err := writePromptFiles(env, commands, "$ARGUMENTS", seatHost(a)); err != nil {
			return err
		}
		if err := env.mergeAllowRules(settings, claudeAllowRules(o)); err != nil {
			return err
		}
	}
	if len(mcpServers(o)) > 0 {
		if err := env.mergeJSON(mcp, func(m map[string]any) {
			servers := subMap(m, "mcpServers")
			for _, s := range mcpServers(o) {
				entry := map[string]any{"type": "stdio", "command": s.Command}
				if len(s.Args) > 0 {
					entry["args"] = s.Args
				}
				servers[s.Name] = entry
			}
		}); err != nil {
			return err
		}
	}
	if policyReady(o) {
		return env.mergeJSON(settings, func(m map[string]any) {
			addHook(m, "PreToolUse", "", policyHookCommand(o, ""), 10)
		})
	}
	return nil
}

func configuredClaude(env *Env, a *Agent) bool {
	o := env.Opts
	commands, settings, mcp := claudePaths(o)
	if factoryReady(o) && (!promptsPresent(commands) || !loaderNamesHost(o, a) || !hasAllowRules(settings, claudeAllowRules(o))) {
		return false
	}
	if len(mcpServers(o)) > 0 {
		m, err := readJSON(mcp)
		if err != nil || !kindexWired(subMap(m, "mcpServers"), o) {
			return false
		}
	}
	if policyReady(o) {
		m, err := readJSON(settings)
		if err != nil || !hasHook(m, "PreToolUse", policyHookCommand(o, "")) {
			return false
		}
	}
	return true
}

// ---- Codex ---------------------------------------------------------------

// Codex removed ~/.codex/prompts in March 2026 (openai/codex#16115); its
// user-invoked commands are skills, mentioned as $name. Its source calls
// ~/.codex/skills deprecated in favour of ~/.agents/skills but still loads
// it; the installer uses it because OpenCode and pi also read
// ~/.agents/skills, and OpenCode could load the Factory lanes unasked.
func codexPaths(o Options) (skills, config, hooks string) {
	home := codexHome(o)
	return filepath.Join(home, "skills"),
		filepath.Join(home, "config.toml"),
		filepath.Join(home, "hooks.json")
}

// codexHome honours CODEX_HOME, as claudePaths honours CLAUDE_CONFIG_DIR: a
// Codex profile keeps its config, hooks, skills and AGENTS.md there.
func codexHome(o Options) string {
	if dir := os.Getenv("CODEX_HOME"); dir != "" {
		return absDir(dir)
	}
	return filepath.Join(o.Home, ".codex")
}

// absDir makes a profile directory from the environment absolute, so a relative
// CODEX_HOME or CLAUDE_CONFIG_DIR names one place rather than one per working
// directory.
func absDir(dir string) string {
	if abs, err := filepath.Abs(dir); err == nil {
		return abs
	}
	return dir
}

// codexMCPTableBody renders the key lines for one [mcp_servers.<name>] table.
func codexMCPTableBody(s MCPServer) []string {
	lines := []string{fmt.Sprintf("command = %q", s.Command)}
	if len(s.Args) > 0 {
		args := make([]string, len(s.Args))
		for i, a := range s.Args {
			args[i] = fmt.Sprintf("%q", a)
		}
		lines = append(lines, fmt.Sprintf("args = [%s]", strings.Join(args, ", ")))
	}
	return lines
}

// codexExplicitOnly keeps a Codex skill out of implicit invocation, so the
// model never starts a lane on its own. /factory stays implicit: "use the
// factory to complete this" is meant to reach it.
const codexExplicitOnly = "policy:\n  allow_implicit_invocation: false\n"

// configureCodex edits Codex's config.toml ($CODEX_HOME, default ~/.codex) table by table. A user's existing
// [features] or [mcp_servers.*] tables are merged into, never duplicated: a
// second [features] header is a TOML duplicate-key error and Codex refuses to
// start. The file is backed up before any change and the result is parsed
// before it is written.
func configureCodex(_ context.Context, env *Env, a *Agent) error {
	o := env.Opts
	skills, config, hooks := codexPaths(o)
	if factoryReady(o) {
		if err := writeSkillDirs(env, skills, seatHost(a)); err != nil {
			return err
		}
		for _, p := range FactoryPrompts {
			if p.Name == "factory" {
				continue
			}
			if err := env.WriteFile(filepath.Join(skills, p.Name, "agents", "openai.yaml"), []byte(codexExplicitOnly), 0o644); err != nil {
				return err
			}
		}
	}
	if len(mcpServers(o)) > 0 || policyReady(o) {
		if err := env.editTOML(config, func(d *tomlDoc) bool {
			changed := false
			if len(mcpServers(o)) > 0 {
				for _, s := range mcpServers(o) {
					// A user-defined server of the same name wins; only add
					// what is absent. kindex's is the installer's: it follows
					// the kindex installed, so an older one is not left wired.
					if s.Name == "kindex" && d.replaceTable("mcp_servers."+s.Name, codexMCPTableBody(s)) {
						changed = true
					} else if d.ensureTable("mcp_servers."+s.Name, codexMCPTableBody(s)) {
						changed = true
					}
				}
			}
			if policyReady(o) {
				// Hooks are feature-gated; the current key is hooks (the
				// deprecated alias was codex_hooks).
				if d.setKey("features", "hooks", "true") {
					changed = true
				}
			}
			return changed
		}); err != nil {
			return err
		}
	}
	if policyReady(o) {
		return env.mergeJSON(hooks, func(m map[string]any) {
			addHook(m, "PreToolUse", "*", policyHookCommand(o, "codex"), 30)
			addHook(m, "PermissionRequest", "*", policyHookCommand(o, "codex-permission"), 30)
		})
	}
	return nil
}

// codexConfigHas reports whether config.toml parses and contains the table
// (and, when key is non-empty, that key inside it, set to want when given).
func codexConfigHas(config, table, key string, want ...string) bool {
	src, err := os.ReadFile(config)
	if err != nil {
		return false
	}
	var m map[string]any
	if _, err := toml.Decode(string(src), &m); err != nil {
		return false
	}
	var cur any = m
	for _, part := range strings.Split(table, ".") {
		mm, ok := cur.(map[string]any)
		if !ok {
			return false
		}
		if cur, ok = mm[part]; !ok {
			return false
		}
	}
	if key == "" {
		return true
	}
	mm, ok := cur.(map[string]any)
	if !ok {
		return false
	}
	v, ok := mm[key]
	if ok && len(want) > 0 {
		return v == want[0]
	}
	return ok
}

func configuredCodex(env *Env, a *Agent) bool {
	o := env.Opts
	skills, config, hooks := codexPaths(o)
	if factoryReady(o) && (!skillDirsPresent(skills) || !loaderNamesHost(o, a)) {
		return false
	}
	if len(mcpServers(o)) > 0 && !codexConfigHas(config, "mcp_servers.kindex", "command", kindexCommand(o)) {
		return false
	}
	if policyReady(o) && !codexConfigHas(config, "features", "hooks") {
		return false
	}
	if policyReady(o) {
		m, err := readJSON(hooks)
		if err != nil || !hasHook(m, "PreToolUse", policyHookCommand(o, "codex")) {
			return false
		}
	}
	return true
}

// ---- OpenCode ------------------------------------------------------------

func opencodePaths(o Options) (commands, config, plugin string) {
	base := filepath.Join(o.Home, ".config", "opencode")
	return filepath.Join(base, "commands"),
		filepath.Join(base, "opencode.json"),
		filepath.Join(base, "plugins", "wander-policy.js")
}

// opencodePolicyPlugin blocks a tool call when signet-eval denies it. OpenCode
// plugins hook tool.execute.before and abort by throwing.
const opencodePolicyPlugin = `// Installed by factory-setup. Routes every tool call through signet-eval.
import { spawnSync } from "node:child_process";

const SIGNET = %q;

// The policy's --adapter opencode passes names through, but its rules use
// Claude Code's tool names and file_path. Unlisted tools keep their own id.
const TOOLS = new Map([["bash", "Bash"], ["read", "Read"], ["edit", "Edit"], ["write", "Write"],
  ["glob", "Glob"], ["grep", "Grep"], ["webfetch", "WebFetch"], ["websearch", "WebSearch"]]);

export const WanderPolicy = async () => ({
  "tool.execute.before": async (input, output) => {
    const tool_name = TOOLS.get(input.tool) ?? input.tool;
    const args = output.args ?? {};
    const tool_input = /^(Read|Edit|Write)$/.test(tool_name) ? { ...args, file_path: args.filePath } : args;
    const payload = JSON.stringify({ tool_name, tool_input });
    const res = spawnSync(SIGNET, ["--adapter", "opencode"], { input: payload, encoding: "utf8", timeout: 10000 });
    if (res.status !== 0 || !res.stdout) return;
    let decision;
    try { decision = JSON.parse(res.stdout); } catch { return; }
    const d = decision.hookSpecificOutput?.permissionDecision ?? decision.permissionDecision;
    // Nothing here can prompt, so an ask blocks too, with the policy's reason.
    if (d === "deny" || d === "ask") {
      const why = decision.hookSpecificOutput?.permissionDecisionReason ?? decision.reason ?? "denied by policy";
      throw new Error("wander policy: " + why);
    }
  },
});
`

func configureOpenCode(_ context.Context, env *Env, a *Agent) error {
	o := env.Opts
	commands, config, plugin := opencodePaths(o)
	if factoryReady(o) {
		if err := writePromptFiles(env, commands, "$ARGUMENTS", seatHost(a)); err != nil {
			return err
		}
	}
	if len(mcpServers(o)) > 0 {
		if err := env.mergeJSON(config, func(m map[string]any) {
			if _, ok := m["$schema"]; !ok {
				m["$schema"] = "https://opencode.ai/config.json"
			}
			mcp := subMap(m, "mcp")
			for _, s := range mcpServers(o) {
				mcp[s.Name] = map[string]any{
					"type":    "local",
					"command": append([]string{s.Command}, s.Args...),
					"enabled": true,
				}
			}
		}); err != nil {
			return err
		}
	}
	if policyReady(o) {
		body := fmt.Sprintf(opencodePolicyPlugin, o.DevshellBin("signet-eval"))
		return env.WriteFile(plugin, []byte(body), 0o644)
	}
	return nil
}

func configuredOpenCode(env *Env, _ *Agent) bool {
	o := env.Opts
	commands, config, plugin := opencodePaths(o)
	if factoryReady(o) && !promptsPresent(commands) {
		return false
	}
	if len(mcpServers(o)) > 0 {
		m, err := readJSON(config)
		if err != nil || !kindexWired(subMap(m, "mcp"), o) {
			return false
		}
	}
	// An older plugin sends OpenCode's own tool ids or lets an ask through; rewrite it.
	return !policyReady(o) || FileContains(plugin, `d === "ask"`)
}

// ---- prime-agent and pi (shared pi-mono layout) ---------------------------

// piPolicyExtension blocks denied tool calls. Both pi and prime-agent expose
// pi.on("tool_call") returning { block, reason }.
const piPolicyExtension = `// Installed by factory-setup. Routes every tool call through signet-eval.
import { spawnSync } from "node:child_process";

const SIGNET = %q;

// The policy's rules use Claude Code's tool names and file_path; pi's are
// lowercase with path. Unlisted tools keep their own name.
const TOOLS = new Map([["bash", "Bash"], ["powershell", "Bash"], ["read", "Read"], ["edit", "Edit"],
  ["write", "Write"], ["grep", "Grep"], ["find", "Glob"]]);

export default function (pi) {
  pi.on("tool_call", async (event) => {
    const tool_name = TOOLS.get(event.toolName) ?? event.toolName;
    const args = event.input ?? {};
    const tool_input = /^(Read|Edit|Write)$/.test(tool_name) ? { ...args, file_path: args.path } : args;
    const payload = JSON.stringify({ tool_name, tool_input });
    const res = spawnSync(SIGNET, ["--adapter", "claude"], { input: payload, encoding: "utf8", timeout: 10000 });
    if (res.status !== 0 || !res.stdout) return;
    let decision;
    try { decision = JSON.parse(res.stdout); } catch { return; }
    const d = decision.hookSpecificOutput?.permissionDecision;
    // Nothing here can prompt, so an ask blocks too, with the policy's reason.
    if (d === "deny" || d === "ask") {
      return { block: true, reason: decision.hookSpecificOutput?.permissionDecisionReason ?? "denied by policy" };
    }
  });
}
`

// piLayout returns the prompt directory, the file holding MCP servers and the
// policy extension under an agent root. prime-agent keeps MCP servers in
// settings.json; pi (0.99 and later) in mcp.json. Both use mcpServers.
func piLayout(root, mcpFile string) (prompts, mcp, extension string) {
	return filepath.Join(root, "prompts"),
		filepath.Join(root, mcpFile),
		filepath.Join(root, "extensions", "wander-policy.ts")
}

const (
	primeMCPFile = "settings.json"
	piMCPFile    = "mcp.json"
)

func configurePiLike(env *Env, root, mcpFile, host string) error {
	o := env.Opts
	prompts, mcp, extension := piLayout(root, mcpFile)
	if factoryReady(o) {
		if err := writePromptFiles(env, prompts, "$ARGUMENTS", host); err != nil {
			return err
		}
	}
	if len(mcpServers(o)) > 0 {
		if err := env.mergeJSON(mcp, func(m map[string]any) {
			servers := subMap(m, "mcpServers")
			for _, s := range mcpServers(o) {
				entry := map[string]any{"type": "stdio", "command": s.Command}
				if len(s.Args) > 0 {
					entry["args"] = s.Args
				}
				servers[s.Name] = entry
			}
		}); err != nil {
			return err
		}
	}
	if policyReady(o) {
		body := fmt.Sprintf(piPolicyExtension, o.DevshellBin("signet-eval"))
		return env.WriteFile(extension, []byte(body), 0o644)
	}
	return nil
}

func configuredPiLike(env *Env, a *Agent, root, mcpFile string) bool {
	o := env.Opts
	prompts, mcp, extension := piLayout(root, mcpFile)
	if factoryReady(o) && (!promptsPresent(prompts) || !loaderNamesHost(o, a)) {
		return false
	}
	if len(mcpServers(o)) > 0 {
		m, err := readJSON(mcp)
		if err != nil || !kindexWired(subMap(m, "mcpServers"), o) {
			return false
		}
	}
	// An older extension sends pi's own tool names or lets an ask through; rewrite it.
	return !policyReady(o) || FileContains(extension, `d === "ask"`)
}

func primeRoot(o Options) string {
	if v := os.Getenv("PRIME_AGENT_CODING_AGENT_DIR"); v != "" {
		return v
	}
	return filepath.Join(o.Home, ".prime", "agent")
}
func piRoot(o Options) string { return filepath.Join(o.Home, ".pi", "agent") }

func configurePrime(_ context.Context, env *Env, a *Agent) error {
	return configurePiLike(env, primeRoot(env.Opts), primeMCPFile, seatHost(a))
}
func configuredPrime(env *Env, a *Agent) bool {
	return configuredPiLike(env, a, primeRoot(env.Opts), primeMCPFile)
}

// An older pi ignores mcp.json, so writing it is harmless before 0.99.
func configurePi(_ context.Context, env *Env, a *Agent) error {
	return configurePiLike(env, piRoot(env.Opts), piMCPFile, seatHost(a))
}
func configuredPi(env *Env, a *Agent) bool {
	return configuredPiLike(env, a, piRoot(env.Opts), piMCPFile)
}

// ---- Antigravity -----------------------------------------------------------

// The agy CLI reads global skills from ~/.gemini/antigravity-cli/skills;
// ~/.gemini/config/skills is the Antigravity app's. MCP servers and hooks are
// shared with the app under ~/.gemini/config.
func antigravityPaths(o Options) (skills, mcp, hooks string) {
	base := filepath.Join(o.Home, ".gemini", "config")
	return filepath.Join(o.Home, ".gemini", "antigravity-cli", "skills"),
		filepath.Join(base, "mcp_config.json"),
		filepath.Join(base, "hooks.json")
}

func configureAntigravity(_ context.Context, env *Env, a *Agent) error {
	o := env.Opts
	skills, mcp, hooks := antigravityPaths(o)
	if factoryReady(o) {
		// Antigravity skills double as slash commands: one SKILL.md per dir.
		if err := writeSkillDirs(env, skills, seatHost(a)); err != nil {
			return err
		}
		if err := env.mergeAllowRules(antigravitySettingsPath(o), antigravityAllowRules(o)); err != nil {
			return err
		}
	}
	if len(mcpServers(o)) > 0 {
		if err := env.mergeJSON(mcp, func(m map[string]any) {
			servers := subMap(m, "mcpServers")
			for _, s := range mcpServers(o) {
				entry := map[string]any{"command": s.Command}
				if len(s.Args) > 0 {
					entry["args"] = s.Args
				}
				servers[s.Name] = entry
			}
		}); err != nil {
			return err
		}
	}
	if policyReady(o) {
		return env.mergeJSON(hooks, func(m map[string]any) {
			addHook(m, "PreToolUse", "", policyHookCommand(o, "antigravity"), 10)
		})
	}
	return nil
}

func configuredAntigravity(env *Env, a *Agent) bool {
	o := env.Opts
	skills, mcp, hooks := antigravityPaths(o)
	if factoryReady(o) && (!skillDirsPresent(skills) || !loaderNamesHost(o, a) || !hasAllowRules(antigravitySettingsPath(o), antigravityAllowRules(o))) {
		return false
	}
	if len(mcpServers(o)) > 0 {
		m, err := readJSON(mcp)
		if err != nil || !kindexWired(subMap(m, "mcpServers"), o) {
			return false
		}
	}
	if policyReady(o) {
		m, err := readJSON(hooks)
		if err != nil || !hasHook(m, "PreToolUse", policyHookCommand(o, "antigravity")) {
			return false
		}
	}
	return true
}

// ConfigFiles lists, per agent, the files the installer touches. Used by the
// summary and by --print-paths.
func ConfigFiles(o Options, id AgentID) []string {
	var files []string
	switch id {
	case AgentClaude:
		c, s, m := claudePaths(o)
		files = append(files, c+"/*.md", s, m)
	case AgentCodex:
		s, c, h := codexPaths(o)
		files = append(files, s+"/*/SKILL.md", s+"/*/agents/openai.yaml", c, h)
	case AgentOpenCode:
		c, cfg, pl := opencodePaths(o)
		files = append(files, c+"/*.md", cfg, pl)
	case AgentPrime:
		p, m, e := piLayout(primeRoot(o), primeMCPFile)
		files = append(files, p+"/*.md", m, e)
	case AgentPi:
		p, m, e := piLayout(piRoot(o), piMCPFile)
		files = append(files, p+"/*.md", m, e)
	case AgentAntigravity:
		s, m, h := antigravityPaths(o)
		files = append(files, s+"/*/SKILL.md", m, h)
	}
	if p := memoryPath(o, id); p != "" {
		files = append(files, p)
	}
	sort.Strings(files)
	return files
}

// Agent configuration keys off what is actually installed, not only what was
// selected in this run, so `--components agents` on a machine that already
// has the devshell, policy or Factory wires everything that exists. A
// component selected in this run counts as ready because its steps precede
// the agent steps in the plan.
func devshellReady(o Options) bool {
	return o.Has(ComponentDevshell) || IsDir(filepath.Join(o.DevshellLink(), "bin"))
}

func policyReady(o Options) bool {
	return o.Has(ComponentPolicy) || FileContains(signetRules(o), wanderRulesMarker)
}

func factoryReady(o Options) bool {
	return o.Has(ComponentFactory) || Exists(filepath.Join(o.FactoryDir(), "prompts", "validate.md"))
}
