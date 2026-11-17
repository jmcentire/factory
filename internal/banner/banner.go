// Package banner draws what factory-setup opens with: the Wander globe and
// "Factory" set like the Wander wordmark, rasterized into block characters at
// the size the terminal allows.
package banner

import (
	"os"
	"strings"
	"sync"

	"github.com/charmbracelet/lipgloss"
	"github.com/mattn/go-runewidth"
	"github.com/muesli/termenv"
)

// margin is the indent the banner sits at, as the rest of the output does.
const margin = 2

// The lockup's width in columns: no wider than maxCols, and no narrower than
// minCols, below which the letters stop reading.
const (
	maxCols = 72
	minCols = 40
)

// styleInk is white on a dark terminal, and black on a light one, where
// white would vanish. A terminal that reports only 16 colors is still sent
// the 256-color white: its 16 are its theme's, and a theme's "bright white"
// can be lavender or grey.
var styleInk = sync.OnceValue(func() lipgloss.Style { return inkStyle(lipgloss.NewRenderer(os.Stdout)) })

func inkStyle(r *lipgloss.Renderer) lipgloss.Style {
	if r.ColorProfile() == termenv.ANSI {
		r.SetColorProfile(termenv.ANSI256)
	}
	return r.NewStyle().Foreground(lipgloss.AdaptiveColor{Dark: "#ffffff", Light: "#000000"})
}

var lockupShape = sync.OnceValue(func() shape { return parseShape(globePath, wordmarkPath) })

// Render is the banner for a terminal width columns wide and height rows tall
// (0 when unknown): the lockup as wide as fits while taking at most half the
// height, or the name in a line of text where the lockup would not read.
// Without unicode it is always the line. It is "" when nothing fits.
func Render(width, height int, unicode bool) string {
	s := lockupShape()
	cols := min(maxCols, width-margin-1)
	if height > 0 {
		// A lockup cols wide is about cols*h/(2w) rows tall, a cell being
		// twice as tall as it is wide; it gets half the height less a blank
		// either side.
		cols = min(cols, int(float64(height/2-2)*2*s.width()/s.height()))
	}
	var art []string
	for ; unicode && cols >= minCols; cols-- {
		// Rounding can add a row; the drawing that fits is the one that counts.
		if art = lockup(s, cols); height <= 0 || 2*(len(art)+2) <= height {
			break
		}
		art = nil
	}
	name := "Wander " + wordmarkText
	var lines []string
	switch {
	case art != nil:
		lines = append(append([]string{""}, art...), "")
	case len(name)+margin < width && (height <= 0 || height >= 6):
		lines = []string{"", styleInk().Render(name), ""}
	default:
		return ""
	}
	for i, l := range lines {
		if l != "" {
			lines[i] = strings.Repeat(" ", margin) + l
		}
	}
	return strings.Join(lines, "\n")
}

// lockup draws the shape cols columns wide in quadrant blocks, which stay
// solid at every size where braille's finer dots draw faint in common fonts.
// A small lockup is inked more boldly so its strokes stay whole. Rows that
// rounding left blank, and trailing blanks a narrow terminal would wrap, are
// dropped.
func lockup(s shape, cols int) []string {
	ink := 0.5
	if cols < 50 {
		ink = 0.4
	}
	var lines []string
	grid, _ := s.dots(2*cols, 2, ink, wordmarkBaseline)
	for _, l := range quadrants(grid) {
		if l = strings.TrimRight(l, " "); l != "" || len(lines) > 0 {
			lines = append(lines, l)
		}
	}
	for len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	for i, l := range lines {
		if l != "" {
			lines[i] = styleInk().Render(l)
		}
	}
	return lines
}

// Unicode reports whether the terminal can be trusted with block characters:
// a UTF-8 locale, or none set (macOS terminals are UTF-8 either way), and not
// the Linux console, whose fonts lack the quadrants. Nor a CJK locale: the
// quadrants are East Asian "ambiguous" characters, which terminals there
// commonly draw two cells wide, doubling the lockup until it wraps.
func Unicode() bool {
	if t := os.Getenv("TERM"); t == "linux" || t == "dumb" {
		return false
	}
	if v := os.Getenv("RUNEWIDTH_EASTASIAN"); v == "1" || v == "" && runewidth.IsEastAsian() {
		return false
	}
	for _, k := range []string{"LC_ALL", "LC_CTYPE", "LANG"} {
		if v := strings.ToLower(os.Getenv(k)); v != "" {
			return strings.Contains(v, "utf-8") || strings.Contains(v, "utf8")
		}
	}
	return true
}
