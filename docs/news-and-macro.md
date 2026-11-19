# News and Macro analysts

New analyses select `market`, `macro`, `news`, and `fundamentals`. The former
`social` selection is no longer accepted by the CLI or analysis API. Clients
that explicitly send an analyst list must update it before deploying this change.

News collects company news once, sector-only headlines, reported insider sales,
StockTwits messages. Company news and social evidence request
the trailing 30 calendar days. Macro collects six FRED series and global macro
headlines. Independent fetches run concurrently; each analyst invokes its model
once, with no structured-output retry or per-post classification calls.

The Yahoo sector search excludes the global macro query list. Alpha Vantage
does not support this free-text sector query and reports that source unavailable
instead of silently substituting global macro headlines.

The social window is a request, not guaranteed historical coverage. StockTwits
serves recent messages and is not an
archival source. Reports must state actual coverage and missing sources and must
not call a few minutes of messages a monthly trend. Social direction is qualitative;
news events are described once and compared with social reaction rather than repeated.

The social evidence excludes author usernames and redacts @handles in message
bodies. Python supplies the eligible pool size, sampled size, label counts and
ratios, and the sampled timestamp span. Investor Reaction uses one sample-summary
line, up to three themes and a short direction/confidence assessment. Python
inserts the sample line; the model produces only themes and its assessment. Up to eight
cursor pages are fetched within a ten-second request-time budget. Message IDs
are deduplicated and only Bullish/Bearish-tagged posts with timestamps are eligible.
Up to 30 are selected without replacement across six equal-duration bins over
the observed labeled pool, with random selection within bins. Empty bins receive
no fabricated posts; sparse pools yield fewer than 30 messages. Ratios describe
this label-only, time-balanced sample, not all users or the full stream.

Insider sales are summarized in Python by person, role and explicitly supplied
currency, with the latest transaction and recent-window records. Missing values
are flagged; missing or blank currencies default to USD, while explicit currencies are preserved.
The table includes a calculated share-weighted average price using only records
with both a reported value and positive shares. Python renders this table directly,
without passing the sales rows or aggregates to the model. There is no coverage
column or important-sales ranking. A partial-price average gets a small footnote
marker; the method and limitations are a single sentence. When currency defaults
to USD, this assumption is stated in that note.

One model call generates news, social themes and new developments to watch using
fixed section markers; Python inserts the numerical sections afterward. Verification
warnings are brief labels and remaining coverage limits use one short note, rather
than repeated paragraphs. The catalysts section must not recap news already covered.
Exact duplicate transaction records are collapsed. Company and sector news are
deduplicated together by canonical URL or publisher/title/date, keeping the
richest copy. Distinct reporting about the same event remains available.

The graph writes `macro_report` instead of `sentiment_report`; the service section
code is `macro`. Research and risk agents consume the macro report and the combined
news report. Existing saved HTML/Markdown files are unchanged. The former standalone sentiment module, schema and unused post classifier have been removed.
