"""Read public tic-treatment guidance without sending patient context to the web."""

from __future__ import annotations

import re

import httpx
from lxml import html


SOURCES = (
    ('CDC tic treatment', 'https://www.cdc.gov/tourette-syndrome/treatment/index.html',
     (('do not need any treatment',), ('CBIT', 'tics'))),
    ('AAN tic treatment guideline summary', 'https://www.aan.com/PressRoom/Home/PressRelease/2721',
     (('botulinum toxin', 'muscles that produce tics'),)),
    ('AMA CPT 90834', 'https://www.ama-assn.org/practice-management/cpt/cpt-code-90834-psychotherapy-45-minutes',
     (('90834', '45 minutes'),)),
)


def fetch_tic_guidance(client: httpx.Client | None = None) -> list[dict[str, str]]:
    """Return short excerpts from fixed official pages, or mark a page unavailable."""
    owned = client is None
    client = client or httpx.Client(timeout=4, follow_redirects=False)
    evidence = []
    try:
        for label, url, searches in SOURCES:
            excerpt = ''
            try:
                response = client.get(url)
                response.raise_for_status()
                if len(response.content) <= 350_000 and 'text/html' in response.headers.get('content-type', ''):
                    document = html.fromstring(response.content)
                    passages = [' '.join(' '.join(node.itertext()).split())
                                for node in document.xpath('//main//p|//main//li')]
                    matches = []
                    for terms in searches:
                        passage = next((text for text in passages if all(
                            re.search(re.escape(term), text, re.I) for term in terms)), '')
                        sentence = next((text for text in re.split(r'(?<=[.!?])\s+', passage) if all(
                            re.search(re.escape(term), text, re.I) for term in terms)), '')
                        matches.append(sentence[:500])
                    if all(matches):
                        excerpt = ' '.join(matches)
            except (httpx.HTTPError, ValueError, html.etree.ParserError):
                pass
            evidence.append({'label': label, 'url': url,
                             'status': 'retrieved' if excerpt else 'unavailable', 'excerpt': excerpt})
    finally:
        if owned:
            client.close()
    return evidence
