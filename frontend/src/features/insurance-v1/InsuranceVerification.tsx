import { useInsuranceV1Model } from './useInsuranceV1Model'
import InsuranceVerificationView from './InsuranceVerificationView'

export default function InsuranceVerification(props: {
  onBack: () => void
  staffMode?: boolean
  staffIntakeId?: string
}) {
  const model = useInsuranceV1Model(props)
  return <InsuranceVerificationView model={model} />
}
