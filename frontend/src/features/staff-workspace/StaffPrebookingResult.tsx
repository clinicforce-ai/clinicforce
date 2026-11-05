import type { PrebookingEstimate, PrebookingTerms } from '../../services/api/prebooking-eligibility'

const money = (cents: number | null | undefined) =>
  cents == null
    ? 'Not reported'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)

function BenefitTerms({ terms }: { terms: PrebookingTerms }) {
  return (
    <dl className="prebooking-benefits">
      <div>
        <dt>Copay</dt>
        <dd>{money(terms.copay_cents)}</dd>
      </div>
      <div>
        <dt>Coinsurance</dt>
        <dd>
          {terms.coinsurance_percent == null ? 'Not reported' : `${terms.coinsurance_percent}%`}
        </dd>
      </div>
      <div>
        <dt>Annual deductible</dt>
        <dd>{money(terms.deductible_annual_cents)}</dd>
      </div>
      <div>
        <dt>Deductible remaining</dt>
        <dd>{money(terms.deductible_remaining_cents)}</dd>
      </div>
      <div>
        <dt>Annual out-of-pocket max</dt>
        <dd>{money(terms.oop_annual_cents)}</dd>
      </div>
      <div>
        <dt>Out-of-pocket remaining</dt>
        <dd>{money(terms.oop_remaining_cents)}</dd>
      </div>
    </dl>
  )
}

export default function StaffPrebookingResult({ result }: { result: PrebookingEstimate }) {
  const address = result.patient_address
  const locality = [address?.city, address?.state].filter(Boolean).join(', ')
  const localityAndPostal = [locality, address?.postal_code].filter(Boolean).join(' ')
  const amount =
    result?.patient_min_cents != null && result.patient_max_cents != null
      ? result.patient_min_cents === result.patient_max_cents
        ? money(result.patient_max_cents)
        : `${money(result.patient_min_cents)}–${money(result.patient_max_cents)}`
      : null

  return (
    <section className="staff-panel prebooking-result" role="status" aria-live="polite">
      <h2>Prospective visit estimate</h2>
      <p
        className={
          result.coverage.status === 'active_today'
            ? 'staff-status'
            : 'staff-status staff-status-attention'
        }
      >
        {result.coverage.status === 'active_today'
          ? 'Coverage active for checked date'
          : result.coverage.status === 'inactive_today'
            ? 'Coverage inactive for checked date'
            : 'Coverage needs review'}
      </p>
      <p>{result.coverage.message}</p>
      <p className="staff-small staff-muted">
        {result.payer}
        {result.plan_name ? ` · ${result.plan_name}` : ''} · Checked for {result.checked_on}
        {result.check_ids.length ? ` · Stedi ${result.check_ids.join(', ')}` : ''}
      </p>
      <h3>Patient address from Stedi</h3>
      {address ? (
        <address className="prebooking-patient-address">
          {address.line1 && <span>{address.line1}</span>}
          {address.line2 && <span>{address.line2}</span>}
          {localityAndPostal && <span>{localityAndPostal}</span>}
          {address.country && <span>{address.country}</span>}
        </address>
      ) : (
        <p className="staff-muted">Not available from a matched Stedi response.</p>
      )}
      <h3>Clinic network</h3>
      <p>
        {result.network_status === 'in_network'
          ? 'In network for the matched plan and clinic context'
          : result.network_status === 'out_of_network'
            ? 'Out of network for the matched plan and clinic context'
            : 'Network participation unverified'}
      </p>
      {result.network_evidence?.reason && (
        <p className="staff-small staff-muted">{result.network_evidence.reason}</p>
      )}
      {result.benefits && (
        <>
          <h3>General medical benefits</h3>
          <BenefitTerms terms={result.benefits} />
        </>
      )}
      {result.lines.length > 0 && (
        <>
          <h3>Selected services</h3>
          {result.lines.map((line) => (
            <div className="prebooking-result-line" key={line.code}>
              <strong>
                {line.code} × {line.units}
              </strong>
              {line.role !== 'visit' && (
                <span className="prebooking-procedure-label">Additional procedure or test</span>
              )}
              <span>
                Allowed {money(line.allowed_cents)} · Patient share {money(line.patient_cents)}
              </span>
              <BenefitTerms terms={line.benefits} />
              {line.issues.map((issue, index) => (
                <p key={index} className="staff-small staff-muted">
                  {issue}
                </p>
              ))}
            </div>
          ))}
        </>
      )}
      <div className="prebooking-total">
        <span>Estimated patient share</span>
        <strong>{amount || 'Amount needs review'}</strong>
      </div>
      {result.estimate_status === 'conditional' && (
        <p className="staff-small staff-muted">
          Conditional planning estimate. Resolve benefit or approval conditions before collecting.
        </p>
      )}
      {result.issues.length > 0 && (
        <>
          <h3>What to verify</h3>
          <ul>
            {result.issues.map((issue, index) => (
              <li key={index}>{issue}</li>
            ))}
          </ul>
        </>
      )}
      <p className="staff-small staff-muted">
        For the selected services and date, not a payment guarantee or amount to collect. Recheck
        after booking.
      </p>
    </section>
  )
}
