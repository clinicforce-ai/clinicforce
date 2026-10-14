import { useInsuranceV2Model } from './useInsuranceV2Model'
import InsuranceV2View from './InsuranceV2View'
import type { InsuranceV2Props } from './v2-props'

export default function InsuranceVerificationV2(props: InsuranceV2Props) {
  const model = useInsuranceV2Model(props)
  return <InsuranceV2View model={model} />
}
