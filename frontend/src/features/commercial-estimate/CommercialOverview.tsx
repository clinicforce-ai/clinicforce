import { ShieldCheck, Network, Wallet } from 'lucide-react'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { RequirementCards } from './EstimatePanels'
import { money, range, visitDate } from './format'
import { patientBenefitsBound as hasPatientBinding } from './identityBinding'
import PayerReportedPlanFigures from './PayerReportedPlanFigures'
import SelectedServiceRequirements from './SelectedServiceRequirements'
import VisitPatientTotals from './VisitPatientTotals'
import { coinsuranceFormula } from './coinsuranceFormula'
import FinancialLineItems from './FinancialLineItems'
import TemporaryRateReference from './TemporaryRateReference'
import EstimateSources from './EstimateSources'
import { openServiceDetails } from './openServiceDetails'
import { overviewExplanation } from './overviewExplanation'

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
    amountText,
    amountLabel,
    requirementIssue,
    amountParts,
    officeDeductible,
    anticipatedService,
    session,
  } = model
  const payerForAction = (!readOnly && result.stedi_payer?.name) || result.payer
  const plannedCodes =
    result.price_range?.mode === 'services'
      ? [...new Set(result.price_range.lines?.map((line) => line.code).filter(Boolean) || [])]
      : []
  const plannedCodeLabel = plannedCodes.map((code) => `CPT ${code}`).join(', ')
  const showSelectedRequirements =
    !cash &&
    !previousEstimate &&
    plannedCodes.length > 0 &&
    result.benefits?.active === true &&
    result.benefits.service_date_matches !== false &&
    !result.benefits.coverage_status_conflict &&
    !result.benefits.coverage_dates_invalid &&
    !result.benefits.network_conflict &&
    !result.benefits.historical_only &&
    !result.benefits.source_errors?.length &&
    !result.benefits.has_secondary &&
    result.recovery?.reason_code !== 'multiple_active_policies' &&
    hasPatientBinding(result.identity)
  const fullBreakdown =
    !cash &&
    amount &&
    !previousEstimate &&
    result.payment_explanation?.reason_code === 'insured_services' &&
    amountParts &&
    guidance?.amount_cents != null &&
    amountParts.copay_cents != null &&
    amountParts.deductible_cents != null &&
    amountParts.coinsurance_cents != null &&
    amountParts.oop_adjustment_cents != null &&
    amountParts.copay_cents +
      amountParts.deductible_cents +
      amountParts.coinsurance_cents -
      (amountParts.allowed_cap_adjustment_cents ?? 0) -
      amountParts.oop_adjustment_cents -
      (result.encounter_summary?.payments_verified_cents ?? 0) ===
      guidance.amount_cents
  const paid = result.encounter_summary?.payments_verified_cents ?? 0
  const coinsuranceDetail = coinsuranceFormula(result)
  const officeCopayBreakdown =
    !cash &&
    amount &&
    !previousEstimate &&
    guidance?.basis === 'office_copay' &&
    amountParts?.copay_cents != null &&
    guidance.amount_cents != null &&
    amountParts.copay_cents >= paid &&
    amountParts.copay_cents - paid === guidance.amount_cents
  const benefitTerms = result.benefits
  const staleInsuredGuidance = !cash && guidance?.amount_cents != null && !amount
  const showAssumptions = Boolean(
    amount ||
    (!staleInsuredGuidance && (guidance?.patient_actions.length || guidance?.assumptions.length)),
  )
  const patientBenefitsBound = hasPatientBinding(result.identity)
  const applicablePlanLimits =
    !cash &&
    amount &&
    !previousEstimate &&
    !['claim_after_service', 'ecw_billing_note'].includes(guidance?.basis || '') &&
    benefitTerms?.active === true &&
    benefitTerms.service_date_matches !== false &&
    benefitTerms.coverage_status_conflict !== true &&
    benefitTerms.network_conflict !== true &&
    benefitTerms.historical_only !== true &&
    !benefitTerms.source_errors?.length &&
    !benefitTerms.review_reasons?.some((reason) =>
      reason.toLowerCase().includes('provider-network evidence reports nonparticipation'),
    ) &&
    benefitTerms?.network_status === 'in_network' &&
    ['clinic_library_contract', 'payer_participation_message'].includes(
      benefitTerms.network_source || '',
    ) &&
    patientBenefitsBound &&
    !benefitTerms.has_secondary &&
    result.recovery?.reason_code !== 'multiple_active_policies' &&
    (benefitTerms.oop_annual_cents != null || benefitTerms.oop_remaining_cents != null)
  const chooseServices =
    !readOnly &&
    result.service_selection_status === 'preview' &&
    result.benefits?.network_source === 'clinic_contract_inventory'
  const enterAllowance =
    !readOnly &&
    session?.role === 'admin' &&
    result.payment_explanation?.reason_code === 'out_of_network_allowance_missing' &&
    !!result.selected_ids?.length
  return (
    <section id="visit-estimate" className="visit-overview" aria-labelledby="payment-heading">
      <div className="overview-heading">
        <h2 id="payment-heading">
          {readOnly ? 'Saved visit estimate' : cash ? 'Self-pay estimate' : 'Arrival collection'}
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
      <div className="overview-amounts">
        <div
          className={`arrival-total${!amount && !calculating && !tooEarly ? ' is-blocked' : ''}`}
        >
          <span>{amountLabel}</span>
          <strong className={!amount ? 'amount-pending' : undefined} role="status">
            {amountText}
          </strong>
          <p>{overviewExplanation(model, result, Boolean(fullBreakdown))}</p>
          {!readOnly && guidance?.policy_setup_required && (
            <p>
              Clinic administrator: approve a payer collection policy before instructing $0
              deferral.
            </p>
          )}
          {(chooseServices || enterAllowance) && (
            <button type="button" className="cash-package-shortcut" onClick={openServiceDetails}>
              {chooseServices
                ? 'Choose consultation, EMG/NCS, or both'
                : 'Record payer allowed amount for selected CPT'}
            </button>
          )}
          {!cash &&
            (!amount || guidance?.basis === 'claim_after_service') &&
            !calculating &&
            result.benefits?.active &&
            result.benefits.service_date_matches !== false &&
            result.benefits.coverage_status_conflict !== true &&
            result.benefits.coverage_dates_invalid !== true &&
            result.benefits.network_conflict !== true &&
            !result.benefits.review_reasons?.some((reason) =>
              reason.toLowerCase().includes('provider-network evidence reports nonparticipation'),
            ) &&
            !result.benefits.has_secondary &&
            result.benefits.historical_only !== true &&
            !result.benefits.source_errors?.length &&
            patientBenefitsBound &&
            result.recovery?.reason_code !== 'multiple_active_policies' &&
            [
              result.benefits.copay_cents,
              result.benefits.deductible_remaining_cents,
              result.benefits.coinsurance_percent,
              result.benefits.oop_remaining_cents,
              result.benefits.oop_annual_cents,
            ].some((value) => value != null) && (
              <PayerReportedPlanFigures benefits={result.benefits} />
            )}
          {!amount && <TemporaryRateReference result={result} />}
          {!cash &&
            !previousEstimate &&
            (fullBreakdown ||
              officeCopayBreakdown ||
              showSelectedRequirements ||
              (result.price_range?.mode === 'services' && !!result.price_range.lines?.length)) && (
              <details className="financial-breakdown">
                <summary>Itemized financial breakdown</summary>
                <FinancialLineItems result={result} />
                {fullBreakdown && (
                  <dl
                    className="arrival-money-picture"
                    aria-label="What the arrival amount includes"
                  >
                    <div>
                      <dt>Copay</dt>
                      <dd>
                        {amountParts?.copay_cents != null
                          ? money(amountParts.copay_cents)
                          : guidance?.basis === 'office_copay' && guidance.amount_cents != null
                            ? money(guidance.amount_cents)
                            : '—'}
                      </dd>
                    </div>
                    <div>
                      <dt>Deductible</dt>
                      <dd>
                        {amountParts?.deductible_cents != null
                          ? money(amountParts.deductible_cents)
                          : officeDeductible === 'DOES_NOT_APPLY'
                            ? 'Does not apply'
                            : officeDeductible === 'ALREADY_MET'
                              ? 'Already met'
                              : 'See assumptions'}
                      </dd>
                    </div>
                    <div>
                      <dt>Coinsurance</dt>
                      <dd>
                        {amountParts?.coinsurance_cents != null
                          ? money(amountParts.coinsurance_cents)
                          : 'See assumptions'}
                        {coinsuranceDetail && (
                          <small className="coinsurance-formula">{coinsuranceDetail}</small>
                        )}
                      </dd>
                    </div>
                    {amountParts?.allowed_cap_adjustment_cents != null &&
                      amountParts.allowed_cap_adjustment_cents > 0 && (
                        <div>
                          <dt>Payer allowed amount reduction</dt>
                          <dd>−{money(amountParts.allowed_cap_adjustment_cents)}</dd>
                        </div>
                      )}
                    {amountParts?.oop_adjustment_cents != null &&
                      amountParts.oop_adjustment_cents > 0 && (
                        <div>
                          <dt>
                            {amountParts.allowed_cap_adjustment_cents != null
                              ? 'Out-of-pocket limit reduction'
                              : 'Plan limit reduction'}
                          </dt>
                          <dd>−{money(amountParts.oop_adjustment_cents)}</dd>
                        </div>
                      )}
                    {result.encounter_summary?.payments_verified_cents != null &&
                      result.encounter_summary.payments_verified_cents > 0 && (
                        <div>
                          <dt>Already paid</dt>
                          <dd>−{money(result.encounter_summary.payments_verified_cents)}</dd>
                        </div>
                      )}
                  </dl>
                )}
                {officeCopayBreakdown && (
                  <dl
                    className="arrival-money-picture"
                    aria-label="What the office copay advance includes"
                  >
                    <div>
                      <dt>Office copay</dt>
                      <dd>{money(amountParts.copay_cents!)}</dd>
                    </div>
                    {paid > 0 && (
                      <div>
                        <dt>Already paid</dt>
                        <dd>−{money(paid)}</dd>
                      </div>
                    )}
                  </dl>
                )}
                {(fullBreakdown || officeCopayBreakdown) && (
                  <VisitPatientTotals result={result} officeOnly={Boolean(officeCopayBreakdown)} />
                )}
                {applicablePlanLimits && (
                  <div className="plan-limit-summary">
                    <small>Applicable in-network plan limits</small>
                    <dl
                      className="arrival-money-picture"
                      aria-label="Applicable in-network plan limits"
                    >
                      {benefitTerms?.oop_annual_cents != null && (
                        <div>
                          <dt>Individual out-of-pocket maximum</dt>
                          <dd>{money(benefitTerms.oop_annual_cents)}</dd>
                        </div>
                      )}
                      {benefitTerms?.oop_remaining_cents != null && (
                        <div>
                          <dt>Individual out-of-pocket remaining</dt>
                          <dd>{money(benefitTerms.oop_remaining_cents)}</dd>
                        </div>
                      )}
                    </dl>
                  </div>
                )}
                {showSelectedRequirements && (
                  <SelectedServiceRequirements
                    result={result}
                    payerName={payerForAction || 'the payer'}
                  />
                )}
              </details>
            )}
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
            <p className="visit-identity-notice">{result.payment_explanation.notice}</p>
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
              {amount && (
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
              {!staleInsuredGuidance && !!guidance?.patient_actions.length && (
                <div>
                  <b>Tell the patient:</b>
                  <ul>
                    {guidance.patient_actions.map((action) => (
                      <li key={action}>{action}</li>
                    ))}
                  </ul>
                </div>
              )}
              {!staleInsuredGuidance && !!guidance?.assumptions.length && (
                <ul>
                  {guidance.assumptions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}
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
          <p>
            {readOnly
              ? 'Service plan recorded for this appointment.'
              : 'Based on the scheduled appointment. Modify the services below if the planned care changes.'}
          </p>
        </div>
      </div>
      <div className="visit-snapshots" aria-label="Coverage summary">
        <div className={coverageTone}>
          <ShieldCheck size={20} aria-hidden="true" />
          <span>
            Coverage<strong>{policyStatus}</strong>
            <small>
              {result?.benefit_source
                ? `Checked via ${result.benefit_source}`
                : result?.benefits?.active
                  ? 'Payer source not recorded'
                  : 'Payer result unavailable'}
            </small>
          </span>
        </div>
        <div className={networkTone}>
          <Network size={20} aria-hidden="true" />
          <span>
            Network<strong>{networkLabel}</strong>
            {result?.network_evidence?.reason && <small>{result.network_evidence.reason}</small>}
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
        {!showSelectedRequirements && !requirementIssue && (
          <RequirementCards result={result} readOnly={readOnly} payerName={payerForAction} />
        )}
      </div>
      <EstimateSources result={result} tooEarly={!!tooEarly} />
      <div className="overview-plan-name">
        <ShieldCheck size={16} aria-hidden="true" />
        <span>
          {cash
            ? 'Clinic self-pay schedule'
            : planName || result.payer || 'Policy details not returned'}
        </span>
        {!cash && cashPrice && <span className="cash-comparison">Cash option {cashPrice}</span>}
      </div>
    </section>
  )
}
