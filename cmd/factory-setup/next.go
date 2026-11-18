package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/term"

	"github.com/wandercom/chiron/internal/setup"
)

var (
	styleStepNum   = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("212"))
	styleStepPart  = lipgloss.NewStyle().Foreground(lipgloss.Color("212"))
	styleStepTitle = lipgloss.NewStyle().Bold(true)
	styleStepText  = lipgloss.NewStyle().Foreground(lipgloss.AdaptiveColor{Light: "238", Dark: "250"})
	styleCmd       = lipgloss.NewStyle().Foreground(lipgloss.AdaptiveColor{Light: "28", Dark: "114"})
)

// nextSteps is what is left for the person to do once the installer is
// done, in the order to do it.
func nextSteps(env *setup.Env, outcomes []setup.Outcome, runErr error) []setup.NextStep {
	o := env.Opts
	if runErr != nil {
		// A step that knows what it needs says so, commands and all.
		for _, oc := range outcomes {
			if oc.Next != nil && oc.Status == setup.StatusBlocked {
				return []setup.NextStep{*oc.Next, {Title: "Then run the same command again", Text: "The steps that finished are skipped."}}
			}
		}
		return []setup.NextStep{{
			Title: "Fix what stopped the run (above)",
			Text:  "Then run the same command again; the steps that finished are skipped.",
		}}
	}
	var steps []setup.NextStep
	if o.Has(setup.ComponentDevshell) {
		steps = append(steps, setup.NextStep{Title: "Open a new terminal", Text: "So the direnv hook and the devshell tools load."})
	}
	if !strings.Contains(":"+os.Getenv("PATH")+":", ":"+filepath.Dir(o.LocalBin("kin"))+":") {
		if names := localBinTools(o, outcomes); names != nil {
			steps = append(steps, pathStep(names))
		}
	}
	if o.Has(setup.ComponentKindex) {
		if step, ok := shadowingKindex(o); ok {
			steps = append(steps, step)
		}
	}
	if tools := setup.RuntimeTools(env); tools != nil {
		steps = append(steps, *tools)
	}
	if o.Has(setup.ComponentFactory) {
		steps = append(steps, setup.MissingSeatTools(env)...)
	}
	if signIns := setup.SignIns(context.Background(), env); signIns != nil {
		steps = append(steps, *signIns)
	}
	if len(o.Agents) > 0 {
		var names []string
		for _, a := range setup.Agents() {
			if slices.Contains(o.Agents, a.ID) {
				names = append(names, a.Name)
			}
		}
		step := setup.NextStep{
			Title: "Restart your coding agents",
			Text:  listed(names) + " load the new commands, MCP servers and memory when they start.",
		}
		if len(names) == 1 {
			step = setup.NextStep{Title: "Restart " + names[0], Text: "It loads the new commands, MCP servers and memory when it starts."}
		}
		steps = append(steps, step)
	}
	// What the run could not do yet, each saying what to get.
	for _, oc := range outcomes {
		if oc.Next != nil {
			steps = append(steps, *oc.Next)
		}
	}
	if o.Has(setup.ComponentKindex) || o.Has(setup.ComponentDevshell) {
		steps = append(steps, kinbaseSteps(o, outcomes)...)
	}
	if o.Has(setup.ComponentFactory) {
		steps = append(steps, setup.NextStep{
			Title: "Use the factory",
			Text:  `Open a repo and ask your agent to "use the factory" for a task, or in Claude Code run /factory <task>. It records your task and starts the run.`,
		})
	}
	if len(steps) == 0 {
		steps = append(steps, setup.NextStep{Title: "Nothing", Text: "Run factory-setup again any time to update."})
	}
	return steps
}

