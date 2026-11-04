import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import CoverageRecovery from './CoverageRecovery'
import PatientPolicy from './PatientPolicy'

export default function CommercialCoverageDetail({
  model,
  result,
  expanded = false,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
  expanded?: boolean
}) {
  return (
    <details
      id={expanded ? 'latest-visit-detail-coverage' : 'visit-detail-coverage'}
      className="visit-detail-section"
      open={expanded || undefined}
    >
      <summary>Detailed benefits, insurance & card</summary>
      <div className="visit-detail-body">
        <CoverageRecovery model={model} result={result} />
        <PatientPolicy model={model} result={result} />
      </div>
    </details>
  )
}
