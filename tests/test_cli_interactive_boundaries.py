from types import SimpleNamespace

import pytest
import typer

from cli import prompts


def answer(monkeypatch, method, value):
    monkeypatch.setattr(prompts.questionary, method, lambda *args, **kwargs: SimpleNamespace(ask=lambda: value))


@pytest.mark.parametrize(("choice", "expected"), [(" aapl ", "AAPL"), (" ", "SPY")])
def test_ticker_prompt_normalizes_or_uses_default(monkeypatch, choice, expected):
    answer(monkeypatch, "text", choice)
    assert prompts.get_ticker() == expected


def test_ticker_prompt_cancel_exits(monkeypatch):
    answer(monkeypatch, "text", None)
    with pytest.raises(SystemExit):
        prompts.get_ticker()


@pytest.mark.parametrize("choice", [None, []])
def test_analyst_selection_requires_at_least_one(monkeypatch, choice):
    answer(monkeypatch, "checkbox", choice)
    with pytest.raises(SystemExit):
        prompts.select_analysts()


def test_analyst_selection_returns_selected_values(monkeypatch):
    answer(monkeypatch, "checkbox", ["market"])
    assert prompts.select_analysts() == ["market"]


@pytest.mark.parametrize(("function", "choice"), [
    (prompts.get_analysis_horizon, None), (prompts.select_research_depth, None),
    (prompts.select_llm_provider, None), (prompts.prompt_openai_compatible_url, None),
])
def test_cancelled_required_prompt_exits(monkeypatch, function, choice):
    method = "text" if function == prompts.prompt_openai_compatible_url else "select"
    answer(monkeypatch, method, choice)
    with pytest.raises((SystemExit, typer.Exit)):
        function()


def test_horizon_and_depth_choices_are_returned(monkeypatch):
    answer(monkeypatch, "select", "medium_3m")
    assert prompts.get_analysis_horizon() == "medium_3m"
    answer(monkeypatch, "select", 3)
    assert prompts.select_research_depth() == 3


def test_openai_compatible_url_is_trimmed(monkeypatch):
    answer(monkeypatch, "text", " https://example.org/v1 ")
    assert prompts.prompt_openai_compatible_url() == "https://example.org/v1"


def test_required_text_is_trimmed(monkeypatch):
    answer(monkeypatch, "text", " model-name ")
    assert prompts._require_text("model", "required") == "model-name"


def test_model_selection_routes_azure_and_live_providers(monkeypatch):
    monkeypatch.setitem(prompts.LISTED_LIVE, "test-live", lambda mode, default: f"{mode}/{default}")
    assert prompts._select_model("test-live", "quick", "model") == "quick/model"
    answer(monkeypatch, "text", " deployment-name ")
    assert prompts._select_model("azure", "deep") == "deployment-name"


@pytest.mark.parametrize("choice", [None, "custom", "gpt-6-sol"])
def test_model_selection_cancel_custom_or_listed(monkeypatch, choice):
    answer(monkeypatch, "select", choice)
    monkeypatch.setattr(prompts, "_prompt_custom_model_id", lambda: "my-model")
    if choice is None:
        with pytest.raises(SystemExit):
            prompts._select_model("openai", "quick")
    else:
        assert prompts._select_model("openai", "quick") == ("my-model" if choice == "custom" else choice)


def test_subscription_selection_cancel_exits(monkeypatch):
    answers = iter([prompts._SUBSCRIPTION, None])
    monkeypatch.setattr(prompts.questionary, "select", lambda *args, **kwargs: SimpleNamespace(ask=lambda: next(answers)))
    with pytest.raises(SystemExit):
        prompts.select_llm_provider()
