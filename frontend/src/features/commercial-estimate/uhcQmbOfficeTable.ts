import type { PrevisitEstimate } from '../../services/api/commercial-previsit'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { money } from './format'

/** Present the matched QMB product's visit decision beside the unchanged payer rows. */
export function uhcQmbOfficeRows(
  result: PrevisitEstimate,
  rows: CostBreakdownRow[],
): CostBreakdownRow[] | null {
  if (
    result.collection_guidance?.basis !== 'published_uhc_tx_qmb_office' ||
    result.collection_guidance.amount_cents !== 0
  )
    return null

  const row = (label: string): CostBreakdownRow =>
    rows.find((item) => item.label === label) || { label, value: 'Not returned for this visit' }
  const line = result.price_range?.mode === 'services' ? result.price_range.lines?.[0] : null
  const service = rows.find(
    (item) => item.label.includes('CPT 99213') && item.label !== 'LLM-proposed CPTs',
  )
  const deductible = row('Individual deductible')
  const familyDeductible = row('Family deductible')
  const oop = row('Individual out-of-pocket maximum')
  const familyOop = row('Family out-of-pocket maximum')
  const coinsurance = row('Coinsurance')
  const percent = line?.benefits?.coinsurance_percent ?? result.benefits?.coinsurance_percent
  const allowed = row('Total payer allowed')
  return [
    row('Coverage'),
    row('Member and group'),
    {
      ...row('Network'),
      detail:
        'Eligibility does not establish this clinician’s product-specific network tier. Confirm participation with UHC.',
    },
    row('Reason for visit'),
    rows.find((item) => item.label === 'LLM-proposed CPTs' || item.label === 'CPTs') || {
      label: 'CPTs',
      value: 'CPT 99213 × 1',
    },
    service || {
      label: `${result.reason || 'Specialist follow-up'} · CPT 99213 × 1`,
      value: line?.allowed_cents == null ? 'Allowed amount unavailable' : money(line.allowed_cents),
    },
    {
      ...allowed,
      value: line?.allowed_cents == null ? 'Not fully priced' : allowed.value,
      detail:
        'The clinic fee schedule, if shown, is an estimate. The processed UHC claim determines the final allowance.',
    },
    {
      label: 'Copay',
      value: money(0),
      detail:
        'Payer-reported specialist office copay for CPT 99213; the matched QMB product also prohibits Medicare cost-sharing collection.',
    },
    {
      ...deductible,
      detail:
        'Raw payer-reported plan accumulator. The published QMB medical benefit has no patient-pay medical deductible for covered care.',
    },
    {
      ...familyDeductible,
      detail:
        'Raw payer-reported plan accumulator; it does not create a collectible QMB office charge.',
    },
    {
      ...coinsurance,
      value:
        percent == null
          ? coinsurance.value
          : `${percent}% reported; $0.00 collectible for covered QMB care`,
      detail:
        'The 271 cost-sharing row is retained for review. QMB billing protection controls patient collection.',
    },
    {
      ...oop,
      detail:
        'Raw payer-reported accumulator; confirm the applicable medical limit. QMB protection controls collection for covered care.',
    },
    familyOop,
    {
      label: 'Referral',
      value: 'CPT 99213: required · confirm on file',
      detail:
        'The 2026 UHC Texas Dual Complete guide requires a PCP specialist referral. Confirm the member’s referral and PCP group.',
    },
    {
      label: 'Prior authorization',
      value: 'CPT 99213: verify delegated requirements',
      detail:
        'CPT 99213 is absent from the July 2026 WellMed list; check this member’s delegated group and current payer rules.',
    },
    {
      label: 'Estimated patient share',
      value: money(0),
      detail:
        'For one proposed covered CPT 99213 specialist visit under the matched 2026 UHC QMB product. Final coding and claim adjudication still apply.',
    },
    {
      label: 'Amount to collect at visit',
      value: money(0),
      detail: result.collection_guidance.reason,
      emphasis: true,
    },
  ]
}
