import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'
import {
  potentialOfficeFormula,
  potentialOfficeIssue,
  potentialOfficeIssueLabel,
} from './potentialOfficeAmount'

export default function PotentialOfficeArrival({ result }: { result: PrevisitEstimate }) {
  const potential = result.collection_guidance?.potential_office_collection
  if (!potential) return null
  const payer = result.payer || 'payer'
  return (
    <>
      <span>Potential office amount · conditional</span>
      <strong role="status">{money(potential.amount_cents)}</strong>
      <p>
        Based on the payer’s office copay for CPT {potential.office_code}; confirm before charging.
      </p>
      <p>Amount to collect now: {result.collection_guidance?.amount_cents == null
        ? 'Pending review' : money(result.collection_guidance.amount_cents)}.</p>
      <details className="potential-office-review">
        <summary>
          Review before collecting:{' '}
          {potential.issues.map(potentialOfficeIssueLabel).join(', ') || 'payer terms'}
        </summary>
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
        <p>{potentialOfficeFormula(potential)}</p>
      </details>
    </>
  )
}
