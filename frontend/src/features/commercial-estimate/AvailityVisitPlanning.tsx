import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import CostBreakdownTable, { type CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money, visitDate } from './format'
import { requirementStatus } from './commercialRequirementStatus'

function plannedCollection(result: PrevisitEstimate, copay: number): number {
  if (
    result.billing_protection?.status === 'qmb' ||
    result.collection_guidance?.basis === 'qmb_protection' ||
    result.collection_guidance?.basis === 'claim_after_service'
  )
    return 0
  const prior = Math.max(0, result.encounter_summary?.payments_verified_cents || 0)
  return Math.max(0, Math.min(copay, result.availity_visit_reference!.oop_remaining_cents) - prior)
}

export function AvailityArrivalAmount({ result }: { result: PrevisitEstimate }) {
  const evidence = result.availity_visit_reference!
  const amount = plannedCollection(result, evidence.copay_cents)
  const deferred = result.collection_guidance?.basis === 'claim_after_service'
  const protectedPatient =
    result.billing_protection?.status === 'qmb' ||
    result.collection_guidance?.basis === 'qmb_protection'
  return (
    <div aria-label="Availity office visit planning amount">
      <span className="visit-label">Amount to collect at visit · pending verification</span>
      <h3>{money(amount)}</h3>
      <p>
        {protectedPatient
          ? 'Confirmed QMB billing protection prevents collection of Medicare cost sharing.'
          : deferred
            ? 'Clinic policy defers any patient collection until after the claim.'
            : 'Specialist office copay from visit-date Availity eligibility. Verify QMB status and prior payments before charging.'}
      </p>
      <small>This eligibility PDF is a copay reference, not a real-time claim estimate.</small>
    </div>
  )
}

export function AvailityVisitTable({
  result,
  model,
}: {
  result: PrevisitEstimate
  model: CommercialEstimateModel
}) {
  const evidence = result.availity_visit_reference!
  const code =
    result.codes.length === 1 &&
    result.codes[0].code === '99203' &&
    Number(result.codes[0].units) === 1 &&
    !result.codes[0].modifiers?.length
      ? result.codes[0]
      : null
  const rate = code
    ? result.price_range?.lines?.find(
        (line) => line.id === code.id && line.allowed_basis === 'aetna_fee_schedule' &&
          line.rate_resolution?.status === 'RESOLVED',
      )
    : null
  const amount = plannedCollection(result, evidence.copay_cents)
  const protectedPatient =
    result.billing_protection?.status === 'qmb' ||
    result.collection_guidance?.basis === 'qmb_protection'
  const collectionNote = protectedPatient
    ? 'Confirmed QMB billing protection prevents collection.'
    : result.collection_guidance?.basis === 'claim_after_service'
      ? 'Clinic policy defers collection until after the claim.'
      : 'Pending QMB and prior-payment verification; do not charge until cleared.'
  const rows: CostBreakdownRow[] = [
    {
      label: 'Coverage',
      value: `AETNA · ${evidence.plan}`,
      detail: `Active on ${visitDate(evidence.service_date)} · Availity eligibility response`,
    },
    {
      label: 'Network',
      value: 'In network',
      detail:
        'Availity says the entered requesting provider participates in this patient’s network.',
    },
    {
      label: 'Reason for visit',
      value: result.reason || model.anticipatedService || 'Not recorded',
    },
    {
      label: 'LLM-proposed CPTs',
      value: code ? 'CPT 99203 × 1' : 'No CPT 99203 selected',
      detail: code
        ? 'Code proposal for planning; billed services depend on care performed.'
        : 'Review and select the office visit code before using the fee reference.',
    },
    {
      label: 'CPT 99203 · Aetna allowed fee',
      value: rate?.allowed_cents != null ? money(rate.allowed_cents) : 'Not available',
      detail: rate
        ? `${rate.rate_source || 'Clinic-approved Aetna fee schedule'}; final claim payment and patient share still depend on benefits and billing protection.`
        : 'An applicable database fee requires an identified Aetna plan category and one unmodified CPT 99203 office line.',
    },
    {
      label: 'Total payer allowed',
      value: rate?.allowed_cents != null ? money(rate.allowed_cents) : 'Not established',
      detail: 'The scheduled fee is not a processed claim payment.',
    },
    {
      label: 'Copay',
      value: money(evidence.copay_cents),
      detail: 'In-network specialist visit or evaluation, place of service Office, individual.',
    },
    {
      label: 'Individual deductible',
      value: '$0.00 for in-network office visit',
      detail:
        'Published 2026 Signature PPO plan has no in-network deductible; the $750 general row is not assigned to this office visit.',
    },
    { label: 'Family deductible', value: 'Not returned for this visit' },
    {
      label: 'Coinsurance',
      value: `${evidence.coinsurance_percent}%`,
      detail: 'Availity in-network specialist office row.',
    },
    {
      label: 'Individual out-of-pocket maximum',
      value: `${money(evidence.oop_annual_cents)} annual · ${money(evidence.oop_remaining_cents)} remaining`,
      detail: 'Availity in-network individual balance on the visit date.',
    },
    { label: 'Family out-of-pocket maximum', value: 'Not returned for this visit' },
    { label: 'Referral', value: code ? requirementStatus(result, 'referral') : 'Not determined' },
    {
      label: 'Prior authorization',
      value: code ? requirementStatus(result, 'authorization') : 'Not determined',
    },
    {
      label: 'Estimated patient share',
      value: money(Math.min(evidence.copay_cents, evidence.oop_remaining_cents)),
      detail: 'Provisional office-only copay before the processed claim and any prior payments.',
    },
    {
      label: 'Amount to collect at visit',
      value: money(amount),
      emphasis: true,
    },
  ]
  return (
    <>
      <CostBreakdownTable rows={rows} />
      <p className="visit-identity-notice" role="note">
        {collectionNote}
      </p>
    </>
  )
}
