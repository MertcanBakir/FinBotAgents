# Analyst code organization

`finbotagents/agents/analysts/` contains only analyst factories and their graph
node implementations. Those files orchestrate sources, prompts, model invocation
and state updates; they do not define helper functions. The nested graph node
returned by a factory is the agent entry point, not a helper.

`finbotagents/agents/analyst_support/` owns supporting implementation:

| Module | Responsibility |
| --- | --- |
| `collection.py` | Safe evidence loading, source status formatting and context-preserving task submission |
| `news.py` | Deduplication across company and sector news |
| `insider_sales.py` | Sale filtering, aggregation and weighted price calculations |
| `macro.py` | Macro source windows and compact numerical evidence |
| `fundamentals.py` | Financial statement history limits |
| `market.py` | Instrument price loading and indicator selection parsing |
| `news_render.py` | Deterministic sales tables, social statistics and report composition |

Functions imported by analysts have public names, such as `fetch_evidence`,
`prepare_insider_sales`, `load_prices` and `select_indicator_groups`. Private
helpers stay within their supporting module. The support package does not import
analyst factories, preventing circular dependencies.

Vendor clients remain in `dataflows/`; technical calculations remain in
`technical_analysis/`. Support modules adapt that functionality for analysts
without duplicating it. Unit tests import supporting functions from their owning
module; agent tests patch the names where the analyst uses them. End-to-end graph
tests continue to verify execution, callback ownership and report production.
