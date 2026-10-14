import type { ReactNode } from 'react'
import { Network, ShieldCheck, Wallet } from 'lucide-react'
import type { PrevisitEstimate, RequirementEvidence } from '../../services/api/commercial-estimator'
import RequirementSourceLink from './RequirementSourceLink'
import { money, visitDate } from './format'

export function ApplicableBenefits({
  result,
  readOnly = false,
}: {
  result: PrevisitEstimate
  readOnly?: boolean
}) {
  const services = result.collection_guidance?.services || []
  const otherNetworkTerms = (result.benefits?.reported_options || []).filter((row) =>
    ['N', 'NO', 'OUT_OF_NETWORK'].includes(row.network),
  )
  const responseRow = (id?: string | null) => id && <small>Response row: {id}</small>
  return (
    <section>
      <h3>
        {result.payment_mode === 'cash'
          ? 'Insurance benefits returned for review'
          : 'Benefits used for this visit'}
      </h3>
      <p>
        {result.payment_mode === 'cash'
          ? 'These benefits do not set the clinic cash price.'
          : 'Matched to the selected services, visit date and location.'}
      </p>
      {services.map((row) => {
        const terms = result.price_range?.lines?.find((line) => line.id === row.id)?.benefits
        return (
          <article className="service-evidence" key={row.id}>
            <h4>
              CPT {row.code} · {row.benefit}
            </h4>
            <dl>
              <div>
                <dt>Benefit source</dt>
                <dd>{row.benefit_source || result.benefit_source || 'Not confirmed'}</dd>
              </div>
              {row.copay_cents != null && (
                <div>
                  <dt>Applicable copay</dt>
                  <dd>
                    {money(row.copay_cents)}
                    {responseRow(terms?.copay_id)}
                  </dd>
                </div>
              )}
              {row.coinsurance_percent != null && (
                <div>
                  <dt>Coinsurance</dt>
                  <dd>
                    {row.coinsurance_percent}%{responseRow(terms?.coinsurance_id)}
                  </dd>
                </div>
              )}
              {row.deductible_remaining_cents != null && (
                <div>
                  <dt>Deductible remaining</dt>
                  <dd>
                    {money(row.deductible_remaining_cents)}
                    {responseRow(terms?.deductible_id)}
                  </dd>
                </div>
              )}
              {row.deductible_annual_cents != null && (
                <div>
                  <dt>Annual deductible</dt>
                  <dd>
                    {money(row.deductible_annual_cents)}
                    {responseRow(terms?.deductible_annual_id)}
                  </dd>
                </div>
              )}
              <div>
                <dt>Deductible for this service</dt>
                <dd>
                  {
                    {
                      DOES_NOT_APPLY: 'Does not apply',
                      APPLIES: 'Applies first',
                      ALREADY_MET: 'Already met',
                      UNKNOWN: 'Not confirmed',
                    }[row.deductible_applicability || 'UNKNOWN']
                  }
                  {responseRow(terms?.deductible_applicability_ids?.join(' · '))}
                </dd>
              </div>
              {row.oop_remaining_cents != null && (
                <div>
                  <dt>Out-of-pocket remaining</dt>
                  <dd>
                    {money(row.oop_remaining_cents)}
                    {responseRow(terms?.oop_id)}
                  </dd>
                </div>
              )}
            </dl>
            {!!row.selected_benefit_ids?.length && (
              <details open={readOnly || undefined}>
                <summary>Response rows used</summary>
                <small>{row.selected_benefit_ids.join(' · ')}</small>
              </details>
            )}
            {row.oop_remaining_cents === 0 && (
              <p>
                The remaining out-of-pocket limit reduces the estimated cost for covered in-network
                care to $0.
              </p>
            )}
          </article>
        )
      })}
      {!services.length && (
        <p>Applicable benefits are still needed to calculate the collection amount.</p>
      )}
      {result.benefits?.network_status !== 'out_of_network' && !!otherNetworkTerms.length && (
        <div className="service-evidence">
          <h4>Other network terms reported by the plan</h4>
          <p>
            The plan also returned out-of-network terms. They do not show whether this doctor is out
            of network.
          </p>
          <ul>
            {otherNetworkTerms.map((row) => (
              <li key={row.id}>
                {row.procedure_code
                  ? `CPT ${row.procedure_code}`
                  : `Service category ${row.service_type_code || 'not stated'}`}{' '}
                · {row.kind.replaceAll('_', ' ')}{' '}
                {row.percent != null
                  ? `${row.percent}%`
                  : row.amount_cents != null
                    ? money(row.amount_cents)
                    : 'amount not returned'}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

export function AppointmentNotes({ result }: { result: PrevisitEstimate }) {
  const fields = result.appointment_context?.imported_fields || []
  const isStatus = (label: string) =>
    /^(?:(?:appointment|encounter|visit)[ _-]+)?status$/i.test(label.trim())
  const statuses = fields.filter((field) => isStatus(field.label))
  const details = fields.filter((field) => !isStatus(field.label))
  return (
    <section className="visit-imported">
      <h3>{result.reason || 'Scheduled visit'}</h3>
      <p className="visit-muted">
        {[
          result.appointment_context?.rendering_provider?.name,
          result.appointment_context?.location,
        ]
          .filter(Boolean)
          .join(' · ')}
      </p>
      {statuses.map((field, index) => (
        <p className="visit-muted" key={index}>
          Appointment status: {field.text}
        </p>
      ))}
      {details.map((field, index) => (
        <article key={`${field.source}:${index}`}>
          <h3>{field.label}</h3>
          <p>{field.text}</p>
          <small>From eCW{field.updated_at ? ` · ${visitDate(field.updated_at)}` : ''}</small>
        </article>
      ))}
    </section>
  )
}

export function CostPicture({ result }: { result: PrevisitEstimate }) {
  const price = result.price_range
  const patient = price?.patient_min_cents,
    insurer = price?.plan_min_cents
  const split =
    result.benefits?.network_status === 'in_network' &&
    price?.complete &&
    price.mode === 'services' &&
    patient != null &&
    insurer != null &&
    patient === price.patient_max_cents &&
    insurer === price.plan_max_cents &&
    patient + insurer > 0
  if (result.payment_mode === 'cash')
    return (
      <div className="cost-picture">
        <Wallet size={36} aria-hidden="true" />
        <h3>Clinic cash price</h3>
        <p>{result.cash_quote?.message || 'The clinic’s saved price for the selected services.'}</p>
      </div>
    )
  if (result.planning_estimate?.basis === 'out_of_pocket_limit')
    return (
      <div className="cost-picture cost-limit">
        <div className="cost-emblem">
          <ShieldCheck size={42} aria-hidden="true" />
        </div>
        <h3>Out-of-pocket limit reached</h3>
        <p>
          Payer reports <strong>$0 remaining</strong> for in-network care.
        </p>
        <span>Covered services · assumptions apply</span>
      </div>
    )
  if (result.benefits?.network_status === 'out_of_network')
    return (
      <div className="cost-picture">
        <Network size={36} aria-hidden="true" />
        <h3>Out-of-network benefits</h3>
        <p>
          {price?.complete
            ? 'The known share uses the payer’s allowed amount. Extra doctor charges are still unknown.'
            : 'The plan returned benefit terms, but the payer’s allowed price is still unknown.'}
        </p>
      </div>
    )
  if (split)
    return (
      <div className="cost-picture">
        <span className="visit-label">Estimated cost sharing</span>
        <div
          className="cost-share-bar"
          role="img"
          aria-label={`Patient ${money(patient)}, insurer ${money(insurer)}`}
        >
          <span style={{ flex: patient }} />
          <span style={{ flex: insurer }} />
        </div>
        <dl className="cost-share-legend">
          <div>
            <dt>
              <i />
              Patient
            </dt>
            <dd>{money(patient)}</dd>
          </div>
          <div>
            <dt>
              <i />
              Insurance
            </dt>
            <dd>{money(insurer)}</dd>
          </div>
        </dl>
        <p>
          Contracted visit total <strong>{money(patient + insurer)}</strong>
        </p>
      </div>
    )
  return (
    <div className="cost-picture">
      <Network size={36} aria-hidden="true" />
      <h3>{result.benefits?.active ? 'Policy benefits received' : 'Coverage check'}</h3>
      <p>
        {result.benefits?.active
          ? 'A final insured amount also depends on the service, provider network and clinic contract.'
          : 'The saved clinic cash price is available separately when provided.'}
      </p>
    </div>
  )
}

export function RequirementCard({
  title,
  evidence,
  icon,
  readOnly = false,
}: {
  title: string
  evidence?: RequirementEvidence
  icon: ReactNode
  readOnly?: boolean
}) {
  const status = evidence?.status || 'Verification required'
  const reason =
    evidence?.reason ||
    'The saved plan information does not state this rule for the selected services.'
  const sources = evidence?.sources || []
  return (
    <div
      className={
        status === 'Not required'
          ? 'is-confirmed'
          : status === 'Required' && evidence?.approval_status === 'Verified'
            ? 'is-confirmed'
            : 'is-review'
      }
    >
      {icon}
      <div className="requirement-content">
        {title}
        <strong>
          {status === 'Required' ? 'Yes' : status === 'Not required' ? 'No' : 'Unverified'}
        </strong>
        {evidence?.approval_status && (
          <small>
            Approval:{' '}
            {evidence.approval_status === 'Verified'
              ? 'Recorded'
              : evidence.approval_status === 'Not verified'
                ? 'Not recorded'
                : evidence.approval_status}
          </small>
        )}
        <details className="requirement-evidence" open={readOnly || undefined}>
          <summary>Why and sources</summary>
          <p>{reason}</p>
          {sources.map((source, index) => (
            <p key={`${source.document_id || source.label}:${source.page || index}`}>
              <RequirementSourceLink source={source} />
            </p>
          ))}
          {!sources.length && <p>No confirmed source</p>}
          {evidence?.approval_reference && (
            <p>
              Approval reference: {evidence.approval_reference}
              {evidence.approval_verified_at
                ? ` · verified ${visitDate(evidence.approval_verified_at)}`
                : ''}
            </p>
          )}
          {evidence?.verification && <p>{evidence.verification}</p>}
          {evidence?.contact && <p>{evidence.contact}</p>}
          {evidence?.verify_url && (
            <p>
              <a href={evidence.verify_url} target="_blank" rel="noopener noreferrer">
                Open payer portal
              </a>
            </p>
          )}
        </details>
      </div>
    </div>
  )
}
