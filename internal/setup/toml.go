package setup

import (
	"fmt"
	"os"
	"slices"
	"strings"

	"github.com/BurntSushi/toml"
)

// Codex keeps its user configuration in TOML with many tables the installer
// does not own. Appending text is not safe: a second `[features]` header is a
// duplicate-key error and Codex refuses to start. These helpers edit a TOML
// file table-by-table, preserve everything else byte for byte, and refuse to
// write a result the TOML parser rejects.

// tomlDoc is a line-oriented view of a TOML file: a preamble (root keys and
// comments) followed by tables in file order. Formatting inside untouched
// tables is preserved exactly.
type tomlDoc struct {
	preamble []string
	tables   []tomlTable
}

type tomlTable struct {
	header string // exact header line, e.g. `[features]` or `[mcp_servers.kindex]`
	name   string // normalised table name, e.g. `features`
	lines  []string
}

func parseTOMLDoc(src string) tomlDoc {
	var d tomlDoc
	var cur *tomlTable
	for _, line := range strings.Split(strings.TrimRight(src, "\n"), "\n") {
		trim := strings.TrimSpace(line)
		if strings.HasPrefix(trim, "[") && strings.HasSuffix(trim, "]") && !strings.HasPrefix(trim, "[[") {
			d.tables = append(d.tables, tomlTable{header: line, name: tomlTableName(trim)})
			cur = &d.tables[len(d.tables)-1]
			continue
		}
		if cur == nil {
			d.preamble = append(d.preamble, line)
		} else {
			cur.lines = append(cur.lines, line)
		}
	}
	return d
}

func tomlTableName(header string) string {
	return strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(header, "["), "]"))
}

func (d tomlDoc) String() string {
	var b strings.Builder
	for _, l := range d.preamble {
		b.WriteString(l + "\n")
	}
	for _, t := range d.tables {
		b.WriteString(t.header + "\n")
		for _, l := range t.lines {
			b.WriteString(l + "\n")
		}
	}
	return b.String()
}

func (d *tomlDoc) table(name string) *tomlTable {
	for i := range d.tables {
		if d.tables[i].name == name {
			return &d.tables[i]
		}
	}
	return nil
}

// setKey sets key = value inside table, creating the table at the end of the
// file when absent and replacing an existing assignment in place. value is
// written verbatim, so callers pass TOML syntax (`true`, `"str"`, `["a"]`).
func (d *tomlDoc) setKey(table, key, value string) (changed bool) {
	t := d.table(table)
	if t == nil {
		if len(d.tables) > 0 || len(d.preamble) > 0 {
			d.tables = append(d.tables, tomlTable{header: "", name: "", lines: nil}) // spacer
			d.tables[len(d.tables)-1] = tomlTable{header: "\n[" + table + "]", name: table}
		} else {
			d.tables = append(d.tables, tomlTable{header: "[" + table + "]", name: table})
		}
		t = &d.tables[len(d.tables)-1]
	}
	assign := key + " = " + value
	for i, l := range t.lines {
		k, _, ok := strings.Cut(strings.TrimSpace(l), "=")
		if ok && strings.TrimSpace(k) == key {
			if strings.TrimSpace(l) == assign {
				return false
			}
			t.lines[i] = assign
			return true
		}
	}
	// Insert before trailing blank lines so the table stays compact.
	insertAt := len(t.lines)
	for insertAt > 0 && strings.TrimSpace(t.lines[insertAt-1]) == "" {
		insertAt--
	}
	t.lines = append(t.lines[:insertAt], append([]string{assign}, t.lines[insertAt:]...)...)
	return true
}

// ensureTable adds a whole table when no table of that name exists. An
// existing table is left exactly as the user wrote it.
func (d *tomlDoc) ensureTable(name string, body []string) (changed bool) {
	if d.table(name) != nil {
		return false
	}
	header := "[" + name + "]"
	if len(d.tables) > 0 || len(d.preamble) > 0 {
		header = "\n" + header
	}
	d.tables = append(d.tables, tomlTable{header: header, name: name, lines: body})
	return true
}

// replaceTable makes the table named name hold exactly body, adding it when
// absent.
func (d *tomlDoc) replaceTable(name string, body []string) (changed bool) {
	t := d.table(name)
	if t == nil {
		return d.ensureTable(name, body)
	}
	// Blank lines at the end keep separating it from the next table.
	trail := 0
	for trail < len(t.lines) && strings.TrimSpace(t.lines[len(t.lines)-1-trail]) == "" {
		trail++
	}
	want := append(append([]string{}, body...), t.lines[len(t.lines)-trail:]...)
	if slices.Equal(t.lines, want) {
		return false
	}
	t.lines = want
	return true
}

// editTOML applies fn to the file at path and writes the result only when it
// changed and still parses as TOML. A missing file starts empty.
func (e *Env) editTOML(path string, fn func(d *tomlDoc) bool) error {
	src, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if len(src) > 0 {
		var probe map[string]any
		if _, err := toml.Decode(string(src), &probe); err != nil {
			return fmt.Errorf("%s does not parse as TOML; fix it before the installer edits it: %w", path, err)
		}
	}
	d := parseTOMLDoc(string(src))
	if !fn(&d) {
		return nil
	}
	out := d.String()
	var probe map[string]any
	if _, err := toml.Decode(out, &probe); err != nil {
		return fmt.Errorf("refusing to write %s: the edited result does not parse: %w", path, err)
	}
	return e.WriteConfigFile(path, []byte(out), 0o600)
}
