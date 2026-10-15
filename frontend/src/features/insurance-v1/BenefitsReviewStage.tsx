import type { InsuranceV1Model } from './useInsuranceV1Model'
import { FactList } from './InsuranceV1Details'

export default function BenefitsReviewStage({ model }: { model: InsuranceV1Model }) {
  const { busy, advance, reviewing, facts } = model
  return (
    <>
      <p className="section-kicker">6 · Review benefits</p>
      <h2>Check the source values before calculating</h2>
      <p>
        These are the benefit values read from your upload. The estimate uses your selected CPT
        rates and these balances. If a required value is missing or ambiguous, no patient amount
        will be invented.
      </p>
      <FactList facts={facts.filter((fact) => fact.stage === 'network')} />
      {!reviewing && (
        <button
          className="button button-primary"
          type="button"
          disabled={busy}
          onClick={() => advance('benefits')}
        >
          Calculate pay estimate
        </button>
      )}
    </>
  )
}
