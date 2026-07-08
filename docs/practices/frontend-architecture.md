# Front-end architecture — a factory practice

**Status:** recommended architecture for any front-end the factory produces or reviews.
Target-agnostic: it names no product, client, or domain; it describes *how* a front end is
structured so that it stays legible, testable, and safe to change. It pairs with the FE↔BE
contract discipline the core already owns (`factory_core/contract.py`) and the Change-Surface
Audit (`docs/practices/change-surface-audit.md`).

The through-line: **a front end is three separable concerns — design, structure, and data —
and the whole point of good architecture is to keep them separable.** When they bleed
together, every change touches everything, and nothing is safe to move.

---

## 1. Separate design from structure from data (the CSS Zen Garden approach)

Treat the classic CSS Zen Garden demonstration as the north star: the *same* semantic HTML
document can be given radically different visual designs purely by swapping the stylesheet,
because the markup carries **meaning and structure**, not appearance.

- **Structure (HTML / templates)** describes *what a thing is* — a document, a list, a form,
  a region, a control — using semantic elements and stable, meaningful class/attribute hooks.
  It does not encode how the thing looks.
- **Design (CSS)** describes *how it looks* — spacing, color, type, layout — and attaches to
  the structure's hooks. Reskinning is a stylesheet change, not a markup rewrite.
- **Data (content / state)** is what fills the structure. It comes from the server or an API
  and is rendered *into* the structure, never hardcoded into the presentation layer.

**Acceptance signal:** you can restyle a page end-to-end by editing only CSS, and you can
change the data source without touching either the markup's meaning or its styling. If a
visual change forces markup surgery, or a copy change forces a CSS change, the concerns have
bled together — that is the defect to fix.

---

## 2. Progressive enhancement — server-rendered completion paths first

**Every task a user needs to complete must be completable with server-rendered HTML alone.**
JavaScript is used *only* to make an already-working experience more convenient — it is never
a requirement for correctness or for completing a task.

- **The baseline is a full, server-rendered completion path.** Forms submit and produce a
  server-rendered result; navigation works via real links and real responses; the task can be
  finished with no client script running at all. This is what makes a front end robust,
  scriptable, testable without a browser engine, and usable by automated agents and
  assistive tech.
- **JavaScript is an enhancement layer on top.** It may improve latency, add inline
  validation, or smooth an interaction — but if it fails to load or is disabled, the
  underlying server-rendered path still completes the task. Nothing essential lives *only* in
  a client script.
- **No behavior hides behind untrusted or inline script.** Prefer deferred, same-origin
  external scripts as optional aid. A strict content-security posture (no inline script/style
  execution) is a natural fit and is worth adopting.

**Acceptance signal (a gate worth writing):** disable JavaScript and every user-facing task
still completes against a server-rendered response. A test that asserts "there exists a
server-rendered completion path and no task *requires* client script" turns this principle
into an enforced invariant rather than a hope.

This is the front-end half of the same contract discipline the core enforces on the wire: a
completion path that only *appears* to work in a scripted browser but has no server-rendered
backing is the UI analogue of a caller with no provider — a contract break. See
`factory_core/contract.py` (forward/reverse FE↔BE contract).

---

## 3. Let CSS cascade from fixed containers; scope widget definitions

Define components against **fixed containers** and let styles cascade *within* that container,
rather than scattering appearance rules across global selectors that reach anywhere.

- **A widget owns a container.** Give each component a well-named container (a scoping class,
  a custom element, a layer, or a nesting root). The widget's rules are written relative to
  that container and cascade down into it. Layout flexibility (fluid sizing, wrapping,
  responsive behavior) lives *inside* the container's contract, so a widget can flex without
  leaking rules outward.
- **The container is the contract boundary.** What is inside cascades and can flex; what is
  outside is unaffected. This makes a widget movable and reusable: drop the container
  anywhere and it renders correctly, because it does not depend on ambient global state.
- **Prefer composition over deep, brittle selector chains.** A rule that depends on a long
  ancestor chain is a rule that breaks when the DOM is rearranged. Scope to the container and
  compose.

**Acceptance signal:** moving a widget's container to a different part of the page does not
change how the widget looks, and does not disturb anything around it.

---

## 4. Be judicious about global values

Globals are a shared surface — the most expensive kind to change safely, because every
consumer inherits them. Treat them the way the factory treats any shared object (see the
Change-Surface Audit): default to *scoped*, opt in to *global*, and keep the global set small
and deliberate.

- **Prefer scoped tokens and container-local values** over a sprawling global namespace. A
  handful of curated design tokens (color, spacing, type scale) exposed as named variables is
  good; hundreds of ad-hoc global rules that any component silently depends on is a liability.
- **A global is a commitment.** Adding one means every present *and future* consumer inherits
  it. Before promoting a value to global scope, ask the Change-Surface Audit questions: who
  consumes it now, who will inherit it by default later, and how would someone diagnose a
  regression it causes? If the answer is "everyone, silently," scope it instead.
- **Name and document the intentional globals.** The small set you *do* keep global (the token
  layer, the reset, the base typography) should be explicit and reviewable, not accreted.

**Acceptance signal:** the global surface is small, named, and documented; new appearance
lives in scoped tokens or container-local rules by default, and promoting something to global
is a deliberate, reviewed act.

---

## How this rides the rails the factory already has

- **FE↔BE contract (`factory_core/contract.py`).** Progressive enhancement makes the
  server-rendered path the source of truth; the forward/reverse contract check proves every
  caller edge resolves to a real provider and every provider is either called or excused. The
  two together catch the "it works only in the scripted happy path" class of break.
- **Change-Surface Audit (`docs/practices/change-surface-audit.md`).** The globals discipline
  is a direct application: a shared-surface change (a global token, a base stylesheet, a
  container contract) enumerates its consumers and classifies each HELD-INVARIANT or
  INTENTIONALLY-CHANGED, with a locking or new-contract test.
- **Completeness (`factory_core/completeness.py`).** "There is a server-rendered completion
  path for every task" is a falsifiable inventory row, not a vibe — it can be enumerated and
  proved like any other completeness claim.
