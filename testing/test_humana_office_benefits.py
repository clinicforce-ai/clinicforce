"""Humana's shared STC 98 response must select the in-person specialist tier."""
from app.insurance import commercial


def test_humana_specialist_office_terms_exclude_other_services_and_combined_tier():
    def row(service, **values):
        return {'service': {'system': 'STC', 'value': service}, **values}

    def office(label, **values):
        return row('98', network={'indicator': 'IN_NETWORK'},
                   coverageLevel='INDIVIDUAL', messages=[label], **values)

    plan = {'name': 'Humana Honor', 'benefits': {
        'statuses': [row('30', status='ACTIVE_COVERAGE', messages=['Medicare PPO']),
                     row('98', status='ACTIVE_COVERAGE')],
        'coPayment': [office('PHYSICIAN OFFICE VISIT PCP', amount=0, timePeriod='CALENDAR_YEAR'),
                      office('PHYSICIAN OFFICE VISIT SPECIALIST', amount=40, timePeriod='VISIT'),
                      office('PHYSICIAN OFFICE VISIT FREESTANDING RAD CTR', amount=0),
                      office('PHYSICIAN OFFICE VISIT COMP OUTPT REHAB FACILITY', amount=0),
                      office('TELEHEALTH VISIT SPECIALIST', amount=40,
                             placesOfService=[{'code': '10'}])],
        'coInsurance': [office('PHYSICIAN OFFICE VISIT PCP', percent=0),
                        office('PHYSICIAN OFFICE VISIT SPECIALIST', percent=0),
                        office('PHYSICIAN OFFICE VISIT FREESTANDING RAD CTR', percent=0),
                        office('PHYSICIAN OFFICE VISIT COMP OUTPT REHAB FACILITY', percent=0)],
        'outOfPocket': [row('30', amount=7900, timePeriod='CALENDAR_YEAR',
                            coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'}),
                        row('30', amount=7522.99, timePeriod='REMAINING',
                            coverageLevel='INDIVIDUAL', network={'indicator': 'IN_AND_OUT_OF_NETWORK'}),
                        row('30', amount=4237.99, timePeriod='REMAINING',
                            coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'})],
        'limitations': [office('PHYSICIAN OFFICE VISIT SPECIALIST', percent=1,
                               timePeriod='VISIT'),
                        row('30', amount=9987724.98, timePeriod='REMAINING',
                            coverageLevel='INDIVIDUAL', network={'indicator': 'IN_AND_OUT_OF_NETWORK'})],
        'nonCovered': [office('PHYSICIAN OFFICE DOCTOR ON DEMAND NOT COVERED', amount=0)],
    }}
    payload = {'payerId': 'UYORK', 'plans': [plan]}
    benefits = commercial.normalize_stedi_271(
        payload, '98', True, procedure_code='99203', office_visit=True,
        service_date='2026-11-16', place_of_service='11',
        provider_specialties=['Neurology'], visit_context={'reason': 'memory'})
    assert benefits['active'] is True
    assert benefits['coverage_status_conflict'] is False
    assert benefits['copay_cents'] == 4000
    assert benefits['coinsurance_percent'] == '0'
    assert benefits['copay_period'] == 'VISIT'
    assert benefits['oop_remaining_cents'] == 423799
    assert benefits['oop_annual_cents'] == 790000
    assert benefits['source_errors'] == []
    assert benefits['deductible_applicability'] == 'UNKNOWN'
    assert benefits['plan_details']['medicare_ppo_evidence']
    assert all('both active coverage and inactive' not in reason
               for reason in benefits['review_reasons'])
