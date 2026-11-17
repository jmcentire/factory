package setup

import (
	"os"
	"strings"
)

// linuxInstall is the command that installs packages with this machine's
// package manager, or "" when it has neither apt-get nor dnf. apt-get comes
// with an update: a fresh machine or container has no package lists, so an
// install alone finds nothing. As root, where sudo is often not installed (a
// container, a Modal box), it runs directly.
func linuxInstall(env *Env, apt, dnf []string) string {
	sudo := "sudo "
	if os.Geteuid() == 0 {
		sudo = ""
	}
	switch {
	case haveAny(env, "apt-get"):
		return sudo + "apt-get update && " + sudo + "apt-get install -y " + strings.Join(apt, " ")
	case haveAny(env, "dnf"):
		return sudo + "dnf install -y " + strings.Join(dnf, " ")
	}
	return ""
}
