"""Metering what the answer tool spends on synthesis.

The answer tool has no token accounting of its own: nothing captures ``usage``
from the provider response, and no cost row is written anywhere. Every cost
figure quoted about it so far has been inferred from the *agent* side - how
many tool calls and file reads a caller needed afterwards - which is the
number that matters commercially but says nothing about what the tool itself
costs to run.

This meters it from outside, for the length of an eval run. The seam is
``registry.get_provider``: the answer path imports it inside the resolver
function on every call, so replacing the module attribute is enough, and each
provider that comes back has its ``generate`` wrapped to add up the tokens it
reports.

**This is the eval's number, not the tool's.** It measures synthesis by one
model at one set of published rates, from a local index, and it is not the
cache-neutral dollars-per-question a caller pays. Reported under its own name
so the two cannot be confused.

Two ways this could quietly report nothing, both refused: a run that answered
questions while metering zero calls, and a model the price table does not
know, which would otherwise cost exactly $0.00 per question.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Providers that genuinely cost nothing per token, so a zero rate is a fact
#: rather than a missing row in the price table.
FREE_PROVIDERS = frozenset({"mock", "ollama", "codex_cli", "opencode", "litellm"})


class TokenMeterError(RuntimeError):
    """A cost figure that would be wrong rather than merely absent."""


@dataclass
class TokenMeter:
    """Running totals for one metered stretch."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    models: set[str] = field(default_factory=set)
    """Every model that answered under the meter.

    More than one means the run mixed models and a single cost figure over it
    would be an average of two different prices."""

    def record(self, response: Any, model: str) -> None:
        self.calls += 1
        self.input_tokens += int(getattr(response, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(response, "output_tokens", 0) or 0)
        self.cached_tokens += int(getattr(response, "cached_tokens", 0) or 0)
        if model:
            self.models.add(model)

    def usd(self, model: str, provider: str) -> float:
        """Cost of everything metered, at the product's own price table.

        Uses the same table the product quotes costs from, so an eval figure
        and a product figure cannot drift apart. A model the table does not
        know raises rather than costing nothing.
        """
        from repowise.core.cost_estimator.pricing import _lookup_cost

        input_rate, output_rate = _lookup_cost(model)
        if (input_rate, output_rate) == (0.0, 0.0) and provider not in FREE_PROVIDERS:
            raise TokenMeterError(
                f"no price for model {model!r} (provider {provider!r}), so its cost would "
                "be reported as $0.00 per question. Add it to the price table or record "
                "the run without a cost figure."
            )
        return (self.input_tokens * input_rate + self.output_tokens * output_rate) / 1000

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "models": sorted(self.models),
        }


@contextmanager
def meter_synthesis(*, expect_calls: bool = True):
    """Count synthesis tokens for the duration of the block.

    ``expect_calls`` false skips the did-anything-happen check, for callers
    that legitimately meter a stretch with no model in it.
    """
    from repowise.core.providers.llm import registry

    original = registry.get_provider
    meter = TokenMeter()

    def counting_get_provider(*args, **kwargs):
        provider = original(*args, **kwargs)
        _wrap(provider, meter)
        return provider

    registry.get_provider = counting_get_provider
    try:
        yield meter
    finally:
        registry.get_provider = original

    if expect_calls and meter.calls == 0:
        raise TokenMeterError(
            "metered no synthesis calls at all. Either the answer tool stopped "
            "resolving its provider through the registry - in which case every cost "
            "figure from this meter is silently zero - or nothing was asked."
        )
    if len(meter.models) > 1:
        logger.warning(
            "metered more than one model (%s); one cost figure over them is an average "
            "of different prices",
            ", ".join(sorted(meter.models)),
        )


def _wrap(provider: Any, meter: TokenMeter) -> None:
    """Replace ``provider.generate`` with a counting version, once."""
    if getattr(provider, "_answer_eval_metered", False):
        return
    inner = provider.generate
    model = getattr(provider, "model_name", "") or ""

    async def counting_generate(*args, **kwargs):
        response = await inner(*args, **kwargs)
        meter.record(response, model)
        return response

    try:
        provider.generate = counting_generate
        provider._answer_eval_metered = True
    except AttributeError:
        # A provider that refuses attribute assignment would meter zero and
        # look free. Better to say so than to publish a $0.00 per question.
        raise TokenMeterError(
            f"cannot meter provider {type(provider).__name__}: it does not allow "
            "wrapping generate, so its spend would be reported as zero"
        ) from None
