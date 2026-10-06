"""Model profiles: named bindings of every factory role to the agent and model that runs it.

Models come from the operator (SOFTWARE-FACTORY.md §6): the factory never picks, downloads or
installs one. A profile is where the operator names them once, per role, instead of exporting a
variable per seat before every run. An operator keeps as many profiles as they like (a local
one, a hosted one, a cheap one for small work) in one file, marks one the default, and picks
another for a single run with ``factory.sh --profile <name>``.

The invariant: **a profile binds every role, and nothing fills a gap.** A role with no agent or
no model is a malformed profile, refused when the file is read, never completed from a default.
``factory.sh`` writes the profile it used into the run as ``model-profile.json``, so every lane
launched later in that run uses the same bindings even if the operator edits the file meanwhile.

The file holds no secrets: an agent's credentials stay with the agent.

Stdlib only, and no imports from this package: ``make doctor`` loads this file by path on the
bootstrap interpreter, before the virtualenv exists. The harness reaches it through
``harness/model_profile.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = "factory-model-profiles/1"
SNAPSHOT_VERSION = "factory-model-profile/1"
ROLES: tuple[str, ...] = ("validator", "orchestrator", "coder", "tester")

# The agents each launch script can start: factory.sh starts the Validator and the Orchestrator,
# tmux_lane.sh the Coder and the Tester. tests/test_model_profiles.py holds these equal to the
# scripts' own case lists, so a profile can never name a seat the harness cannot launch.
LAUNCHABLE: Mapping[str, tuple[str, ...]] = {
    "validator": ("claude", "codex", "ollama"),
    "orchestrator": ("agy", "codex"),
    "coder": ("codex", "codex-ollama"),
    "tester": ("codex", "codex-ollama"),
}

# Agents whose CLI takes no model argument. The profile still names the model; the run records
# that the operator selects it inside the agent, not that the factory passed it.
MODEL_SET_IN_AGENT = frozenset({"agy"})

# Agents backed by a local Ollama model, which must already be on the machine.
OLLAMA_AGENTS = frozenset({"ollama", "codex-ollama"})

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# The model charset harness/model_availability.sh accepts. Lane commands splice the model into
# an argument list, so the charset is also what keeps it a single shell word.
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")


class ProfileError(ValueError):
    """A profiles file, a profile or a request for one that the factory refuses."""


@dataclass(frozen=True)
class Binding:
    agent: str
    model: str


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    roles: Mapping[str, Binding]

    def document(self) -> dict[str, object]:
        body: dict[str, object] = {
            "roles": {
                role: {"agent": self.roles[role].agent, "model": self.roles[role].model}
                for role in ROLES
            }
        }
        if self.description:
            body["description"] = self.description
        return body

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            {"name": self.name, **self.document()}, sort_keys=True, separators=(",", ":")
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Profiles:
    default: str | None
    profiles: Mapping[str, Profile]

    def document(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "default": self.default,
            "profiles": {name: self.profiles[name].document() for name in sorted(self.profiles)},
        }


def default_path(environ: Mapping[str, str] | None = None) -> Path:
    """``$FACTORY_PROFILES``, else ``${XDG_CONFIG_HOME:-~/.config}/factory/profiles.json``."""

    env = os.environ if environ is None else environ
    if env.get("FACTORY_PROFILES"):
        return Path(env["FACTORY_PROFILES"]).expanduser()
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME", "~")) / ".config")
    return Path(base).expanduser() / "factory" / "profiles.json"


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise ProfileError(
            f"profile name {name!r} must be lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return name


def _validate_binding(role: str, agent: str, model: str) -> Binding:
    """One role's binding, refused unless the harness can launch it with a named model."""

    if role not in LAUNCHABLE:
        raise ProfileError(f"unknown role {role!r}; roles are {', '.join(ROLES)}")
    if agent not in LAUNCHABLE[role]:
        raise ProfileError(
            f"{role}: agent {agent!r} cannot be launched for this role; "
            f"choose one of {', '.join(LAUNCHABLE[role])}"
        )
    if not MODEL_RE.fullmatch(model):
        raise ProfileError(
            f"{role}: model {model!r} must be named, 1-128 characters of letters, digits "
            "and . _ : / -; the factory never picks one"
        )
    return Binding(agent, model)


def parse_binding(role: str, text: str) -> Binding:
    """``AGENT:MODEL``. The agent never contains ':', so a model such as ``glm-5:cloud`` may."""

    agent, sep, model = text.partition(":")
    if not sep:
        raise ProfileError(f"{role}: expected AGENT:MODEL, got {text!r}")
    return _validate_binding(role, agent, model)


def _profile(name: str, raw: object) -> Profile:
    validate_name(name)
    if not isinstance(raw, dict) or not set(raw) <= {"description", "roles"}:
        raise ProfileError(f"profile {name!r} must hold only 'roles' and an optional 'description'")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ProfileError(f"profile {name!r}: description must be a string")
    roles = raw.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise ProfileError(f"profile {name!r} must bind every role: {', '.join(ROLES)}")
    bindings = {}
    for role in ROLES:
        entry = roles[role]
        if not isinstance(entry, dict) or set(entry) != {"agent", "model"}:
            raise ProfileError(f"profile {name!r}: {role} needs exactly 'agent' and 'model'")
        agent, model = entry["agent"], entry["model"]
        if not isinstance(agent, str) or not isinstance(model, str):
            raise ProfileError(f"profile {name!r}: {role} agent and model must be strings")
        try:
            bindings[role] = _validate_binding(role, agent, model)
        except ProfileError as error:
            raise ProfileError(f"profile {name!r}: {error}") from None
    return Profile(name, description, bindings)


