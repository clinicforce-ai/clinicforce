import type { InsuranceV2Model } from './useInsuranceV2Model'
import { Clipboard, Mail } from 'lucide-react'
import V2CostBreakdown from './V2CostBreakdown'
import { money, readable } from './v2-display'

export default function EcwDecisionSection({
  model,
  insuranceCase,
}: {
  model: InsuranceV2Model
  insuranceCase: NonNullable<InsuranceV2Model['insuranceCase']>
}) {
  const {
    copied,
    ecwPreview,
    appointmentDate,
    upcomingAppointment,
    visitStatus,
    stediCoverage,
    copySummary,
  } = model
  return (
    <>
      {ecwPreview ? (
        <section className="v2-decision v2-decision-verify" aria-labelledby="front-desk-answer">
          <p>Appointment to estimate</p>
          <h2 id="front-desk-answer">
            Review this scheduled visit, then choose the clinician-reviewed services.
          </h2>
          <div className="v2-decision-grid">
            <div>
              <span>Service date</span>
              <strong>{appointmentDate || 'Scheduled date unavailable'}</strong>
              <small>
                {upcomingAppointment
                  ? 'Upcoming appointment'
                  : insuranceCase.sources.ecw?.appointment_status || 'Scheduled appointment'}
              </small>
            </div>
            {insuranceCase.inputs.reason && (
              <div>
                <span>Reason for visit</span>
                <strong>{insuranceCase.inputs.reason}</strong>
                <small>Context returned by eClinicalWorks</small>
              </div>
            )}
            {visitStatus && (
              <div>
                <span>Patient visit status</span>
                <strong>{visitStatus}</strong>
                <small>Explicitly recorded by eClinicalWorks</small>
              </div>
            )}
            <div>
              <span>CPT/HCPCS from eCW</span>
              <strong>
                {insuranceCase.inputs.lines.length
                  ? `${insuranceCase.inputs.lines.length} coded service${insuranceCase.inputs.lines.length === 1 ? '' : 's'}`
                  : 'No coded service attached'}
              </strong>
              <small>
                {insuranceCase.inputs.lines.length
                  ? 'Use these source-coded services first.'
                  : 'ClinicForce will prepare clinician-reviewed choices from the appointment and prior visit.'}
              </small>
            </div>
          </div>
        </section>
      ) : (
        <>
          <section
            className={`v2-decision v2-decision-${insuranceCase.summary.action}`}
            aria-labelledby="front-desk-answer"
          >
            <p>Front-desk answer</p>
            <h2 id="front-desk-answer">{insuranceCase.summary.heading}</h2>
            <div className="v2-decision-grid">
              <div>
                <span>Coverage</span>
                <strong>{readable(insuranceCase.summary.coverage)}</strong>
                <small>
                  {insuranceCase.summary.hmo ? 'HMO plan detected' : 'Plan type from Availity'}
                </small>
              </div>
              <div>
                <span>Network</span>
                <strong>{insuranceCase.summary.scheduled.network || 'Not returned'}</strong>
                <small>For the submitted provider and services</small>
              </div>
              <div>
                <span>Referral</span>
                <strong>
                  {insuranceCase.summary.referral.status ||
                    (insuranceCase.summary.referral.required
                      ? 'Required'
                      : 'Not identified as required')}
                </strong>
                <small>
                  {insuranceCase.summary.referral.reference
                    ? `Ref ${insuranceCase.summary.referral.reference} · ${insuranceCase.summary.referral.effective || 'no start'} to ${insuranceCase.summary.referral.expiration || 'no end'}`
                    : 'No reference returned'}
                </small>
              </div>
              <div>
                <span>Prior authorization</span>
                <strong>
                  {insuranceCase.summary.authorization.status ||
                    (insuranceCase.summary.authorization.required
                      ? 'Required'
                      : insuranceCase.summary.authorization.unknown
                        ? 'Requirement unclear'
                        : 'Not identified as required')}
                </strong>
                <small>
                  {insuranceCase.summary.authorization.reference
                    ? `Ref ${insuranceCase.summary.authorization.reference} · ${insuranceCase.summary.authorization.effective || 'no start'} to ${insuranceCase.summary.authorization.expiration || 'no end'}`
                    : 'No reference returned'}
                </small>
              </div>
            </div>
            {insuranceCase.summary.action === 'review_oon' && (
              <p className="v2-next-step">
                Do not collect this estimate automatically. Confirm OON benefits, allowed amount,
                and the clinic's collection policy first.
              </p>
            )}
            <div className="v2-summary-actions">
              <button
                className="button button-secondary"
                type="button"
                onClick={() => void copySummary()}
              >
                <Clipboard size={16} aria-hidden="true" /> {copied ? 'Copied' : 'Copy summary'}
              </button>
              <a
                className="button button-secondary"
                href="mailto:?subject=Insurance%20verification%20complete&body=ClinicForce%20insurance%20verification%20is%20complete.%20Sign%20in%20to%20ClinicForce%20to%20review%20the%20result."
              >
                <Mail size={16} aria-hidden="true" /> Email completion notice
              </a>
            </div>
          </section>

          <section className="v2-prices" aria-label="Visit price comparison">
            <article>
              <p>Scheduled visit</p>
              <h3>{money(insuranceCase.summary.scheduled.insurance_cents)}</h3>
              <span>
                {stediCoverage ? 'Stedi + clinic contract estimate' : 'Availity patient estimate'}
              </span>
              <V2CostBreakdown insuranceCase={insuranceCase} kind="scheduled" />
            </article>
            <article>
              <p>Possible procedure</p>
              <h3>{money(insuranceCase.summary.possible.insurance_cents)}</h3>
              <span>
                {stediCoverage
                  ? 'Separate Stedi + clinic contract estimate'
                  : 'Separate Availity patient estimate'}
              </span>
              <V2CostBreakdown insuranceCase={insuranceCase} kind="possible" />
            </article>
          </section>
        </>
      )}
    </>
  )
}
