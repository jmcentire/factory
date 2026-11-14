"""Start-up for the Factory's own Python helpers, as the lane launchers run them.

``tmux_lane.sh`` and ``tmux_lane_message.sh`` put this directory first on ``PYTHONPATH`` and
set ``PYTHONSAFEPATH``, so every helper they start, inline or from a file, begins here. The
invariant: a trusted helper imports nothing by way of the directory it happens to be run
in. Safe-path mode keeps the working directory itself off the import path; this keeps
every other relative entry off it as well (an editable install leaves a placeholder entry,
which Python reads against the working directory if a directory of that name is there).
The launchers are run from wherever the operator is, and that can be a lane's clone, which
the lane can write: a module found there would run outside the lane's boundary.

Nothing else is done here, and nothing is imported beyond the two modules below.
"""

import os
import sys

sys.path[:] = [entry for entry in sys.path if os.path.isabs(entry)]