def _parse(document: object) -> Profiles:
    if not isinstance(document, dict) or set(document) != {"schema_version", "default", "profiles"}:
        raise ProfileError("profiles file must hold exactly schema_version, default and profiles")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ProfileError(f"profiles file schema_version must be {SCHEMA_VERSION}")
    raw_profiles = document["profiles"]
    if not isinstance(raw_profiles, dict):
        raise ProfileError("profiles must be an object of name -> profile")
    profiles = {name: _profile(name, raw) for name, raw in raw_profiles.items()}
    default = document["default"]
    if default is not None and (not isinstance(default, str) or default not in profiles):
        raise ProfileError(f"default {default!r} names no profile in the file")
    return Profiles(default, profiles)


def load(path: Path) -> Profiles:
    """The profiles at ``path``; an absent file is no profiles, a malformed one is refused."""

    if not path.exists() and not path.is_symlink():
        return Profiles(None, {})
    if path.is_symlink() or not path.is_file():
        raise ProfileError(f"{path} is not a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProfileError(f"{path} is not readable JSON: {error}") from None
    try:
        return _parse(document)
    except ProfileError as error:
        raise ProfileError(f"{path}: {error}") from None


def save(path: Path, profiles: Profiles) -> None:
    """Replace the file atomically. The directory is the operator's alone."""

    _parse(profiles.document())  # never write what load would refuse
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(profiles.document(), indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".profiles-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def resolve(path: Path, name: str = "", environ: Mapping[str, str] | None = None) -> Profile | None:
    """The profile a run uses: ``name``, else ``$FACTORY_PROFILE``, else the file's default.

    None only when nothing was asked for and the operator has no profiles at all; the caller
    then keeps its pre-profile behaviour. Asking for a profile that does not exist is refused.
    """

    env = os.environ if environ is None else environ
    wanted = name or env.get("FACTORY_PROFILE", "")
    profiles = load(path)
    if not profiles.profiles:
        if wanted:
            raise ProfileError(
                f"no model profiles at {path}; create one with `factory profile create`"
            )
        return None
    chosen = wanted or profiles.default
    if chosen is None:
        raise ProfileError(
            f"{path} has profiles but no default; pass --profile or run `factory profile use`"
        )
    if chosen not in profiles.profiles:
        raise ProfileError(
            f"no model profile named {chosen!r} in {path} "
            f"(have: {', '.join(sorted(profiles.profiles))})"
        )
    return profiles.profiles[chosen]


def _snapshot(profile: Profile, source: Path) -> dict[str, object]:
    """What a run records about the profile it was ignited with."""

    return {
        "schema_version": SNAPSHOT_VERSION,
        "name": profile.name,
        "digest": profile.digest,
        "source": str(source),
        "roles": {
            role: {
                "agent": profile.roles[role].agent,
                "model": profile.roles[role].model,
                "model_passing": (
                    "set-in-agent"
                    if profile.roles[role].agent in MODEL_SET_IN_AGENT
                    else "command-line"
                ),
            }
            for role in ROLES
        },
    }


def read_snapshot(path: Path) -> dict[str, Binding]:
    """The bindings a run recorded. Refused unless the file is the shape ``snapshot`` writes."""

    if path.is_symlink() or not path.is_file():
        raise ProfileError(f"{path} is not a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProfileError(f"{path} is not readable JSON: {error}") from None
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != SNAPSHOT_VERSION
        or not isinstance(document.get("roles"), dict)
        or set(document["roles"]) != set(ROLES)
    ):
        raise ProfileError(f"{path} is not a {SNAPSHOT_VERSION} snapshot")
    bindings = {}
    for role in ROLES:
        entry = document["roles"][role]
        if not isinstance(entry, dict):
            raise ProfileError(f"{path}: {role} binding is malformed")
        bindings[role] = _validate_binding(role, str(entry.get("agent")), str(entry.get("model")))
    return bindings


def prompt_profile(
    ask: Callable[[str], str],
    say: Callable[[str], None],
    *,
    name: str = "",
    description: str = "",
) -> Profile:
    """Build a profile by asking for every binding. Nothing is suggested: the operator names
    each agent and each model, and an answer the harness could not launch is asked again."""

    while not name:
        try:
            name = validate_name(ask("Profile name (for example local or hosted): ").strip())
        except ProfileError as error:
            say(str(error))
    roles = {}
    for role in ROLES:
        choices = LAUNCHABLE[role]
        while True:
            agent = ask(f"{role.capitalize()} agent ({' | '.join(choices)}): ").strip()
            if agent not in choices:
                say(f"choose one of: {', '.join(choices)}")
                continue
            note = " (selected inside agy with /model; recorded, not passed)" if (
                agent in MODEL_SET_IN_AGENT
            ) else ""
            model = ask(f"{role.capitalize()} model{note}: ").strip()
            try:
                roles[role] = _validate_binding(role, agent, model)
                break
            except ProfileError as error:
                say(str(error))
    return Profile(name, description, roles)


def write_snapshot(profile: Profile, source: Path, target: Path) -> None:
    """Record ``profile`` in a run. Replaced, never followed: a symlink there is refused."""

    if target.is_symlink():
        raise ProfileError(f"refusing a symlinked model profile snapshot: {target}")
    payload = json.dumps(_snapshot(profile, source), indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".model-profile-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
