import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

export default function StediOfficeArrival({ result }: { result: PrevisitEstimate }) {
  return (
    <>
      <span>Amount to collect at visit · provisional office copay</span>
      <strong role="status">{money(result.collection_guidance!.amount_cents!)}</strong>
      <p>{result.collection_guidance!.reason}</p>
    </>
  )
}
