// Command factory-setup provisions a macOS developer machine with Wander's agent
// tooling: Determinate Nix, the tooling-devshell flake, the Software Factory,
// the signet-eval tool-call policy, and the configuration that exposes all of
// it to the coding agents the user selects.
//
// Interactive by default (a Charm huh form); fully scriptable with flags:
//
//	factory-setup                          # menu
//	factory-setup --yes                    # everything, detected agents
//	factory-setup --agents claude-code,codex --components devshell,policy,agents
//	factory-setup --dry-run                # show the plan, change nothing
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/log"
	"github.com/charmbracelet/x/term"

	"github.com/wandercom/chiron/internal/banner"
	"github.com/wandercom/chiron/internal/setup"
)

var (
	styleTitle  = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("212"))
	styleDim    = lipgloss.NewStyle().Foreground(lipgloss.Color("240"))
	styleOK     = lipgloss.NewStyle().Foreground(lipgloss.Color("42"))
	styleWarn   = lipgloss.NewStyle().Foreground(lipgloss.Color("214"))
	styleFail   = lipgloss.NewStyle().Foreground(lipgloss.Color("196"))
	styleBox    = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(lipgloss.Color("240")).Padding(0, 2)
	styleBullet = lipgloss.NewStyle().PaddingLeft(2)
)

// version is stamped by the release workflow via -ldflags "-X main.version=…".
var version = "dev"

func main() {
	closeInheritedFiles()
	for _, a := range os.Args[1:] {
		if a == "--version" || a == "-version" || a == "version" {
			fmt.Println("factory-setup " + version)
			return
		}
		if a == "--check-update" {
			checkUpdate()
			return
		}
	}
	var err error
	if len(os.Args) > 1 && os.Args[1] == "ceremony" {
		err = runCeremony(os.Args[2:])
	} else {
		err = run()
	}
	if err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			fmt.Fprintln(os.Stderr, styleDim.Render("aborted"))
			os.Exit(130)
		}
		fmt.Fprintln(os.Stderr, styleFail.Render("error: ")+err.Error())
		os.Exit(1)
	}
}

