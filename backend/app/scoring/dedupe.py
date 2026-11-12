"""Near-duplicate detection for headlines.

The same URL is already one row (news_items.dedupe_hash). This finds the same story stored under
different URLs: the three news feeds, and syndicated copies. Pure functions, no database.

Rule: two headlines published within WINDOW of each other are the same story when their
normalized titles are equal, or when one title's words are all in the other and the Jaccard
similarity of the two word sets is at least THRESHOLD ("Title" and "Title - Update").

Similarity alone is not enough. Checked on the first 1,563 recorded headlines (2026-11-12): at
0.8, 19 of the 20 pairs that differed by a swapped word were different stories written from
one template (the same question asked about another company), so a swapped word never makes a
duplicate.

The first-stored item (lowest id) stays; later ones point to it. An item is only ever compared
with items that are shown, never with another duplicate, so a chain of small differences cannot
drift into a different story.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

THRESHOLD = 0.8
WINDOW = timedelta(hours=72)
MIN_TOKENS_FOR_FUZZY = 5  # a shorter title must match exactly

_NOT_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class DedupeItem:
    id: int
    title: str
    at: datetime  # published_at, or ingested_at when the source gave no time


def normalize_title(title: str) -> str:
    """Lower case, accents and punctuation removed, single spaces."""
    decomposed = unicodedata.normalize("NFKD", title)
    text = "".join(char for char in decomposed if not unicodedata.combining(char)).lower()
    return _NOT_WORD.sub(" ", text).strip()


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(normalize_title(title).split())


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two word sets: shared words over all words."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_story(a: frozenset[str], b: frozenset[str]) -> bool:
    if a == b:
        return bool(a)
    if min(len(a), len(b)) < MIN_TOKENS_FOR_FUZZY:
        return False
    return (a <= b or b <= a) and similarity(a, b) >= THRESHOLD


def assign_duplicates(new: list[DedupeItem], shown: list[DedupeItem]) -> dict[int, int | None]:
    """For each new item: the id of the shown item it duplicates, or None when it is new.

    `shown` are items already stored that are not duplicates. New items are taken in id order,
    and a new item that is not a duplicate joins the shown set for the ones after it. When
    several shown items match, the most similar wins, then the lowest id.
    """
    pool = [(item, title_tokens(item.title)) for item in sorted(shown, key=lambda i: i.id)]
    result: dict[int, int | None] = {}
    for item in sorted(new, key=lambda i: i.id):
        tokens = title_tokens(item.title)
        best_id, best_score = None, 0.0
        for other, other_tokens in pool:
            if abs(item.at - other.at) > WINDOW or not same_story(tokens, other_tokens):
                continue
            score = similarity(tokens, other_tokens)
            if score > best_score:
                best_id, best_score = other.id, score
        result[item.id] = best_id
        if best_id is None:
            pool.append((item, tokens))
    return result
