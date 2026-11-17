package banner

import (
	"io"
	"math"
	"reflect"
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
)

func TestTheBannerFitsEveryTerminal(t *testing.T) {
	for w := 0; w <= 200; w++ {
		for _, h := range []int{0, 5, 8, 12, 16, 20, 24, 30, 40, 60} {
			for _, unicode := range []bool{true, false} {
				out := Render(w, h, unicode)
				if out == "" {
					continue
				}
				lines := strings.Split(out, "\n")
				if h > 0 && 2*len(lines) > h {
					t.Fatalf("%dx%d: %d rows is more than half the terminal", w, h, len(lines))
				}
				for _, l := range lines {
					if lw := lipgloss.Width(l); lw >= w {
						t.Fatalf("%dx%d: a %d-column line fills or overflows the terminal:\n%s", w, h, lw, out)
					}
					if !unicode && strings.ContainsFunc(l, func(r rune) bool { return r > 0x7f }) {
						t.Fatalf("%dx%d: non-ASCII in the ASCII banner: %q", w, h, l)
					}
				}
			}
		}
	}
}

func TestTheLockupGrowsWithTheTerminal(t *testing.T) {
	widest := func(out string) int {
		w := 0
		for _, l := range strings.Split(out, "\n") {
			w = max(w, lipgloss.Width(l))
		}
		return w
	}
	prev := 0
	for w := minCols + margin + 1; w <= 200; w++ {
		got := widest(Render(w, 0, true))
		if got < prev || got > maxCols+margin {
			t.Fatalf("%d columns: lockup %d wide after %d", w, got, prev)
		}
		prev = got
	}
	if prev <= minCols+margin {
		t.Fatalf("the lockup never grew past its minimum")
	}
	for _, c := range []struct {
		w, h int
		want string
	}{
		{80, 24, lockup(lockupShape(), maxCols)[3]}, // full size
		{40, 24, "  Wander Factory"},                // too narrow for the lockup to read
		{120, 10, "  Wander Factory"},               // too short for it
	} {
		if out := Render(c.w, c.h, true); !strings.Contains(out, c.want) {
			t.Errorf("%dx%d lacks %q:\n%s", c.w, c.h, c.want, out)
		}
	}
	if out := Render(12, 10, true); out != "" {
		t.Errorf("nothing fits 12 columns, got:\n%s", out)
	}
}

// The lockup exactly as the widest banner draws it. Losing the globe or the
// wordmark, the nonzero fill, curve parsing or the aspect ratio changes it.
func TestTheLockupDrawsGlobeAndWordmark(t *testing.T) {
	want := []string{
		"    ▗▄▄▄▄▄▄",
		"  ▄▛▀ ▄▛▀▐▛▜█▄    ▄▄▄▄▄▄▄▄",
		"▗█▀▀▀█▀▜▄█▖▐▌▀▙   ██                         ██",
		"█▙  █▘ ▗█▘▀█▙▖▜▌  ██       ▟█▀▀▀▙█▌ ▄█▛▀▀█▄▝▀██▀▀ ▄█▀▀▀█▙  █▙█▀▀▜█▖  ▗█▌",
		"█▀█▟▌  █▘ ▗▛▝▜▟▌  ██▀▀▀▀▀▘▐█▘   ▐█▌▗█▌    ▀  ██  ▐█▘    █▙ ██    ▜▙  ▟▛",
		"▜▙ █▀▙▟▙ ▗█▘ ▗█▘  ██      ▐█▖   ▐█▌▝█▌   ▗▄  ██  ▐█▖   ▗█▛ ██     ▜▙▟▛",
		" ▜▙█ ▟▌▀█▛▀▀██▘   ██       ▀█▙▄▟▀█▌ ▝▜▙▄▄█▀  ▀█▄▄ ▀█▙▄▟█▀  ██     ▝██▘",
		"  ▝▀██▟█▙▄▄▛▀                                                     ▗█▘",
		"                                                                ▝█▀▘",
	}
	if got := lockup(lockupShape(), maxCols); !reflect.DeepEqual(got, want) {
		t.Fatalf("lockup changed:\n%s", strings.Join(got, "\n"))
	}
}

