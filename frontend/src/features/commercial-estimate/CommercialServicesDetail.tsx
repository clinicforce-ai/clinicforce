import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { ServicePlanner } from './ServicePlanner'
import { PayerAllowanceEntry } from './PayerAllowanceEntry'

export default function CommercialServicesDetail({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const { readOnly, busy, setNotice, recovering, saveReview, anticipatedService } = model
  return (
    <details id="visit-detail-services" className="visit-detail-section">
      <summary>
        {readOnly ? 'Saved service plan' : 'Review planned CPTs and additional care'}
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
          <>
            <ServicePlanner
              key={`${result.appointment_id}:${result.check_id}:${result.context_token}:${result.revision}:${(result.selected_ids || []).join(',')}`}
              result={result}
              busy={!!busy || recovering}
              save={saveReview}
              viewEstimate={(message) => {
                setNotice(message || 'Estimate updated.')
                document.getElementById('visit-estimate')?.scrollIntoView({ block: 'start' })
              }}
            />
            {model.session?.role === 'admin' &&
              result.payment_mode === 'insurance' &&
              result.benefits?.network_status === 'out_of_network' &&
              [
                'clinic_nonparticipation_rule',
                'payer_participation_message',
                'clinic_contract_inventory',
              ].includes(result.benefits.network_source || '') && (
                <PayerAllowanceEntry
                  key={`${result.check_id}:${result.revision}`}
                  result={result}
                  busy={!!busy || recovering}
                  save={saveReview}
                  viewEstimate={() => {
                    setNotice('Payer allowance updated.')
                    document.getElementById('visit-estimate')?.scrollIntoView({ block: 'start' })
                  }}
                />
              )}
          </>
        )}
      </div>
    </details>
  )
}
