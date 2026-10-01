"""NMCT direct-pay schedule transcribed from the clinic's September 4, 2026 Word file.

Package prices are intentionally separate from insurance contract rates. A CPT
example cannot identify an EMG package: the same codes describe different scopes.
"""
from app.insurance import cpt_rules

PRACTICE_ID = 'f378bc13-af1d-4ea5-bfbd-8cd102e42ddc'
SOURCE = 'NMCT Internal Self Pay Cash Pricelist 2026-09-04.docx'
SOURCE_SHA256 = '994c2c47fb6ce9b50100134127778525e0dbcd8b90eee170dfbd22e1b230ebb6'
EFFECTIVE_FROM = '2026-09-04'
NEW = {str(n) for n in range(99202, 99206)}
ESTABLISHED = {str(n) for n in range(99212, 99216)}
BOTOX = {str(n) for n in (*range(64611, 64617), *range(64642, 64646))}
PACKAGES = [
    {'id': 'carpal_one', 'label': 'Carpal tunnel · one hand', 'amount_cents': 50000, 'max_limbs': 1},
    {'id': 'carpal_both', 'label': 'Carpal tunnel · both hands', 'amount_cents': 70000, 'max_limbs': 2},
    {'id': 'neck_arm', 'label': 'Neck-related nerve problem · one arm', 'amount_cents': 80000, 'max_limbs': 1},
    {'id': 'back_leg', 'label': 'Back-related nerve problem · one leg', 'amount_cents': 70000, 'max_limbs': 1},
    {'id': 'neuropathy_legs', 'label': 'Neuropathy · both legs', 'amount_cents': 80000, 'max_limbs': 2},
    {'id': 'full_study', 'label': 'Comprehensive study · arms and legs', 'amount_cents': 130000, 'max_limbs': 4},
    {'id': 'single_nerve', 'label': 'Single nerve injury', 'amount_cents': 40000, 'max_limbs': 1},
]
# Planning examples from the document; never final coding or a study-count mandate.
for package, code, limbs in zip(PACKAGES, ['95910', '95911', '95910', '95910', '95911', '95912', '95907'], [1, 2, 1, 1, 2, 4, 1]):
    package.update(ncs_example=code, emg_example='95886', limbs_example=limbs)

INCLUDES = 'Includes focused evaluation, nerve studies, needle EMG when indicated, interpretation, written report and same-day results discussion. No extra office-visit charge.'


def schedule(practice_id: str, service_date: str) -> dict | None:
    if practice_id != PRACTICE_ID or service_date < EFFECTIVE_FROM:
        return None
    return {'source': SOURCE, 'sha256': SOURCE_SHA256, 'effective_from': EFFECTIVE_FROM,
            'packages': PACKAGES, 'package_includes': INCLUDES,
            'proposed': 'Pending NMCT approval: 3-site biopsy with special report $450 (outside lab excluded); biopsy report only $150; routine outside EEG interpretation/report $125. These proposed prices are not used in this estimate.'}


def catalog() -> list[dict]:
    descriptions = {**{code: 'Office consultation' for code in NEW | ESTABLISHED},
                    **{code: 'Nerve conduction study' for code in cpt_rules.NCS_CODES},
                    '95885': 'Limited needle EMG', '95886': 'Complete needle EMG',
                    '11104': 'Skin punch biopsy · first site', '11105': 'Skin punch biopsy · additional site',
                    **{code: 'EEG · confirm duration and component' for code in ('95812', '95813', '95816', '95819', '95822')},
                    **{code: 'Botox injection procedure — confirm indication and site' for code in BOTOX},
                    'J0585': 'Botox drug, per unit'}
    return [{'id': 'nmct-cash:' + code, 'code': code, 'description': description,
             'source': 'NMCT cash schedule option', 'units': None if code == 'J0585' else 1, 'modifiers': []}
            for code, description in sorted(descriptions.items())]


def office_price(code: str) -> int | None:
    return 25000 if code in NEW else 15000 if code in ESTABLISHED else None


