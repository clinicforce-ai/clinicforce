"""Public tic guidance must be bounded and never receive patient context."""

import httpx

from app.insurance.tic_web_evidence import SOURCES, fetch_tic_guidance
from app.insurance.ecw.ecw_appointment_fields import _history_summary


def test_tic_guidance_reads_only_fixed_official_pages_and_short_excerpts():
    requested = []
    pages = {
        SOURCES[0][1]: '<main><p>Many people with tics do not need any treatment.</p><p>CBIT helps people with tics.</p></main>',
        SOURCES[1][1]: '<main><p>Other options may help. Botulinum toxin injections target muscles that produce tics.</p></main>',
        SOURCES[2][1]: '<main><p>CPT 90834: Psychotherapy, 45 minutes with patient.</p></main>',
    }
    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(200, text=pages[str(request.url)], headers={'content-type': 'text/html'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        evidence = fetch_tic_guidance(client)
    assert requested == [source[1] for source in SOURCES]
    assert all(item['status'] == 'retrieved' and item['excerpt'] for item in evidence)
    assert 'CBIT' in evidence[0]['excerpt'] and 'botulinum' in evidence[1]['excerpt'].lower()
    assert '90834' in evidence[2]['excerpt']


def test_tic_guidance_marks_failed_or_changed_pages_unavailable():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        assert all(item['status'] == 'unavailable' and not item['excerpt']
                   for item in fetch_tic_guidance(client))


def test_completed_visit_summary_keeps_linked_diagnosis_without_chart_identifiers():
    visit = {'id': 'synthetic-visit', 'status': 'finished',
             'period': {'start': '2026-10-01'},
             'reasonCode': [{'coding': [{'system': 'http://hl7.org/fhir/sid/icd-10-cm',
                                        'code': 'F95.9', 'display': 'Tic disorder'}]}]}
    summary = _history_summary(visit, [], [])
    assert summary['diagnoses'] == ['F959']
    assert 'synthetic-visit' not in str(summary)
