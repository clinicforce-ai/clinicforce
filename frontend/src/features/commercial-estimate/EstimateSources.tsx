import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { visitDate, visitTime } from './format'

export default function EstimateSources({
  result,
  tooEarly,
}: {
  result: PrevisitEstimate
  tooEarly: boolean
}) {
  return (
    <p className="arrival-sources">
      Sources: eCW
      {tooEarly
        ? ' · Payer check later'
        : result.checked_at
          ? ` + ${result.benefit_source || 'payer'} · ${visitDate(result.checked_at)} ${visitTime(result.checked_at)}`
          : result.benefits?.active
            ? ' · Payer check date unknown'
            : ' · Payer check needed'}
    </p>
  )
}
