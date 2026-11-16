"""Start-up for the Factory's own Python helpers, as the lane launchers run them.

``tmux_lane.sh`` and ``tmux_lane_message.sh`` put this directory first on ``PYTHONPATH`` and
set ``PYTHONSAFEPATH``, for every helper they start, inline or from a file, and in the
rebuilt environments they start the lane wrapper and its watcher from. So each begins here.
The invariant: a trusted helper imports nothing by way of the directory it happens to be
run in. Safe-path mode keeps the working directory itself off the import path; this keeps
every other relative entry off it as well (an editable install leaves a placeholder entry,
which Python reads against the working directory if a directory of that name is there).
The launchers are run from wherever the operator is, and the wrapper runs in a lane's
clone, which the lane can write: a module found there would run outside the lane's
boundary.

An interpreter may ship a ``sitecustomize`` of its own (Homebrew's does, to name its
site-packages). This module stands in front of it, so that one is run from here, found
only among the absolute entries that remain, and never from a relative one.
"""

import importlib.machinery
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _absolute() -> None:
    sys.path[:] = [entry for entry in sys.path if os.path.isabs(entry)]


_absolute()
_NEXT = importlib.machinery.PathFinder.find_spec(
    "sitecustomize", [entry for entry in sys.path if os.path.abspath(entry) != _HERE])
if _NEXT is not None and _NEXT.loader is not None:
    _NEXT.loader.exec_module(importlib.util.module_from_spec(_NEXT))
    _absolute()
