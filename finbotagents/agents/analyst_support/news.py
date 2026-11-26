"""Deduplicate exact news records while preserving distinct reporting."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def deduplicate_news_blocks(blocks):
    """Drop only exact URL or publisher/title/date repeats; retain the richest copy."""
    output, candidates = {}, []
    for label, data in blocks.items():
        if not isinstance(data, str) or '\n### ' not in data:
            output[label] = data
            continue
        prefix, *articles = data.split('\n### ')
        output[label] = prefix
        candidates.extend((label, article) for article in articles)
    seen = set()
    for label, article in sorted(candidates, key=lambda item: len(item[1]), reverse=True):
        title = article.splitlines()[0]
        keys = {('title', ' '.join(title.casefold().split()))}
        if link := re.search(r'^Link: (\S+)', article, re.M):
            url = urlsplit(link[1])
            query = [(k, v) for k, v in parse_qsl(url.query)
                     if not k.lower().startswith('utm_') and k.lower() not in {'guccounter', 'guce_referrer', 'guce_referrer_sig'}]
            keys.add(('url', urlunsplit((url.scheme.lower(), url.netloc.lower(),
                                        url.path.rstrip('/'), urlencode(query), ''))))
        if keys & seen:
            continue
        seen.update(keys)
        output[label] += '\n### ' + article
    for label, data in blocks.items():
        if isinstance(data, str) and '\n### ' in data and '\n### ' not in output[label]:
            output[label] += '\nAll articles duplicate richer records in the other news block.'
    return output
