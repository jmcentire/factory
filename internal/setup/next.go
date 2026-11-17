package setup

// NextStep is something left for the person to do once the installer is
// done, as the summary lists it: a short title, why or what it does, the
// commands to run in order, and any lettered parts.
type NextStep struct {
	Title string
	Text  string
	Cmds  []string
	Parts []NextStep
}

// nexter is a step that, when it skips, says what to do about it.
type nexter interface{ NextStep() NextStep }
