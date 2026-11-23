import type { CollectionGuidance } from '../../services/api/commercial-common'
import { money } from './format'

type Potential = NonNullable<CollectionGuidance['potential_office_collection']>
type Issue = Potential['issues'][number]

const identityLabels: Record<string, string> = {
  first_name: 'patient first name',
  last_name: 'patient surname',
  birth_date: 'patient date of birth',
  member_id: 'member ID',
  subscriber_first: 'subscriber first name',
  subscriber_last: 'subscriber surname',
  relationship: 'subscriber relationship',
}

export function potentialOfficeFormula(value: Potential): string {
  const caps = [`${money(value.copay_cents)} office copay`]
  if (value.allowed_cents != null) caps.push(`${money(value.allowed_cents)} office allowed amount`)
  if (value.oop_remaining_cents != null)
    caps.push(`${money(value.oop_remaining_cents)} remaining out-of-pocket`)
  if (value.separate_copay_remaining_cents != null)
    caps.push(`${money(value.separate_copay_remaining_cents)} remaining copay limit`)
  return `max($0.00, min(${caps.join(', ')}) − ${money(value.verified_payments_cents)} verified prior payments) = ${money(value.amount_cents)}. This covers CPT ${value.office_code} only.`
}

export function potentialOfficeIssueLabel(issue: Issue): string {
  if (issue.code === 'identity_difference')
    return identityLabels[issue.field || ''] || issue.field || 'identity'
  return (
    {
      copay_period_unverified: 'copay period',
      medicare_protection_unknown: 'QMB status',
      qmb_protected: 'QMB protection',
      office_not_selected: 'office CPT selection',
      other_service_unpriced: 'other service price',
      mri_contrast_code_mismatch: 'MRI code and description',
      network_tier_unknown: 'network tier',
    }[issue.code] || issue.code.replaceAll('_', ' ')
  )
}

export function potentialOfficeIssue(issue: Issue, payer = 'payer'): string {
  const services = (issue.services || []).map(
    (service) => `CPT ${service.code}${service.description ? ` (${service.description})` : ''}`,
  )
  const codes = services.length
    ? services.join(', ')
    : (issue.codes || []).map((code) => `CPT ${code}`).join(', ')
  switch (issue.code) {
    case 'identity_difference':
      return `eCW ${identityLabels[issue.field || ''] || issue.field}: “${issue.ecw || 'not recorded'}”; ${payer} returned “${issue.payer || 'not returned'}”.`
    case 'patient_identity_unbound':
      return 'The returned policy has no patient identity match in the saved visit.'
    case 'copay_period_unverified':
      return 'The payer described a specialist visit copay but did not specify its billing period in the source row. Confirm it applies once to this office visit.'
    case 'medicare_protection_unknown':
      return 'QMB billing-protection status is unknown for this Medicare member. If QMB applies, the office copay cannot be charged.'
    case 'qmb_protected':
      return 'QMB billing protection applies; the office copay cannot be charged.'
    case 'office_not_selected':
      return `CPT ${issue.office_code} is a proposal, not a selected or eCW-scheduled office service.`
    case 'other_service_unpriced':
      return issue.selection_status === 'approved'
        ? `${codes} is selected but has no resolved price. The potential office amount excludes it.`
        : `${codes} is only a proposed service and has no resolved price. The potential office amount excludes it; select it if planned for this visit, or remove it if it describes past care.`
    case 'other_service_excluded':
      return `${codes} is outside this office-only amount; the total visit amount includes its separate terms.`
    case 'mri_contrast_code_mismatch':
      return `The proposed MRI description says “without contrast,” but CPT ${issue.reported_cpt} means brain MRI with contrast. Confirm whether MRI is planned for this visit and the actual contrast protocol; use CPT ${issue.expected_code} only if a brain MRI without contrast is documented, or remove the proposal if it describes prior care.`
    case 'coverage_not_active':
      return 'The payer did not return active medical coverage for this visit.'
    case 'coverage_ended_before_visit':
      return `The returned coverage ended ${issue.end}, before the visit.`
    case 'coverage_conflict':
      return 'The payer returned conflicting or invalid coverage dates or statuses.'
    case 'provider_out_of_network':
      return 'The saved evidence says this provider is out of network for the selected plan.'
    case 'network_tier_unknown':
      return 'The saved plan and rendering provider have no resolved network tier.'
    case 'network_conflict':
      return 'The payer and clinic network evidence conflict for this provider and plan.'
    case 'office_deductible_unresolved':
      return 'The office deductible applies and has a remaining balance, so the fixed copay alone is incomplete.'
    case 'office_coinsurance_unresolved':
      return 'The office benefit includes nonzero coinsurance, so the fixed copay alone is incomplete.'
    case 'oop_remaining_unknown':
      return 'The individual out-of-pocket remainder was not returned for this office benefit.'
    case 'other_coverage':
      return 'Another active policy may change which insurer pays first.'
    case 'payer_source_error':
      return 'The saved payer benefit row has a source error.'
    case 'adverse_requirement':
      return issue.detail || 'The selected office service has a denied or expired requirement.'
    case 'clinic_no_collection_instruction':
      return 'The clinic record explicitly directs no collection at this visit.'
    default:
      return 'The saved visit has an unresolved collection condition.'
  }
}