func run() error {
	o := setup.DefaultOptions()

	var (
		yes        = flag.Bool("yes", false, "skip the menu; install every component for the detected agents and the seats' agents")
		dryRun     = flag.Bool("dry-run", false, "print the plan and what each step would do; change nothing")
		components = flag.String("components", "", "comma-separated components: nix,devshell,factory,policy,agents (default all); kindex installs Kindex and Kinbase without Nix, in place of the devshell")
		agentsFlag = flag.String("agents", "", "comma-separated agents: claude-code,codex,opencode,prime-agent,pi,antigravity (default: those detected, plus with the Factory every one its seats run)")
		global     = flag.Bool("global-profile", false, "also add the devshell to your nix profile so tools are on PATH everywhere")
		printPaths = flag.Bool("print-paths", false, "list the files the installer would touch for the selected agents, then exit")
	)
	flag.StringVar(&o.CodeDir, "code-dir", o.CodeDir, "where repositories are cloned")
	flag.StringVar(&o.DevshellRef, "tooling-devshell-ref", o.DevshellRef, "tooling-devshell commit, branch or tag (default: the pinned, verified commit)")
	flag.StringVar(&o.FactoryRef, "factory-ref", o.FactoryRef, "factory commit, branch or tag (default: the pinned, verified commit)")
	seatFlags := []struct {
		name string
		seat *setup.Seat
	}{
		{"validator", &o.Seats.Validator},
		{"orchestrator", &o.Seats.Orchestrator},
		{"coder", &o.Seats.Coder},
		{"tester", &o.Seats.Tester},
	}
	for _, f := range seatFlags {
		seat, name := f.seat, f.name
		flag.Func(name, "agent[:model] for the Factory "+name+" seat (default "+seat.String()+")", func(v string) error {
			*seat = setup.ParseSeat(v)
			o.CoderChosen = o.CoderChosen || name == "coder"
			return nil
		})
	}
	flag.Parse()
	if !*printPaths {
		showBanner()
	}

	o.DryRun = *dryRun
	o.GlobalProfile = *global
	env := setup.NewEnv(o)
	detected := setup.DetectAgents(env)

	if *components != "" {
		o.Components = nil
		for _, c := range strings.Split(*components, ",") {
			o.Components = append(o.Components, setup.Component(strings.TrimSpace(c)))
		}
	}
	if *agentsFlag != "" {
		for _, a := range strings.Split(*agentsFlag, ",") {
			o.Agents = append(o.Agents, setup.AgentID(strings.TrimSpace(a)))
		}
	} else {
		o.Agents = defaultAgents(detected, o)
	}

	interactive := !*yes && *components == "" && *agentsFlag == "" && !*printPaths && isTerminal()
	if interactive {
		if err := menu(&o, detected); err != nil {
			return err
		}
	}
	if !o.Has(setup.ComponentAgents) {
		o.Agents = nil
	}
	env = setup.NewEnv(o)
	// Signing in to Kinbase opens a browser tab; only a person at a terminal
	// can complete it.
	env.Interactive = isTerminal() && term.IsTerminal(os.Stdout.Fd())

	if *printPaths {
		for _, id := range o.Agents {
			fmt.Println(styleTitle.Render(string(id)))
			for _, f := range setup.ConfigFiles(o, id) {
				fmt.Println(styleBullet.Render(f))
			}
		}
		return nil
	}

	plan, err := setup.BuildPlan(o)
	if err != nil {
		return err
	}
	printPlan(plan)
	if interactive && !o.DryRun {
		var confirm bool
		if err := huh.NewConfirm().Title("Proceed?").Affirmative("Install").Negative("Cancel").Value(&confirm).Run(); err != nil {
			return err
		}
		if !confirm {
			return huh.ErrUserAborted
		}
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	logger := log.NewWithOptions(os.Stderr, log.Options{ReportTimestamp: false})
	env.Log = func(format string, args ...any) { logger.Debug(fmt.Sprintf(format, args...)) }
	if o.DryRun {
		env.Log = func(format string, args ...any) { fmt.Println(styleDim.Render("    " + fmt.Sprintf(format, args...))) }
	}

	runner := &setup.Runner{
		Env: env,
		OnStart: func(s setup.Step) {
			fmt.Printf("%s %s\n", styleDim.Render("→"), s.Title())
		},
		OnFinish: func(oc setup.Outcome) {
			fmt.Println(renderOutcome(oc))
		},
	}
	outcomes, runErr := runner.Run(ctx, plan)
	fmt.Println()
	fmt.Println(summary(env, outcomes, runErr))
	return runErr
}

// defaultAgents are the agents to install and configure when none are named:
// the ones already installed and, with the Factory, every one its seats run
// (by default Claude Code, Antigravity and Codex), or on a machine with none
// of either, Claude Code, which the Factory's commands need.
func defaultAgents(detected []setup.AgentID, o setup.Options) []setup.AgentID {
	want := slices.Clone(detected)
	if o.Has(setup.ComponentFactory) {
		want = append(want, setup.SeatAgents(o.Seats)...)
	}
	var out []setup.AgentID
	for _, a := range setup.Agents() {
		if slices.Contains(want, a.ID) {
			out = append(out, a.ID)
		}
	}
	if len(out) == 0 {
		return []setup.AgentID{setup.AgentClaude}
	}
	return out
}

// menu drives the interactive form.
func menu(o *setup.Options, detected []setup.AgentID) error {
	seats := [4]string{o.Seats.Validator.String(), o.Seats.Orchestrator.String(), o.Seats.Coder.String(), o.Seats.Tester.String()}
	compOpts := []huh.Option[setup.Component]{
		huh.NewOption("Determinate Nix (package manager the devshell needs)", setup.ComponentNix).Selected(true),
		huh.NewOption("tooling-devshell (kin, constrain, pact, advocate, sim, signet-eval…)", setup.ComponentDevshell).Selected(true),
		huh.NewOption("Software Factory (wandercom/chiron: /validate /engineer /test)", setup.ComponentFactory).Selected(true),
		huh.NewOption("Tool-call policy (signet-eval rules + hooks)", setup.ComponentPolicy).Selected(true),
		huh.NewOption("Configure coding agents", setup.ComponentAgents).Selected(true),
		huh.NewOption("Kindex + Kinbase without Nix (in place of the devshell)", setup.ComponentKindex),
	}
	var agentOpts []huh.Option[setup.AgentID]
	for _, a := range setup.Agents() {
		label := a.Name
		sel := slices.Contains(defaultAgents(detected, *o), a.ID)
		if slices.Contains(detected, a.ID) {
			label += styleDim.Render("  (installed)")
		}
		agentOpts = append(agentOpts, huh.NewOption(label, a.ID).Selected(sel))
	}

	form := huh.NewForm(
		huh.NewGroup(
			huh.NewNote().Title("Wander factory-setup").
				Description("Installs Wander's agent tooling on this Mac and wires it into your coding agents.\nEverything is idempotent; re-run any time."),
			huh.NewMultiSelect[setup.Component]().
				Title("Components").
				Options(compOpts...).
				Value(&o.Components),
		),
		huh.NewGroup(
			huh.NewMultiSelect[setup.AgentID]().
				Title("Coding agents to configure").
				Description("Slash commands for the Factory lanes, devshell MCP servers, and the policy hook.").
				Options(agentOpts...).
				Value(&o.Agents),
		).WithHideFunc(func() bool { return !o.Has(setup.ComponentAgents) }),
		huh.NewGroup(
			huh.NewInput().
				Title("Code directory").
				Description("tooling-devshell and the Software Factory are cloned here.").
				Value(&o.CodeDir),
			huh.NewConfirm().
				Title("Also put the devshell tools on your global PATH?").
				Description("Wander's default is per-repo activation through direnv.").
				Value(&o.GlobalProfile),
		),
		huh.NewGroup(
			huh.NewNote().Title("Factory seats").
				Description("The default agent:model for each role, written into the /factory skill.\nA default, not a rule: the human can name others at run time."),
			seatInput("Validator", &seats[0]),
			seatInput("Orchestrator", &seats[1]),
			seatInput("Coder lane", &seats[2]),
			seatInput("Tester lane", &seats[3]),
		).WithHideFunc(func() bool { return !o.Has(setup.ComponentFactory) }),
	)
	if err := form.Run(); err != nil {
		return err
	}
	o.CoderChosen = o.CoderChosen || seats[2] != o.Seats.Coder.String()
	o.Seats = setup.Seats{
		Validator:    setup.ParseSeat(seats[0]),
		Orchestrator: setup.ParseSeat(seats[1]),
		Coder:        setup.ParseSeat(seats[2]),
		Tester:       setup.ParseSeat(seats[3]),
	}
	return nil
}

func seatInput(title string, v *string) *huh.Input {
	return huh.NewInput().Title(title).Description("agent:model").Value(v)
}

func printPlan(p *setup.Plan) {
	var b strings.Builder
	b.WriteString(styleTitle.Render("Plan") + "\n")
	for i, s := range p.Steps {
		fmt.Fprintf(&b, "%s %2d. %s %s\n", styleDim.Render(""), i+1, s.Title(), styleDim.Render("["+s.ID()+"]"))
	}
	if p.Options.DryRun {
		b.WriteString(styleWarn.Render("dry run: nothing will change"))
	}
	fmt.Println(styleBox.Render(strings.TrimRight(b.String(), "\n")))
}

func renderOutcome(oc setup.Outcome) string {
	switch {
	case oc.Err != nil:
		return styleFail.Render("  ✗ ") + oc.Step.Title() + styleDim.Render("  "+oc.Err.Error())
	case oc.Status == setup.StatusBlocked:
		return styleWarn.Render("  ! ") + oc.Step.Title() + styleDim.Render("  "+oc.Detail)
	case oc.Status == setup.StatusSkipped:
		return styleWarn.Render("  - ") + oc.Step.Title() + styleDim.Render("  not yet; see next steps")
	case oc.Applied:
		return styleOK.Render("  ✓ ") + oc.Step.Title() + styleDim.Render("  applied")
	case oc.Status == setup.StatusMissing:
		return styleWarn.Render("  ~ ") + oc.Step.Title() + styleDim.Render("  "+oc.Detail)
	default:
		return styleOK.Render("  ✓ ") + oc.Step.Title() + styleDim.Render("  already done")
	}
}

func summary(env *setup.Env, outcomes []setup.Outcome, runErr error) string {
	o := env.Opts
	var b strings.Builder
	applied, done := 0, 0
	for _, oc := range outcomes {
		if oc.Applied {
			applied++
		} else if oc.Status == setup.StatusDone {
			done++
		}
	}
	fmt.Fprintf(&b, "%s  %d applied, %d already in place\n", styleTitle.Render("Summary"), applied, done)
	if runErr != nil {
		b.WriteString(styleFail.Render("Stopped early: ") + runErr.Error() + "\n")
		b.WriteString("Fix the cause and re-run; completed steps are skipped.\n")
	}
	if o.Has(setup.ComponentDevshell) {
		b.WriteString("\nDevshell tools: " + o.DevshellLink() + "/bin\n")
		b.WriteString(styleDim.Render("  enter any Wander repo (or "+o.DevshellDir()+") and direnv puts them on PATH; open a new terminal first") + "\n")
	}
	if o.Has(setup.ComponentFactory) {
		b.WriteString("\nSoftware Factory: " + o.FactoryDir() + "\n")
		b.WriteString(styleDim.Render("  slash commands: /factory /validate /engineer /test /build /orchestrate /code-review /review /diff-intent-gate") + "\n")
		b.WriteString(styleDim.Render("  /factory skill: "+o.SkillPath()) + "\n")
		b.WriteString(styleDim.Render("  default seats: validator="+o.Seats.Validator.String()+" orchestrator="+o.Seats.Orchestrator.String()+
			" coder="+o.Seats.Coder.String()+" tester="+o.Seats.Tester.String()) + "\n")
	}
	if o.Has(setup.ComponentKindex) {
		b.WriteString("\nKindex and Kinbase: " + filepath.Dir(o.LocalBin("kin")) + "\n")
		b.WriteString(styleDim.Render("  kin, kin-mcp and kinbase, at the commits the devshell pins; kindex lives in "+o.KindexVenv()) + "\n")
		b.WriteString(styleDim.Render("  kinbase reads the production Company store at kinbase.wander.com, signed in with your Wander Google account") + "\n")
	}
	if len(o.Agents) > 0 {
		b.WriteString("\nConfigured agents:\n")
		for _, id := range o.Agents {
			for _, f := range setup.ConfigFiles(o, id) {
				b.WriteString(styleBullet.Render(string(id)+"  "+styleDim.Render(f)) + "\n")
			}
		}
	}
	b.WriteString("\n" + renderNextSteps(nextSteps(env, outcomes, runErr), summaryWidth()) + "\n")
	return strings.TrimRight(b.String(), "\n")
}

// showBanner opens a run on a terminal with the Wander Factory banner, sized
// to the window and drawn a line at a time. Piped output gets none.
func showBanner() {
	fd := os.Stdout.Fd()
	if !term.IsTerminal(fd) {
		return
	}
	// A pty nobody sized reports 0x0 without an error.
	w, h, err := term.GetSize(fd)
	if err != nil || w <= 0 {
		w, h = 80, 24
	}
	b := banner.Render(w, h, banner.Unicode())
	if b == "" {
		return
	}
	for _, line := range strings.Split(b, "\n") {
		fmt.Println(line)
		time.Sleep(12 * time.Millisecond)
	}
}

// closeInheritedFiles keeps every descriptor this process inherited beyond
// stdin, stdout and stderr from the commands it runs. kinbase refuses to start
// when it inherits extra open files, and some agent shells pass them on (the
// Kinbase instructions' "7>&- 8>&- 9>&-").
func closeInheritedFiles() {
	for fd := 3; fd < 256; fd++ {
		syscall.CloseOnExec(fd)
	}
}

func isTerminal() bool {
	fi, err := os.Stdin.Stat()
	return err == nil && fi.Mode()&os.ModeCharDevice != 0
}
