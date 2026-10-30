import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { CostPicture } from './EstimatePanels'
import { money, range } from './format'
import { patientBenefitsBound } from './identityBinding'

export default function CommercialCalculationDetail({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const {
    price,
    patientPrice,
    calculationReasons,
    cashPrice,
    cash,
    guidance,
    emg,
    emgScenario,
    amount,
    deferred,
    previousEstimate,
  } = model
  const claimAfterService = guidance?.basis === 'claim_after_service'
  const combinedPlan = !cash && !previousEstimate ? result.combined_emg_plan : null
  const knownOonAdvance =
    !cash &&
    !previousEstimate &&
    !!amount &&
    !claimAfterService &&
    guidance?.amount_cents != null &&
    ['anticipated_services', 'office_copay'].includes(guidance.basis) &&
    result.benefits?.network_status === 'out_of_network' &&
    [
      'payer_participation_message',
      'clinic_nonparticipation_rule',
      'clinic_contract_inventory',
    ].includes(result.benefits.network_source || '')
  const knownOfficeCopay =
    !cash && !previousEstimate && !!amount && guidance?.basis === 'office_copay' && !knownOonAdvance
  const knownDiagnosticCopay =
    !cash && !previousEstimate && !!amount && guidance?.basis === 'diagnostic_day_copay'
  const insuredTotalSupported =
    result.encounter_summary?.complete === true &&
    result.benefits?.network_status === 'in_network' &&
    ['clinic_library_contract', 'clinic_group_agreement', 'payer_participation_message'].includes(
      result.benefits.network_source || '',
    ) &&
    patientBenefitsBound(result.identity) &&
    !result.benefits?.has_secondary &&
    result.recovery?.reason_code !== 'multiple_active_policies'
  return (
    <details id="visit-detail-calculation" className="visit-detail-section">
      <summary>Patient estimate</summary>
      <div className="visit-detail-body">
        <section>
          <div className="planning-summary">
            <h3>
              {combinedPlan
                ? 'Planned office and EMG/NCS patient share'
                : cash
                  ? 'Cash price'
                  : claimAfterService
                    ? 'Patient responsibility after claim'
                    : knownOonAdvance
                      ? 'Known out-of-network plan share'
                      : knownOfficeCopay
                        ? 'Known office copay advance'
                        : knownDiagnosticCopay
                          ? 'Known diagnostic copay advance'
                          : !insuredTotalSupported
                            ? 'Complete patient responsibility'
                            : emgScenario
                              ? 'Planning total with testing'
                              : price?.conditional
                                ? 'Conditional patient estimate'
                                : 'Estimated patient responsibility'}{' '}
              ·{' '}
              {combinedPlan
                ? money(combinedPlan.patient_share_cents)
                : cash
                  ? previousEstimate
                    ? 'Needs recheck'
                    : cashPrice || 'Price unavailable'
                  : claimAfterService
                    ? 'Determined by processed claim'
                    : knownOonAdvance
                      ? amount
                      : knownOfficeCopay
                        ? amount
                        : knownDiagnosticCopay
                          ? amount
                          : !insuredTotalSupported
                            ? 'See collection action above'
                            : emgScenario &&
                                emg &&
                                emg.total_min_cents != null &&
                                emg.total_max_cents != null
                              ? range(emg.total_min_cents, emg.total_max_cents)
                              : deferred || !amount
                                ? 'Not yet calculated'
                                : insuredTotalSupported &&
                                    result.planning_estimate?.min_cents != null &&
                                    result.planning_estimate?.max_cents != null
                                  ? range(
                                      result.planning_estimate.min_cents,
                                      result.planning_estimate.max_cents,
                                    )
                                  : patientPrice || 'Applicable advance amount shown above'}
            </h3>
            {combinedPlan && (
              <p>
                {money(combinedPlan.office_copay_cents)} office copay +{' '}
                {combinedPlan.coinsurance_percent}% × {money(combinedPlan.diagnostic_allowed_cents)}{' '}
                diagnostic allowance. Remaining deductible and out-of-pocket balances were not
                returned.
              </p>
            )}
            {amount &&
              !deferred &&
              !claimAfterService &&
              insuredTotalSupported &&
              !emgScenario &&
              result.planning_estimate && <p>{result.planning_estimate.explanation}</p>}
            {amount &&
              !deferred &&
              !claimAfterService &&
              (insuredTotalSupported || knownOonAdvance) &&
              !emgScenario && <CostPicture result={result} />}
          </div>
          {!!result.service_plan?.assumptions?.length && (
            <section>
              <h3>Service-plan assumptions</h3>
              <ul>
                {result.service_plan.assumptions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
          {calculationReasons.map((reason) => (
            <p key={reason}>{reason}</p>
          ))}
          {emgScenario && (
            <p>
              The service details above cover the consultation. The additional EMG scenario and
              combined total are shown above.
            </p>
          )}
          <p className="visit-muted">
            The arrival amount is an advance collection. Final patient responsibility follows the
            insurer’s processed claim.
          </p>
        </section>
      </div>
    </details>
  )
}
