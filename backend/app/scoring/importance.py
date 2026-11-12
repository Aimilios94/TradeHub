"""Importance score for a headline: 0 to 100, computed by code from five signals and one penalty.

    catalyst   0-1   the title names a company event (earnings, deal, lawsuit ...)
    coverage   0-1   how many stored copies tell the same story (the item plus its duplicates)
    mention    0/1   the title itself names one of the item's tickers
    driver     0-1   the tagger found a macro driver (1 with a direction, 0.5 without)
    breadth    0-1   how many watchlist tickers the sources attached
    low_value  0/1   listicle or opinion wording ("3 stocks to buy", "should you buy")

The weights are starting hypotheses (DECISIONS D46). They are checked against a hand-ranked day
once a month (plan: signal checks), and a change is a new VERSION. Time is not part of the
score: ranking a day sorts by score, then by publication time.
"""

import re
from dataclasses import dataclass

VERSION = "importance-v1"

WEIGHTS = {
    "catalyst": 0.30,
    "coverage": 0.25,
    "mention": 0.20,
    "driver": 0.15,
    "breadth": 0.10,
}
LOW_VALUE_PENALTY = 0.30
COVERAGE_FULL_AT = 4  # copies of a story at which coverage is 1
BREADTH_FULL_AT = 3  # watchlist tickers at which breadth is 1


def _compile(*patterns: str) -> re.Pattern:
    return re.compile(r"(?<!\w)(?:" + "|".join(patterns) + r")(?!\w)", re.IGNORECASE)


# A hint for ranking only. The catalyst type that is stored comes from the condenser.
CATALYST_HINTS: tuple[tuple[str, float, re.Pattern], ...] = (
    (
        "earnings",
        1.0,
        _compile(
            r"earnings",
            r"quarterly (results|report|profit|revenue)",
            r"q[1-4] (results|earnings|revenue|profit|sales)",
            r"(first|second|third|fourth)[- ]quarter",
            r"\beps\b",
            r"(beats?|tops?|miss(es)?|misses) (\w+ )?(estimates|expectations|forecasts)",
        ),
    ),
    (
        "guidance",
        1.0,
        _compile(
            r"guidance",
            r"(raises?|lifts?|boosts?|cuts?|lowers?|slashes?|trims?) ([\w-]+ ){0,3}"
            r"(forecast|outlook|target)s?",
            r"(profit|revenue|sales) warning",
            r"warns (of|on|that)",
        ),
    ),
    (
        "m_and_a",
        1.0,
        _compile(
            r"acquir\w+",
            r"acquisition",
            r"merger|merges?",
            r"takeover",
            r"buyout",
            r"to buy \w+( \w+){0,4} for \$",
            r"spin-?off",
            r"divest\w*",
        ),
    ),
    (
        "legal_regulatory",
        0.8,
        _compile(
            r"lawsuit|sues?|sued",
            r"settle(s|d|ment)",
            r"probe|investigat\w+",
            r"(?-i:\bSEC\b|\bFTC\b|\bDOJ\b|\bNHTSA\b|\bFDA\b)",
            r"antitrust",
            r"recalls?",
            r"fine[sd]",
            r"regulators?",
            r"court|ruling|judge|jury|verdict",
            r"subpoena",
        ),
    ),
    (
        "management",
        0.7,
        _compile(
            r"(?-i:\bCEO\b|\bCFO\b|\bCOO\b)",
            r"steps? down|resigns?|ousted",
            r"(appoints?|names?) (\w+ ){0,3}(chief|ceo|cfo|chair\w*|president)",
            r"pay package|compensation (plan|package)",
            r"board of directors",
        ),
    ),
    (
        "macro",
        0.7,
        _compile(
            r"(?-i:\bFed\b)|fomc|federal reserve",
            r"\bcpi\b|inflation",
            r"jobs report|payrolls",
            r"\bgdp\b|recession",
            r"tariffs?",
            r"treasury yields?",
        ),
    ),
    (
        "analyst_rating",
        0.6,
        _compile(
            r"upgrades?|upgraded|downgrades?|downgraded",
            r"price target",
            r"(initiates?|starts?|resumes?) coverage",
            r"overweight|underweight|outperform|underperform",
        ),
    ),
    (
        "product",
        0.5,
        _compile(
            r"launch(es|ed)?",
            r"unveils?|unveiled",
            r"rollout|rolls out",
            r"deliver(y|ies) (numbers|figures|data|record|rise|fall|jump|drop)\w*",
            r"production (ramp|starts|begins|halt\w*)",
        ),
    ),
)

