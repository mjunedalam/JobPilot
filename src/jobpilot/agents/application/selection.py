"""Parse the user's job selection at checkpoint H3: "all", "1,3,5-8", "none"."""

from __future__ import annotations

import re

_RANGE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


class SelectionError(ValueError):
    pass


def parse_selection(text: str, count: int) -> list[int]:
    """Return 1-based positions in the order given, without duplicates."""
    t = text.strip().casefold()
    if t in ("all", "*"):
        return list(range(1, count + 1))
    if t in ("none", ""):
        return []
    picked: list[int] = []
    for part in (p.strip() for p in t.replace(" ", ",").split(",") if p.strip()):
        if part.isdigit():
            numbers = [int(part)]
        elif m := _RANGE.match(part):
            low, high = int(m.group(1)), int(m.group(2))
            if low > high:
                raise SelectionError(f"range {part} is backwards")
            numbers = list(range(low, high + 1))
        else:
            raise SelectionError(f"'{part}' is not a number or range like 5-8")
        for n in numbers:
            if not 1 <= n <= count:
                raise SelectionError(f"#{n} is not in the list (1-{count})")
            if n not in picked:
                picked.append(n)
    return picked