// kinbaseSteps are what is left for Kinbase besides connecting: moving kb off
// staging, an older kinbase that comes first on PATH, and, once connected,
// how to ask it.
func kinbaseSteps(o setup.Options, outcomes []setup.Outcome) []setup.NextStep {
	var steps []setup.NextStep
	if setup.Exists(filepath.Join(o.Home, "bin", "kb")) || setup.Exists(filepath.Join(o.Home, ".kube", "kinbase-staging")) {
		steps = append(steps, setup.NextStep{
			Title: "Move kb from staging to production",
			Text:  "You used Kinbase on staging. This replaces kb and its Claude Code and Codex instructions with the production ones, and the first kb question afterwards removes the old staging setup. Your own CLAUDE.md and AGENTS.md are kept.",
			Cmds:  []string{"~/bin/kb install"},
		})
	}
	if p, err := exec.LookPath("kinbase"); err == nil && p != o.LocalBin("kinbase") &&
		!strings.HasPrefix(p, o.DevshellLink()) && !strings.HasPrefix(p, "/nix/") &&
		exec.Command(p, "auth", "login", "--help").Run() != nil {
		steps = append(steps, setup.NextStep{
			Title: "Move the older kinbase at " + p + " aside",
			Text:  "It comes first on your PATH and is from before Google sign-in, so Kinbase fails wherever it runs instead of the new one.",
			Cmds:  []string{"mv " + p + " ~/kinbase.old"},
		})
	}
	for _, oc := range outcomes {
		if oc.Step != nil && oc.Step.ID() == "kinbase.connect" && (oc.Applied || oc.Status == setup.StatusDone) {
			steps = append(steps, setup.NextStep{
				Title: "Ask Kinbase",
				Text: "Inside a Wander repo, ask Claude Code or Codex: \"check Kinbase before we plan this\", \"who owns the pricing fee code?\". " +
					"Use short nouns, a ticket ID plus two or three words. RULINGS govern (raise any plan that contradicts one), OWNERSHIP says where code lives, " +
					"PAST WORK shows what was raised or shipped. If it asks you to sign in, run kinbase auth login from a Wander repo; anything that looks wrong goes to #team-software-factory.",
			})
		}
	}
	return steps
}

// shadowingKindex is a next step when typing kin would run an older kindex,
// one whose directory comes before ~/.local/bin on PATH. Agents already run
// the new one; this is for the shell. The devshell's own, inside a direnv'd
// repository, is meant to come first and is left out.
func shadowingKindex(o setup.Options) (setup.NextStep, bool) {
	p, err := exec.LookPath("kin")
	if err != nil || p == o.LocalBin("kin") || strings.HasPrefix(p, o.DevshellLink()) || strings.HasPrefix(p, "/nix/") {
		return setup.NextStep{}, false
	}
	real, err := filepath.EvalSymlinks(p)
	if err != nil {
		real = p
	}
	step := setup.NextStep{
		Title: "Retire the older kindex at " + p,
		Text:  "Typing kin runs it, since its directory comes before ~/.local/bin on your PATH; your agents already use the new kindex. Uninstall it with the tool that installed it, or put ~/.local/bin first on your PATH.",
	}
	switch {
	case strings.Contains(real, "/pipx/"):
		step.Cmds = []string{"pipx uninstall kindex"}
	case strings.Contains(real, "/uv/tools/"):
		step.Cmds = []string{"uv tool uninstall kindex"}
	default:
		step.Cmds = []string{"python3 -m pip uninstall kindex"}
	}
	return step, true
}

// localBinTools names what this install put in ~/.local/bin that is run by
// name: kindex's commands, and the Cursor CLI, which the Factory starts as
// agent. nil when it put nothing there.
func localBinTools(o setup.Options, outcomes []setup.Outcome) []string {
	var names []string
	if o.Has(setup.ComponentKindex) {
		names = append(names, "kin", "kinbase")
	}
	for _, oc := range outcomes {
		if oc.Step == nil || oc.Step.ID() != "agent.cursor-agent.install" {
			continue
		}
		// Applied, its installer linked agent there; already done, the detail
		// says where it is.
		if oc.Applied || (oc.Status == setup.StatusDone && filepath.Dir(oc.Detail) == filepath.Dir(o.LocalBin("agent"))) {
			names = append(names, "Cursor's agent")
		}
	}
	if names == nil {
		return nil
	}
	return append(names, "factory-setup")
}

// pathStep puts ~/.local/bin on PATH in the rc file of the person's shell.
func pathStep(names []string) setup.NextStep {
	step := setup.NextStep{Title: "Put ~/.local/bin on your PATH", Text: listed(names) + " live there. Open a new terminal afterwards."}
	shell := filepath.Base(os.Getenv("SHELL"))
	if shell == "." || shell == "" {
		// No SHELL, as in a container: the platform's default shell.
		shell = map[string]string{"darwin": "zsh"}[runtime.GOOS]
	}
	switch shell {
	case "fish":
		step.Cmds = []string{"fish_add_path ~/.local/bin"}
	case "zsh":
		step.Cmds = []string{`echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc`}
	default:
		// bash reads ~/.bash_profile for a Mac's login shells, ~/.bashrc on Linux.
		rc := "~/.bashrc"
		if runtime.GOOS == "darwin" {
			rc = "~/.bash_profile"
		}
		step.Cmds = []string{`echo 'export PATH="$HOME/.local/bin:$PATH"' >> ` + rc}
	}
	return step
}

