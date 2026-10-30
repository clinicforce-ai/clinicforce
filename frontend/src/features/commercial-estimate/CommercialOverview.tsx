import { ShieldCheck, Network, Wallet } from 'lucide-react'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { RequirementCards } from './EstimatePanels'
import { money, range, visitDate } from './format'
import { patientBenefitsBound as hasPatientBinding } from './identityBinding'
import PayerReportedPlanFigures from './PayerReportedPlanFigures'
import SelectedServiceRequirements from './SelectedServiceRequirements'
import { shouldShowSelectedRequirements } from './selectedRequirementsVisibility'
import TemporaryRateReference from './TemporaryRateReference'
import EstimateSources from './EstimateSources'
import { openServiceDetails } from './openServiceDetails'
import ArrivalAmountSummary from './ArrivalAmountSummary'
import {
  aetnaOfficePlanningCopay,
  aetnaPendingOfficeCopay,
  aetnaOfficeReferenceCopay,
  cardOfficePlanningCopay,
  payerReportedPartialCopay,
  publishedHumanaOfficeCopay,
} from './payerReportedPartialCopay'
import PublishedPlanBenefit from './PublishedPlanBenefit'
import UhcMedicarePpoBenefitTable from './UhcMedicarePpoBenefitTable'
import CommercialCostBreakdown from './CommercialCostBreakdown'
export default function CommercialOverview({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const {
    readOnly,
    busy,
    recovering,
    saveReview,
    cashPrice,
    planName,
    tooEarly,
    policyStatus,
    contractEvidence,
    contractUrl,
    cash,
    coverageTone,
    calculating,
    previousEstimate,
    guidance,
    contractCandidate,
    networkLabel,
    networkTone,
    amount,
    provisionalAmount,
    requirementIssue,
    anticipatedService,
    session,
  } = model
  const payerForAction = (!readOnly && result.stedi_payer?.name) || result.payer
  const texasMedicareEnrollmentMissing =
    /^Medicare(?: of)? (?:Texas|TX)(?: Part B)?$/i.test(result.payer?.trim() || '') &&
    result.enrollment?.route === 'original_medicare_hets' &&
    result.enrollment.status === 'MISSING'
  const plannedCodes =
    result.price_range?.mode === 'services'
      ? [...new Set(result.price_range.lines?.map((line) => line.code).filter(Boolean) || [])]
      : []
  const plannedCodeLabel = plannedCodes.map((code) => `CPT ${code}`).join(', ')
  const aetnaReferenceCopay = aetnaOfficeReferenceCopay(result, model)
  const humanaReferenceCopay = publishedHumanaOfficeCopay(result, model)
  const payerReportedPartialCents =
    aetnaReferenceCopay ?? humanaReferenceCopay ?? payerReportedPartialCopay(result, model)
  const pendingAetnaCopay = aetnaPendingOfficeCopay(result, model)
  const cardPlanningCents =
    payerReportedPartialCents == null ? cardOfficePlanningCopay(result, model) : null
  const combinedPlan = !cash && !previousEstimate ? result.combined_emg_plan : null
  const showSelectedRequirements = shouldShowSelectedRequirements(
    result,
    plannedCodes,
    cash,
    previousEstimate,
  )
  const staleInsuredGuidance = !cash && guidance?.amount_cents != null && !amount
  const showAssumptions = Boolean(
    amount ||
    combinedPlan ||
    (!staleInsuredGuidance && (guidance?.patient_actions.length || guidance?.assumptions.length)),
  )
  const visibleAmount =
    Boolean(amount) ||
    combinedPlan != null ||
    provisionalAmount != null ||
    !!model.estimatedShare ||
    aetnaOfficePlanningCopay(result, model, result.price_range?.lines || []) != null ||
    payerReportedPartialCents != null ||
    pendingAetnaCopay != null ||
    cardPlanningCents != null
  const patientBenefitsBound = hasPatientBinding(result.identity)
  const networkWarnings = (result.network_evidence?.mismatch_warnings || []).filter(
    (warning) => !warning.toLowerCase().includes('billing tin is missing from the visit'),
  )
  const networkReason = result.network_evidence?.reason
    ?.replace(/billing TIN is missing from the visit\.?/i, '')
    .trim()
  const networkExplanation =
    networkReason ||
    (result.reference_only &&
    result.reference_benefits?.active === true &&
    networkLabel === 'Not determined'
      ? 'Stedi did not confirm this provider’s network tier'
      : null)
  const chooseServices =
    !readOnly &&
    result.service_selection_status === 'preview' &&
    result.benefits?.network_source === 'clinic_contract_inventory' &&
    !result.service_plan?.recommended_ids?.length
  const enterAllowance =
    !readOnly &&
    session?.role === 'admin' &&
    result.payment_explanation?.reason_code === 'out_of_network_allowance_missing' &&
    !!result.selected_ids?.length
  const automaticCashFallback = cash && result.cash_fallback != null
  const quoteLines = result.price_range?.lines || []
  const canQuoteFollowUp =
    cash &&
    result.service_selection_status === 'approved' &&
    result.price_range?.mode === 'services' &&
    quoteLines.length === 1 &&
    quoteLines[0].code === '99213' &&
    Number(quoteLines[0].units) === 1 &&
    !quoteLines[0].modifiers?.length
  return (
    <section id="visit-estimate" className="visit-overview" aria-labelledby="payment-heading">
      <div className="overview-heading">
        <h2 id="payment-heading">
          {readOnly
            ? 'Saved visit estimate'
            : automaticCashFallback
              ? 'Insurance review · cash option'
              : cash
                ? 'Self-pay estimate'
                : 'Arrival collection'}
        </h2>
        {!readOnly && (
          <div className="payment-toggle" aria-label="Payment method">
            <button
              aria-pressed={!cash}
              disabled={!!busy || recovering}
              onClick={() => {
                if (cash) void saveReview({ payment_mode: 'insurance' })
              }}
            >
              <ShieldCheck size={16} />
              Insurance
            </button>
            <button
              aria-pressed={cash}
              disabled={!!busy || recovering}
              onClick={() => {
                if (!cash) void saveReview({ payment_mode: 'cash' })
              }}
            >
              <Wallet size={16} />
              Cash
            </button>
          </div>
        )}
      </div>
      {!readOnly && canQuoteFollowUp && (
        <form
          key={result.revision}
          className="visit-cash-quote"
          onSubmit={(event) => {
            event.preventDefault()
            const fields = new FormData(event.currentTarget)
            const amountText = String(fields.get('cash_amount') || '')
            const amount = Number(amountText)
            const reason = String(fields.get('cash_reason') || '').trim()
            if (
              !/^\d+(?:\.\d{1,2})?$/.test(amountText) ||
              !Number.isFinite(amount) ||
              amount <= 0 ||
              !reason
            )
              return
            void saveReview({
              payment_mode: 'cash',
              cash_visit_quote: {
                amount_cents: Math.round(amount * 100),
                reason,
              },
            })
          }}
        >
          <label>
            Follow-up cash price
            <input
              name="cash_amount"
              type="number"
              min="0.01"
              step="0.01"
              required
              defaultValue={
                result.cash_quote?.reason ? (result.cash_quote.min_cents || 0) / 100 : ''
              }
            />
          </label>
          <label>
            Reason
            <input
              name="cash_reason"
              type="text"
              minLength={3}
              maxLength={200}
              required
              defaultValue={
                result.cash_quote?.reason ||
                (result.benefits?.active === false ? 'inactive general medical coverage' : '')
              }
            />
          </label>
          <button type="submit" disabled={!!busy || recovering}>
            Save visit cash quote
          </button>
          {result.cash_quote?.reason && (
            <button
              type="button"
              disabled={!!busy || recovering}
              onClick={() => void saveReview({ clear_cash_visit_quote: true })}
            >
              Remove quote
            </button>
          )}
        </form>
      )}
      {texasMedicareEnrollmentMissing && (
        <p className="visit-notice medicare-enrollment-notice" role="status">
          <strong>
            ClinicForce does not have a matching Medicare eligibility transaction enrollment through
            Stedi yet.
          </strong>
        </p>
      )}
      <div className="overview-amounts">
        <div
          className={`arrival-total${!visibleAmount && !calculating && !tooEarly ? ' is-blocked' : ''}`}
        >
          <ArrivalAmountSummary
            model={model}
            result={result}
            partialCopay={payerReportedPartialCents}
            cardPlanningCopay={cardPlanningCents}
            referenceOnly={aetnaReferenceCopay != null || humanaReferenceCopay != null}
            visibleAmount={visibleAmount}
          />
          {payerReportedPartialCents != null &&
            aetnaReferenceCopay == null &&
            humanaReferenceCopay == null && (
              <details className="financial-breakdown">
                <summary>Additional payer response details</summary>
                <UhcMedicarePpoBenefitTable result={result} />
              </details>
            )}
          <PublishedPlanBenefit
            result={result}
            show={
              (!cash || automaticCashFallback) &&
              (result.plan_details?.stedi?.identified_product === 'FEP Blue Basic PSHB' ||
                humanaReferenceCopay != null)
            }
          />
          {(!cash || automaticCashFallback) &&
            !previousEstimate &&
            result.benefits?.active === true &&
            patientBenefitsBound &&
            !result.benefits.historical_only &&
            !result.benefits.source_errors?.length &&
            payerReportedPartialCents == null && (
              <details className="financial-breakdown">
                <summary>Payer response source rows</summary>
                <PayerReportedPlanFigures result={result} />
              </details>
            )}
          {(chooseServices || enterAllowance) && (
            <button type="button" className="cash-package-shortcut" onClick={openServiceDetails}>
              {chooseServices
                ? 'Choose consultation, EMG/NCS, or both'
                : 'Record payer allowed amount for selected CPT'}
            </button>
          )}
          {!readOnly && result.service_plan?.method === 'unavailable' && (
            <button
              type="button"
              className="cash-package-shortcut"
              disabled={!!busy || recovering}
              onClick={() => void saveReview({})}
            >
              Retry CPT suggestion
            </button>
          )}
          {!amount && <TemporaryRateReference result={result} />}
          {cash && !amount && !!result.cash_schedule?.packages.length && !readOnly && (
            <button
              className="cash-package-shortcut"
              onClick={() => {
                const detail = document.getElementById(
                  'visit-detail-services',
                ) as HTMLDetailsElement | null
                if (detail) detail.open = true
                detail?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }}
            >
              View {result.cash_schedule.packages.length} clinic cash packages
            </button>
          )}
          {result.payment_explanation?.notice && !previousEstimate && (
            <details className="financial-breakdown">
              <summary>Estimate notice</summary>
              <p className="visit-identity-notice">{result.payment_explanation.notice}</p>
            </details>
          )}
          {guidance?.billing_note && (
            <details open={readOnly || undefined}>
              <summary>eCW billing note · {visitDate(guidance.billing_note.updated_at)}</summary>
              <blockquote>{guidance.billing_note.text}</blockquote>
            </details>
          )}
          {!cash && showAssumptions && (
            <details className="summary-assumptions">
              <summary>{amount ? 'Assumptions used' : 'Patient steps'}</summary>
              {amount && guidance?.basis === 'claim_after_service' && (
                <p>
                  No advance today. Submit the insurance claim and reconcile any legally collectible
                  patient share.
                </p>
              )}
              {amount && !combinedPlan && (
                <p>
                  {(!staleInsuredGuidance && guidance?.patient_message) ||
                    'The final patient share follows the insurer’s processed claim.'}
                </p>
              )}
              {amount &&
                !previousEstimate &&
                guidance?.basis !== 'ecw_billing_note' &&
                result.encounter_summary?.estimated_total_patient_min_cents != null &&
                result.encounter_summary.estimated_total_patient_max_cents != null &&
                guidance?.basis !== 'claim_after_service' && (
                  <dl className="arrival-breakdown" aria-label="Patient cost breakdown">
                    <div>
                      <dt>
                        {result.encounter_summary.conditional
                          ? 'Conditional patient estimate'
                          : 'Full patient estimate'}
                      </dt>
                      <dd>
                        {range(
                          result.encounter_summary.estimated_total_patient_min_cents,
                          result.encounter_summary.estimated_total_patient_max_cents,
                        )}
                      </dd>
                    </div>
                    {result.encounter_summary.estimated_later_balance_cents != null && (
                      <div>
                        <dt>Estimated later balance</dt>
                        <dd>{money(result.encounter_summary.estimated_later_balance_cents)}</dd>
                      </div>
                    )}
                  </dl>
                )}
              {combinedPlan && (
                <ul>
                  {combinedPlan.assumptions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}
              {!combinedPlan && !staleInsuredGuidance && !!guidance?.patient_actions.length && (
                <div>
                  <b>Tell the patient:</b>
                  <ul>
                    {guidance.patient_actions.map((action) => (
                      <li key={action}>{action}</li>
                    ))}
                  </ul>
                </div>
              )}
              {!combinedPlan && !staleInsuredGuidance && !!guidance?.assumptions.length && (
                <ul>
                  {guidance.assumptions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}
            </details>
          )}
        </div>
        <div className="visit-cost-table">
          <CommercialCostBreakdown model={model} result={result} />
          {!cash && !calculating && model.summaryExplanation && (
            <details className="financial-breakdown">
              <summary>
                {combinedPlan
                  ? 'How the planning amount was calculated'
                  : amount
                    ? 'Why this amount?'
                    : 'Why this needs review'}
              </summary>
              <p>
                {combinedPlan
                  ? `${money(combinedPlan.office_copay_cents)} office copay plus ${combinedPlan.coinsurance_percent}% of ${money(combinedPlan.diagnostic_allowed_cents)} diagnostic allowance. Final responsibility follows the processed claim.`
                  : model.summaryExplanation}
              </p>
            </details>
          )}
        </div>
        <div className="arrival-context">
          <span className="visit-label">Anticipated service</span>
          <h3>
            {anticipatedService ||
              result.reason ||
              (showSelectedRequirements
                ? 'Planned services'
                : plannedCodeLabel || 'Service not established')}
          </h3>
          {result.reason && result.reason !== anticipatedService && (
            <p>eCW visit reason: {result.reason}</p>
          )}
          {!readOnly && (
            <button type="button" className="cash-package-shortcut" onClick={openServiceDetails}>
              Review planned CPTs
            </button>
          )}
        </div>
      </div>
      <div className="visit-snapshots" aria-label="Coverage summary">
        <div className={coverageTone}>
          <ShieldCheck size={20} aria-hidden="true" />
          <span>
            Coverage<strong>{policyStatus}</strong>
            <small>
              {result.reference_only && result.reference_benefits?.active === true
                ? result.reference_benefits.service_date_matches === false
                  ? `Stedi requested ${result.service_date ? visitDate(result.service_date) : 'visit date'}; Aetna returned service date ${result.reference_benefits.returned_service_dates?.start ? visitDate(result.reference_benefits.returned_service_dates.start) : 'unavailable'} · visit coverage unverified`
                  : 'Stedi payer reference · appointment-date applicability unverified'
                : result?.benefit_source
                  ? `Checked via ${result.benefit_source}`
                  : result?.benefits?.active
                    ? 'Payer source not recorded'
                    : 'Payer check needed'}
            </small>
          </span>
        </div>
        <div className={networkTone}>
          <Network size={20} aria-hidden="true" />
          <span>
            Network<strong>{networkLabel}</strong>
            {networkWarnings.length ? (
              <small className="network-mismatch-warning" role="note">
                Agreement mismatch: {networkWarnings.join(' ')}
              </small>
            ) : (
              networkExplanation && <small>{networkExplanation}</small>
            )}
            {result?.network_evidence?.product_source && (
              <small>Contract plan lookup: {result.network_evidence.product_source}</small>
            )}
            {contractCandidate && !result?.network_evidence?.reason && (
              <small>{contractCandidate} contract could not be matched to this visit</small>
            )}
            {contractUrl && (
              <small>
                <a href={contractUrl} target="_blank" rel="noopener noreferrer">
                  {contractEvidence?.related_document_id
                    ? 'Clinic Library contract'
                    : `${contractEvidence?.network_name || 'Payer plan'} contract`}
                </a>
              </small>
            )}
          </span>
        </div>
        {!showSelectedRequirements &&
          !requirementIssue &&
          !(result.reference_only && result.reference_benefits?.active === true) && (
            <RequirementCards result={result} readOnly={readOnly} payerName={payerForAction} />
          )}
      </div>
      {showSelectedRequirements && <SelectedServiceRequirements result={result} />}
      <EstimateSources result={result} tooEarly={!!tooEarly} />
      <div className="overview-plan-name">
        <ShieldCheck size={16} aria-hidden="true" />
        <span>
          {cash && !automaticCashFallback
            ? 'Clinic self-pay schedule'
            : planName || result.payer || 'Policy details not returned'}
        </span>
        {(!cash || automaticCashFallback) && cashPrice && (
          <span className="cash-comparison">Cash option {cashPrice}</span>
        )}
      </div>
    </section>
  )
}
