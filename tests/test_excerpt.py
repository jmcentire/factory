"""A bounded excerpt keeps the reason a process failed, and the bound is in bytes.

Oracle: the contract in factory_runtime/excerpt.py, stated before the code — output that fits
is returned unchanged; output that does not is cut to at most the byte bound, keeps a true
prefix and a true suffix of the original, and names exactly how many bytes it omitted.
"""

from __future__ import annotations

import re

import pytest

from factory_runtime.excerpt import bounded_excerpt

_MARKER = re.compile(r"\n\[\.\.\. (\d+) bytes omitted \.\.\.\]\n")


def _split(excerpt: str) -> tuple[str, int, str]:
    found = _MARKER.search(excerpt)
    assert found is not None, excerpt
    return excerpt[: found.start()], int(found.group(1)), excerpt[found.end() :]


@pytest.mark.parametrize(
    "text",
    [
        "x" * 5000,
        "—" * 3000,
        "aé\U0001f600" * 700,
        "\n".join(f"line {number}: noise" for number in range(400)),
    ],
)
@pytest.mark.parametrize("limit", [64, 65, 100, 257, 1024, 4095])
def test_an_excerpt_is_a_bounded_prefix_and_suffix_that_names_what_it_dropped(
    text: str, limit: int
) -> None:
    excerpt = bounded_excerpt(text, limit)

    assert len(excerpt.encode("utf-8")) <= limit
    head, omitted, tail = _split(excerpt)
    assert text.startswith(head)
    assert text.endswith(tail)
    assert omitted == len(text.encode("utf-8")) - len(head.encode("utf-8")) - len(
        tail.encode("utf-8")
    )
    assert "�" not in excerpt


def test_output_that_fits_is_returned_unchanged_at_and_below_the_bound() -> None:
    text = "exact" * 20

    assert bounded_excerpt(text, len(text)) == text
    assert bounded_excerpt(text, len(text) + 1) == text
    assert bounded_excerpt("", 64) == ""


def test_the_last_line_survives_because_that_is_where_the_reason_is() -> None:
    noise = "".join(f"  File \"module_{number}.py\", line {number}\n" for number in range(300))
    output = f"Traceback (most recent call last):\n{noise}KeyError: 'refund_window_days'\n"

    excerpt = bounded_excerpt(output, 400)

    assert "KeyError: 'refund_window_days'" in excerpt
    assert excerpt.startswith("Traceback (most recent call last):")


def test_the_bound_is_bytes_so_multibyte_text_cannot_pass_several_times_over() -> None:
    # 1000 characters, 3000 bytes: a character-count bound of 1000 would keep all of it.
    text = "—" * 1000

    excerpt = bounded_excerpt(text, 1000)

    assert len(excerpt.encode("utf-8")) <= 1000
    assert _split(excerpt)[1] > 0


def test_a_bound_too_small_for_the_marker_is_refused() -> None:
    with pytest.raises(ValueError, match="omission marker"):
        bounded_excerpt("y" * 100, 10)
