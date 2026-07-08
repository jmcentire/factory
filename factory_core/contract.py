"""factory_core.contract — neutral path normalization + forward/reverse contract diff.

This is the generic extraction of the "does every caller reach a real provider, and does
every provider have a caller (or an explicit excuse)?" discipline proven on the first
consuming target. The target implementation scans that target's route modules and its
provider specs directly and hardcodes that target's excuse categories. Here, none of that
scanning is present: the caller/provider **inventories are inputs**, surfaced through the
adapter seams (``RepoAdapter.caller_edges`` / ``RepoAdapter.provider_operations``), and the
excuse rules are **data** (a list of ``ExcuseRule`` the target supplies). This core owns only
the target-agnostic parts: path normalization, the set-difference diffs, and the rule engine
that applies the supplied rules.

What is generic (here) vs. what is per-target data:

  * Generic (this module): collapsing a concrete path to a comparable template
    (``{param}`` segments -> ``{}``, query/fragment stripped, trailing slash trimmed); the
    forward diff (caller edges whose normalized (method, path) has no provider -> breaks);
    the reverse diff (provider operations with no caller -> orphans); and the rule engine
    that buckets each orphan by the first matching supplied rule.
  * Per-target data (supplied through the seams): the actual caller edges and provider
    operations (the target scans its own source and specs), and the ``ExcuseRule`` set that
    says which path/method/tag patterns are internal-by-design vs. a user-facing gap. This
    core names no route, no service, no gateway method, and no excuse category.

Posture (matching ``manifest.py`` / ``invariant_kernel.py``): stdlib only, side-effect free
at import, no clock, no disk-reading, no target contact. The scanning is impurity that lives
behind the adapter seams; this module never scans a target.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# Path normalization (pure string logic, no framework assumption)
# --------------------------------------------------------------------------- #

_PARAM_SEGMENT = re.compile(r"\{[^}]*\}")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://[^/]*")


def normalize_path(path: str) -> str:
    """Collapse a concrete route/endpoint path into a comparable template.

    Pure string logic, target-agnostic:

      * a leading ``scheme://host`` is stripped (so an absolute URL compares to a bare path);
      * a query string (``?...``) and fragment (``#...``) are dropped;
      * every ``{param}`` segment collapses to ``{}`` (so ``/x/{id}`` and ``/x/{userId}``
        compare equal);
      * a trailing slash is trimmed (but the root ``/`` is preserved).

    The empty result collapses to ``/`` so a bare host or empty path is still a valid key.
    """
    text = path.strip()
    text = _SCHEME.sub("", text, count=1)
    text = text.split("#", 1)[0]
    text = text.split("?", 1)[0]
    text = _PARAM_SEGMENT.sub("{}", text)
    text = text.rstrip("/")
    return text or "/"


def normalize_method(method: str) -> str:
    """Uppercase and trim an HTTP-style method label. A blank method normalizes to ""."""
    return method.strip().upper()


# --------------------------------------------------------------------------- #
# Neutral inventory records (what the adapter seams return)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Endpoint:
    """One provider operation a backend declares: a (method, path) it will serve.

    ``provider`` groups operations (a service/module name in the target's terms; opaque to
    the core). ``raw_path`` preserves the un-normalized path for reporting and for the
    excuse rules to match against; ``tags`` are opaque labels the target may attach to help
    its excuse rules classify (the core assigns them no meaning). ``location`` is a free-form
    source pointer for diagnostics.
    """

    method: str
    path: str
    provider: str = ""
    raw_path: str = ""
    tags: frozenset[str] = frozenset()
    location: str = ""

    @property
    def key(self) -> tuple[str, str]:
        """The comparable identity: normalized (method, path)."""
        return (normalize_method(self.method), normalize_path(self.path))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Endpoint:
        return cls(
            method=str(raw.get("method", "")),
            path=str(raw.get("path", "")),
            provider=str(raw.get("provider", "")),
            raw_path=str(raw.get("raw_path", "") or raw.get("path", "")),
            tags=frozenset(_clean_strs(_as_tuple(raw.get("tags")))),
            location=str(raw.get("location", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"method": normalize_method(self.method), "path": self.path}
        if self.provider:
            out["provider"] = self.provider
        if self.raw_path and self.raw_path != self.path:
            out["raw_path"] = self.raw_path
        if self.tags:
            out["tags"] = sorted(self.tags)
        if self.location:
            out["location"] = self.location
        return out


@dataclass(frozen=True)
class CallEdge:
    """One caller edge a frontend/client declares: a (method, path) it will call.

    The core resolves the edge against the provider set by normalized (method, path). An edge
    whose ``path`` could not be statically resolved is represented with an empty ``path`` and
    surfaces as ``unresolved`` (never silently dropped, never counted as a match).
    """

    method: str
    path: str
    caller: str = ""
    location: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.path.strip())

    @property
    def key(self) -> tuple[str, str]:
        return (normalize_method(self.method), normalize_path(self.path))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CallEdge:
        return cls(
            method=str(raw.get("method", "")),
            path=str(raw.get("path", "")),
            caller=str(raw.get("caller", "")),
            location=str(raw.get("location", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"method": normalize_method(self.method), "path": self.path}
        if self.caller:
            out["caller"] = self.caller
        if self.location:
            out["location"] = self.location
        return out


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _clean_strs(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(s for s in (str(v).strip() for v in values) if s)


# --------------------------------------------------------------------------- #
# Data-driven excuse classifier (the RULES are input, not hardcoded categories)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ExcuseRule:
    """One target-supplied rule that buckets an orphan endpoint.

    A rule matches an :class:`Endpoint` when every predicate it sets is satisfied:

      * ``path_prefix`` — the endpoint's ``raw_path`` starts with this string;
      * ``path_contains`` — this substring appears anywhere in ``raw_path``;
      * ``path_suffix`` — the endpoint's ``raw_path`` ends with this string;
      * ``path_equals`` — the endpoint's ``raw_path`` equals one of these exact paths;
      * ``methods`` — the endpoint's normalized method is one of these;
      * ``providers`` — the endpoint's ``provider`` is one of these;
      * ``tags`` — the endpoint bears at least one of these tags.

    An empty predicate is not tested (so a rule with only ``providers`` matches any path from
    that provider). A rule with no predicates set matches nothing (fail-safe: a target cannot
    accidentally excuse everything with a blank rule).

    ``label`` is the bucket name the target chooses (opaque to the core). ``excused`` is the
    target's verdict: ``True`` means "internal-by-design / expected to lack a caller";
    ``False`` means "a real user-facing gap" — a labelled orphan that still counts against the
    contract. The core owns the matching; the target owns which patterns mean what.
    """

    label: str
    excused: bool = True
    path_prefix: str = ""
    path_contains: str = ""
    path_suffix: str = ""
    path_equals: frozenset[str] = frozenset()
    methods: frozenset[str] = frozenset()
    providers: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ExcuseRule:
        return cls(
            label=str(raw.get("label", "")).strip(),
            excused=bool(raw.get("excused", True)),
            path_prefix=str(raw.get("path_prefix", "")),
            path_contains=str(raw.get("path_contains", "")),
            path_suffix=str(raw.get("path_suffix", "")),
            path_equals=frozenset(_clean_strs(_as_tuple(raw.get("path_equals")))),
            methods=frozenset(
                normalize_method(m) for m in _clean_strs(_as_tuple(raw.get("methods")))
            ),
            providers=frozenset(_clean_strs(_as_tuple(raw.get("providers")))),
            tags=frozenset(_clean_strs(_as_tuple(raw.get("tags")))),
        )

    def _has_predicate(self) -> bool:
        return bool(
            self.path_prefix
            or self.path_contains
            or self.path_suffix
            or self.path_equals
            or self.methods
            or self.providers
            or self.tags
        )

    def matches(self, endpoint: Endpoint) -> bool:
        """True iff every set predicate holds. A rule with no predicates matches nothing."""
        if not self._has_predicate():
            return False
        raw_path = endpoint.raw_path or endpoint.path
        if self.path_prefix and not raw_path.startswith(self.path_prefix):
            return False
        if self.path_contains and self.path_contains not in raw_path:
            return False
        if self.path_suffix and not raw_path.endswith(self.path_suffix):
            return False
        if self.path_equals and raw_path not in self.path_equals:
            return False
        if self.methods and normalize_method(endpoint.method) not in self.methods:
            return False
        if self.providers and endpoint.provider not in self.providers:
            return False
        if self.tags and not (self.tags & endpoint.tags):
            return False
        return True


#: The label the classifier assigns to an orphan that no supplied rule matches. It is treated
#: as a user-facing gap (unexcused) — the fail-closed default, so an unclassified orphan
#: cannot silently be excused.
UNCLASSIFIED_LABEL = "unclassified"


@dataclass(frozen=True)
class Classification:
    """The classifier's verdict for one orphan endpoint."""

    endpoint: Endpoint
    label: str
    excused: bool

    def to_dict(self) -> dict[str, Any]:
        return {"endpoint": self.endpoint.to_dict(), "label": self.label, "excused": self.excused}


@dataclass(frozen=True)
class ExcuseClassifier:
    """Applies an ordered list of target-supplied :class:`ExcuseRule` to an orphan.

    The FIRST rule that matches wins (order is the target's precedence). An orphan matched by
    no rule is ``UNCLASSIFIED_LABEL`` and ``excused=False`` — a user-facing gap by default, so
    the absence of a rule never hides an orphan.
    """

    rules: tuple[ExcuseRule, ...] = ()

    @classmethod
    def from_dicts(cls, raws: Iterable[Mapping[str, Any]]) -> ExcuseClassifier:
        return cls(rules=tuple(ExcuseRule.from_dict(r) for r in raws))

    def classify(self, endpoint: Endpoint) -> Classification:
        for rule in self.rules:
            if rule.matches(endpoint):
                return Classification(endpoint=endpoint, label=rule.label, excused=rule.excused)
        return Classification(endpoint=endpoint, label=UNCLASSIFIED_LABEL, excused=False)


# --------------------------------------------------------------------------- #
# Forward / reverse contract diff (pure set-diff over normalized inventories)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ForwardReport:
    """The forward contract: every caller edge should target a real provider operation.

    ``breaks`` are resolved caller edges whose normalized (method, path) is in no provider —
    the hard failures (a call to an endpoint that does not exist). ``unresolved`` are edges the
    adapter could not statically resolve (empty path); they are reported separately, never
    counted as a match and never counted as a break.
    """

    breaks: tuple[CallEdge, ...] = ()
    unresolved: tuple[CallEdge, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.breaks

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "breaks": [e.to_dict() for e in self.breaks],
            "unresolved": [e.to_dict() for e in self.unresolved],
        }


@dataclass(frozen=True)
class ReverseReport:
    """The reverse contract: every provider operation should have a caller, or be excused.

    ``orphans`` are provider operations with no resolved caller, each carried with its
    :class:`Classification`. ``unexcused`` is the residual: orphans the supplied rules did not
    mark excused — the real product gaps. ``excused`` is informational (internal-by-design).
    """

    orphans: tuple[Classification, ...] = ()

    @property
    def excused(self) -> tuple[Classification, ...]:
        return tuple(c for c in self.orphans if c.excused)

    @property
    def unexcused(self) -> tuple[Classification, ...]:
        return tuple(c for c in self.orphans if not c.excused)

    @property
    def ok(self) -> bool:
        return not self.unexcused

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "orphans": [c.to_dict() for c in self.orphans],
            "unexcused": [c.to_dict() for c in self.unexcused],
        }


