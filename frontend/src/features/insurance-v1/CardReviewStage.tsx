import type { InsuranceV1Model } from './useInsuranceV1Model'

export default function CardReviewStage({ model }: { model: InsuranceV1Model }) {
  const { busy, advance, reviewing, cardReview } = model
  return (
    <>
      <p className="section-kicker">2 · Card review</p>
      <h2>Card details confirmed</h2>
      <p>
        An insurance card identifies the member; it does not by itself verify active coverage or
        current benefit balances. Next, upload the member’s dated benefit details and the applicable
        fee schedule.
      </p>
      {cardReview}
      {!reviewing && (
        <button
          className="button button-primary"
          type="button"
          disabled={busy}
          onClick={() => advance('eligibility')}
        >
          Continue to benefits & rates
        </button>
      )}
    </>
  )
}
