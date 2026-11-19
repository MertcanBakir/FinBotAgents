"""Company news and investor reaction, collected before one model invocation."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tradingagents.agents.analyst_support.collection import (
    fetch_evidence,
    format_evidence,
    submit_context,
)
from tradingagents.agents.analyst_support.insider_sales import prepare_insider_sales
from tradingagents.agents.analyst_support.news import deduplicate_news_blocks
from tradingagents.agents.analyst_support.news_render import (
    compose_news_report,
    insider_table,
    social_sample_line,
)
from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.agents.structured import NO_EXTERNAL_TOOLS
from tradingagents.agents.tools import get_global_news, get_insider_transactions, get_news
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.vendors.stocktwits import fetch_stocktwits_messages


def create_news_analyst(llm):
    def news_node(state):
        ticker, trade_date = state["company_of_interest"], state["trade_date"]
        start_date = (date.fromisoformat(trade_date) - timedelta(days=30)).isoformat()
        context = get_instrument_context_from_state(state)
        classification = re.search(
            r"(?:Business classification|Industry|Sector): (.*?)(?=;|\. Do not substitute|$)", context)
        sector = classification[1].split(" / ")[-1].rstrip(".") if classification else ticker
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = {
                "Instrument news": submit_context(pool, fetch_evidence, get_news, ticker=ticker,
                    start_date=start_date, end_date=trade_date, trade_date=trade_date),
                "Sector news": submit_context(pool, fetch_evidence, get_global_news, curr_date=trade_date,
                    trade_date=trade_date, sector_query=f"{sector} industry news outlook", sector_only=True),
                "StockTwits": submit_context(pool, fetch_stocktwits_messages, ticker, limit=30,
                    start_date=start_date, end_date=trade_date),
            }
            if state.get("asset_type", "stock") == "stock":
                jobs["Insider sales"] = submit_context(pool, fetch_evidence, get_insider_transactions,
                                               ticker=ticker, trade_date=trade_date)
            blocks = []
            collected = {}
            for label, job in jobs.items():
                try:
                    data = job.result()
                except Exception:
                    data = "Data could not be retrieved."
                if label == "Insider sales":
                    data = prepare_insider_sales(data, trade_date)
                collected[label] = data
            collected.update(deduplicate_news_blocks({
                label: collected[label] for label in ("Instrument news", "Sector news")
            }))
            # Track every source, but do not ask the model to reproduce numerical
            # sales evidence; the final report gets its table directly from Python.
            blocks = []
            for label, data in collected.items():
                block = format_evidence(label, data)
                if label != 'Insider sales':
                    blocks.append(block)
        result = llm.invoke([
            SystemMessage(content=(
                "You are the News Analyst. Use exactly these literal headings: "
                "### NEWS, ### REACTION, ### CATALYSTS; translate their content, not the markers.\n"
                "NEWS: Group related company/sector headlines, cite supplied dates, publishers and links, "
                "and distinguish events from opinions; briefly label high-impact single-source claims unverified.\n"
                "REACTION: Interpret StockTwits tags and bodies in at most three brief themes followed "
                "by qualitative direction and confidence. Relate them to news without retelling posts/events "
                "or treating social claims and repeated stories as independent verification. "
                "Respect actual counts and timestamps: this time-balanced, label-only sample excludes "
                "unlabeled views; never present that sample as a monthly trend or population ratio. "
                "Missing data is not neutral sentiment.\n"
                "CATALYSTS: List only new upcoming developments or verification points, not a news recap.\n"
                "The renderer supplies the insider-sales table and social statistics: do not reproduce "
                "them, add sales rankings, or discuss individual users. Consolidate remaining coverage "
                "limitations into one short note; omit methods and conclusion sections. "
                "Do not give price targets, numerical sentiment scores, ratings or trading recommendations. "
                "Leave general macro analysis to the Macro Analyst. "
                + NO_EXTERNAL_TOOLS + get_language_instruction(preserve_decision_labels=False)
            )),
            HumanMessage(content=f"Analysis date: {trade_date}\nRequested news and social window: "
                f"{start_date} to {trade_date}\n{context}\n\n" + "\n\n".join(blocks)),
        ])
        tr = get_config().get('output_language', 'English').lower() in {'turkish', 'tr', 'türkçe'}
        table = (insider_table(collected['Insider sales'], trade_date, tr)
                 if 'Insider sales' in collected else '')
        report = compose_news_report(result.text, table, social_sample_line(collected['StockTwits'], tr), tr)
        return {"messages": [AIMessage(content=report)], "news_report": report}
    return news_node
