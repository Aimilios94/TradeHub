"""The number check: every figure Hermes writes in digits must be a number it was given.

Rules (plan, "Hard rules"):
- A written figure matches an input number when they differ by at most half a step of the last
  decimal shown: "2.3%" matches 2.31 or 2.34, not 2.36; "2%" matches 1.5 to 2.49.
- Units scale the figure: 1.2B is 1,200,000,000, with a step of 0.1B.
- A figure written without a sign may match a negative input ("fell 1.2%" for -1.2). A figure
  written with a sign must match the sign.
- Allowed without a match: bare whole counts up to 10 (not a percentage or an amount of
  money), and ordinals (1st, 25th).
- Years and dates are ordinary numbers here: they pass only if the input holds them.
- Digits that are part of a name (Q3, F-150, 10-K is read as the count 10) are not figures.

Everything else fails. Tuning continues in Phase 2, tracked by eval_numbers.py.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

MAX_FREE_COUNT = 10

_UNITS = {
    "k": Decimal(10) ** 3,
    "thousand": Decimal(10) ** 3,
    "m": Decimal(10) ** 6,
    "mn": Decimal(10) ** 6,
    "million": Decimal(10) ** 6,
    "b": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "billion": Decimal(10) ** 9,
    "t": Decimal(10) ** 12,
    "tn": Decimal(10) ** 12,
    "trillion": Decimal(10) ** 12,
}
_NUMBER = re.compile(
    r"""
    (?<![\w.,])(?<![A-Za-z]-)          # not inside a word, a number or a name like F-150
    (?P<sign>[-+−](?=[\d$€£]))?
    (?P<currency>[$€£])?
    (?P<digits>\d{1,3}(?:,\d{3})+|\d+)
    (?:\.(?P<decimals>\d+))?
    (?P<ordinal>st|nd|rd|th)?
    (?:\s?(?P<unit>thousand|million|billion|trillion|bn|mn|tn|[kmbt])(?!\w))?
    (?P<percent>%)?
    (?!\w)
    """,
    re.VERBOSE | re.IGNORECASE,
)


@dataclass(frozen=True)
class Figure:
    text: str
    value: Decimal  # scaled by its unit, signed as written
    step: Decimal  # one unit of the last decimal shown, scaled
    signed: bool
    is_ordinal: bool
    is_plain_integer: bool  # a bare whole number: no decimals, unit, currency or percent


def extract_figures(text: str) -> list[Figure]:
    figures = []
    for match in _NUMBER.finditer(text):
        decimals = match["decimals"] or ""
        scale = _UNITS[match["unit"].lower()] if match["unit"] else Decimal(1)
        value = Decimal(match["digits"].replace(",", "") + ("." + decimals if decimals else ""))
        negative = match["sign"] in ("-", "−")
        figures.append(
            Figure(
                text=match.group(0).strip(),
                value=(-value if negative else value) * scale,
                step=Decimal(10) ** -len(decimals) * scale,
                signed=match["sign"] is not None,
                is_ordinal=match["ordinal"] is not None,
                is_plain_integer=not (
                    decimals or match["unit"] or match["currency"] or match["percent"]
                ),
            )
        )
    return figures


def _matches(figure: Figure, known: Decimal) -> bool:
    written = figure.value
    if not figure.signed:
        written, known = abs(written), abs(known)
    half = figure.step / 2
    return -half <= known - written < half


def check_numbers(
    output: str, input_text: str, input_numbers: tuple[float | int | Decimal, ...] = ()
) -> list[str]:
    """Figures in `output` that match nothing in the input. An empty list means the text passes."""
    known = [figure.value for figure in extract_figures(input_text)]
    known += [Decimal(str(number)) for number in input_numbers]
    failures = []
    for figure in extract_figures(output):
        if figure.is_ordinal:
            continue
        if figure.is_plain_integer and not figure.signed and figure.value <= MAX_FREE_COUNT:
            continue
        if not any(_matches(figure, number) for number in known):
            failures.append(figure.text)
    return failures
