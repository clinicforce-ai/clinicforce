import { useCardCapture } from './useCardCapture'
import CardCaptureView from './CardCaptureView'
import './InsuranceCardScanner.css'

interface Props {
  onCaptured: (files: File[]) => void
  onCancel: () => void
}

export default function InsuranceCardScanner(props: Props) {
  const model = useCardCapture()
  return <CardCaptureView model={model} {...props} />
}
