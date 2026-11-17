package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestNewer(t *testing.T) {
	for _, c := range []struct {
		a, b string
		want bool
	}{
		{"v0.1.20", "v0.1.19", true},
		{"v0.2.0", "v0.1.19", true},
		{"v0.1.19", "v0.1.19", false},
		{"v0.1.9", "v0.1.19", false},
		{"", "v0.1.19", false},
		{"v0.1.20", "dev", false},
		{"v0.1.20", "v0.1.20-rc1", true},
		{"v0.1.21", "v0.1.20-rc1", true},
		{"v0.1.20-rc1", "v0.1.20", false},
		{"v0.1.20-rc2", "v0.1.20-rc1", false},
	} {
		if got := newer(c.a, c.b); got != c.want {
			t.Errorf("newer(%q, %q) = %v, want %v", c.a, c.b, got, c.want)
		}
	}
}

func TestLatestReleaseCachesAndBacksOff(t *testing.T) {
	home, bin := t.TempDir(), t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CACHE_HOME", filepath.Join(home, ".cache"))
	t.Setenv("PATH", bin)
	calls := filepath.Join(bin, "calls")
	gh := func(script string) {
		t.Helper()
		body := "#!/bin/sh\necho x >> " + calls + "\n" + script + "\n"
		if err := os.WriteFile(filepath.Join(bin, "gh"), []byte(body), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	count := func() int {
		b, _ := os.ReadFile(calls)
		return strings.Count(string(b), "x")
	}

	gh(`echo '[[{"tag_name":"v0.1.20","assets":[{"name":"factory-setup-linux-amd64"},{"name":"checksums.txt"}]}]]'`)
	if got := latestRelease(); got != "v0.1.20" || count() != 1 {
		t.Fatalf("first check: %q after %d calls", got, count())
	}
	if got := latestRelease(); got != "v0.1.20" || count() != 1 {
		t.Fatalf("within a day: %q after %d calls, want the cache and no call", got, count())
	}

	dir, _ := os.UserCacheDir()
	cache := filepath.Join(dir, "factory-setup", "latest-installer-release")
	old := time.Now().Add(-48 * time.Hour)
	if err := os.Chtimes(cache, old, old); err != nil {
		t.Fatal(err)
	}
	gh("exit 1")
	if got := latestRelease(); got != "v0.1.20" || count() != 2 {
		t.Fatalf("failed check: %q after %d calls, want the last known release", got, count())
	}
	if got := latestRelease(); got != "v0.1.20" || count() != 2 {
		t.Fatalf("after a failed check: %q after %d calls, want a day of quiet", got, count())
	}
}

func TestADevBuildSaysItGetsNoUpdateNotices(t *testing.T) {
	saved := version
	defer func() { version = saved }()
	version = "dev"
	var out strings.Builder
	checkUpdateTo(&out)
	if !strings.Contains(out.String(), "dev build") || !strings.Contains(out.String(), updateCommand) {
		t.Fatalf("a dev build stayed silent or gave no install command: %q", out.String())
	}
}

func TestLatestInstallerReleaseSkipsRuntimeOnlyAndIncompleteAssets(t *testing.T) {
	data := []byte(`[[
        {"tag_name":"v9.0.0","assets":[{"name":"factory_core.whl"}]},
        {"tag_name":"v8.0.0","assets":[{"name":"checksums.txt"}]},
        {"tag_name":"v7.0.0","draft":true,"assets":[{"name":"factory-setup-linux-amd64"},{"name":"checksums.txt"}]}
    ],[{"tag_name":"v1.0.0","assets":[{"name":"factory-setup-linux-amd64"},{"name":"checksums.txt"}]}]]`)
	if got := latestInstallerRelease(data); got != "v1.0.0" {
		t.Fatalf("got %q", got)
	}
	if got := latestInstallerRelease([]byte(`[[{"tag_name":"v9.0.0","assets":[]}]]`)); got != "" {
		t.Fatalf("runtime-only release: %q", got)
	}
}
