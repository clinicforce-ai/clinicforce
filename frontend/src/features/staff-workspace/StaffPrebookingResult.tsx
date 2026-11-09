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
  const patient =
    result.patient_min_cents != null && result.patient_max_cents != null
      ? result.patient_min_cents === result.patient_max_cents
        ? money(result.patient_max_cents)
        : `${money(result.patient_min_cents)}–${money(result.patient_max_cents)}`
      : null
  const priced =
    result.estimate_status !== 'needs_review' &&
    result.allowed_total_cents != null &&
    patient != null
  const patientPercent =
    priced && result.allowed_total_cents! > 0
      ? Math.min(
          100,
          Math.max(0, ((result.patient_max_cents ?? 0) / result.allowed_total_cents!) * 100),
        )
      : 0
  const networkLabel =
    result.network_status === 'in_network'
      ? 'In network'
      : result.network_status === 'out_of_network'
        ? 'Out of network'
        : 'Needs review'
  const networkSource =
    result.network_evidence?.source === 'clinic_library_contract'
      ? 'NMCT clinic contract'
      : result.network_evidence?.source === 'payer_participation_message'
        ? 'Payer provider statement'
        : null

  return (
    <section
      className="staff-panel prebooking-result"
      role="status"
      aria-live="polite"
      aria-labelledby="prebooking-result-title"
    >
      <header className="prebooking-result-header">
        <div>
          <p className="staff-eyebrow">Check before booking</p>
          <h2 id="prebooking-result-title">Prospective visit estimate</h2>
          <p className="staff-small staff-muted">
            {result.payer}
            {result.plan_name ? ` · ${result.plan_name}` : ''} · Checked for {result.checked_on}
          </p>
        </div>
        <span className={`prebooking-result-badge ${priced ? 'is-ready' : 'needs-review'}`}>
          {priced
            ? result.estimate_status === 'conditional'
              ? 'Conditional estimate'
              : 'Estimate ready'
            : 'Needs review'}
        </span>
      </header>
      <div className="prebooking-status-row">
        <div>
          <span>Coverage</span>
          <strong>
            {result.coverage.status === 'active_today'
              ? 'Active for checked date'
              : result.coverage.status === 'inactive_today'
                ? 'Inactive for checked date'
                : 'Needs review'}
          </strong>
          <small>{result.coverage.message}</small>
        </div>
        <div>
          <span>NMCT network</span>
          <strong>{networkLabel}</strong>
          {networkSource && <small>Source: {networkSource}</small>}
          <small>
            {result.network_evidence?.reason ||
              'Provider participation is not established for this check.'}
          </small>
        </div>
      </div>
      <div className="prebooking-cost-grid">
        <div className="prebooking-cost-card">
          <span>Total estimated visit cost</span>
          <strong>{priced ? money(result.allowed_total_cents) : 'Needs review'}</strong>
          <small>Contract-allowed cost for selected services</small>
        </div>
        <div className="prebooking-cost-card is-patient">
          <span>Estimated patient share</span>
          <strong>{priced ? patient : 'Needs review'}</strong>
          <small>Not an amount to collect before the visit</small>
        </div>
      </div>
      {priced && result.plan_total_cents != null && (
        <div className="prebooking-share">
          <div className="prebooking-share-labels">
            <span>Patient {patient}</span>
            <span>Plan {money(result.plan_total_cents)}</span>
          </div>
          <div
            className="prebooking-share-track"
            role="img"
            aria-label={`Estimated visit cost: patient ${patient}, plan ${money(result.plan_total_cents)}`}
          >
            <span style={{ width: `${patientPercent}%` }} />
          </div>
        </div>
      )}
      {result.benefits && (
        <section className="prebooking-benefit-section" aria-labelledby="prebooking-benefit-title">
          <h3 id="prebooking-benefit-title">General medical benefits</h3>
          <BenefitTerms terms={result.benefits} />
        </section>
      )}
      {result.lines.length > 0 && (
        <section aria-labelledby="prebooking-services-title">
          <h3 id="prebooking-services-title">Clinician-confirmed planned services</h3>
          <div className="prebooking-service-list">
            {result.lines.map((line) => (
              <article className="prebooking-service-row" key={line.code}>
                <div className="prebooking-service-name">
                  <strong>
                    {line.code} × {line.units}
                  </strong>
                  <span>
                    {line.role !== 'visit' ? 'Possible procedure or test' : 'Office visit'}
                  </span>
                </div>
                <div>
                  <span>Allowed cost</span>
                  <strong>{money(line.allowed_cents)}</strong>
                </div>
                <div>
                  <span>Patient share</span>
                  <strong>{money(line.patient_cents)}</strong>
                </div>
                <small className="prebooking-rate-source">
                  {line.rate_source
                    ? `Rate source: ${line.rate_source}`
                    : 'Rate source needs review'}
                </small>
                {(line.issues.length > 0 || line.conditions.length > 0) && (
                  <ul className="prebooking-line-issues">
                    {[...line.issues, ...line.conditions].map((issue, index) => (
                      <li key={index}>{issue}</li>
                    ))}
                  </ul>
                )}
                <details className="prebooking-line-benefits">
                  <summary>Service benefits</summary>
                  <BenefitTerms terms={line.benefits} />
                </details>
              </article>
            ))}
          </div>
        </section>
      )}
      <section className="prebooking-detail-row" aria-labelledby="prebooking-address-title">
        <h3 id="prebooking-address-title">Patient address from Stedi</h3>
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
      </section>
      {result.issues.length > 0 && (
        <section className="prebooking-review" aria-labelledby="prebooking-review-title">
          <h3 id="prebooking-review-title">What to verify</h3>
          <ul>
            {result.issues.map((issue, index) => (
              <li key={index}>{issue}</li>
            ))}
          </ul>
        </section>
      )}
      {result.estimate_status === 'conditional' && (
        <p className="staff-small staff-muted">
          Resolve the listed benefit or approval conditions before using this estimate.
        </p>
      )}
      <p className="staff-small staff-muted prebooking-result-footnote">
        Estimate for selected services and checked date. Final responsibility depends on services
        performed and payer adjudication. Recheck after booking.
        {result.check_ids.length ? ` Stedi check: ${result.check_ids.join(', ')}.` : ''}
      </p>
    </section>
  )
}
