import { Mail, Plus } from 'lucide-react'
import type { RefObject } from 'react'

export default function ClinicalPatientHeader({
  onRequestCard,
  onPullEcw,
  onAddCard,
  heading,
}: {
  onRequestCard: () => void
  onPullEcw: () => void
  onAddCard: () => void
  heading: RefObject<HTMLHeadingElement | null>
}) {
  return (
    <header className="staff-header">
      <div>
        <div className="staff-eyebrow">Clinical access · Provider workspace</div>
        <h1 ref={heading} tabIndex={-1}>
          Clinical patients
        </h1>
        <p className="staff-muted">
          Patients shared with your clinic, and clinic records created from insurance submissions.
        </p>
      </div>
      <div className="staff-inbox-actions">
        <button onClick={onRequestCard}>
          <Mail size={18} /> Request insurance by email
        </button>
        <button onClick={onPullEcw}>Pull patient from eCW</button>
        <button className="staff-primary" onClick={onAddCard}>
          <Plus size={18} /> Scan or upload card
        </button>
      </div>
    </header>
  )
}
