import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'
import { requirementForLine } from './requirementForLine'

export default function UhcMedicarePpoBenefitTable({ result }: { result: PrevisitEstimate }) {
  const benefits = result.benefits!
  const lines = result.price_range?.lines || []
  const options = (benefits.reported_options || []).filter(
    (row) =>
      row.kind === 'out_of_pocket' &&
      row.service_type_code === '30' &&
      row.plan_association !== 'supplementary' &&
      row.coverage_level === 'INDIVIDUAL',
  )
  const reportedOop = (period: string) => {
    const amounts = [
      ...new Set(
        options
          .filter((row) => row.time_period === period && row.amount_cents != null)
          .map((row) => row.amount_cents!),
      ),
    ]
    return amounts.length === 1 ? money(amounts[0]) : null
  }
  const oopAnnual =
    benefits.oop_annual_cents != null
      ? money(benefits.oop_annual_cents)
      : reportedOop('CALENDAR_YEAR')
  const oopRemaining =
    benefits.oop_remaining_cents != null
      ? money(benefits.oop_remaining_cents)
      : reportedOop('REMAINING')
  const oopUsed = reportedOop('YEAR_TO_DATE')
  const oopUnassigned = benefits.oop_annual_cents == null || benefits.oop_remaining_cents == null
  const networkSource = benefits.network_source
  const tierEstablished =
    (benefits.network_status === 'in_network' &&
      [
        'clinic_library_contract',
        'clinic_group_agreement',
        'payer_participation_message',
      ].includes(networkSource || '')) ||
    (benefits.network_status === 'out_of_network' &&
      [
        'payer_participation_message',
        'clinic_nonparticipation_rule',
        'clinic_contract_inventory',
      ].includes(networkSource || ''))
  const network = tierEstablished
    ? benefits.network_status === 'out_of_network'
      ? 'Out of network'
      : 'In network'
    : result.agreement_exclusion
      ? 'Available agreement excludes this PPO; provider tier not confirmed'
      : 'Provider tier not confirmed; office copay applies in either tier'
  const termsByCode = (field: 'copay_cents' | 'coinsurance_percent') => {
    const values = lines
      .filter(
        (line) =>
          line.benefits.active === true &&
          line.benefits[field] != null &&
          Number.isFinite(Number(line.benefits[field])),
      )
      .map(
        (line) =>
          `CPT ${line.code}: ${field === 'copay_cents' ? money(Number(line.benefits[field])) : `${Number(line.benefits[field])}%`}`,
      )
    return values.length ? values.join('; ') : 'Not returned for selected CPTs'
  }
  const requirement = (kind: 'referral' | 'authorization') =>
    lines
      .map((line) => {
        const evidence = requirementForLine(
          result.requirement_evidence?.procedures,
          line.id,
          line.code,
        )?.[kind]
        const status = evidence?.status
        return `CPT ${line.code}: ${status === 'Not required' ? 'not required' : status === 'Required' ? 'required' : 'not determined'}${kind === 'authorization' && status === 'Required' && evidence?.approval_status !== 'Verified' ? ' (approval not verified)' : ''}`
      })
      .join('; ') || 'No CPT selected'
  const deductible = [
    benefits.deductible_annual_cents != null
      ? `${money(benefits.deductible_annual_cents)} annual`
      : null,
    benefits.deductible_remaining_cents != null
      ? `${money(benefits.deductible_remaining_cents)} remaining`
      : null,
    lines.some(
      (line) =>
        /^992(?:0[2-5]|1[2-5])$/.test(line.code) && line.benefits.deductible_applies === false,
    )
      ? 'does not apply to office visit'
      : null,
  ]
    .filter(Boolean)
    .join('; ')
  const oop = [
    oopAnnual ? `${oopAnnual} annual` : null,
    oopUsed ? `${oopUsed} used` : null,
    oopRemaining ? `${oopRemaining} remaining` : null,
  ]
    .filter(Boolean)
    .join('; ')
  return (
    <section className="uhc-ppo-benefit-table" aria-label="UHC Medicare PPO benefit summary">
      <h3>Insurance details for this visit</h3>
      <table>
        <tbody>
          <tr>
            <th scope="row">Plan and coverage</th>
            <td>
              {result.plan_details?.stedi.names?.[0]} ·{' '}
              {benefits.service_date_matches === true
                ? 'active for visit'
                : 'payer reports active; visit-date match not established'}
            </td>
          </tr>
          <tr>
            <th scope="row">Network</th>
            <td>{network}</td>
          </tr>
          <tr>
            <th scope="row">Deductible</th>
            <td>{deductible || 'Not returned'}</td>
          </tr>
          <tr>
            <th scope="row">Copay</th>
            <td>{termsByCode('copay_cents')}</td>
          </tr>
          <tr>
            <th scope="row">Coinsurance</th>
            <td>{termsByCode('coinsurance_percent')}</td>
          </tr>
          <tr>
            <th scope="row">Out-of-pocket maximum</th>
            <td>
              {oop || 'Not returned'}
              {oop && oopUnassigned
                ? ' · payer-reported; medical-plan association not confirmed'
                : ''}
            </td>
          </tr>
          <tr>
            <th scope="row">Planned CPTs</th>
            <td>
              {lines
                .map(
                  (line) => `CPT ${line.code}${Number(line.units) > 1 ? ` × ${line.units}` : ''}`,
                )
                .join(', ')}
            </td>
          </tr>
          <tr>
            <th scope="row">Referral</th>
            <td>{requirement('referral')}</td>
          </tr>
          <tr>
            <th scope="row">Prior authorization</th>
            <td>{requirement('authorization')}</td>
          </tr>
        </tbody>
      </table>
    </section>
  )
}
