"""Metering the answer tool's own synthesis spend.

The two failures worth testing are the ones that produce a number rather than
an error: a meter that saw nothing and reports $0.00, and a model the price
table does not know, which reports the same.
"""

import pytest

from answer_eval.token_meter import TokenMeter, TokenMeterError, meter_synthesis


class Response:
    def __init__(self, input_tokens=100, output_tokens=50, cached_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cached_tokens = cached_tokens


class FakeProvider:
    provider_name = "gemini"
    model_name = "gemini-3.1-flash-lite-preview"

    def __init__(self, response=None):
        self.response = response or Response()
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        return self.response


class TestTotals:
    def test_tokens_accumulate_across_calls(self):
        meter = TokenMeter()
        meter.record(Response(100, 50), "m")
        meter.record(Response(10, 5), "m")
        assert (meter.calls, meter.input_tokens, meter.output_tokens) == (2, 110, 55)

    def test_a_response_with_no_usage_counts_as_zero_rather_than_raising(self):
        """Some providers omit usage. That is a gap, not a crash - the
        did-anything-happen check is what catches a meter of all zeroes."""
        meter = TokenMeter()
        meter.record(object(), "m")
        assert (meter.calls, meter.input_tokens) == (1, 0)

    def test_every_model_that_answered_is_recorded(self):
        meter = TokenMeter()
        meter.record(Response(), "one")
        meter.record(Response(), "two")
        assert meter.as_dict()["models"] == ["one", "two"]


class TestCost:
    def test_cost_comes_from_the_products_own_price_table(self):
        meter = TokenMeter(calls=1, input_tokens=1000, output_tokens=1000)
        # $0.00025/1k in + $0.0015/1k out
        assert meter.usd("gemini-3.1-flash-lite-preview", "gemini") == pytest.approx(0.00175)

    def test_an_unpriced_model_raises_rather_than_costing_nothing(self):
        meter = TokenMeter(calls=1, input_tokens=1_000_000, output_tokens=1_000_000)
        with pytest.raises(TokenMeterError, match="no price for model"):
            meter.usd("some-new-model", "acme")

    def test_a_genuinely_free_provider_costs_zero_without_raising(self):
        meter = TokenMeter(calls=1, input_tokens=1000, output_tokens=1000)
        assert meter.usd("whatever-local-model", "ollama") == 0.0


class TestMeteringTheRegistry:
    async def test_a_provider_taken_from_the_registry_is_counted(self):
        from repowise.core.providers.llm import registry

        provider = FakeProvider()
        original = registry.get_provider
        registry.get_provider = lambda *a, **kw: provider
        try:
            with meter_synthesis() as meter:
                got = registry.get_provider("gemini")
                await got.generate(system_prompt="s", user_prompt="u")
        finally:
            registry.get_provider = original

        assert meter.calls == 1
        assert meter.input_tokens == 100

    async def test_the_registry_is_restored_afterwards(self):
        from repowise.core.providers.llm import registry

        original = registry.get_provider
        with meter_synthesis(expect_calls=False):
            pass
        assert registry.get_provider is original

    def test_metering_nothing_raises_rather_than_reporting_a_free_run(self):
        """If the tool stops resolving through the registry, every cost is
        silently zero. That has to be the loud failure, not the quiet one."""
        with pytest.raises(TokenMeterError, match="no synthesis calls"):
            with meter_synthesis():
                pass

    async def test_wrapping_the_same_provider_twice_does_not_double_count(self):
        from repowise.core.providers.llm import registry

        provider = FakeProvider()
        original = registry.get_provider
        registry.get_provider = lambda *a, **kw: provider
        try:
            with meter_synthesis() as meter:
                registry.get_provider("gemini")
                registry.get_provider("gemini")
                await provider.generate(system_prompt="s", user_prompt="u")
        finally:
            registry.get_provider = original

        assert meter.calls == 1

    async def test_more_than_one_model_under_one_meter_is_warned_about(self, caplog):
        from repowise.core.providers.llm import registry

        original = registry.get_provider
        providers = [FakeProvider(), FakeProvider()]
        providers[1].model_name = "gemini-3-flash-preview"
        registry.get_provider = lambda *a, **kw: providers.pop(0)
        try:
            with caplog.at_level("WARNING"), meter_synthesis() as meter:
                first = registry.get_provider("gemini")
                second = registry.get_provider("gemini")
                await first.generate(system_prompt="s", user_prompt="u")
                await second.generate(system_prompt="s", user_prompt="u")
        finally:
            registry.get_provider = original

        assert meter.calls == 2
        assert "more than one model" in caplog.text
