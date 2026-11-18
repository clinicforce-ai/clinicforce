import type { PrevisitEstimate } from '../../services/api/commercial-previsit'
import type { ClaimCollection } from '../../services/api/commercial-estimator'
import CostBreakdownTable, { type CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { money, visitDate } from './format'

export function PaidClaimAmount({ claim }: { claim: ClaimCollection }) {
  return (
    <>
      <span>
        Amount to collect for this visit ·{' '}
        {claim.status === 'estimated' ? 'claim-based estimate' : 'processed claim'}
      </span>
      <strong role="status">{money(claim.amount_cents)}</strong>
      <p>{claim.reason}</p>
    </>
  )
}

export function PaidClaimTable({
  claim,
  result,
}: {
  claim: ClaimCollection
  result: PrevisitEstimate
}) {
  const planned =
    result.price_range?.mode === 'services'
      ? [...new Set(result.price_range.lines?.map((line) => line.code) || [])].join(', ')
      : ''
  const rows: CostBreakdownRow[] = [
    {
      label: 'Coverage',
      value: 'Aetna · finalized paid office claim',
      detail: `Fresh payer eligibility matched this patient and the ${visitDate(result.service_date)} service date.`,
    },
    {
      label: 'Network',
      value: 'In network',
      detail:
        'Aetna stated that the queried rendering provider participates in this patient’s network.',
    },
    { label: 'Reason for visit', value: result.reason || 'Not returned' },
    {
      label: 'CPTs',
      value: `CPT ${claim.code} × ${claim.units} · actual paid claim`,
      detail:
        planned && planned !== claim.code
          ? `The saved estimate used provisional CPT ${planned}; the paid claim used CPT ${claim.code}.`
          : 'The claim service line controls this post-visit calculation.',
    },
    {
      label: 'Submitted charge',
      value: money(claim.submitted_cents),
      detail: 'Reported by Aetna claim status.',
    },
    {
      label: claim.allowed_confirmed ? 'Payer allowed amount' : 'Implied allowed amount',
      value: money(claim.allowed_cents),
      detail: claim.allowed_confirmed
        ? 'Reported by the linked 835 remittance.'
        : 'Calculated from Aetna’s payment and the reported coinsurance; the 277 did not return an allowed amount.',
    },
    {
      label: 'Aetna paid',
      value: money(claim.insurer_paid_cents),
      detail: 'Finalized paid claim.',
    },
    {
      label: 'Copay',
      value: money(claim.copay_cents),
      detail: 'Fresh in-network specialist office benefit.',
    },
    {
      label: 'Individual deductible',
      value:
        claim.deductible_remaining_cents == null
          ? 'Not returned'
          : `${money(claim.deductible_remaining_cents)} remaining at fresh inquiry`,
      detail: 'A fresh remaining balance does not reconstruct the October claim-date balance.',
    },
    {
      label: 'Coinsurance',
      value: `${claim.coinsurance_percent}% in-network`,
      detail: 'Fresh specialist office benefit; applied here as an inference from the paid claim.',
    },
    {
      label: 'Individual out-of-pocket maximum',
      value:
        claim.oop_remaining_cents == null
          ? 'Not returned'
          : `${money(claim.oop_remaining_cents)} remaining at fresh inquiry`,
      detail: 'The fresh remaining balance is not used as the historical claim-date balance.',
    },
    {
      label: 'Patient share',
      value: `${money(claim.patient_share_cents)} ${claim.status === 'estimated' ? 'inferred' : 'adjudicated'}`,
      detail:
        claim.status === 'estimated'
          ? `${money(claim.allowed_cents)} implied allowance − ${money(claim.insurer_paid_cents)} Aetna payment.`
          : 'Patient responsibility from the linked 835 remittance.',
    },
    {
      label: 'Amount to collect for this visit',
      value: `${money(claim.amount_cents)} ${claim.status === 'estimated' ? 'estimated' : 'after verified payments'}`,
      detail: claim.reason,
      emphasis: true,
    },
  ]
  return <CostBreakdownTable rows={rows} open />
}