def quote(codes: list[dict], *, selection: str = '', alternatives: bool = False) -> dict:
    result = {'complete': False, 'source': SOURCE, 'effective_from': EFFECTIVE_FROM,
              'selection': selection, 'message': 'Choose the planned services to see NMCT cash pricing.'}
    if not codes:
        return result
    values = {row['code'] for row in codes}
    def done(amount: int, message: str) -> dict:
        return {**result, 'complete': True, 'min_cents': amount, 'max_cents': amount, 'message': message}
    if any(set(row.get('modifiers') or []) & {'26', 'TC'} for row in codes):
        return {**result, 'message': 'The established cash prices cover the whole service. Obtain a clinic quote for professional-only or technical-only services.'}
    if any(type(row.get('units')) is not int or row['units'] < 1 for row in codes):
        return {**result, 'message': 'Confirm the planned extent of each procedure before quoting cash.'}
    if values <= NEW | ESTABLISHED:
        if any(row['units'] != 1 for row in codes) or (not alternatives and len(codes) != 1):
            return {**result, 'message': 'Select one office visit for the flat cash price.'}
        if selection == 'friends_family' and values <= NEW:
            return done(10000, 'NMCT Friends & Family new-patient discount selected by the clinician.')
        if selection:
            return {**result, 'message': 'The selected cash package does not match these services. Review the cash choice.'}
        amounts = [office_price(row['code']) for row in codes]
        return {**done(min(amounts), 'Flat office-visit price; final CPT level does not change the cash fee.'), 'max_cents': max(amounts)}
    if values & (cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS):
        if alternatives or cpt_rules.service_selection_issues([row['code'] for row in codes]):
            return {**result, 'message': 'Confirm one nerve-study level and any accompanying EMG before quoting a package.'}
        if values - (cpt_rules.NCS_CODES | {'95885', '95886'} | NEW | ESTABLISHED):
            return {**result, 'message': 'The EMG package does not price all selected services. Obtain a separate quote for additional care.'}
        package = next((row for row in PACKAGES if row['id'] == selection), None)
        if not package:
            return {**result, 'message': 'Select the clinical EMG package in Plan visit. NMCT packages range from $400 to $1,300; CPT codes alone do not identify the package.'}
        limbs = sum(row['units'] for row in codes if row['code'] in {'95885', '95886'})
        if limbs > package['max_limbs'] or any(row['units'] != 1 for row in codes if row['code'] not in {'95885', '95886'}):
            return {**result, 'message': 'The planned extent exceeds this package. Review the package and inform the patient before expanding care.'}
        return done(package['amount_cents'], package['label'] + '. ' + INCLUDES)
    if values <= BOTOX | {'J0585'} and values & BOTOX:
        drugs = [row for row in codes if row['code'] == 'J0585']
        if alternatives or len(drugs) != 1 or any(row['units'] != 1 for row in codes if row['code'] in BOTOX) or selection:
            return {**result, 'message': 'Confirm one Botox session and the planned J0585 drug amount to calculate the cash price.'}
        units = drugs[0]['units']
        return done(45000 + 750 * units, f'One injection session $450 + {units} Botox units × $7.50. The procedure fee is charged once per session.')
    return {**result, 'message': 'The NMCT document has no established cash price covering this selection. Obtain a clinic quote; proposed biopsy/EEG prices remain pending approval.'}


def apply(result: dict, practice_id: str, service_date: str, review: dict) -> None:
    config = schedule(practice_id, service_date)
    if not config:
        return
    result['cash_schedule'] = config
    priced = result['price_range']
    cash = quote(priced['lines'], selection=review.get('cash_selection', ''), alternatives=priced.get('mode') != 'services')
    result['cash_quote'] = cash
    # Never use earlier contract-upload cash values for NMCT: this document is
    # the sole source. Bundle totals are never allocated artificially to CPTs.
    for row in result['available_codes']:
        row['unit_cash_cents'] = office_price(row['code'])
    for line in priced['lines']:
        office = office_price(line['code']) if {row['code'] for row in result['codes']} <= NEW | ESTABLISHED else None
        line.update(unit_cash_cents=10000 if office and cash['selection'] == 'friends_family' else office, cash_cents=(10000 if cash['selection'] == 'friends_family' else office) if cash['complete'] and office else None,
                    cash_source=SOURCE, cash_included=bool(cash['complete'] and office is None))
    priced['cash_complete'] = cash['complete']
    priced.pop('cash_min_cents', None)
    priced.pop('cash_max_cents', None)
    if cash['complete']:
        priced.update(cash_min_cents=cash['min_cents'], cash_max_cents=cash['max_cents'])
