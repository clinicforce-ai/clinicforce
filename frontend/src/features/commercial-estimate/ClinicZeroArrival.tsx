import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'
import { clinicFallbackReason } from './isClinicZeroArrival'

export default function ClinicZeroArrival({ result }: { result: PrevisitEstimate }) {
  return (
    <>
      <span>Amount to collect at visit</span>
      <strong role="status">{money(0)}</strong>
      <p>
        {result.collection_guidance?.collection_rule?.mode === 'clinic_zero_arrival_default'
          ? result.collection_guidance.reason
          : clinicFallbackReason}
      </p>
    </>
  )
}
