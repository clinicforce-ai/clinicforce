import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

export default function TemporaryRateReference({ result }: { result: PrevisitEstimate }) {
  const reference = result.temporary_rate_reference
  if (
    !reference?.lines.length ||
    (/\baetna\b/i.test(result.payer || '') && !reference.source?.startsWith('Aetna MEPPO')) ||
    (/\bhumana\b/i.test(result.payer || '') && /blue cross/i.test(reference.source || ''))
  )
    return null
  return (
    <details className="summary-assumptions">
      <summary>Temporary CPT price reference</summary>
      <p>{reference.message}</p>
      <dl>
        {reference.lines.map((line) => (
          <div key={line.code}>
            <dt>CPT {line.code}</dt>
            <dd>{money(line.unit_reference_cents)} per unit</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}
