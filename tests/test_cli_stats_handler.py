from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from cli.stats_handler import StatsCallbackHandler


def test_tracks_model_tool_calls_and_token_usage():
    handler = StatsCallbackHandler()
    handler.on_llm_start({}, ["prompt"])
    handler.on_chat_model_start({}, [[]])
    handler.on_tool_start({}, "input")
    message = AIMessage(content="ok", usage_metadata={
        "input_tokens": 12, "output_tokens": 7, "total_tokens": 19,
    })
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
    assert handler.get_stats() == {"llm_calls": 2, "tool_calls": 1, "tokens_in": 12, "tokens_out": 7}


def test_empty_or_missing_usage_does_not_change_totals():
    handler = StatsCallbackHandler()
    handler.on_llm_end(LLMResult(generations=[]))
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]]))
    assert handler.get_stats() == {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0}
