import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

export default function PayerReportedPlanFigures({ result }: { result: PrevisitEstimate }) {
  const benefits = result.benefits!
  const oopRows = (benefits.reported_options || []).filter((row) => row.kind === 'out_of_pocket')
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  const officeLines = lines.filter((line) => /^992(?:0[2-5]|1[2-5])$/.test(line.code))
  const officeCopays = officeLines.map((line) =>
    line.benefits?.active === true ? line.benefits.copay_cents : null,
  )
  const officeCopay =
    officeLines.length && officeCopays.every((value) => value != null && value === officeCopays[0])
      ? officeCopays[0]
      : null
  const coinsuranceGroups = new Map<string, string[]>()
  for (const line of lines) {
    const percent = line.benefits?.coinsurance_percent
    if (percent == null || !Number.isFinite(Number(percent))) continue
    const label = `${Number(percent).toLocaleString()}%`
    coinsuranceGroups.set(label, [...(coinsuranceGroups.get(label) || []), line.code])
  }
  const deductibleGroups = new Map<string, string[]>()
  for (const line of lines) {
    const terms = line.benefits
    if (!terms) continue
    const annual = terms.deductible_annual_cents ?? benefits.deductible_annual_cents
    const remaining = terms.deductible_remaining_cents ?? benefits.deductible_remaining_cents
    const label =
      terms.deductible_applies === false
        ? 'Does not apply'
        : `${remaining != null ? `${money(remaining)} remaining` : 'Remaining balance not returned'}${annual != null ? ` · ${money(annual)} annual` : ''}`
    deductibleGroups.set(label, [...(deductibleGroups.get(label) || []), line.code])
  }
  const providerTierEstablished =
    (benefits.network_status === 'in_network' &&
      [
        'clinic_library_contract',
        'clinic_group_agreement',
        'clinic_nmct_policy',
        'payer_participation_message',
      ].includes(benefits.network_source || '')) ||
    (benefits.network_status === 'out_of_network' &&
      [
        'payer_participation_message',
        'clinic_nonparticipation_rule',
        'clinic_contract_inventory',
      ].includes(benefits.network_source || ''))
  return (
    <section className="payer-reported-terms" aria-label="Payer-reported benefit terms">
      <h3>Payer-reported benefit terms</h3>
      {!providerTierEstablished && (
        <p>
          Match this plan and rendering provider’s network tier before using these figures for
          collection.
        </p>
      )}
      <dl className="arrival-money-picture" aria-label="Payer-reported benefit terms">
        {(officeLines.length ? officeCopay != null : benefits.copay_cents != null) && (
          <div>
            <dt>{officeLines.length ? 'Office visit copay' : 'Copay'}</dt>
            <dd>{money(officeLines.length ? officeCopay! : benefits.copay_cents!)}</dd>
          </div>
        )}
        {[...deductibleGroups].map(([label, codes]) => (
          <div key={label}>
            <dt>Individual deductible · {codes.map((code) => `CPT ${code}`).join(', ')}</dt>
            <dd>{label}</dd>
          </div>
        ))}
        {!deductibleGroups.size &&
          (benefits.deductible_annual_cents != null ||
            benefits.deductible_remaining_cents != null) && (
            <div>
              <dt>Individual deductible</dt>
              <dd>
                {benefits.deductible_remaining_cents != null
                  ? `${money(benefits.deductible_remaining_cents)} remaining`
                  : 'Remaining balance not returned'}
                {benefits.deductible_annual_cents != null &&
                  ` · ${money(benefits.deductible_annual_cents)} annual`}
              </dd>
            </div>
          )}
        {coinsuranceGroups.size > 0 ? (
          [...coinsuranceGroups].map(([percent, codes]) => (
            <div key={percent}>
              <dt>Coinsurance · {codes.map((code) => `CPT ${code}`).join(', ')}</dt>
              <dd>{percent}</dd>
            </div>
          ))
        ) : benefits.coinsurance_percent != null &&
          Number.isFinite(Number(benefits.coinsurance_percent)) ? (
          <div>
            <dt>Coinsurance</dt>
            <dd>{Number(benefits.coinsurance_percent).toLocaleString()}%</dd>
          </div>
        ) : null}
        {benefits.oop_remaining_cents != null && (
          <div>
            <dt>Individual out-of-pocket remaining</dt>
            <dd>{money(benefits.oop_remaining_cents)}</dd>
          </div>
        )}
        {benefits.oop_remaining_cents == null && !!benefits.oop_remaining_conflict_ids?.length && (
          <div>
            <dt>Individual out-of-pocket remaining</dt>
            <dd>Payer rows conflict</dd>
          </div>
        )}
        {benefits.oop_annual_cents != null && (
          <div>
            <dt>Individual out-of-pocket maximum</dt>
            <dd>{money(benefits.oop_annual_cents)}</dd>
          </div>
        )}
        {benefits.oop_annual_cents == null && !!benefits.oop_annual_conflict_ids?.length && (
          <div>
            <dt>Individual out-of-pocket maximum</dt>
            <dd>Payer rows conflict</dd>
          </div>
        )}
      </dl>
      {!!oopRows.length && (
        <details>
          <summary>Out-of-pocket source rows and plan association</summary>
          <p>
            Each payer row stays separate. A medical candidate is not a confirmed limit for today’s
            charges; an unassigned row is excluded from medical pricing when another coverage
            product appears in the response.
          </p>
          <ul>
            {oopRows.map((row, index) => (
              <li key={row.id || index}>
                {row.plan_association === 'medical_candidate'
                  ? 'Medical candidate'
                  : row.plan_association === 'supplementary'
                    ? 'Supplementary coverage'
                    : row.plan_association === 'medical'
                      ? 'Medical coverage'
                      : 'Unassigned'}{' '}
                · {row.amount_cents != null ? money(row.amount_cents) : 'amount unavailable'} ·{' '}
                {row.time_period || 'period not returned'} ·{' '}
                {row.insurance_type || 'insurance type not returned'} ·{' '}
                {row.network || 'network not returned'} · STC{' '}
                {row.service_type_code || 'not returned'}
                {row.plan_name && ` · ${row.plan_name}`}
                {row.payer_note && ` · ${row.payer_note}`}
                <small>
                  Response row: {row.id || 'not retained'}
                  {row.effective_dates &&
                    Object.entries(row.effective_dates).map(
                      ([field, value]) => ` · ${field}: ${value}`,
                    )}
                </small>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
