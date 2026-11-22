import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'
import { potentialOfficeFormula, potentialOfficeIssue } from './potentialOfficeAmount'

export default function PotentialOfficeArrival({ result }: { result: PrevisitEstimate }) {
  const potential = result.collection_guidance?.potential_office_collection
  if (!potential) return null
  const payer = result.payer || 'payer'
  return (
    <>
      <span>Potential office amount to collect · conditional</span>
      <strong role="status">{money(potential.amount_cents)}</strong>
      <p>{potentialOfficeFormula(potential)}</p>
      <p>Exact reasons this is not an authorized charge yet:</p>
      <ul>
        {potential.issues.length ? (
          potential.issues.map((issue, index) => (
            <li key={`${issue.code}-${issue.field || index}`}>
              {potentialOfficeIssue(issue, payer)}
            </li>
          ))
        ) : (
          <li>{result.collection_guidance?.unresolved_collection_reason}</li>
        )}
      </ul>
      <p>Amount to collect now: {money(result.collection_guidance?.amount_cents || 0)}.</p>
    </>
  )
}
