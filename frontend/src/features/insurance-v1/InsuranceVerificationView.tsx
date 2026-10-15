import InsuranceCardStage from './InsuranceCardStage'
import CardReviewStage from './CardReviewStage'
import BenefitsUploadStage from './BenefitsUploadStage'
import ServiceSelectionStage from './ServiceSelectionStage'
import AuthorizationStage from './AuthorizationStage'
import BenefitsReviewStage from './BenefitsReviewStage'
import PayEstimateStage from './PayEstimateStage'
import { AlertTriangle, ArrowLeft, Check, ShieldCheck } from 'lucide-react'
import StaffLibraryPicker from '../staff-library/StaffLibraryPicker'
import type { InsuranceV1Model } from './useInsuranceV1Model'
import { steps } from './v1-display'

export default function InsuranceVerificationView({ model }: { model: InsuranceV1Model }) {
  const {
    onBack,
    staffMode,
    insuranceCase,
    setViewStage,
    busy,
    message,
    error,
    uploadEvidence,
    activeStage,
    stage,
    reviewing,
    disabled,
    estimate,
    completed,
  } = model
  return (
    <section className="insurance-beta insurance-v1" aria-labelledby="insurance-heading">
      <button className="back-control" type="button" disabled={busy} onClick={onBack}>
        <ArrowLeft aria-hidden="true" className="size-4" />{' '}
        {staffMode ? 'Back to patient insurance' : 'Back to clinical summary'}
      </button>
      <header className="insurance-beta-hero">
        <div>
          <p className="eyebrow">
            <ShieldCheck aria-hidden="true" className="size-4" />{' '}
            {staffMode ? 'Uploaded document review' : 'Beta V1 · Uploaded documents'}
          </p>
          <h1 id="insurance-heading">From your documents to a pay estimate</h1>
          <p>
            Upload the patient’s card, benefit details and fee schedule. Review the extracted
            information, choose CPT codes and see an itemized estimate.
          </p>
        </div>
        <div className="beta-session">
          <span>
            <i aria-hidden="true" />{' '}
            {insuranceCase?.status === 'complete' ? 'Review complete' : 'Document-based review'}
          </span>
          <strong>Your uploads are the source</strong>
          <small>CPT suggestions are generated separately and require clinician review.</small>
        </div>
      </header>
      <div className="insurance-workspace">
        <nav className="insurance-stepper" aria-label="Insurance verification progress">
          {steps.map((item, index) => (
            <button
              type="button"
              key={item.id}
              aria-current={item.id === stage ? 'step' : undefined}
              disabled={busy || (!completed.has(item.id) && item.id !== activeStage)}
              onClick={() => setViewStage(item.id)}
            >
              <span className="step-number">
                {completed.has(item.id) ? <Check aria-hidden="true" /> : index + 1}
              </span>
              <span className="step-copy">
                <strong>{item.label}</strong>
                <small>
                  {completed.has(item.id)
                    ? 'Completed · review'
                    : item.id === activeStage
                      ? 'Current step'
                      : 'Upcoming'}
                </small>
              </span>
            </button>
          ))}
        </nav>
        <div className="insurance-layout">
          <main className="insurance-stage-card" aria-busy={busy}>
            <div className="insurance-stage-toolbar">
              <div>
                <span>{reviewing ? 'Reviewing completed step' : 'Current step'}</span>
                <strong>{steps.find((item) => item.id === stage)?.label}</strong>
              </div>
              <p className="insurance-status" role="status">
                {busy ? message : error ? 'Please check the message below.' : message}
              </p>
            </div>
            {error && (
              <p className="form-error" role="alert">
                <AlertTriangle aria-hidden="true" className="size-4" /> {error}
              </p>
            )}
            {reviewing && (
              <button
                className="button button-secondary"
                type="button"
                onClick={() => setViewStage(null)}
              >
                Return to current step
              </button>
            )}

            {staffMode && !insuranceCase && (
              <p role="status">
                {error
                  ? 'Return to the patient record and reopen document review to retry.'
                  : 'Opening the saved card review…'}
              </p>
            )}
            {staffMode && insuranceCase && stage === 'network' && (
              <StaffLibraryPicker
                kinds={['contract', 'fee_schedule', 'self_pay']}
                disabled={disabled}
                onFile={(kind, file) => uploadEvidence(kind, file)}
              />
            )}
            {stage === 'intake' && !staffMode && <InsuranceCardStage model={model} />}

            {stage === 'eligibility' && <CardReviewStage model={model} />}

            {stage === 'network' && <BenefitsUploadStage model={model} />}

            {stage === 'service' && insuranceCase && (
              <ServiceSelectionStage model={model} insuranceCase={insuranceCase} />
            )}

            {stage === 'authorization' && insuranceCase && (
              <AuthorizationStage model={model} insuranceCase={insuranceCase} />
            )}

            {stage === 'benefits' && <BenefitsReviewStage model={model} />}

            {stage === 'estimate' && estimate && (
              <PayEstimateStage model={model} estimate={estimate} />
            )}
          </main>
        </div>
      </div>
    </section>
  )
}