LOW_VALUE = _compile(
    r"\d+ (\w+ ){0,3}(stocks?|etfs?|reasons?|things|ways|picks)",
    r"stocks? to (buy|watch|own|sell|avoid|hold)",
    r"should you (buy|sell|invest)",
    r"is it (too late|time) to",
    r"(a |the )?better (buy|stock|etf)",
    r"prediction:",
    r"here'?s (why|what|how)",
    r"millionaire",
    r"could (soar|double|triple|make you|turn)",
    r"if you('d| had) invested",
    r"buy and hold",
    r"forever",
    r"passive income",
    r"retire(ment)?",
    r"no-brainer",
    r"my top|i'm buying|i would buy",
    r"smartest",
    r"\w+ vs\.? \w+",
    r"wall street analysts",
    r"bullish or bearish",
    r"where will [\w ]+ be in \d+ years?",
)


@dataclass(frozen=True)
class Importance:
    score: float  # 0 to 100, two decimals
    components: dict


def catalyst_hint(title: str) -> tuple[str | None, float]:
    """The strongest catalyst the title wording points to, and its weight. First wins on ties."""
    best: tuple[str | None, float] = (None, 0.0)
    for name, weight, pattern in CATALYST_HINTS:
        if weight > best[1] and pattern.search(title):
            best = (name, weight)
    return best


def mentions_any(title: str, names: list[str]) -> bool:
    """True when the title holds one of the names: a symbol in capitals or a company name."""
    for name in names:
        if not name:
            continue
        flags = 0 if name.isupper() else re.IGNORECASE
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", title, flags):
            return True
    return False


def score_item(
    title: str,
    *,
    copies: int,
    watchlist_tickers: int,
    ticker_names: list[str],
    tag_directions: list[int],
) -> Importance:
    """Score one shown headline.

    copies: the item plus its duplicates. ticker_names: symbols and company names of the item's
    tickers. tag_directions: the direction of each driver tag on the item.
    """
    hint, catalyst = catalyst_hint(title)
    coverage = min(max(copies, 1) - 1, COVERAGE_FULL_AT - 1) / (COVERAGE_FULL_AT - 1)
    mention = 1.0 if mentions_any(title, ticker_names) else 0.0
    driver = 0.0
    if tag_directions:
        driver = 1.0 if any(tag_directions) else 0.5
    breadth = min(max(watchlist_tickers, 1) - 1, BREADTH_FULL_AT - 1) / (BREADTH_FULL_AT - 1)
    low_value = 1.0 if LOW_VALUE.search(title) else 0.0

    parts = {
        "catalyst": catalyst,
        "coverage": coverage,
        "mention": mention,
        "driver": driver,
        "breadth": breadth,
    }
    raw = sum(WEIGHTS[name] * value for name, value in parts.items())
    raw -= LOW_VALUE_PENALTY * low_value
    score = round(100 * min(max(raw, 0.0), 1.0), 2)
    components = {
        "version": VERSION,
        **{name: round(value, 4) for name, value in parts.items()},
        "low_value": low_value,
        "catalyst_hint": hint,
        "copies": copies,
        "watchlist_tickers": watchlist_tickers,
    }
    return Importance(score, components)