def forward_contract(
    calls: Iterable[CallEdge], endpoints: Iterable[Endpoint]
) -> ForwardReport:
    """Diff caller edges against provider operations.

    Pure set logic over normalized keys: a resolved caller edge whose (method, path) key is in
    no provider is a break; an unresolved edge is set aside. No target contact.
    """
    provider_keys = {ep.key for ep in endpoints}
    breaks: list[CallEdge] = []
    unresolved: list[CallEdge] = []
    for edge in calls:
        if not edge.resolved:
            unresolved.append(edge)
        elif edge.key not in provider_keys:
            breaks.append(edge)
    breaks.sort(key=lambda e: (e.location, normalize_method(e.method), normalize_path(e.path)))
    unresolved.sort(key=lambda e: (e.location, normalize_method(e.method)))
    return ForwardReport(breaks=tuple(breaks), unresolved=tuple(unresolved))


def reverse_contract(
    endpoints: Iterable[Endpoint],
    calls: Iterable[CallEdge],
    classifier: ExcuseClassifier | None = None,
) -> ReverseReport:
    """Diff provider operations against caller edges, classifying each orphan by the rules.

    Pure set logic: a provider operation whose (method, path) key is matched by no resolved
    caller is an orphan; the (data-driven) classifier buckets each orphan as excused
    (internal-by-design) or unexcused (a user-facing gap). No target contact.
    """
    classifier = classifier if classifier is not None else ExcuseClassifier()
    called_keys = {edge.key for edge in calls if edge.resolved}
    orphans: list[Classification] = []
    for endpoint in endpoints:
        if endpoint.key not in called_keys:
            orphans.append(classifier.classify(endpoint))
    orphans.sort(
        key=lambda c: (
            c.endpoint.provider,
            normalize_path(c.endpoint.path),
            normalize_method(c.endpoint.method),
        )
    )
    return ReverseReport(orphans=tuple(orphans))


@dataclass(frozen=True)
class ContractReport:
    """Both directions of the FE<->BE contract over one pair of neutral inventories."""

    forward: ForwardReport
    reverse: ReverseReport

    @property
    def ok(self) -> bool:
        return self.forward.ok and self.reverse.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "forward": self.forward.to_dict(),
            "reverse": self.reverse.to_dict(),
        }


def check_contract(
    calls: Iterable[CallEdge],
    endpoints: Iterable[Endpoint],
    classifier: ExcuseClassifier | None = None,
) -> ContractReport:
    """Run both directions. Inputs are neutral inventories (from the adapter seams); the
    classifier rules are per-target data. The core never scans a target."""
    call_list = tuple(calls)
    endpoint_list = tuple(endpoints)
    return ContractReport(
        forward=forward_contract(call_list, endpoint_list),
        reverse=reverse_contract(endpoint_list, call_list, classifier),
    )
