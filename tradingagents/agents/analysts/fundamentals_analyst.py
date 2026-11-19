import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.agents.analyst_support.fundamentals import limit_statement_history
from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.agents.structured import NO_EXTERNAL_TOOLS
from tradingagents.agents.tools import (
    get_analyst_price_targets,
    get_balance_sheet,
    get_cashflow,
    get_estimate_revisions,
    get_fundamentals,
    get_income_statement,
    get_upcoming_earnings_and_guidance,
    get_valuation_benchmarks,
)
from tradingagents.dataflows.outlook import compare_consensus_to_guidance
from tradingagents.dataflows.vendors.yahoo.valuation import compare_pe_benchmarks
from tradingagents.horizons import HORIZON_CONFIG, parse_analysis_horizon

logger = logging.getLogger(__name__)
def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        ticker = state["company_of_interest"]
        trade_date = state["trade_date"]
        horizon = parse_analysis_horizon(state.get("analysis_horizon", "medium_3m"))
        months = HORIZON_CONFIG[horizon]["fundamentals_lookback_months"]
        instrument_context = get_instrument_context_from_state(state)
        dated_args = {"ticker": ticker, "curr_date": trade_date, "trade_date": trade_date}
        sources = (
            (get_fundamentals, dated_args),
            (get_income_statement, dated_args),
            (get_balance_sheet, dated_args),
            (get_cashflow, dated_args),
            (get_estimate_revisions, dated_args),
            (get_upcoming_earnings_and_guidance, dated_args),
            (get_analyst_price_targets, dated_args),
            (get_valuation_benchmarks, dated_args),
        )
        data_by_source = {}
        for tool, args in sources:
            try:
                data = tool.func(**args)
            except Exception as exc:
                # The router can raise when every configured vendor fails.
                logger.warning("Fundamentals Analyst could not fetch %s (%s)", tool.name, type(exc).__name__)
                data = "Data could not be retrieved."
            data_by_source[tool.name] = data or "No data returned."

        for tool in (get_income_statement, get_balance_sheet, get_cashflow):
            data_by_source[tool.name] = limit_statement_history(data_by_source[tool.name], months)

        outlook = data_by_source[get_upcoming_earnings_and_guidance.name]
        if isinstance(outlook, dict):
            outlook["consensus_comparison"] = compare_consensus_to_guidance(
                data_by_source[get_estimate_revisions.name], outlook,
            )
        benchmarks = data_by_source[get_valuation_benchmarks.name]
        if isinstance(benchmarks, dict):
            data_by_source[get_valuation_benchmarks.name] = compare_pe_benchmarks(
                data_by_source[get_fundamentals.name], benchmarks,
            )
        evidence = [
            f"### {name}\n{data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)}"
            for name, data in data_by_source.items()
        ]

        system_message = (
            "You are the Fundamentals Analyst. Write a concise Markdown report for the research team. "
            "Use exactly seven level-three headings in this order: Company Overview and Valuation, "
            "Income Statement, Balance Sheet, Cash Flow, EPS and Revenue Revisions, "
            "Upcoming Earnings and Guidance, and Analyst Price Targets. "
            "Translate the headings into the report language. Use the corresponding source block "
            "for each section. Give key figures with their periods and units once. Use compact bullets "
            "or a small table for comparisons. Add a sentence after them only if it contributes a new "
            "comparison or implication. State a data gap briefly when it prevents a requested finding. "
            "Include get_valuation_benchmarks under Company Overview and Valuation. Briefly give the company "
            "historical trailing P/E mean and median with the date range and observation count. Identify the "
            "industry and exchange or market. Label FMP's aggregate as published and use only supplied percentage "
            "comparisons. Label a Yahoo sample as selected peers and state its count, mean and median. "
            "When either industry benchmark is available, report the supplied benchmark without discussing the other provider. "
            "Use the company overview's trailing P/E as the single current trailing P/E for both benchmarks. "
            "In Income Statement, compare the latest standalone quarter with the same quarter last year "
            "when both are supplied. Otherwise use the most recent comparable standalone quarter if supplied. "
            "When both comparisons are possible, include the sequential comparison only if it adds a distinct finding. "
            "In Balance Sheet, use "
            "the current ratio to summarize liquidity when supported. In Cash Flow, compare like reported "
            "periods, label year-to-date figures, and give free cash flow when supported. Use reported periods "
            "for cash flow and omit standalone quarters derived by subtracting cumulative totals. "
            "For revisions, focus on the nearest estimate quarter and current fiscal year. Combine the 7- and "
            "30-day results when they match and use the actual observation dates for revenue revisions. "
            "For upcoming earnings, distinguish an estimated date or date range from a confirmed date. "
            "For company guidance, cite its filing date and source link. Use only the supplied "
            "consensus_comparison for comparisons with guidance, in one sentence without repeating the inputs. "
            "Do not expose internal field names or present inferred fiscal period candidates as dates stated "
            "by the company. If there is no comparable consensus, say so briefly in plain language. "
            "For Yahoo aggregate targets, give the median, mean, range and any supplied difference from the "
            "reference price in one line. Use the supplied currency and attribute the data to Yahoo. "
            "Omit the introduction, closing summary, summary table, and trade recommendations. "
            + NO_EXTERNAL_TOOLS
            + get_language_instruction(preserve_decision_labels=False)
        )
        source_data = "\n\n".join(evidence)
        result = llm.invoke([
            SystemMessage(content=system_message),
            HumanMessage(content=f"Analysis date: {trade_date}\n{instrument_context}\n\n{source_data}"),
        ])

        return {
            "messages": [result],
            "fundamentals_report": result.text,
        }

    return fundamentals_analyst_node
