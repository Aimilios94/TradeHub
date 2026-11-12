"""Driver tagger v1: keyword rules from rules.json, applied to a headline title.

Pure code, no database and no LLM. A tag's direction is +1 or -1 only when the rules that fired
agree; a topic match alone, or rules pointing both ways, give 0 (unclear), which the condenser
may fill in later. Tags and directions are checked against the fixed menu when the rules load.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

RULES_FILE = Path(__file__).with_name("rules.json")

DriverId = Literal[
    "geopolitical",
    "monetary",
    "macro_risk",
    "supply_flows",
    "positioning",
    "dollar",
    "liquidity_flush",
]
DRIVER_IDS: tuple[str, ...] = DriverId.__args__


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    driver: DriverId
    direction: Literal[-1, 0, 1]
    patterns: list[str]
    excludes: str | None = None
    scheduled: bool = False

    @model_validator(mode="after")
    def _menu(self):
        if self.driver == "liquidity_flush" and self.direction == -1:
            raise ValueError("liquidity_flush has no -1 direction")
        if not self.patterns:
            raise ValueError(f"rule {self.id} has no patterns")
        return self


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    note: str = ""
    rules: list[Rule]

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        return self


@dataclass(frozen=True)
class Tag:
    driver: str
    direction: int  # -1, 0 or +1
    scheduled: bool
    rules: tuple[str, ...]  # ids of the rules that fired, in file order


@dataclass(frozen=True)
class _Compiled:
    rule: Rule
    pattern: re.Pattern
    excludes: re.Pattern | None


def _compile(expression: str) -> re.Pattern:
    return re.compile(rf"(?<!\w)(?:{expression})(?!\w)", re.IGNORECASE)


class Tagger:
    def __init__(self, rule_set: RuleSet):
        self.version = rule_set.version
        self._rules = [
            _Compiled(
                rule,
                _compile("|".join(f"(?:{p})" for p in rule.patterns)),
                _compile(rule.excludes) if rule.excludes else None,
            )
            for rule in rule_set.rules
        ]

    def tag(self, title: str) -> list[Tag]:
        """Tags for one headline, in taxonomy order. No match gives an empty list."""
        fired: dict[str, list[Rule]] = {}
        for compiled in self._rules:
            if not compiled.pattern.search(title):
                continue
            if compiled.excludes and compiled.excludes.search(title):
                continue
            fired.setdefault(compiled.rule.driver, []).append(compiled.rule)
        tags = []
        for driver in DRIVER_IDS:
            rules = fired.get(driver)
            if not rules:
                continue
            directions = {rule.direction for rule in rules} - {0}
            tags.append(
                Tag(
                    driver=driver,
                    direction=directions.pop() if len(directions) == 1 else 0,
                    scheduled=any(rule.scheduled for rule in rules),
                    rules=tuple(rule.id for rule in rules),
                )
            )
        return tags


def load_rule_set(path: Path = RULES_FILE) -> RuleSet:
    return RuleSet.model_validate(json.loads(path.read_text(encoding="utf-8")))


@lru_cache
def default_tagger() -> Tagger:
    return Tagger(load_rule_set())
