package setup

import (
	"context"
	"os/exec"
	"strings"
	"testing"
)

func TestCloneStepTreatsThePinAsAMinimum(t *testing.T) {
	dir := t.TempDir()
	git := func(args ...string) string {
		t.Helper()
		out, err := exec.Command("git", append([]string{"-C", dir, "-c", "user.email=t@t", "-c", "user.name=t"}, args...)...).Output()
		if err != nil {
			t.Fatalf("git %v: %v", args, err)
		}
		return strings.TrimSpace(string(out))
	}
	git("init", "-q")
	git("remote", "add", "origin", "https://github.com/example/repo")
	git("commit", "-q", "--allow-empty", "-m", "pin")
	pin := git("rev-parse", "HEAD")
	git("commit", "-q", "--allow-empty", "-m", "newer")

	step := &cloneStep{id: "x.clone", repo: "https://github.com/example/repo", ref: pin, dest: dir}
	env := NewEnv(Options{Home: t.TempDir()})
	if status, detail, _ := step.Check(context.Background(), env); status != StatusDone {
		t.Fatalf("checkout past the pin: status %v (%s), want done", status, detail)
	}

	git("checkout", "-q", "--detach", pin)
	git("commit", "-q", "--allow-empty", "-m", "elsewhere")
	other := &cloneStep{id: "x.clone", repo: "https://github.com/example/repo", ref: git("rev-parse", "HEAD"), dest: dir}
	git("checkout", "-q", "--detach", pin)
	if status, _, _ := other.Check(context.Background(), env); status != StatusMissing {
		t.Fatalf("checkout behind the pin: status %v, want missing", status)
	}
}
