"""Explain EMG/NCS planning options without inventing prices."""
from __future__ import annotations

from collections.abc import Callable


def emg_option(result: dict, rates: list[dict], rules: dict, payload: dict | None,
               service_date: str, review: dict, *, build_collection: Callable[[dict, dict], dict]) -> dict:
    """Price complete office + NCS + limb EMG scenarios without choosing a CPT level."""
    from itertools import product
    from app.insurance import cpt_rules, visit_estimate

    option = {'calculation_version': 2, 'requested': bool(review.get('emg_required')), 'additional_min_cents': None,
              'additional_max_cents': None, 'total_min_cents': None, 'total_max_cents': None,
              'message': 'Select EMG / nerve testing to calculate its additional patient cost.', 'scenario_count': 0}
    codes = result.get('codes') or []
    testing = [row for row in codes if row['code'] in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS]
    option['included'] = bool(testing)
    if not option['requested'] and not option['included']:
        return option
    baseline = [row for row in codes if row not in testing]
    if not baseline:
        option['message'] = ('The booked NCS service is already in the visit estimate. '
                             'Needle muscle work is excluded unless documented.' if testing else
                             'An office consultation is needed as the comparison for additional testing cost.')
        return option
    alternatives = result.get('price_range', {}).get('mode') == 'alternatives'
    baselines = [[row] for row in baseline] if alternatives else [baseline]
    nerves = [row for row in testing if row['code'] in cpt_rules.NCS_CODES]
    needles = [row for row in testing if row['code'] in cpt_rules.EMG_ADD_ONS]
    def preview(code, units=1):
        return {'id': 'emg-preview:' + code, 'code': code, 'units': units, 'source': 'Additional testing cost scenario'}
    if needles:
        extras = [testing]
        option['message'] = 'Additional patient cost for the testing already selected below.'
    else:
        # At most 224 combinations: four office levels, seven NCS levels,
        # limited/complete limb EMG, and one through four limbs. No order is created.
        extras = [[nerve, preview(code, units)] for nerve, code, units in product(
            nerves or [preview(code) for code in sorted(cpt_rules.NCS_CODES)], ('95885', '95886'), range(1, 5))]
        option['message'] = 'Range includes one nerve-conduction study level and limited or complete needle EMG of 1–4 limbs. Specify the testing below to narrow the amount. This is a cost scenario, not a procedure order.'
    calculation_benefits = dict(result.get('benefits') or {})
    if result.get('pricing_assumptions') and calculation_benefits.get('network_status') != 'out_of_network':
        calculation_benefits['network_status'] = 'in_network'
    def amount_for(lines):
        priced = visit_estimate.price_lines(lines, rates, calculation_benefits, rules, payload=payload,
            service_date=service_date, reviews=review.get('lines'))
        # A copay-only advance cannot establish the incremental price of EMG.
        candidate = {**result, 'price_range': priced,
            'arrival_collection': visit_estimate.arrival_collection(priced, result['benefits'], review),
            'planning_estimate': visit_estimate.planning_estimate(priced, result['benefits'], result.get('identity') or {})}
        guidance = build_collection(candidate, review)
        # This is a patient-cost scenario, not the check-in advance. A clinic
        # deferral, eCW no-collection note, or prior payment may change today's
        # collection without changing the cost of the planned services.
        if guidance['basis'] == 'out_of_pocket_limit':
            return 0
        if guidance['basis'] not in {'patient_obligations', 'anticipated_services'}:
            return None
        minimum, maximum = priced.get('patient_min_cents'), priced.get('patient_max_cents')
        if (priced.get('complete') and not priced.get('conditional') and type(minimum) is int
                and minimum == maximum and minimum >= 0):
            return minimum
        return None
    differences, totals = [], []
    for office in baselines:
        base_amount = amount_for(office)
        if base_amount is None:
            option['message'] = 'The applicable office and testing benefits do not yet support an additional patient-cost calculation.'
            return option
        for extra in extras:
            total = amount_for([*office, *extra])
            if total is None:
                option['message'] = 'The returned benefits or clinic rates do not cover every testing option. Specify the nerve study and EMG scope below to calculate the additional cost.'
                return option
            differences.append(max(0, total - base_amount))
            totals.append(total)
    if totals:
        option.update(additional_min_cents=min(differences), additional_max_cents=max(differences),
                      total_min_cents=min(totals), total_max_cents=max(totals), scenario_count=len(totals))
    return option