// listed joins names the way a sentence does: "a, b and c".
func listed(names []string) string {
	if len(names) < 2 {
		return strings.Join(names, "")
	}
	return strings.Join(names[:len(names)-1], ", ") + " and " + names[len(names)-1]
}

// summaryWidth is how wide the summary may run: the terminal's width, no
// wider than a comfortable line.
func summaryWidth() int {
	w, _, err := term.GetSize(os.Stdout.Fd())
	if err != nil || w <= 0 {
		w = 100
	}
	return min(max(w-1, 40), 100)
}

// renderNextSteps lays the steps out width columns wide: a numbered bold
// title, its text wrapped under it, each command on its own line, and any
// lettered parts indented beneath.
func renderNextSteps(steps []setup.NextStep, width int) string {
	var b strings.Builder
	b.WriteString(styleTitle.Render("Next steps") + "\n")
	for i, st := range steps {
		b.WriteString("\n")
		writeStep(&b, styleStepNum.Render(fmt.Sprint(i+1)), st, 2, width)
	}
	return strings.TrimRight(b.String(), "\n")
}

// writeStep writes one step whose marker sits at column indent and whose
// words start three columns further in.
func writeStep(b *strings.Builder, marker string, st setup.NextStep, indent, width int) {
	body := strings.Repeat(" ", indent+3)
	avail := max(width-len(body), 20)
	for i, line := range wrapWords(st.Title, avail) {
		lead := body
		if i == 0 {
			lead = strings.Repeat(" ", indent) + marker + strings.Repeat(" ", 3-lipgloss.Width(marker))
		}
		b.WriteString(lead + styleStepTitle.Render(line) + "\n")
	}
	if st.Text != "" {
		for _, line := range wrapWords(st.Text, avail) {
			b.WriteString(body + styleStepText.Render(line) + "\n")
		}
	}
	for _, cmd := range st.Cmds {
		for i, line := range commandLines(cmd, avail-4) {
			// Continued lines line up under the command's first word.
			lead := body + "  " + styleDim.Render("$") + " "
			if i > 0 {
				lead = body + "    "
			}
			b.WriteString(lead + styleCmd.Render(line) + "\n")
		}
	}
	for i, part := range st.Parts {
		writeStep(b, styleStepPart.Render(string(rune('a'+i))), part, indent+3, width)
	}
}

// wrapWords breaks text into lines of at most limit columns, only at spaces:
// "prime-agent" stays one word. A word longer than limit gets a line of its
// own.
func wrapWords(text string, limit int) []string {
	var lines []string
	line := ""
	for _, w := range strings.Fields(text) {
		if line != "" && lipgloss.Width(line)+1+lipgloss.Width(w) > limit {
			lines = append(lines, line)
			line = w
			continue
		}
		if line != "" {
			line += " "
		}
		line += w
	}
	return append(lines, line)
}

// commandLines breaks a command too long for limit columns at the spaces
// between its words, never inside quotes, with a shell line continuation, so
// it still pastes as one command. A single word longer than limit stays whole.
func commandLines(cmd string, limit int) []string {
	if len(cmd) <= limit {
		return []string{cmd}
	}
	var words []string
	var cur strings.Builder
	var quote rune
	for _, r := range cmd {
		switch {
		case quote != 0:
			if r == quote {
				quote = 0
			}
		case r == '\'' || r == '"':
			quote = r
		case r == ' ':
			if cur.Len() > 0 {
				words = append(words, cur.String())
				cur.Reset()
			}
			continue
		}
		cur.WriteRune(r)
	}
	if cur.Len() > 0 {
		words = append(words, cur.String())
	}
	var lines []string
	line := ""
	for _, w := range words {
		if line != "" && len(line)+1+len(w)+2 > limit {
			lines = append(lines, line+" \\")
			line = w
			continue
		}
		if line != "" {
			line += " "
		}
		line += w
	}
	return append(lines, line)
}