// Round letters dip a little below the baseline, as type does. At any size
// and either ink, no dot that sits mostly below the baseline is inked except
// the globe's and the y's descender: a row straddling the baseline inked a
// nub under every bowl.
func TestNothingButTheYDropsBelowTheBaseline(t *testing.T) {
	s := lockupShape()
	text := polygons(wordmarkPath)
	textLeft, yLeft := math.Inf(1), math.Inf(1)
	for _, poly := range text {
		for _, p := range poly {
			textLeft = min(textLeft, p.x)
		}
	}
	for _, p := range text[len(text)-1] { // the y, the last letter
		yLeft = min(yLeft, p.x)
	}
	for cols := minCols; cols <= maxCols; cols++ {
		for _, ink := range []float64{0.4, 0.5} {
			grid, top := s.dots(2*cols, 2, ink, wordmarkBaseline)
			scale := float64(2*cols) / s.width()
			for r, row := range grid {
				if top+(float64(r)+0.5)*2/scale <= wordmarkBaseline {
					continue
				}
				for c, on := range row {
					if x := s.minX + (float64(c)+0.5)/scale; on && x > textLeft && x < yLeft-1/scale {
						t.Fatalf("%d cols, ink %.1f: a dot below the baseline at x=%.0f, under a letter with no descender", cols, ink, x)
					}
				}
			}
		}
	}
}

func TestTheInkIsWhiteAtEveryColorDepth(t *testing.T) {
	for _, c := range []struct {
		profile termenv.Profile
		dark    bool
		want    string
	}{
		{termenv.TrueColor, true, "\x1b[38;2;255;255;255m"},
		{termenv.ANSI256, true, "\x1b[38;5;231m"},
		// 16-color white is the theme's bright white: send 256-color white.
		{termenv.ANSI, true, "\x1b[38;5;231m"},
		{termenv.ANSI256, false, "\x1b[38;5;16m"},
		{termenv.Ascii, true, "x"},
	} {
		r := lipgloss.NewRenderer(io.Discard)
		r.SetColorProfile(c.profile)
		r.SetHasDarkBackground(c.dark)
		if got := inkStyle(r).Render("x"); !strings.HasPrefix(got, c.want) {
			t.Errorf("profile %v, dark %v: got %q, want it to start %q", c.profile, c.dark, got, c.want)
		}
	}
}

func TestNonzeroFillKeepsHolesOpen(t *testing.T) {
	const outer = "M0 0h10v10h-10Z"
	hole, _ := parseShape(outer+"M3 3v4h4v-4Z").dots(10, 1, 0.5, 0)
	if !hole[1][1] || hole[5][5] {
		t.Fatalf("a counter-wound inner square is a hole:\n%v", hole)
	}
	if filled, _ := parseShape(outer+"M3 3h4v4h-4Z").dots(10, 1, 0.5, 0); !filled[5][5] {
		t.Fatalf("a same-wound inner square is filled under nonzero:\n%v", filled)
	}
}

func TestPathData(t *testing.T) {
	sc := pathScanner{s: "1.5.5-3-4e1,2"}
	var got []float64
	for v, ok := sc.number(); ok; v, ok = sc.number() {
		got = append(got, v)
	}
	if want := []float64{1.5, .5, -3, -40, 2}; !reflect.DeepEqual(got, want) {
		t.Fatalf("numbers: got %v, want %v", got, want)
	}
	for _, c := range [][2]string{
		{"M0 0L10 0L10 10L0 10Z", "m0 0l10 0 0 10-10 0z"},
		{"M0 0L10 0L10 10L0 10Z", "M0 0H10V10H0Z"},
		{"M0 0L10 0L10 10L0 10Z", "M0 0 10 0 10 10 0 10Z"},
		{"M0 0C0 10 10 10 10 0C10 -10 20 -10 20 0Z", "M0 0C0 10 10 10 10 0S20 -10 20 0Z"},
		{"M0 0C0 10 10 10 10 0C10 -10 20 -10 20 0Z", "M0 0c0 10 10 10 10 0s10-10 10 0z"},
		{"M0 0Q10 10 20 0Q30 -10 40 0Z", "M0 0Q10 10 20 0T40 0Z"},
	} {
		if a, b := parseShape(c[0]), parseShape(c[1]); !reflect.DeepEqual(a, b) {
			t.Errorf("%q and %q drew different shapes", c[0], c[1])
		}
	}
}

func TestUnicodeFollowsTheLocale(t *testing.T) {
	for _, c := range []struct {
		term, lcAll, lang string
		want              bool
	}{
		{"xterm-256color", "", "en_US.UTF-8", true},
		{"xterm-256color", "", "", true},
		{"xterm-256color", "C", "en_US.UTF-8", false},
		{"xterm-256color", "", "en_US.ISO8859-1", false},
		{"linux", "", "en_US.UTF-8", false},
		{"xterm-256color", "", "ja_JP.UTF-8", false},
		{"xterm-256color", "", "zh_CN.UTF-8", false},
		{"dumb", "", "", false},
	} {
		t.Setenv("TERM", c.term)
		t.Setenv("LC_ALL", c.lcAll)
		t.Setenv("LC_CTYPE", "")
		t.Setenv("LANG", c.lang)
		t.Setenv("RUNEWIDTH_EASTASIAN", "")
		if got := Unicode(); got != c.want {
			t.Errorf("TERM=%s LC_ALL=%s LANG=%s: got %v", c.term, c.lcAll, c.lang, got)
		}
	}
}
