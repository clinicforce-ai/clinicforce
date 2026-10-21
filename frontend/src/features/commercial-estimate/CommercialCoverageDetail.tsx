import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import CoverageRecovery from './CoverageRecovery'
import PatientPolicy from './PatientPolicy'

export default function CommercialCoverageDetail({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  return (
    <details id="visit-detail-coverage" className="visit-detail-section">
      <summary>Detailed benefits, insurance & card</summary>
      <div className="visit-detail-body">
        <CoverageRecovery model={model} result={result} />
        <PatientPolicy model={model} result={result} />
      </div>
    </details>
  )
}
