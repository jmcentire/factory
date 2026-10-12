"""A bounded excerpt of process output keeps both ends and says what it dropped.

Diagnostics are bounded so a runaway process cannot fill the evidence store. Cutting the tail
is the wrong bound: a failing tool states its command and context first and its reason last
(the exception, the refused path, the exit summary), so a head-only cut keeps the boilerplate
and drops the answer, and two different failures read the same. The bound is in UTF-8 bytes
because the limit protects bytes on disk; a character count lets multi-byte text pass several
times the limit.
"""

from __future__ import annotations

# The tail carries the reason, so it gets the larger share of what is kept.
_HEAD_SHARE_NUMERATOR = 1
_HEAD_SHARE_DENOMINATOR = 3


def bounded_excerpt(text: str, max_bytes: int) -> str:
    """``text`` itself when it fits in ``max_bytes`` UTF-8 bytes; otherwise its head and tail
    around a marker naming how many bytes were omitted, never more than ``max_bytes``."""

    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    widest_marker = _marker(len(raw))
    budget = max_bytes - len(widest_marker.encode("utf-8"))
    if budget <= 0:
        raise ValueError(f"max_bytes={max_bytes} cannot hold an omission marker")
    head = raw[: budget * _HEAD_SHARE_NUMERATOR // _HEAD_SHARE_DENOMINATOR].decode(
        "utf-8", errors="ignore"
    )
    tail_bytes = budget - len(head.encode("utf-8"))
    tail = raw[len(raw) - tail_bytes :].decode("utf-8", errors="ignore")
    omitted = len(raw) - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}{_marker(omitted)}{tail}"


def _marker(omitted: int) -> str:
    return f"\n[... {omitted} bytes omitted ...]\n"
