import { useCommercialEstimateActions } from './useCommercialEstimateActions'
import { deriveCommercialEstimateView } from './deriveCommercialEstimateView'
import type { CommercialEstimatorProps } from './commercial-props'

export function useCommercialEstimateModel(props: CommercialEstimatorProps) {
  const actions = useCommercialEstimateActions(props)
  return { ...actions, ...deriveCommercialEstimateView(actions) }
}
export type CommercialEstimateModel = ReturnType<typeof useCommercialEstimateModel>
