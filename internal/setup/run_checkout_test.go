package setup

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The run-checkout script end to end against real git: a human's dirty
// checkout on their own branch, an origin with a submodule, and the run's
// clone. The human's checkout must come out byte-for-byte the same.
func TestRunCheckoutLeavesTheHumansCheckoutAlone(t *testing.T) {
	for _, bin := range []string{"git", "bash"} {
		if _, err := exec.LookPath(bin); err != nil {
			t.Skip(bin + " not installed")
		}
	}
	dir, err := filepath.EvalSymlinks(t.TempDir())
	must(t, err)
	for k, v := range map[string]string{
		"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
		"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
		"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
		// Local submodule URLs are file transport, off by default.
		"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always",
		"GIT_CONFIG_KEY_1": "init.defaultBranch", "GIT_CONFIG_VALUE_1": "main",
	} {
		t.Setenv(k, v)
	}
	script := filepath.Join(dir, "run-checkout.sh")
	must(t, os.WriteFile(script, runCheckoutScript, 0o755))
	run := func(cwd string, args ...string) (string, error) {
		c := exec.Command(args[0], args[1:]...)
		c.Dir = cwd
		out, err := c.CombinedOutput()
		return strings.TrimSpace(string(out)), err
	}
	ok := func(cwd string, args ...string) string {
		t.Helper()
		out, err := run(cwd, args...)
		if err != nil {
			t.Fatalf("%v: %v\n%s", args, err, out)
		}
		return out
	}
	rc := func(args ...string) (string, error) { return run(dir, append([]string{script}, args...)...) }
	write := func(path, content string) { must(t, os.WriteFile(path, []byte(content), 0o644)) }

	// origin: main with a submodule at a relative URL. The remotes live in
	// their own directory so that URL resolves only against origin, never
	// against the human's checkout.
	must(t, os.Mkdir(filepath.Join(dir, "remote"), 0o755))
	ok(dir, "git", "init", "-q", "--bare", "remote/sub.git")
	ok(dir, "git", "init", "-q", "--bare", "remote/origin.git")
	ok(dir, "git", "clone", "-q", "remote/sub.git", "sub-seed")
	write(filepath.Join(dir, "sub-seed", "s"), "s\n")
	ok(filepath.Join(dir, "sub-seed"), "git", "add", "s")
	ok(filepath.Join(dir, "sub-seed"), "git", "commit", "-qm", "s")
	ok(filepath.Join(dir, "sub-seed"), "git", "push", "-q", "origin", "HEAD:main")
	ok(dir, "git", "clone", "-q", "remote/origin.git", "seed")
	seed := filepath.Join(dir, "seed")
	write(filepath.Join(seed, "a"), "a\n")
	ok(seed, "git", "add", "a")
	ok(seed, "git", "commit", "-qm", "init")
	ok(seed, "git", "submodule", "-q", "add", "../sub.git", "sub")
	ok(seed, "git", "commit", "-qm", "sub")
	ok(seed, "git", "push", "-q", "origin", "HEAD:main")

	// The human: their own branch with an unpushed commit, a stash, a
	// modified file and an untracked file.
	ok(dir, "git", "clone", "-q", "--recurse-submodules", "remote/origin.git", "human")
	human := filepath.Join(dir, "human")
	ok(human, "git", "switch", "-qc", "feature/mine")
	write(filepath.Join(human, "b"), "b\n")
	ok(human, "git", "add", "b")
	ok(human, "git", "commit", "-qm", "my unpushed work")
	ok(human, "git", "tag", "feature/mine") // a tag sharing their branch's name
	write(filepath.Join(human, "a"), "stashed\n")
	ok(human, "git", "stash", "-q")
	write(filepath.Join(human, "a"), "a\nmy edit\n")
	write(filepath.Join(human, "notes"), "untracked\n")
	// Their own exclude rule, with no trailing newline, must survive start.
	write(filepath.Join(human, ".git", "info", "exclude"), "secret.env")
	write(filepath.Join(human, "secret.env"), "x\n")
	state := func() string {
		var b strings.Builder
		for _, args := range [][]string{
			{"git", "symbolic-ref", "HEAD"}, {"git", "rev-parse", "HEAD"}, {"git", "status", "--porcelain"},
			{"git", "diff"}, {"git", "stash", "list"}, {"git", "branch", "-a"}, {"git", "worktree", "list"},
			{"git", "for-each-ref"}, {"cat", "a", "notes"},
		} {
			b.WriteString(ok(human, args...) + "\n")
		}
		return b.String()
	}
	before := state()

	work, err := rc("start", human, "r1", "main", "feature/plat-1-fix")
	if err != nil {
		t.Fatalf("start: %v\n%s", err, work)
	}
	if want := filepath.Join(human, ".factory", "checkouts", "r1"); work != want {
		t.Fatalf("start printed %q, want %q", work, want)
	}
	if got := ok(work, "git", "branch", "--show-current"); got != "feature/plat-1-fix" {
		t.Fatalf("run checkout on %q", got)
	}
	if ok(work, "git", "rev-parse", "HEAD") != ok(dir, "git", "-C", "remote/origin.git", "rev-parse", "main") {
		t.Fatal("run checkout does not start at origin's main")
	}
	if got := ok(work, "git", "branch", "--format=%(refname:short)"); got != "feature/plat-1-fix" {
		t.Fatalf("run checkout kept other branches, including the human's unpushed one: %q", got)
	}
	if got := ok(work, "git", "remote", "-v"); strings.Contains(got, human) || !strings.Contains(got, "origin.git") {
		t.Fatalf("run checkout remotes: %q", got)
	}
	if _, err := os.Stat(filepath.Join(work, "sub", "s")); err != nil {
		t.Fatalf("submodule not checked out: %v", err)
	}
	if got := state(); got != before {
		t.Fatalf("start changed the human's checkout:\nbefore:\n%s\nafter:\n%s", before, got)
	}

	// The Validator's work: a commit and a push from the run checkout.
	write(filepath.Join(work, "c"), "c\n")
	ok(work, "git", "add", "c")
	ok(work, "git", "commit", "-qm", "fix\n\nFactoryRun: r1")
	if out, err := rc("finish", human, "r1"); err == nil || !strings.Contains(out, "no origin branch holds") {
		t.Fatalf("finish deleted an unpushed checkout: %v %s", err, out)
	}
	ok(work, "git", "push", "-q", "origin", "feature/plat-1-fix")
	if out, err := rc("check", human, "r1"); err != nil {
		t.Fatalf("check after a run-checkout commit: %v\n%s", err, out)
	}
	// The human's own edits while the run goes do not stop it.
	write(filepath.Join(human, "a"), "a\nmy edit\nmore\n")
	if out, err := rc("check", human, "r1"); err != nil {
		t.Fatalf("an edit in the human's checkout stopped the run: %v\n%s", err, out)
	}
	write(filepath.Join(human, "a"), "a\nmy edit\n")
	// The harness's .factory/ files in the run checkout never reach a commit.
	must(t, os.MkdirAll(filepath.Join(work, ".factory", "runs"), 0o755))
	write(filepath.Join(work, ".factory", "runs", "x"), "x\n")
	if got := ok(work, "git", "status", "--porcelain"); got != "" {
		t.Fatalf(".factory shows in the run checkout's status: %q", got)
	}
	// A push to the PR branch from anywhere else leaves origin ahead of the run checkout.
	ok(seed, "git", "fetch", "-q", "origin", "feature/plat-1-fix")
	ok(seed, "git", "switch", "-q", "--detach", "FETCH_HEAD")
	write(filepath.Join(seed, "elsewhere"), "e\n")
	ok(seed, "git", "add", "elsewhere")
	ok(seed, "git", "commit", "-qm", "pushed from elsewhere")
	ok(seed, "git", "push", "-q", "origin", "HEAD:refs/heads/feature/plat-1-fix")
	if out, err := rc("check", human, "r1"); err == nil || !strings.Contains(out, "something other than this run pushed") {
		t.Fatalf("check missed a push to the PR branch from outside the run: %v\n%s", err, out)
	}
	ok(work, "git", "merge", "-q", "--ff-only", "refs/remotes/origin/feature/plat-1-fix")
	ok(dir, script, "check", human, "r1")
	if got := ok(work, "git", "tag"); got != "" {
		t.Fatalf("the human's tags were copied into the run checkout: %q", got)
	}
	// A second run on the open PR's branch continues from it, with the base fetched too.
	pr, err := rc("start", human, "r3", "main", "feature/plat-1-fix")
	if err != nil {
		t.Fatalf("start on an existing PR branch: %v\n%s", err, pr)
	}
	if ok(pr, "git", "rev-parse", "HEAD") != ok(work, "git", "rev-parse", "HEAD") {
		t.Fatal("a run on an open PR's branch did not start from its pushed head")
	}
	ok(pr, "git", "rev-parse", "--verify", "refs/remotes/origin/main")
	if got := state(); got != before {
		t.Fatalf("the run's commit and push changed the human's checkout:\n%s", got)
	}

	// A second start with the same run name refuses and leaves the first alone;
	// a failed start removes only what it made.
	if out, err := rc("start", human, "r1", "main", "feature/plat-1-fix"); err == nil || !strings.Contains(out, "pick a new one") {
		t.Fatalf("restart of r1: %v %s", err, out)
	}
	if _, err := rc("start", human, "r2", "no-such-base", "feature/plat-2"); err == nil {
		t.Fatal("start from a missing base succeeded")
	}
	if _, err := os.Stat(filepath.Join(human, ".factory", "checkouts", "r2")); !os.IsNotExist(err) {
		t.Fatalf("failed start left its checkout behind: %v", err)
	}

	// The reported failure: the Validator commits in the human's checkout and
	// even resets it away. check must fail and name it.
	write(filepath.Join(human, "v"), "v\n")
	ok(human, "git", "add", "v")
	ok(human, "git", "commit", "-qm", "fix\n\nFactoryRun: PLAT-1")
	ok(human, "git", "reset", "-q", "--soft", "HEAD~1")
	ok(human, "git", "restore", "--staged", "v")
	must(t, os.Remove(filepath.Join(human, "v")))
	out, err := rc("check", human, "r1")
	if err == nil || !strings.Contains(out, "run-commits") {
		t.Fatalf("check missed a run commit in the human's checkout: %v\n%s", err, out)
	}
	// A human's own change also stops the run until they confirm it.
	ok(human, "git", "switch", "-q", "main")
	if _, err := rc("check", human, "r1"); err == nil {
		t.Fatal("check missed the human's branch switch")
	}
	ok(dir, script, "accept", human, "r1")
	ok(dir, script, "check", human, "r1")

	// A stash in the run's checkout is work finish must not delete.
	write(filepath.Join(work, "c"), "stash me\n")
	ok(work, "git", "stash", "-q")
	if out, err := rc("finish", human, "r1"); err == nil || !strings.Contains(out, "stashes") {
		t.Fatalf("finish deleted a checkout holding a stash: %v %s", err, out)
	}
	ok(work, "git", "stash", "drop", "-q")
	// A blobless partial clone as the human's checkout is refused: its copy would miss blobs.
	ok(dir, "git", "clone", "-q", "--filter=blob:none", "--no-checkout", "file://"+filepath.Join(dir, "remote", "origin.git"), "partial")
	if out, err := rc("start", filepath.Join(dir, "partial"), "r5", "main", "feature/plat-5"); err == nil || !strings.Contains(out, "partial clone") {
		t.Fatalf("start accepted a partial clone: %v %s", err, out)
	}
	// A linked worktree as the human's checkout works the same way.
	ok(human, "git", "worktree", "add", "-q", filepath.Join(dir, "human-wt"), "-b", "feature/wt")
	wt := filepath.Join(dir, "human-wt")
	wtBefore := ok(wt, "git", "status", "--porcelain") + ok(wt, "git", "rev-parse", "HEAD")
	if out, err := rc("start", wt, "r4", "main", "feature/plat-4"); err != nil {
		t.Fatalf("start from a linked worktree: %v\n%s", err, out)
	}
	if got := ok(wt, "git", "status", "--porcelain") + ok(wt, "git", "rev-parse", "HEAD"); got != wtBefore {
		t.Fatalf("start changed the linked worktree: %q", got)
	}
	if out, err := rc("finish", human, "r1"); err != nil {
		t.Fatalf("finish: %v\n%s", err, out)
	}
	if _, err := os.Stat(work); !os.IsNotExist(err) {
		t.Fatalf("finish left %s", work)
	}
	// list reports and deletes nothing; clean removes only the rebuildable bulk and
	// keeps the run's evidence.
	runDir := filepath.Join(human, ".factory", "runs", "r1")
	// Cleanup needs observed inactivity, not absence of a client or record.
	inactiveClient := filepath.Join(dir, "inactive-tmux")
	must(t, os.WriteFile(inactiveClient, []byte("#!/bin/sh\necho 'no server running on fixture' >&2\nexit 1\n"), 0o755))
	t.Setenv("FACTORY_TMUX", inactiveClient)
	must(t, os.MkdirAll(runDir, 0o755))
	write(filepath.Join(runDir, "harness.json"), `{"tmux_socket":"fixture-inactive","tmux_tmpdir":"/fixture/original"}`)
	must(t, os.MkdirAll(filepath.Join(runDir, "tmux-lanes", "coder-agent-home"), 0o755))
	write(filepath.Join(runDir, "tmux-lanes", "coder-lane.log"), "keep\n")
	must(t, os.MkdirAll(filepath.Join(runDir, "validation", "c1"), 0o755))
	must(t, os.MkdirAll(filepath.Join(runDir, "evidence"), 0o755))
	write(filepath.Join(runDir, "evidence", "e"), "keep\n")
	if out := ok(dir, script, "list", human); !strings.Contains(out, "r1  checkout -") || !strings.Contains(out, "ready") || !strings.Contains(out, "free on this disk") {
		t.Fatalf("list: %s", out)
	}
	if _, err := os.Stat(filepath.Join(runDir, "tmux-lanes", "coder-agent-home")); err != nil {
		t.Fatal("list deleted something")
	}
	if _, err := rc("list", human, "extra"); err == nil {
		t.Fatal("list accepted an extra argument")
	}
	ok(dir, script, "clean", human, "r1")
	for _, gone := range []string{"tmux-lanes/coder-agent-home", "validation"} {
		if _, err := os.Stat(filepath.Join(runDir, gone)); !os.IsNotExist(err) {
			t.Fatalf("clean left %s", gone)
		}
	}
	if _, err := os.Stat(filepath.Join(runDir, "tmux-lanes", "coder-lane.log")); err != nil {
		t.Fatal("clean removed a lane log")
	}
	if _, err := os.Stat(filepath.Join(runDir, "evidence", "e")); err != nil {
		t.Fatal("clean removed the run's evidence")
	}
	// A run folder that is a symlink elsewhere is never cleaned through.
	elsewhere := filepath.Join(dir, "elsewhere")
	must(t, os.MkdirAll(filepath.Join(elsewhere, "validation"), 0o755))
	must(t, os.Symlink(elsewhere, filepath.Join(human, ".factory", "runs", "r9")))
	if out, err := rc("clean", human, "r9"); err == nil || !strings.Contains(out, "symlink") {
		t.Fatalf("clean followed a symlinked run folder: %v %s", err, out)
	}
	if _, err := os.Stat(filepath.Join(elsewhere, "validation")); err != nil {
		t.Fatal("clean deleted through a symlink")
	}
	// A checkout with unpushed work is listed as kept, and clean refuses it.
	r4 := filepath.Join(wt, ".factory", "checkouts", "r4")
	write(filepath.Join(r4, "wip"), "mine\n")
	if out := ok(dir, script, "list", wt); !strings.Contains(out, "r4") || !strings.Contains(out, "keep: uncommitted changes") {
		t.Fatalf("list did not flag unpushed work: %s", out)
	}
	if out, err := rc("clean", wt, "r4"); err == nil || !strings.Contains(out, "uncommitted changes") {
		t.Fatalf("clean deleted a checkout with unpushed work: %v %s", err, out)
	}
	if _, err := os.Stat(r4); err != nil {
		t.Fatal("refused clean still removed the checkout")
	}
	// A run checkout that lost its .git must not let git fall through to the human's.
	must(t, os.Rename(filepath.Join(pr, ".git"), filepath.Join(dir, "r3-git")))
	if out, err := rc("check", human, "r3"); err == nil || !strings.Contains(out, "not the run's own checkout") {
		t.Fatalf("check ran git against the human's repository: %v\n%s", err, out)
	}
	if got := ok(human, "git", "status", "--porcelain"); strings.Contains(got, ".factory") {
		t.Fatalf(".factory shows in the human's status: %q", got)
	}
}
