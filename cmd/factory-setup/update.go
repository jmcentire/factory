package main

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// The installer ships beside the Factory on the repository default branch.
const updateCommand = `gh api -H "Accept: application/vnd.github.raw" "repos/wandercom/chiron/contents/install.sh" | bash`

// checkUpdate prints one line when a newer factory-setup release is out, or
// when this is a dev build (a source build has no release to compare, so it
// would never hear of one), and nothing otherwise. GitHub is asked at most once
// a day, failed attempts included; any failure is silent, since the /factory
// preflight must never stop on it.
func checkUpdate() { checkUpdateTo(os.Stdout) }

func checkUpdateTo(w io.Writer) {
	if version == "dev" {
		fmt.Fprintf(w, "factory-setup is a dev build, not a release, so it gets no update notices. Install the release: %s\n",
			updateCommand)
		return
	}
	if latest := latestRelease(); newer(latest, version) {
		fmt.Fprintf(w, "factory-setup %s is out (this machine has %s). Update before the run: %s\n",
			latest, version, updateCommand)
	}
}

func latestRelease() string {
	dir, err := os.UserCacheDir()
	if err != nil {
		return ""
	}
	cache := filepath.Join(dir, "factory-setup", "latest-release")
	b, _ := os.ReadFile(cache)
	known := strings.TrimSpace(string(b))
	if info, err := os.Stat(cache); err == nil && time.Since(info.ModTime()) < 24*time.Hour {
		return known
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "gh", "api", "repos/wandercom/chiron/releases/latest", "--jq", ".tag_name").Output()
	if tag := strings.TrimSpace(string(out)); err == nil && strings.HasPrefix(tag, "v") {
		known = tag
	}
	// Written on failure too, so an offline or rate-limited machine waits a
	// day instead of asking on every run.
	_ = os.MkdirAll(filepath.Dir(cache), 0o755)
	_ = os.WriteFile(cache, []byte(known+"\n"), 0o644)
	return known
}

// newer reports whether release tag a is later than b: vMAJOR.MINOR.PATCH,
// where a pre-release (v0.1.20-rc1) comes before its release (v0.1.20).
func newer(a, b string) bool {
	pa, preA := semver(a)
	pb, preB := semver(b)
	if pa == nil || pb == nil {
		return false
	}
	for i := range pa {
		if pa[i] != pb[i] {
			return pa[i] > pb[i]
		}
	}
	return preB && !preA
}

func semver(tag string) (nums []int, prerelease bool) {
	core, pre, _ := strings.Cut(strings.TrimPrefix(tag, "v"), "-")
	parts := strings.Split(core, ".")
	if len(parts) != 3 {
		return nil, false
	}
	nums = make([]int, 3)
	for i, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil {
			return nil, false
		}
		nums[i] = n
	}
	return nums, pre != ""
}
