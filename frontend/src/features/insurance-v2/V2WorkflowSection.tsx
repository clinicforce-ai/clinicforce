import type { InsuranceV2Model } from './useInsuranceV2Model'
import { RefreshCw } from 'lucide-react'
import { refreshV2Transaction, retryV2Transaction } from '../../services/api/insurance-v2'
import { TransactionStatus } from './V2TransactionDetails'
import { resources } from './v2-display'

export default function V2WorkflowSection({
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
    stediTemporary,
    demo,
    ecwPreview,
    additionalMissing,
    coverage,
    coverageDone,
    coverageActive,
    run,
    getAnswer,
  } = model
  return (
    <>
      {ecwPreview ? (
        <section className="v2-workflow" aria-labelledby="workflow-heading">
          <p className="section-kicker">eClinicalWorks source</p>
          <h2 id="workflow-heading">
            {!insuranceCase.inputs.lines.length
              ? 'No eCW-coded service attached yet'
              : additionalMissing.length
                ? 'Some appointment details are pending'
                : 'Available eCW appointment details loaded'}
          </h2>
          <p>
            {!insuranceCase.inputs.lines.length
              ? 'This scheduled appointment has no coded service attached yet. ClinicForce uses the current appointment and the most recent completed visit to prepare clinician-reviewed candidates.'
              : additionalMissing.length
                ? 'Some appointment details are still loading. Reload if you need the latest information.'
                : 'ClinicForce received the available appointment, coverage, provider, diagnosis, and service details.'}
          </p>
        </section>
      ) : (
        <section className="v2-workflow" aria-labelledby="workflow-heading">
          <p className="section-kicker">Payer workflow</p>
          <h2 id="workflow-heading">Complete each needed payer check</h2>
          {!coverage && (
            <button
              className="button button-primary"
              type="button"
              disabled={!!busy}
              onClick={getAnswer}
            >
              Get insurance answer
            </button>
          )}
          {!coverage && (
            <p className="v2-next-step">
              ClinicForce checks eligibility first. It requests an insured estimate only after any
              payer-required referral or authorization has an approved result. Referral and
              authorization remain separate actions because they create payer work.
            </p>
          )}
          <div className="v2-action-list">
            {resources.map((resource, index) => {
              const transaction = insuranceCase.transactions[resource.id]
              const missing = insuranceCase.missing_by_resource[resource.id] || []
              const hasLines =
                resource.id === 'coverage' ||
                insuranceCase.inputs.lines.some(
                  (line) =>
                    (line.service_status || 'scheduled') ===
                    (resource.id === 'procedure_estimate' ? 'possible' : 'scheduled'),
                )
              const supportedByStedi =
                !stediTemporary ||
                ['coverage', 'estimate', 'procedure_estimate'].includes(resource.id)
              const canSubmit =
                supportedByStedi &&
                (demo || missing.length === 0) &&
                hasLines &&
                (resource.id === 'coverage' || (coverageDone && coverageActive))
              return (
                <article key={resource.id}>
                  <div className="v2-action-number">{String(index + 1).padStart(2, '0')}</div>
                  <div>
                    <h3>{resource.label}</h3>
                    <p>{resource.help}</p>
                    {!transaction && (
                      <button
                        className="button button-primary"
                        type="button"
                        disabled={!!busy || !canSubmit}
                        onClick={() => run(resource.id)}
                      >
                        {resource.action}
                      </button>
                    )}
                    {!transaction && !canSubmit && (
                      <small className="v2-next-step">
                        {!supportedByStedi
                          ? 'Stedi temporary mode does not determine authorization or referrals; verify them manually.'
                          : resource.id !== 'coverage' && !coverageDone
                            ? 'Complete eligibility first.'
                            : resource.id !== 'coverage' && !coverageActive
                              ? 'Coverage is not active; stop the insured-price path.'
                              : missing[0] || 'Confirm a matching service above.'}
                      </small>
                    )}
                    {transaction && <TransactionStatus transaction={transaction} />}
                    {transaction &&
                      !transaction.resource_id &&
                      [401, 403, 429].includes(transaction.http_status ?? 0) && (
                        <button
                          className="button button-secondary"
                          type="button"
                          disabled={!!busy}
                          onClick={() =>
                            void perform('Retrying the rejected Availity request…', () =>
                              retryV2Transaction(insuranceCase.id, resource.id, staffIntakeId),
                            )
                          }
                        >
                          Retry after access is fixed
                        </button>
                      )}
                    {transaction &&
                      ['pending', 'unavailable'].includes(transaction.outcome) &&
                      transaction.resource_id && (
                        <button
                          className="button button-secondary"
                          type="button"
                          disabled={!!busy}
                          onClick={() =>
                            void perform('Checking the saved Availity response…', () =>
                              refreshV2Transaction(insuranceCase.id, resource.id, staffIntakeId),
                            )
                          }
                        >
                          <RefreshCw size={16} aria-hidden="true" /> Check existing response
                        </button>
                      )}
                  </div>
                </article>
              )
            })}
          </div>
        </section>
      )}
    </>
  )
}
