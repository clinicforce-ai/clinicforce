import { CheckCircle2 } from 'lucide-react'
import type { UploadClinic, UploadReceipt } from '../../services/api/insurance-upload'
export function PatientUploadReceipt({
  clinic,
  receipt,
  patientSpecific,
}: {
  clinic: UploadClinic | null
  receipt: UploadReceipt | null
  patientSpecific: boolean
}) {
  return (
    <section className="staff-panel">
      <CheckCircle2 className="staff-feature-icon" size={34} />
      <h2>Card received</h2>
      <p>
        {patientSpecific
          ? `Your file is saved on the clinic record at ${clinic?.name}.`
          : `${clinic?.name} will review the card and patient details.`}{' '}
        This does not confirm coverage or an appointment.
      </p>
      {receipt && (
        <p className="staff-small staff-muted">
          Received {new Date(receipt.received_at).toLocaleString()} · Receipt {receipt.receipt_id}
        </p>
      )}
      <p className="staff-small staff-muted">
        This private link has been used. Contact your clinic if you need to send a replacement.
      </p>
    </section>
  )
}
