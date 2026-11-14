"""How the Factory's own Python programs start, and what stays the provider's alone.

The invariant: nothing a lane can write, and nothing an operator configured for the
provider, decides what a trusted program loads when it starts. Trusted programs are the
launchers' helpers, the lane wrapper, its watcher, a hosted session's host and message
command, the boundary program, the Codex session driver and the hook commands a provider is
given to run. Several of them start in a lane's clone, or are started by the provider
itself, and all of them run outside any boundary.

Two ways of starting are used, and a program is given exactly one:

- From a launcher's shell, or from an environment a launcher rebuilds: ``TRUSTED_STARTUP``.
  Safe-path mode keeps the working directory off the import path and
  ``harness/trusted_startup`` runs first and drops every other relative entry.
- Everywhere a variable could not be relied on (a child of the wrapper, which is handed the
  provider's environment; a command the provider runs from its own settings): the
  interpreter's isolated, no-site flags, ``ISOLATED``. No ``PYTHON*`` variable is read, no
  site start-up runs, and the program names its own import path (``complete``).

A lane's ``--cache-env`` settings are the provider's. The launchers carry them in one
variable, ``PROVIDER_ENV``, so that no trusted program has any of them in its own
environment under its own name; ``provider_environment`` sets them at the last step, on
what the provider's program is given.
"""

from __future__ import annotations

import json
import os
import site
import sys
from collections.abc import Mapping

_HARNESS = os.path.dirname(os.path.abspath(__file__))

#: Interpreter flags for a trusted program that cannot rely on its environment.
ISOLATED = ("-I", "-S")
#: The start-up isolation the launchers give their helpers and the programs they start from
#: a rebuilt environment. For the Factory's own programs: a provider's is never given them.
TRUSTED_STARTUP = {"PYTHONSAFEPATH": "1",
                   "PYTHONPATH": f"{_HARNESS}/trusted_startup:{_HARNESS}"}
#: The one variable a lane's ``--cache-env`` settings travel in until the provider's program
#: is started: a JSON object of names and values.
PROVIDER_ENV = "FACTORY_LANE_PROVIDER_ENV"


def carried(environ: Mapping[str, str]) -> dict[str, str]:
    """The provider's own settings ``environ`` carries. Unreadable ones stop the start."""

    text = environ.get(PROVIDER_ENV, "")
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if not isinstance(value, dict) or not all(
            isinstance(name, str) and name.isidentifier() and isinstance(setting, str)
            for name, setting in value.items()):
        raise ValueError(f"{PROVIDER_ENV} does not hold the lane's provider settings")
    return dict(value)


def carry(settings: Mapping[str, str]) -> str:
    """``settings`` as the one value ``PROVIDER_ENV`` holds."""

    return json.dumps(dict(sorted(settings.items())), separators=(",", ":"))


def provider_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """What a provider's program is given, from a trusted program's own ``environ``.

    The trusted program's start-up isolation is removed (only the launcher's own values: a
    ``PYTHONPATH`` set some other way is some other value and is left alone), and the
    settings carried for the provider are set under their own names."""

    settings = carried(environ)
    given = {name: value for name, value in environ.items()
             if name != PROVIDER_ENV and TRUSTED_STARTUP.get(name) != value}
    given.update(settings)
    return given


def trusted_environment(given: Mapping[str, str], settings: Mapping[str, str]) -> dict[str, str]:
    """The environment of a trusted program that will itself start the provider's.

    ``given`` is what the provider's program is to have. Its carried ``settings`` go back
    into ``PROVIDER_ENV``; a name the lane's own file has since set to something else is no
    longer a carried setting and stays as it is. ``provider_environment`` over the result
    is ``given`` again."""

    kept = {name: value for name, value in settings.items() if given.get(name) == value}
    environment = {name: value for name, value in given.items() if name not in kept}
    if kept:
        environment[PROVIDER_ENV] = carry(kept)
    return environment


def isolated(command: list[str]) -> list[str]:
    """``command``, a program of this interpreter, started with ``ISOLATED``."""

    if command[:1] == [sys.executable] and command[1:2] != [ISOLATED[0]]:
        return [command[0], *ISOLATED, *command[1:]]
    return command


def complete() -> None:
    """The import path of a trusted program, called first by one that runs as a script.

    Started isolated, a program has only the standard library: this adds the harness, the
    checkout and the interpreter's site-packages directories as plain directories. No
    ``.pth`` file is processed and no ``sitecustomize`` or ``usercustomize`` is imported.
    However it was started, every relative entry is dropped: one is read against the
    working directory, which a lane can write."""

    if sys.flags.no_site:
        for entry in (_HARNESS, os.path.dirname(_HARNESS), *site.getsitepackages()):
            if entry not in sys.path and os.path.isdir(entry):
                sys.path.append(entry)
    sys.path[:] = [entry for entry in sys.path if os.path.isabs(entry)]
