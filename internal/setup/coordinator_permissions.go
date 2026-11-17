package setup

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
)

// An unattended Factory coordinator (the Orchestrator, or a spawned Validator)
// cannot answer an approval prompt, so without these rules Antigravity stalls on
// every harness command and on reading the Factory checkout. The rules name the
// Factory's own programs and folders, to stop stalls, not to confine (several run
// arbitrary commands); every lane keeps its CLI's sandbox.
// Neither Antigravity nor Claude Code matches a directory for commands, so each
// program gets its own rule, listed from the checkout setup installed.

// resolved is path with symlinks resolved, as the Factory compares paths
// (Path.resolve, pwd -P); a path that does not exist yet stays as given.
func resolved(path string) string {
	if p, err := filepath.EvalSymlinks(path); err == nil {
		return p
	}
	return path
}

// coordinatorCommands are the Factory programs a coordinator runs: each
// executable in harness/, and each harness Python program in the forms the
// prompts run it (`python harness/x.py`, or python3) and the venv's interpreter. The bare
// interpreter is not allowed, since that would allow `python -c` with anything.
func coordinatorCommands(o Options) []string {
	dir := resolved(o.FactoryDir())
	venv := filepath.Join(dir, ".venv", "bin", "python")
	entries, err := os.ReadDir(filepath.Join(dir, "harness"))
	if err != nil {
		return nil
	}
	var cmds []string
	for _, e := range entries {
		info, err := e.Info()
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		abs := filepath.Join(dir, "harness", e.Name())
		if info.Mode()&0o111 != 0 {
			cmds = append(cmds, abs)
		}
		if strings.HasSuffix(e.Name(), ".py") {
			cmds = append(cmds, venv+" "+abs, "python "+abs, "python harness/"+e.Name(),
				"python3 "+abs, "python3 harness/"+e.Name()) // a fresh Mac has only python3
		}
	}
	return cmds
}

// coordinatorReads are the folders a coordinator reads outside its workspace:
// the Factory checkout and the /factory skill.
func coordinatorReads(o Options) []string {
	return []string{resolved(o.FactoryDir()), resolved(filepath.Dir(o.SkillPath()))}
}

// antigravityAllowRules are agy's permissions.allow entries for a coordinator.
func antigravityAllowRules(o Options) []string {
	var rules []string
	for _, c := range coordinatorCommands(o) {
		rules = append(rules, "command("+c+")")
	}
	for _, r := range coordinatorReads(o) {
		rules = append(rules, "read_file("+r+")")
	}
	return rules
}

// claudeAllowRules are Claude Code's permissions.allow entries for a coordinator;
// "//" marks an absolute path in a Read rule.
func claudeAllowRules(o Options) []string {
	var rules []string
	for _, c := range coordinatorCommands(o) {
		rules = append(rules, "Bash("+c+":*)")
	}
	for _, r := range coordinatorReads(o) {
		rules = append(rules, "Read(/"+r+"/**)")
	}
	return rules
}

// antigravitySettingsPath is agy's own settings.json, where permissions live.
func antigravitySettingsPath(o Options) string {
	return filepath.Join(o.Home, ".gemini", "antigravity-cli", "settings.json")
}

// allowRules is m's permissions.allow, or an error when the user's settings
// give permissions or allow another shape, which merging would overwrite.
func allowRules(m map[string]any) ([]any, error) {
	perms, ok := m["permissions"]
	if !ok || perms == nil {
		return nil, nil
	}
	pm, ok := perms.(map[string]any)
	if !ok {
		return nil, errors.New(`"permissions" is not an object`)
	}
	allow, ok := pm["allow"]
	if !ok || allow == nil {
		return nil, nil
	}
	list, ok := allow.([]any)
	if !ok {
		return nil, errors.New(`"permissions.allow" is not an array`)
	}
	return list, nil
}

// mergeAllowRules adds rules to the settings file's permissions.allow, keeping
// the user's own, and refuses a file whose permissions it would overwrite.
func (e *Env) mergeAllowRules(path string, rules []string) error {
	m, err := readJSON(path)
	if err != nil {
		return err
	}
	if _, err := allowRules(m); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	return e.mergeJSON(path, func(m map[string]any) {
		allow, _ := allowRules(m)
		for _, r := range rules {
			if !slices.ContainsFunc(allow, func(v any) bool { s, _ := v.(string); return s == r }) {
				allow = append(allow, r)
			}
		}
		subMap(m, "permissions")["allow"] = allow
	})
}

// hasAllowRules reports whether the settings file at path already allows every rule.
func hasAllowRules(path string, rules []string) bool {
	m, err := readJSON(path)
	if err != nil {
		return false
	}
	allow, err := allowRules(m)
	if err != nil {
		return false
	}
	var have []string
	for _, v := range allow {
		if s, ok := v.(string); ok {
			have = append(have, s)
		}
	}
	for _, r := range rules {
		if !slices.Contains(have, r) {
			return false
		}
	}
	return true
}
