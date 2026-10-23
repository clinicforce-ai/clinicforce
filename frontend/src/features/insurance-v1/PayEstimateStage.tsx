import type { InsuranceV1Model } from './useInsuranceV1Model'
import { SourceDates, SourcePages, FactList } from './InsuranceV1Details'
import { money, timestamp } from './v1-display'
import CostBreakdownTable, { type CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { serviceRowLabel } from '../insurance-estimate/serviceRowLabel'

export default function PayEstimateStage({
  model,
  estimate,
}: {
  model: InsuranceV1Model
  estimate: NonNullable<InsuranceV1Model['estimate']>
}) {
  const { insuranceCase, facts, documents } = model
  const rows: CostBreakdownRow[] = [
    ...((estimate.line_items || []).map((item) => ({
      label: serviceRowLabel({ description: item.description, code: item.code, units: item.units }),
      value: money(item.allowed_cents),
      detail:
        item.unit_allowed_cents != null
          ? `${money(item.unit_allowed_cents)} per unit × ${item.units ?? 1} · patient share ${money(item.patient_cents)}`
          : `Patient share ${money(item.patient_cents)}`,
    })) || []),
    { label: 'Total allowed', value: money(estimate.allowed_cents) },
    { label: 'Copay applied', value: money(estimate.copay_cents) },
    {
      label: 'Individual deductible',
      value: `${money(estimate.individual_deductible_remaining_cents)} remaining`,
      detail: `${money(estimate.deductible_applied_cents)} applied to this visit`,
    },
    { label: 'Coinsurance after deductible', value: money(estimate.nominal_coinsurance_cents) },
    {
      label: 'Individual out-of-pocket remaining',
      value: money(estimate.individual_oop_remaining_cents),
      detail: `${money(estimate.oop_cap_adjustment_cents)} reduction applied to this visit`,
    },
    {
      label: 'Estimated patient share',
      value: money(estimate.patient_cents),
      detail: 'Copay + deductible + coinsurance − out-of-pocket adjustment',
    },
    { label: 'Estimated plan payment', value: money(estimate.plan_cents) },
    {
      label: 'Cash alternative',
      value: estimate.self_pay?.available ? money(estimate.self_pay.total_cents) : 'Not quoted',
    },
    {
      label:
        estimate.collection_action === 'collect'
          ? 'Amount to collect at visit'
          : 'Collection decision',
      value:
        estimate.collection_action === 'collect' && estimate.collect_today_cents != null
          ? money(estimate.collect_today_cents)
          : estimate.collection_action === 'do_not_collect'
            ? '$0.00 under the recorded collection decision'
            : 'Verify before collecting',
      emphasis: true,
    },
  ]
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
        <small>Calculated: {timestamp(estimate.calculated_at)} (your local time)</small>
        <CostBreakdownTable rows={rows} />
        <details className="source-excerpt">
          <summary>Why this decision?</summary>
          <p>{estimate.collection_reason}</p>
          {estimate.verification_reasons?.length ? (
            <ul>
              {estimate.verification_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
          <p>
            Estimate confidence: {estimate.confidence ?? 'Not assessed'} ·{' '}
            {estimate.confidence_reason}
          </p>
        </details>
      </section>
      {estimate.estimate_available === false && (
        <p className="form-error" role="status">
          {estimate.unavailable_reason}
        </p>
      )}
      <details className="source-excerpt" aria-label="Self-pay comparison">
        <summary>Cash price details</summary>
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
      </details>
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
        <p>{estimate.disclaimer}</p>
      </details>
    </>
  )
}
