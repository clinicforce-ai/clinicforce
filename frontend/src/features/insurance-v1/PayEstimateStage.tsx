import type { InsuranceV1Model } from './useInsuranceV1Model'
import { SourceDates, SourcePages, FactList } from './InsuranceV1Details'
import { money, timestamp } from './v1-display'

export default function PayEstimateStage({
  model,
  estimate,
}: {
  model: InsuranceV1Model
  estimate: NonNullable<InsuranceV1Model['estimate']>
}) {
  const { insuranceCase, facts, documents } = model
  return (
    <>
      <p className="section-kicker">6 · Pre-visit pay estimate</p>
      <section className="v1-collection" aria-label="Front desk collection decision">
        <span>Front desk action</span>
        <h2>
          {estimate.collection_action === 'collect'
            ? `Collect ${money(estimate.collect_today_cents)} today`
            : estimate.collection_action === 'do_not_collect'
              ? 'Do not collect today'
              : estimate.collection_action === 'out_of_network'
                ? 'Out of network · review with patient'
                : 'Verify before collecting'}
        </h2>
        <p>{estimate.collection_reason}</p>
        {estimate.verification_reasons?.length ? (
          <ul>
            {estimate.verification_reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        ) : null}
        <dl className="insurance-facts">
          <div>
            <dt>Estimated patient share</dt>
            <dd>{money(estimate.patient_cents)}</dd>
          </div>
          <div>
            <dt>Estimated insurance payment</dt>
            <dd>{money(estimate.plan_cents)}</dd>
          </div>
        </dl>
        <p className="missing-source">
          Estimate confidence: {estimate.confidence ?? 'Not assessed'} ·{' '}
          {estimate.confidence_reason}
        </p>
        <small>Calculated: {timestamp(estimate.calculated_at)} (your local time)</small>
      </section>
      <details className="source-excerpt v1-calculation">
        <summary>See calculation · normal share and benefit-limit adjustment</summary>
        <dl className="insurance-facts">
          <div>
            <dt>Selected allowed amounts</dt>
            <dd>{money(estimate.allowed_cents)}</dd>
          </div>
          <div>
            <dt>Deductible applied</dt>
            <dd>{money(estimate.deductible_applied_cents)}</dd>
          </div>
          <div>
            <dt>Normal coinsurance</dt>
            <dd>{money(estimate.nominal_coinsurance_cents)}</dd>
          </div>
          <div>
            <dt>Patient share before benefit limits</dt>
            <dd>{money(estimate.normal_patient_share_cents)}</dd>
          </div>
          <div>
            <dt>Individual out-of-pocket remaining</dt>
            <dd>{money(estimate.individual_oop_remaining_cents)}</dd>
          </div>
          <div>
            <dt>Out-of-pocket adjustment</dt>
            <dd>{money(estimate.oop_cap_adjustment_cents)}</dd>
          </div>
          <div>
            <dt>Patient estimate after limits</dt>
            <dd>{money(estimate.patient_cents)}</dd>
          </div>
        </dl>
        <p>
          The patient estimate applies to eligible covered services. An unresolved requirement means
          staff should verify before deciding what to collect.
        </p>
      </details>
      <h3>Your selected codes, item by item</h3>
      <p>
        Allowed prices come from {documents.fee_schedule?.filename}. Patient and plan shares are
        calculated from {documents.coverage?.filename}.
      </p>
      {estimate.estimate_available === false && (
        <p className="form-error" role="status">
          {estimate.unavailable_reason}
        </p>
      )}
      <section className="v1-estimate-items" aria-label="Selected CPT and HCPCS code estimates">
        {estimate.line_items?.map((item, index) => (
          <article key={item.code}>
            <header>
              <span>
                Item {index + 1} · CPT/HCPCS {item.code}
              </span>
              <h3>{item.description}</h3>
            </header>
            <dl className="insurance-facts">
              <div>
                <dt>Allowed price</dt>
                <dd>{money(item.allowed_cents)}</dd>
              </div>
              <div>
                <dt>Rate and quantity</dt>
                <dd>
                  {money(item.unit_allowed_cents ?? item.allowed_cents)} × {item.units ?? 1}
                </dd>
              </div>
              <div>
                <dt>Estimated patient share</dt>
                <dd>{money(item.patient_cents)}</dd>
              </div>
              <div>
                <dt>Estimated plan share</dt>
                <dd>{money(item.plan_cents)}</dd>
              </div>
            </dl>
            {item.patient_cents != null && (
              <details className="source-excerpt">
                <summary>Calculation details</summary>
                <p>
                  Deductible applied: {money(item.deductible_applied_cents)}. Coinsurance before the
                  out-of-pocket cap: {money(item.coinsurance_cents)}. Balances are applied in the
                  selected code order.
                </p>
              </details>
            )}
          </article>
        ))}
      </section>
      {estimate.estimate_available !== false && (
        <section className="v1-estimate-total" aria-label="Estimate totals">
          <h3>Estimated total</h3>
          <dl className="insurance-facts">
            <div>
              <dt>All selected allowed prices</dt>
              <dd>{money(estimate.allowed_cents)}</dd>
            </div>
            <div>
              <dt>Patient responsibility</dt>
              <dd>{money(estimate.patient_cents)}</dd>
            </div>
            <div>
              <dt>Plan responsibility</dt>
              <dd>{money(estimate.plan_cents)}</dd>
            </div>
          </dl>
        </section>
      )}
      <section className="v1-estimate-total" aria-label="Self-pay comparison">
        <h3>Self-pay option</h3>
        {estimate.self_pay?.available ? (
          <>
            <p>
              Cash prices from {documents.self_pay?.filename}; separate from insurance benefits.
            </p>
            <dl className="insurance-facts">
              {estimate.self_pay.line_items.map((item) => (
                <div key={item.code}>
                  <dt>{item.code}</dt>
                  <dd>{money(item.amount_cents)}</dd>
                </div>
              ))}
              <div>
                <dt>Total self-pay price</dt>
                <dd>{money(estimate.self_pay.total_cents)}</dd>
              </div>
            </dl>
          </>
        ) : (
          <p>
            {documents.self_pay
              ? 'A cash price could not be matched to every selected code. Review the uploaded pricing guide; no total has been invented.'
              : 'No cash-rate document was uploaded. Insurance allowed amounts are not self-pay prices.'}
          </p>
        )}
        {documents.self_pay?.findings?.pages && <SourcePages source={documents.self_pay} />}
      </section>
      <details className="source-excerpt">
        <summary>What we know · what could change</summary>
        <FactList facts={facts.filter((fact) => fact.stage === 'network')} />
        <p>
          Final CPTs, units, medical necessity, benefit applicability, coordination of benefits and
          claim processing can change the final amount. A document’s upload date does not establish
          that its benefits are current for this visit.
        </p>
      </details>
      <details className="source-excerpt">
        <summary>View source evidence and document dates</summary>
        {[
          ...(insuranceCase?.card_source ? [insuranceCase.card_source] : []),
          ...Object.values(documents),
        ].map((source) => (
          <section key={source.id}>
            <h4>{source.filename}</h4>
            <SourceDates source={source} />
            {source.findings?.pages && <SourcePages source={source} />}
          </section>
        ))}
        <FactList facts={facts.filter((fact) => fact.stage !== 'intake')} />
      </details>
      <p className="estimate-disclaimer">{estimate.disclaimer}</p>
    </>
  )
}
