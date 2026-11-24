"""Use a scoped BCBSTX clinic fee when a commercial insurer fee is absent."""

from app.logs.logger import log_warning

from .commercial_previsit_rate_reference import _attach_temporary_blue_cross_rates


def apply_missing_commercial_rates(selected: list[dict], rates: list[dict], rules: dict,
                                   issues: list[str], benefits: dict, source: dict,
                                   inputs: dict, practice_id: str, payer_name: str,
                                   medicare_plan: bool) -> list[str]:
    if (not selected or medicare_plan or benefits.get('network_status') != 'in_network'
            or source.get('payer_plan_bound') is not True or rules['provider_npi_conflict']):
        return issues
    missing = [line for line in selected if not any(
        rate.get('code') == line.get('code')
        and (rate.get('modifier') or '') == ','.join(line.get('modifiers') or [])
        and (not rate.get('line_id') or rate['line_id'] == line.get('id'))
        for rate in rates)]
    if not missing:
        return issues
    reference = {'codes': missing, 'plan_details': {'stedi': benefits.get('plan_details') or {}}}
    try:
        _attach_temporary_blue_cross_rates(reference, practice_id, inputs, source, payer_name)
    except Exception as exc:
        log_warning('BCBSTX fee fallback', 'Scoped fee lookup unavailable (%s).', type(exc).__name__)
    for row in (reference.get('temporary_rate_reference') or {}).get('lines') or []:
        if not str(row.get('source_reference') or '').startswith('library:'):
            continue
        rates.append({'id': f"bcbstx-fallback:{row['id']}", 'line_id': row['id'],
                      'code': row['code'], 'modifier': ','.join(row.get('modifiers') or []),
                      'units': row['units'], 'allowed_cents': row['unit_reference_cents'],
                      'basis': 'bcbstx_rate_fallback', 'source_reference': row['source_reference']})
    if any(rate.get('basis') == 'bcbstx_rate_fallback' for rate in rates):
        rules['cost_share'] = rules.get('cost_share') or 'payer_returned'
    unresolved = [line for line in missing if not any(
        rate.get('line_id') == line.get('id') and rate.get('basis') == 'bcbstx_rate_fallback'
        for rate in rates)]
    if not unresolved:
        issues = [issue for issue in issues if 'fee schedule has no' not in issue.lower()]
    for line in unresolved:
        issues.append(f"Please enter rate for CPT {line['code']} from the {payer_name} fee schedule.")
    return issues


def annotate_missing_rate_prompts(priced: dict, payer_name: str, medicare_plan: bool) -> None:
    if medicare_plan:
        return
    for line in priced.get('lines') or []:
        if (line.get('rate_resolution') or {}).get('status') == 'RATE_UNAVAILABLE':
            prompt = f"Please enter rate for CPT {line['code']} from the {payer_name} fee schedule."
            if prompt not in line['issues']:
                line['issues'].insert(0, prompt)
