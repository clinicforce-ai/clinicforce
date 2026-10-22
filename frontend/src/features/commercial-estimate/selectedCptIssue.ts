import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { patientBenefitsBound } from './identityBinding'

export function firstSelectedCptIssue(result?: PrevisitEstimate | null): string | null {
  const network = result?.benefits?.network_status
  return (
    result?.price_range?.lines
      ?.map((line) => {
        const terms = line.benefits
        const lineIdentity = terms.identity
        const code = `CPT ${line.code}`
        if (terms.active === false)
          return `${code} is marked inactive or noncovered. Ask the payer whether it is covered on this visit date.`
        if (terms.service_date_matches === false)
          return `${code} benefits cover a different date. Recheck this CPT for the visit date.`
        if (terms.coverage_status_conflict === true)
          return `${code} has conflicting active and inactive coverage. Ask the payer which status applies.`
        if (terms.coverage_dates_invalid === true)
          return `${code} has an unreadable coverage date. Obtain the correct payer date for this visit.`
        if (terms.historical_only === true)
          return `${code} has historical-only payer evidence. Rerun the payer check before collecting.`
        if (terms.has_secondary === true)
          return `${code} has other coverage. Establish payer order and secondary benefits before collecting.`
        if (
          (terms.active !== null || line.calculation != null) &&

          lineIdentity != null &&
          (!patientBenefitsBound(lineIdentity) ||
            lineIdentity.binding !== result?.identity?.binding)
        ) {
          return `${code} benefits do not match this patient. Reconcile patient and subscriber identity with the payer.`
        }
        if (
          terms.network_conflict === true ||
          (['in_network', 'out_of_network'].includes(network || '') &&
            ['in_network', 'out_of_network'].includes(terms.network_status || '') &&
            network !== terms.network_status)
        ) {
          return `${code} network tier conflicts with this provider's plan result. Ask the payer which tier applies.`
        }
        return null
      })
      .find((issue) => issue != null) || null
  )
}
