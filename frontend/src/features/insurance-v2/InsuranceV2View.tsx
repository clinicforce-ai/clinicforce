import EcwDecisionSection from './EcwDecisionSection'
import ManualReviewSection from './ManualReviewSection'
import V2ServicesSection from './V2ServicesSection'
import V2WorkflowSection from './V2WorkflowSection'
import RecentTransactionsSection from './RecentTransactionsSection'
import V2TechnicalDetails from './V2TechnicalDetails'
import { AlertTriangle, ArrowLeft, ArrowRight, LoaderCircle, ShieldCheck } from 'lucide-react'
import { createV2Case } from '../../services/api/insurance-v2'
import { readable, dateTime } from './v2-display'
import type { InsuranceV2Model } from './useInsuranceV2Model'

export default function InsuranceV2View({ model }: { model: InsuranceV2Model }) {
  const {
    onBack,
    staffMode,
    staffIntakeId,
    onUseDocuments,
    config,
    insuranceCase,
    appointments,
    appointmentId,
    setAppointmentId,
    busy,
    error,
    perform,
    stediTemporary,
    demo,
    ecwPreview,
  } = model
  if (
    config &&
    (staffMode && demo
      ? !config.ecw_configured
      : !config.credentials_configured ||
        (!demo && !stediTemporary && (!config.production_enabled || !config.ecw_configured)))
  ) {
    return (
      <section className="insurance-beta insurance-v2" aria-labelledby="insurance-v2-unavailable">
        <button className="back-control" type="button" onClick={onBack}>
          <ArrowLeft aria-hidden="true" className="size-4" />{' '}
          {staffMode ? 'Back to patient insurance' : 'Back to clinical summary'}
        </button>
        <div className="panel">
          <p className="eyebrow">
            <ShieldCheck aria-hidden="true" size={17} /> Insurance verification
          </p>
          <h1 id="insurance-v2-unavailable">Live insurance verification is unavailable</h1>
          <p>
            {staffMode
              ? 'Your patient intake and card are saved. Use uploaded documents while the clinic completes its live connection setup.'
              : 'You can continue reviewing the patient’s shared record.'}
          </p>
          {onUseDocuments && (
            <button className="button button-primary" type="button" onClick={onUseDocuments}>
              Use uploaded documents
            </button>
          )}
        </div>
      </section>
    )
  }

  return (
    <section className="insurance-beta insurance-v2" aria-labelledby="insurance-v2-heading">
      <button className="back-control" type="button" onClick={onBack}>
        <ArrowLeft aria-hidden="true" className="size-4" />{' '}
        {staffMode ? 'Back to patient insurance' : 'Back to clinical summary'}
      </button>
      <header className="insurance-beta-hero v2-hero">
        <div>
          <p className="eyebrow">
            <ShieldCheck aria-hidden="true" size={17} /> Front-desk insurance decision
          </p>
          <h1 id="insurance-v2-heading">Know what to do before the patient is scheduled.</h1>
          <p>
            {ecwPreview
              ? 'ClinicForce reads the appointment, coverage, providers, diagnoses, and coded services available in eClinicalWorks.'
              : stediTemporary
                ? 'ClinicForce reads the visit from eClinicalWorks, checks Stedi eligibility, and calculates a clinic-contract-rate estimate.'
                : 'ClinicForce reads the visit from eClinicalWorks and gets coverage, review decisions, and insured pricing from Availity.'}
          </p>
        </div>
        <div className="beta-session">
          <span>
            <i aria-hidden="true" />{' '}
            {config
              ? ecwPreview
                ? 'eCW live'
                : stediTemporary
                  ? 'Stedi temporary bridge'
                  : `${demo ? 'Demo' : 'Production'} environment`
              : 'Connecting'}
          </span>
          <strong>
            {ecwPreview
              ? 'Live eCW appointment'
              : demo
                ? 'Synthetic Availity patient'
                : 'Live patient inquiry'}
          </strong>
          <small>
            {ecwPreview
              ? 'Saved to the clinic patient record'
              : demo
                ? 'Training data only'
                : staffMode
                  ? 'Saved to the clinic patient record'
                  : 'Saved for this QR session'}
          </small>
        </div>
      </header>

      <main className="v2-decision-sheet">
        <div className="v2-live-status" role="status" aria-live="polite">
          {busy ? (
            <>
              <LoaderCircle size={16} className="animate-spin" aria-hidden="true" /> {busy}
            </>
          ) : insuranceCase ? (
            'Case changes are saved.'
          ) : (
            'Ready to start.'
          )}
        </div>
        {error && (
          <div className="form-error" role="alert">
            <AlertTriangle size={18} aria-hidden="true" />
            <span>{error}</span>
            {onUseDocuments && (
              <button className="button button-secondary" type="button" onClick={onUseDocuments}>
                Use uploaded documents
              </button>
            )}
          </div>
        )}

        {!insuranceCase && (
          <section className="v2-start">
            <p className="section-kicker">Start here</p>
            <h2>
              {staffMode
                ? 'Choose the eCW appointment to verify'
                : demo
                  ? 'Run the Availity synthetic workflow'
                  : 'Load the patient’s next scheduled visit'}
            </h2>
            <p>
              {staffMode
                ? 'The insurance card is already saved with this clinic intake. Choose the visit that this verification should cover.'
                : config?.message}
            </p>
            {staffMode && (
              <label className="v2-appointment-choice">
                <span>Scheduled appointment</span>
                <select
                  value={appointmentId}
                  onChange={(event) => setAppointmentId(event.target.value)}
                  disabled={!!busy || !appointments.length}
                >
                  <option value="">
                    {appointments.length
                      ? 'Choose an appointment'
                      : 'No scheduled appointments found'}
                  </option>
                  {appointments.map((visit) => (
                    <option key={visit.id} value={visit.id}>
                      {dateTime(visit.start)} · {visit.reason || 'Reason not returned'} ·{' '}
                      {readable(visit.status)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button
              className="button button-primary"
              type="button"
              disabled={!!busy || (staffMode && !appointmentId)}
              onClick={() =>
                void perform('Starting a saved insurance case…', () =>
                  createV2Case(appointmentId || undefined, undefined, staffIntakeId),
                )
              }
            >
              {staffMode
                ? 'Verify selected appointment'
                : demo
                  ? 'Start demo case'
                  : 'Load eCW appointment'}{' '}
              <ArrowRight size={17} aria-hidden="true" />
            </button>
          </section>
        )}

        {insuranceCase && (
          <>
            <EcwDecisionSection model={model} insuranceCase={insuranceCase} />

            <ManualReviewSection model={model} insuranceCase={insuranceCase} />

            <V2ServicesSection model={model} insuranceCase={insuranceCase} />

            <V2WorkflowSection model={model} insuranceCase={insuranceCase} />

            <RecentTransactionsSection model={model} insuranceCase={insuranceCase} />

            <V2TechnicalDetails model={model} insuranceCase={insuranceCase} />
          </>
        )}
      </main>
    </section>
  )
}
