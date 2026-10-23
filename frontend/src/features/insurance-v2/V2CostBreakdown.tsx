import type { V2Case, V2Transaction } from '../../services/api/insurance-v2-types'
import CostBreakdownTable, { type CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { serviceRowLabel } from '../insurance-estimate/serviceRowLabel'
import { money, readable } from './v2-display'

export default function V2CostBreakdown({
  insuranceCase,
  kind,
}: {
  insuranceCase: V2Case
  kind: 'scheduled' | 'possible'
}) {
  const price = insuranceCase.summary[kind]
  const transaction: V2Transaction | undefined =
    kind === 'scheduled'
      ? insuranceCase.transactions.estimate
      : insuranceCase.transactions.procedure_estimate
  const lines = transaction?.result?.lines || []
  const component = (field: 'copay_cents' | 'deductible_cents' | 'coinsurance_cents') =>
    lines.length && lines.every((line) => line[field] != null)
      ? money(lines.reduce((total, line) => total + line[field]!, 0))
      : 'Not returned for these CPTs'
  const rows: CostBreakdownRow[] = [
    {
      label: 'Coverage',
      value: `${transaction?.result?.payer || 'Payer not returned'} · ${readable(insuranceCase.summary.coverage)}`,
    },
    { label: 'Network', value: price.network || 'Not returned' },
    ...(kind === 'scheduled' && insuranceCase.inputs.reason
      ? [{ label: 'Reason for visit', value: insuranceCase.inputs.reason }]
      : []),
    ...(lines.length
      ? lines.map((line) => ({
          label: line.code
            ? serviceRowLabel({
                description: line.description,
                reason: kind === 'scheduled' ? insuranceCase.inputs.reason : 'Possible procedure',
                code: line.code,
                units: line.quantity,
              })
            : 'Service code not returned',
          value: money(line.allowed_cents),
          detail: line.description,
        }))
      : [{ label: 'Planned CPTs', value: price.codes.join(', ') || 'Not selected' }]),
    { label: 'Total allowed', value: money(price.allowed_cents) },
    { label: 'Copay applied', value: component('copay_cents') },
    { label: 'Deductible applied', value: component('deductible_cents') },
    { label: 'Coinsurance applied', value: component('coinsurance_cents') },
    {
      label: 'Referral',
      value:
        insuranceCase.summary.referral.status ||
        (insuranceCase.summary.referral.required ? 'Required' : 'Not verified'),
    },
    {
      label: 'Prior authorization',
      value:
        insuranceCase.summary.authorization.status ||
        (insuranceCase.summary.authorization.required ? 'Required' : 'Not verified'),
    },
    { label: 'Cash alternative', value: money(price.cash_cents) },
    {
      label: 'Estimated patient share',
      value: money(price.insurance_cents),
      detail: 'Copay + deductible + coinsurance, subject to the payer result and plan limits',
    },
    {
      label:
        kind === 'scheduled' && insuranceCase.summary.action === 'collect'
          ? 'Amount to collect at visit'
          : 'Collection decision',
      value:
        kind === 'scheduled' &&
        insuranceCase.summary.action === 'collect' &&
        price.insurance_cents != null
          ? money(price.insurance_cents)
          : kind === 'possible'
            ? 'Separate scenario; collect only if performed and approved'
            : insuranceCase.summary.heading,
      emphasis: true,
    },
  ]
  return (
    <CostBreakdownTable
      rows={rows}
      title={
        kind === 'scheduled' ? 'Scheduled visit cost and collection' : 'Possible procedure cost'
      }
    />
  )
}
