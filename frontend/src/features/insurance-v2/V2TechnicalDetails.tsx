import type { InsuranceV2Model } from './useInsuranceV2Model'
import { RefreshCw } from 'lucide-react'
import { createV2Case, getV2Case } from '../../services/api/insurance-v2'
import { readable, dateTime } from './v2-display'

export default function V2TechnicalDetails({
  model,
  insuranceCase,
}: {
  model: InsuranceV2Model
  insuranceCase: NonNullable<InsuranceV2Model['insuranceCase']>
}) {
  const {
    staffIntakeId,
    busy,
    perform,
    demo,
    ecwPreview,
    ecwSource,
    additionalMissing,
    coverage,
    stediCoverage,
    hasPending,
  } = model
  return (
    <details className="v2-technical">
      <summary>Patient, payer, benefits, and response details</summary>
      <div className="v2-technical-body">
        <h3>Request preview</h3>
        <dl className="v2-facts">
          <div>
            <dt>Patient</dt>
            <dd>
              {[
                insuranceCase.inputs.patient.patient_first_name,
                insuranceCase.inputs.patient.patient_last_name,
              ]
                .filter(Boolean)
                .join(' ')}
            </dd>
          </div>
          <div>
            <dt>Date of birth</dt>
            <dd>{insuranceCase.inputs.patient.patient_birth_date || 'Missing'}</dd>
          </div>
          {ecwPreview ? (
            <>
              <div>
                <dt>Patient phone</dt>
                <dd>{insuranceCase.inputs.patient_phone || 'Not returned'}</dd>
              </div>
              <div>
                <dt>Payer</dt>
                <dd>{ecwSource?.payer || 'Not returned'}</dd>
              </div>
              <div>
                <dt>Member / group</dt>
                <dd>
                  {insuranceCase.inputs.member_id || 'Not returned'} /{' '}
                  {insuranceCase.inputs.group_number || 'Not returned'}
                </dd>
              </div>
              <div>
                <dt>Subscriber</dt>
                <dd>
                  {[
                    insuranceCase.inputs.subscriber?.firstName,
                    insuranceCase.inputs.subscriber?.lastName,
                  ]
                    .filter(Boolean)
                    .join(' ') || 'Not returned'}
                </dd>
              </div>
              <div>
                <dt>Rendering provider</dt>
                <dd>
                  {[ecwSource?.rendering_provider?.name, ecwSource?.rendering_provider?.npi]
                    .filter(Boolean)
                    .join(' · ') || 'Not returned'}
                </dd>
              </div>
              <div>
                <dt>Billing provider</dt>
                <dd>
                  {[ecwSource?.billing_provider?.name, ecwSource?.billing_provider?.npi]
                    .filter(Boolean)
                    .join(' · ') || 'Not returned'}
                </dd>
              </div>
              <div>
                <dt>Location</dt>
                <dd>{ecwSource?.location || 'Not returned'}</dd>
              </div>
            </>
          ) : (
            <div>
              <dt>Member / payer ID</dt>
              <dd>
                {insuranceCase.inputs.member_id || 'Missing'} /{' '}
                {insuranceCase.inputs.payer_id || 'Missing'}
              </dd>
            </div>
          )}
          <div>
            <dt>Service date</dt>
            <dd>{insuranceCase.inputs.service_date || 'Missing'}</dd>
          </div>
          <div>
            <dt>Reason</dt>
            <dd>{insuranceCase.inputs.reason || 'Not returned'}</dd>
          </div>
          <div>
            <dt>Diagnoses</dt>
            <dd>{insuranceCase.inputs.diagnoses.join(', ') || 'Not returned'}</dd>
          </div>
        </dl>
        {additionalMissing.length > 0 && (
          <details className="v2-callout">
            <summary>
              {ecwPreview ? 'Additional appointment details' : 'Data still needed'} (
              {additionalMissing.length})
            </summary>
            <ul>
              {additionalMissing.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </details>
        )}
        {coverage?.result?.plans?.map((plan, index) => (
          <details className="v2-plan" key={index}>
            <summary>
              {plan.name || plan.planName || `Plan ${index + 1}`} ·{' '}
              {plan.status || 'Status not returned'}
            </summary>
            <p>
              {plan.insuranceType} · Group {plan.groupNumber || 'not returned'} · Effective{' '}
              {plan.effectiveDate || 'not returned'} · Ends {plan.expirationDate || 'not returned'}
            </p>
            <div className="v2-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Benefit</th>
                    <th>Network</th>
                    <th>Amount</th>
                    <th>Remaining / total</th>
                    <th>Conditions</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.benefits.map((benefit, row) => (
                    <tr key={row}>
                      <td>
                        {readable(benefit.category)}
                        <small>{benefit.service}</small>
                      </td>
                      <td>
                        {readable(benefit.network)}
                        <small>{benefit.level}</small>
                      </td>
                      <td>{benefit.amount ?? 'Not returned'}</td>
                      <td>
                        {benefit.remaining ?? 'Not returned'} / {benefit.total ?? 'Not returned'}
                      </td>
                      <td>
                        {benefit.description}
                        {benefit.authorizationRequired && <small>Authorization required</small>}
                        {benefit.referralNumber && <small>Referral {benefit.referralNumber}</small>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ))}
        <h3>Response history</h3>
        <ol className="v2-history">
          {insuranceCase.events.map((event, index) => (
            <li key={index}>
              <span>{readable(event.stage.replaceAll('-', ' '))}</span>
              <time dateTime={event.at}>{dateTime(event.at)}</time>
            </li>
          ))}
        </ol>
        <div className="v2-technical-actions">
          <button
            className="button button-secondary"
            type="button"
            disabled={!!busy}
            onClick={() =>
              void perform('Refreshing saved case…', () =>
                getV2Case(insuranceCase.id, staffIntakeId),
              )
            }
          >
            <RefreshCw size={15} aria-hidden="true" /> Refresh saved case
          </button>
          <button
            className="button button-secondary"
            type="button"
            disabled={!!busy || hasPending}
            onClick={() =>
              void perform('Loading a new appointment…', () =>
                createV2Case(undefined, undefined, staffIntakeId),
              )
            }
          >
            {hasPending
              ? 'Finish pending response first'
              : ecwPreview
                ? 'Reload eCW appointment'
                : demo
                  ? 'Start new demo case'
                  : 'Reload eCW appointment'}
          </button>
        </div>
        <small>
          {stediCoverage
            ? 'Stedi benefits and the clinic’s contract allowed amounts produce a pre-visit estimate, not a payment guarantee. Switching Availity to production disables this bridge.'
            : 'Only Availity-returned amounts appear as insured estimates. Clinic cash prices come from the uploaded fee CSV.'}{' '}
          Case {insuranceCase.id}{' '}
          {insuranceCase.expires_at
            ? `expires ${dateTime(insuranceCase.expires_at)}`
            : 'is saved to the clinic record'}
          .
        </small>
      </div>
    </details>
  )
}
