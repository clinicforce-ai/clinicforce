import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { visitDate } from './format'

export default function EcwEligibilityEvidenceUpload({
  result,
  model,
}: {
  result: PrevisitEstimate
  model: CommercialEstimateModel
}) {
  if (model.session?.role !== 'admin' || !result.check_id) return null
  return (
    <form
      className="visit-cash-quote"
      onSubmit={(event) => {
        event.preventDefault()
        const file = (
          event.currentTarget.elements.namedItem('ecw_eligibility_file') as HTMLInputElement
        )?.files?.[0]
        if (file) void model.attachEcwEligibility(file)
      }}
    >
      <label>
        eCW eligibility report PDF
        <input name="ecw_eligibility_file" type="file" accept="application/pdf,.pdf" required />
      </label>
      <label>
        <input type="checkbox" required /> I checked the member, birth date, payer and provider on
        this report against this visit.
      </label>
      <button type="submit" disabled={!!model.busy || model.recovering}>
        Attach EHR report
      </button>
      {result.ecw_eligibility_reference && (
        <small>
          EHR report dated {visitDate(result.ecw_eligibility_reference.report_date)} attached as
          reference evidence. Its figures do not change the collection amount.
        </small>
      )}
    </form>
  )
}
