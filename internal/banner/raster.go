package banner

import (
	"math"
	"sort"
	"strconv"
	"strings"
)

// shape is filled SVG path data flattened to straight edges, filled the way
// SVG fills by default (nonzero winding), so the holes in the globe and in
// the letters stay open.
type shape struct {
	edges                  []edge
	minX, minY, maxX, maxY float64
}

// edge runs downward from (x0, y0) to (x1, y1); winding is +1 when the path
// ran down it and -1 when the path ran up it.
type edge struct {
	x0, y0, x1, y1 float64
	winding        int
}

type point struct{ x, y float64 }

// curveSteps is how many straight segments stand in for one Bézier curve:
// far finer than a braille dot at any size a terminal can show.
const curveSteps = 16

// parseShape flattens SVG path data into one shape. It reads the commands the
// logo uses, absolute and relative (M L H V C S Z), plus Q and T, and stops at
// anything else.
func parseShape(paths ...string) shape {
	s := shape{minX: math.Inf(1), minY: math.Inf(1), maxX: math.Inf(-1), maxY: math.Inf(-1)}
	for _, d := range paths {
		for _, poly := range polygons(d) {
			s.add(poly)
		}
	}
	return s
}

func (s *shape) add(poly []point) {
	for i, a := range poly {
		s.minX, s.maxX = math.Min(s.minX, a.x), math.Max(s.maxX, a.x)
		s.minY, s.maxY = math.Min(s.minY, a.y), math.Max(s.maxY, a.y)
		b := poly[(i+1)%len(poly)]
		switch {
		case a.y < b.y:
			s.edges = append(s.edges, edge{a.x, a.y, b.x, b.y, 1})
		case a.y > b.y:
			s.edges = append(s.edges, edge{b.x, b.y, a.x, a.y, -1})
		}
	}
}

func (s shape) width() float64  { return s.maxX - s.minX }
func (s shape) height() float64 { return s.maxY - s.minY }

// polygons reads one path's subpaths as closed polygons.
func polygons(d string) [][]point {
	sc := pathScanner{s: d}
	var (
		polys            [][]point
		cur              []point
		pos, start, ctrl point
		prev             byte
		cubic, quadratic bool
	)
	flush := func() {
		if len(cur) > 2 {
			polys = append(polys, cur)
		}
		cur = nil
	}
	lineTo := func(p point) {
		if len(cur) == 0 {
			cur = append(cur, pos)
		}
		cur = append(cur, p)
		pos = p
	}
	curveTo := func(c1, c2, end point) {
		p0 := pos
		for i := 1; i <= curveSteps; i++ {
			t := float64(i) / curveSteps
			u := 1 - t
			lineTo(point{
				u*u*u*p0.x + 3*u*u*t*c1.x + 3*u*t*t*c2.x + t*t*t*end.x,
				u*u*u*p0.y + 3*u*u*t*c1.y + 3*u*t*t*c2.y + t*t*t*end.y,
			})
		}
		ctrl = c2
	}
	quadTo := func(c, end point) {
		p0 := pos
		curveTo(point{p0.x + 2*(c.x-p0.x)/3, p0.y + 2*(c.y-p0.y)/3}, point{end.x + 2*(c.x-end.x)/3, end.y + 2*(c.y-end.y)/3}, end)
		ctrl = c
	}
	for {
		cmd, ok := sc.command()
		if !ok {
			break
		}
		rel := cmd >= 'a'
		at := func(x, y float64) point {
			if rel {
				return point{pos.x + x, pos.y + y}
			}
			return point{x, y}
		}
		upper := cmd &^ 0x20
		cubic, quadratic = prev == 'C' || prev == 'S', prev == 'Q' || prev == 'T'
		switch upper {
		case 'Z':
			flush()
			pos = start
		case 'M':
			first := true
			for {
				n, ok := sc.numbers(2)
				if !ok {
					break
				}
				if first {
					flush()
					pos = at(n[0], n[1])
					start, first = pos, false
					continue
				}
				lineTo(at(n[0], n[1]))
			}
		case 'L':
			for n, ok := sc.numbers(2); ok; n, ok = sc.numbers(2) {
				lineTo(at(n[0], n[1]))
			}
		case 'H':
			for n, ok := sc.numbers(1); ok; n, ok = sc.numbers(1) {
				x := n[0]
				if rel {
					x += pos.x
				}
				lineTo(point{x, pos.y})
			}
		case 'V':
			for n, ok := sc.numbers(1); ok; n, ok = sc.numbers(1) {
				y := n[0]
				if rel {
					y += pos.y
				}
				lineTo(point{pos.x, y})
			}
		case 'C':
			for n, ok := sc.numbers(6); ok; n, ok = sc.numbers(6) {
				curveTo(at(n[0], n[1]), at(n[2], n[3]), at(n[4], n[5]))
			}
		case 'S':
			for n, ok := sc.numbers(4); ok; n, ok = sc.numbers(4) {
				c1 := pos
				if cubic {
					c1 = point{2*pos.x - ctrl.x, 2*pos.y - ctrl.y}
				}
				curveTo(c1, at(n[0], n[1]), at(n[2], n[3]))
				cubic = true
			}
		case 'Q':
			for n, ok := sc.numbers(4); ok; n, ok = sc.numbers(4) {
				quadTo(at(n[0], n[1]), at(n[2], n[3]))
			}
		case 'T':
			for n, ok := sc.numbers(2); ok; n, ok = sc.numbers(2) {
				c := pos
				if quadratic {
					c = point{2*pos.x - ctrl.x, 2*pos.y - ctrl.y}
				}
				quadTo(c, at(n[0], n[1]))
				quadratic = true
			}
		default:
			flush()
			return polys
		}
		prev = upper
	}
	flush()
	return polys
}

