import { useCommercialEstimateModel } from './useCommercialEstimateModel'
import CommercialEstimateView from './CommercialEstimateView'
import type { CommercialEstimatorProps } from './commercial-props'
import './CommercialEstimator.css'

export default function CommercialEstimator(props: CommercialEstimatorProps) {
  const model = useCommercialEstimateModel(props)
  return <CommercialEstimateView model={model} />
}
