import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export function availityVisitPlanning(result: PrevisitEstimate, model: CommercialEstimateModel) {
  const evidence = result.availity_visit_reference
  const officeOnly =
    result.codes.length === 0 ||
    (result.codes.length === 1 &&
      result.codes[0].code === '99203' &&
      Number(result.codes[0].units) === 1 &&
      !result.codes[0].modifiers?.length)
  return !model.cash &&
    !model.previousEstimate &&
    evidence?.active === true &&
    evidence.service_date === result.service_date &&
    evidence.network_status === 'in_network' &&
    officeOnly
    ? evidence
    : null
}
