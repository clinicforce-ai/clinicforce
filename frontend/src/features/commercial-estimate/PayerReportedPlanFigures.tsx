import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

export default function PayerReportedPlanFigures({
  benefits,
}: {
  benefits: NonNullable<PrevisitEstimate['benefits']>
}) {
  const oopRows = (benefits.reported_options || []).filter((row) => row.kind === 'out_of_pocket')
  const providerTierEstablished =
    (benefits.network_status === 'in_network' &&
      ['clinic_library_contract', 'payer_participation_message'].includes(
        benefits.network_source || '',
      )) ||
    (benefits.network_status === 'out_of_network' &&
      [
        'payer_participation_message',
        'clinic_nonparticipation_rule',
        'clinic_contract_inventory',
      ].includes(benefits.network_source || ''))
  return (
    <details className="payer-reported-terms">
      <summary>Payer-reported benefit terms</summary>
      {!providerTierEstablished && (
        <p>
          Match this plan and rendering provider’s network tier before using these figures for
          collection.
        </p>
      )}
      <dl className="arrival-money-picture" aria-label="Payer-reported benefit terms">
        {benefits.copay_cents != null && (
          <div>
            <dt>Copay</dt>
            <dd>{money(benefits.copay_cents)}</dd>
          </div>
        )}
        {benefits.deductible_remaining_cents != null && (
          <div>
            <dt>Deductible remaining</dt>
            <dd>{money(benefits.deductible_remaining_cents)}</dd>
          </div>
        )}
        {benefits.coinsurance_percent != null &&
          Number.isFinite(Number(benefits.coinsurance_percent)) && (
            <div>
              <dt>Coinsurance</dt>
              <dd>{Number(benefits.coinsurance_percent).toLocaleString()}%</dd>
            </div>
          )}
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
    </details>
  )
}