// pathScanner reads SVG path data, including the compact forms the logo is
// written in: "1.5.5" is 1.5 then .5, and "3-4" is 3 then -4.
type pathScanner struct {
	s string
	i int
}

func (p *pathScanner) skip() {
	for p.i < len(p.s) && strings.IndexByte(" ,\t\r\n", p.s[p.i]) >= 0 {
		p.i++
	}
}

func (p *pathScanner) command() (byte, bool) {
	p.skip()
	if p.i < len(p.s) {
		if c := p.s[p.i]; c|0x20 >= 'a' && c|0x20 <= 'z' && c|0x20 != 'e' {
			p.i++
			return c, true
		}
	}
	return 0, false
}

// numbers reads n numbers, or none: a group cut short leaves the scanner
// where it was.
func (p *pathScanner) numbers(n int) ([]float64, bool) {
	mark := p.i
	out := make([]float64, 0, n)
	for range n {
		v, ok := p.number()
		if !ok {
			p.i = mark
			return nil, false
		}
		out = append(out, v)
	}
	return out, true
}

func (p *pathScanner) number() (float64, bool) {
	p.skip()
	start := p.i
	if p.i < len(p.s) && (p.s[p.i] == '-' || p.s[p.i] == '+') {
		p.i++
	}
	digits, dot := false, false
	for ; p.i < len(p.s); p.i++ {
		c := p.s[p.i]
		if c >= '0' && c <= '9' {
			digits = true
		} else if c == '.' && !dot {
			dot = true
		} else {
			break
		}
	}
	if digits && p.i < len(p.s) && (p.s[p.i] == 'e' || p.s[p.i] == 'E') {
		j := p.i + 1
		if j < len(p.s) && (p.s[j] == '-' || p.s[j] == '+') {
			j++
		}
		if j < len(p.s) && p.s[j] >= '0' && p.s[j] <= '9' {
			for p.i = j; p.i < len(p.s) && p.s[p.i] >= '0' && p.s[p.i] <= '9'; p.i++ {
			}
		}
	}
	if !digits {
		p.i = start
		return 0, false
	}
	v, err := strconv.ParseFloat(p.s[start:p.i], 64)
	return v, err == nil
}

// samples is how many points per side each dot is sampled at.
const samples = 4

// dots lays a grid w dots wide over the shape, keeping the shape's aspect
// ratio for dots tall times as high as they are wide, and inks every dot at
// least threshold covered. A row boundary falls on the y anchor: on a
// baseline, the overshoot of round letters below it covers too little of a
// dot to ink one, where a row straddling the baseline inked a nub under every
// bowl. top is the y the grid's first row starts at.
func (s shape) dots(w int, tall, threshold, anchor float64) (grid [][]bool, top float64) {
	scale := float64(w) / s.width()
	row := tall / scale
	top = anchor - math.Ceil((anchor-s.minY)/row-1e-9)*row
	h := max(1, int(math.Ceil((s.maxY-top)/row-1e-9)))
	count := make([][]int, h)
	for y := range count {
		count[y] = make([]int, w)
	}
	type crossing struct {
		x       float64
		winding int
	}
	var xs []crossing
	for sy := range h * samples {
		y := top + (float64(sy)+0.5)*row/samples
		xs = xs[:0]
		for _, e := range s.edges {
			if y >= e.y0 && y < e.y1 {
				xs = append(xs, crossing{e.x0 + (y-e.y0)*(e.x1-e.x0)/(e.y1-e.y0), e.winding})
			}
		}
		sort.Slice(xs, func(i, j int) bool { return xs[i].x < xs[j].x })
		winding, k := 0, 0
		for sx := range w * samples {
			x := s.minX + (float64(sx)+0.5)/(scale*samples)
			for ; k < len(xs) && xs[k].x <= x; k++ {
				winding += xs[k].winding
			}
			if winding != 0 {
				count[sy/samples][sx/samples]++
			}
		}
	}
	grid = make([][]bool, h)
	for y := range grid {
		grid[y] = make([]bool, w)
		for x := range grid[y] {
			grid[y][x] = float64(count[y][x]) >= threshold*samples*samples
		}
	}
	return grid, top
}

// quadrant is the block character for each 2x2 pattern: bit 1 upper left,
// 2 upper right, 4 lower left, 8 lower right.
var quadrant = []rune(" ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█")

// quadrants packs a dot grid into block characters two dots wide and two
// tall. A terminal cell is twice as tall as it is wide, so the grid's dots
// have to be laid out twice as tall as wide (dots with tall = 2).
func quadrants(grid [][]bool) []string {
	if len(grid) == 0 {
		return nil
	}
	cols, rows := (len(grid[0])+1)/2, (len(grid)+1)/2
	lines := make([]string, rows)
	for r := range rows {
		var b strings.Builder
		for c := range cols {
			bits := 0
			for i, at := range [4][2]int{{0, 0}, {0, 1}, {1, 0}, {1, 1}} {
				y, x := r*2+at[0], c*2+at[1]
				if y < len(grid) && x < len(grid[y]) && grid[y][x] {
					bits |= 1 << i
				}
			}
			b.WriteRune(quadrant[bits])
		}
		lines[r] = b.String()
	}
	return lines
}
