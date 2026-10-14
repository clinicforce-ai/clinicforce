import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { ServicePlanner } from './ServicePlanner'

export default function CommercialServicesDetail({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const { readOnly, busy, setNotice, recovering, saveReview, anticipatedService } = model
  return (
    <details
      open={readOnly || undefined}
      id="visit-detail-services"
      className="visit-detail-section"
    >
      <summary>
        {readOnly ? 'Saved service plan' : 'Possible additional services / modify inputs'}
      </summary>
      <div className="visit-detail-body">
        <p>{anticipatedService}</p>
        {readOnly ? (
          <ul>
            {result.codes.map((row, index) => (
              <li key={row.id || index}>
                <strong>
                  CPT {row.code} × {row.units ?? 'Units not retained'}
                </strong>
                {row.description && <p>{row.description}</p>}
                <small>
                  {row.source}
                  {row.modifiers?.length ? ` · Modifiers ${row.modifiers.join(', ')}` : ''}
                </small>
              </li>
            ))}
          </ul>
        ) : (
          <ServicePlanner
            key={`${result.check_id}:${result.revision}`}
            result={result}
            busy={!!busy || recovering}
            save={saveReview}
            viewEstimate={(message) => {
              setNotice(message || 'Estimate updated.')
              document.getElementById('visit-estimate')?.scrollIntoView({ block: 'start' })
            }}
          />
        )}
      </div>
    </details>
  )
}
