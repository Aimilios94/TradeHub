"""Pydantic schemas for Hermes output. A value outside a fixed menu fails validation."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CatalystType = Literal[
    "earnings",
    "guidance",
    "analyst_rating",
    "m_and_a",
    "product",
    "legal_regulatory",
    "management",
    "macro",
    "other",
]
CATALYST_TYPES: tuple[str, ...] = CatalystType.__args__

Symbol = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9.]{1,10}$")]
Fact = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]


class CondenserOutput(BaseModel):
    """Template 1, the sentiment condenser: one object per news item."""

    model_config = ConfigDict(extra="forbid")

    # Field order is the order the model writes them in: the facts and the reason come first,
    # so the verdict fields are chosen after it has restated what the item says.
    key_facts: list[Fact] = Field(max_length=5)
    one_sentence_reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)
    ]
    catalyst_type: CatalystType
    tickers: list[Symbol] = Field(max_length=10)
    sentiment_score: float = Field(ge=-1, le=1)
    driver_direction: Literal[-1, 0, 1]
