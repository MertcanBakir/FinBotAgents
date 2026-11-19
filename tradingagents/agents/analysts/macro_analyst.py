"""Macroeconomic analysis, one model invocation per report."""
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.agents.analyst_support.collection import (
    fetch_evidence,
    format_evidence,
    submit_context,
)
from tradingagents.agents.analyst_support.macro import compact_macro, macro_news_only, macro_windows
from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.agents.structured import NO_EXTERNAL_TOOLS
from tradingagents.agents.tools import get_global_news, get_macro_indicators


def create_macro_analyst(llm):
    """Collect macro evidence and generate a report with exactly one model invocation."""
    def macro_node(state):
        trade_date = state["trade_date"]
        with ThreadPoolExecutor(max_workers=7) as pool:
            jobs = {indicator: submit_context(pool,
                fetch_evidence, get_macro_indicators,
                indicator=indicator, curr_date=trade_date,
                look_back_days=days, trade_date=trade_date,
            ) for indicator, days in macro_windows(trade_date, state.get('analysis_horizon', 'medium_3m'))}
            headlines = submit_context(pool, fetch_evidence, get_global_news,
                                    curr_date=trade_date, trade_date=trade_date)
            evidence = "\n\n".join([
                *[format_evidence(f"FRED {indicator}", compact_macro(job.result(), indicator))
                  for indicator, job in jobs.items()],
                format_evidence("Global macro news", macro_news_only(headlines.result())),
            ])
        result = llm.invoke([
            SystemMessage(content=(
                "You are the Macro Analyst. Produce a compact indicator table, a brief explanation "
                "of conflicting signals and at most three evidence-supported transmission channels "
                "for the instrument and analysis horizon. Use supplied calculations: monthly/yearly "
                "percent changes for inflation, basis-point changes for rates and the yield curve, "
                "and percentage-point changes for unemployment; omit relative rate percentages. "
                "State observation dates and units. FRED's supplied Vintage field identifies the "
                "realtime snapshot used; distinguish it from observation and individual release dates. "
                "Do not claim publication dates were checked or extend this guarantee to news. "
                "Missing calendar reference values mean the comparison is unavailable. "
                "Include only relevant macro headlines, briefly labeling unverified claims. "
                "Do not list unsupported channels or repeat company news; use one short coverage note. "
                "Do not infer causality, forecast prices, assign ratings or recommend trades. "
                + NO_EXTERNAL_TOOLS + get_language_instruction(preserve_decision_labels=False)
            )),
            HumanMessage(content=(f"Analysis date: {trade_date}\n"
                f"Analysis horizon: {state.get('analysis_horizon', 'unspecified')}\n"
                f"{get_instrument_context_from_state(state)}\n\n{evidence}")),
        ])
        return {"messages": [result], "macro_report": result.text}
    return macro_node
