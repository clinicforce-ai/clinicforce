import { Users } from 'lucide-react'

export default function ClinicalPatientEmpty({ filtered }: { filtered: boolean }) {
  return (
    <div className="staff-empty">
      <Users size={30} aria-hidden="true" />
      <h3>{filtered ? 'No matching patients' : 'No clinic patients yet'}</h3>
      <p>
        {filtered
          ? 'Try a different name or card status.'
          : 'Scan a card, request a patient upload, or open a patient’s ClinicForce QR to get started.'}
      </p>
    </div>
  )
}
