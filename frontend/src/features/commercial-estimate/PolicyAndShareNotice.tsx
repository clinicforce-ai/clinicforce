import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export default function PolicyAndShareNotice({ model }: { model: CommercialEstimateModel }) {
  const { guidance, patientPrice, cash, readOnly } = model
  return (
    <>
      {!cash && guidance?.basis === 'claim_after_service' && patientPrice && (
        <p>Estimated patient share: {patientPrice}. Final responsibility follows the claim.</p>
      )}
      {!readOnly && guidance?.policy_setup_required && (
        <p>
          {guidance.positive_advance_policy_missing || guidance.published_copay_policy_missing
            ? 'Clinic administrator: approve this payer route’s check-in advance policy before requesting payment.'
            : 'Clinic administrator: approve a payer collection policy before instructing $0 deferral.'}
        </p>
      )}
    </>
  )
}
