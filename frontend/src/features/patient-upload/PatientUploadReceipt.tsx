import { CheckCircle2 } from 'lucide-react'
import type { UploadClinic, UploadReceipt } from '../../services/api/insurance-upload'
export function PatientUploadReceipt({
  clinic,
  receipt,
}: {
  clinic: UploadClinic | null
  receipt: UploadReceipt | null
}) {
  return (
    <section className="staff-panel">
      <CheckCircle2 className="staff-feature-icon" size={34} />
      <h2>Ready for clinic review</h2>
      <p>
        {clinic?.name} will confirm the patient details and review the card. This does not confirm
        coverage or an appointment.
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
