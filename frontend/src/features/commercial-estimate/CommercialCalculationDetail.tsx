import { PieChart } from 'lucide-react'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { CostPicture } from './EstimatePanels'
import { money, range } from './format'

export default function CommercialCalculationDetail({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const {
    readOnly,
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
    amountText,
    amountLabel,
    explanation,
  } = model
  return (
    <details
      open={readOnly || undefined}
      id="visit-detail-calculation"
      className="visit-detail-section"
    >
      <summary>Patient estimate</summary>
      <div className="visit-detail-body">
        <section>
          <div className="planning-summary">
            <h3>
              {cash
                ? 'Cash price'
                : emgScenario
                  ? 'Planning total with testing'
                  : price?.conditional
                    ? 'Conditional patient estimate'
                    : 'Estimated patient responsibility'}{' '}
              ·{' '}
              {cash
                ? cashPrice || 'Price unavailable'
                : emgScenario && emg && emg.total_min_cents != null && emg.total_max_cents != null
                  ? range(emg.total_min_cents, emg.total_max_cents)
                  : deferred || !amount
                    ? 'Not yet calculated'
                    : result.planning_estimate
                      ? money(result.planning_estimate.amount_cents)
                      : patientPrice || 'Applicable advance amount shown above'}
            </h3>
            {amount && !deferred && !emgScenario && result.planning_estimate && (
              <p>{result.planning_estimate.explanation}</p>
            )}
            {amount && !deferred && !emgScenario && <CostPicture result={result} />}
          </div>
          <div className="calculation-summary">
            <PieChart size={24} aria-hidden="true" />
            <div>
              <h3>
                {amountLabel} · {amountText}
              </h3>
              <p>{explanation}</p>
            </div>
          </div>
          {!!guidance?.assumptions.length && (
            <section>
              <h3>Assumptions used</h3>
              <ul>
                {guidance.assumptions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
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
          {!cash && !deferred && !emgScenario && price?.complete && (
            <dl className="collection-components">
              {(
                [
                  'copay_cents',
                  'deductible_cents',
                  'coinsurance_cents',
                  'oop_adjustment_cents',
                ] as const
              ).map((key) => (
                <div key={key}>
                  <dt>
                    {
                      {
                        copay_cents: 'Copay applied',
                        deductible_cents: 'Deductible applied',
                        coinsurance_cents: 'Coinsurance applied',
                        oop_adjustment_cents: 'Out-of-pocket adjustment',
                      }[key]
                    }
                  </dt>
                  <dd>
                    {price.mode === 'services'
                      ? money(
                          (price.lines || []).reduce(
                            (sum, line) => sum + (line.calculation?.[key] || 0),
                            0,
                          ),
                        )
                      : 'Depends on final visit level'}
                  </dd>
                </div>
              ))}
            </dl>
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
