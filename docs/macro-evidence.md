# Macro evidence and market display

Macro remains a single model invocation. Six FRED series and relevant global
headlines are collected before invocation. Supporting logic lives in
`agents/analyst_support/macro.py`, outside the analyst entry point.

| Series | Short horizon | Medium horizon | Long horizon |
| --- | --- | --- | --- |
| CPI and core PCE | 15 months | 15 months | 24 months |
| Effective fed funds and unemployment | 6 months | 12 months | 24 months |
| 10-year Treasury and 10y–2y spread | 3 months | 6 months | 12 months |

Windows use calendar months converted to days at the analysis date. Daily
series have a ten-day boundary buffer for weekends and delayed final observations.
These are history windows, not forecasts.

Python computes inflation MoM/YoY using exact calendar references. Rate and
spread changes use basis points; unemployment changes use percentage points.
Daily comparisons use the nearest earlier observation within seven days of the
calendar target. Missing references are reported as unavailable, never replaced
with a shorter window and mislabeled as an annual change.

The FRED vendor pins realtime start/end, reports the actual vintage date, and
retains reference observations before truncating its display table. Cache keys
are versioned for this representation. Compaction sends metadata, computed
changes and only five recent observations to the model. Raw window-relative
percentage changes are not forwarded as inflation or rate comparisons.

Global search headlines require an explicit macroeconomic/policy topic to pass
the relevance filter. A search response without relevant headlines is marked as
no usable records, not a claim that no macro events occurred. The model is asked
for a compact table, conflicting signals, at most three supported transmission
channels and one short coverage note. Vintage metadata does not establish an
individual release timestamp or guarantee news publication availability.

Market model instructions request two decimal places for displayed prices,
indicators and percentages. Python-rendered zones and last observed prices use
two decimals as well. Calculation inputs and stored results retain their full
precision; dates, periods, counts and integer volumes are unchanged.
