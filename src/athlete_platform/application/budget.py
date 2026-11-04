"""Provider-neutral model spend gate contract and pricing; no database or provider dependency."""

from datetime import date
from decimal import ROUND_CEILING, Decimal
from math import ceil
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from athlete_platform.application.extraction import ExtractionUsage

MICRO_USD = Decimal("0.000001")
TOKENS_PER_MILLION = Decimal(1_000_000)
# Worst-case input estimate: one token per this many characters (denser than English prose).
CHARACTERS_PER_TOKEN = 3


class ModelPrice(BaseModel):
    """List price in USD per million tokens."""

    model_config = ConfigDict(frozen=True)
    input: Decimal = Field(gt=0)
    output: Decimal = Field(gt=0)


# Update this table whenever a provider changes prices or a route adds a model; the gate
# refuses to run an extractor whose model is missing, so a new model cannot spend unpriced.
# Source: docs/engineering/auth-and-extraction-model.md, section 2 (2026-10-29 list prices).
MODEL_PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(input=Decimal("4.00"), output=Decimal("20.00")),
    "claude-sonnet-5-5": ModelPrice(input=Decimal("2.00"), output=Decimal("10.00")),
    "claude-haiku-5-5": ModelPrice(input=Decimal("0.10"), output=Decimal("0.50")),
}


class UnpricedModel(Exception):
    """The model has no entry in MODEL_PRICES, so its spend cannot be bounded."""


class BudgetExhausted(Exception):
    """Reserving the worst-case cost would exceed the monthly budget; no provider call is made."""


def price_for(model: str) -> ModelPrice:
    try:
        return MODEL_PRICES[model]
    except KeyError:
        raise UnpricedModel(f"No price is configured for model {model!r}") from None


def _round_up(value: Decimal) -> Decimal:
    return value.quantize(MICRO_USD, rounding=ROUND_CEILING)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Cost in USD, rounded up to a micro-dollar."""
    price = price_for(model)
    return _round_up(
        (Decimal(input_tokens) * price.input + Decimal(output_tokens) * price.output)
        / TOKENS_PER_MILLION
    )


def worst_case_cost_usd(model: str, input_characters: int, max_output_tokens: int) -> Decimal:
    """Reservation amount: all input characters at 3 per token, plus the full output cap."""
    input_tokens = ceil(input_characters / CHARACTERS_PER_TOKEN)
    return cost_usd(model, input_tokens, max_output_tokens)


def usage_cost_usd(usage: ExtractionUsage) -> Decimal:
    return cost_usd(usage.model, usage.input_tokens, usage.output_tokens)


class Reservation(BaseModel):
    """An outstanding hold on the budget of one UTC month, keyed by job."""

    model_config = ConfigDict(frozen=True)
    job_id: UUID
    month: date
    reserved_usd: Decimal


class BudgetGate(Protocol):
    def reserve(self, job_id: UUID, *, provider: str, model: str, usd: Decimal) -> Reservation:
        """Hold `usd` for this job in the current UTC month, replacing the job's own hold.

        Raises BudgetExhausted when spent + held amounts of all jobs, including this hold,
        would exceed the monthly budget. Safe to repeat for the same job.
        """
        ...

    def reconcile(self, reservation: Reservation, usage: ExtractionUsage) -> None:
        """Replace the hold by the cost of the reported usage. A second call is a no-op."""
        ...

    def release(self, reservation: Reservation) -> None:
        """Drop the hold. Only for calls known not to have reached the provider's billing."""
        ...
