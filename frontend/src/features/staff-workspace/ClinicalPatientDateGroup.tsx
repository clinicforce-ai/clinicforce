import { ChevronRight, Mail } from 'lucide-react'
import type { ClinicalPatient } from '../../services/api/staff-patients'
import { accountLabel, name } from './clinical-patient-labels'
import { formatVisitDay } from './clinical-patient-groups'

const PATIENTS_PER_GROUP = 50

export default function ClinicalPatientDateGroup({
  day,
  patients,
  expanded,
  visibleRows,
  onToggle,
  onShowMore,
  onOpen,
  onRequestCard,
}: {
  day: string
  patients: ClinicalPatient[]
  expanded: boolean
  visibleRows: number
  onToggle: () => void
  onShowMore: () => void
  onOpen: (patient: ClinicalPatient) => void
  onRequestCard: (patient: ClinicalPatient) => void
}) {
  return (
    <details className="clinical-visit-group" open={expanded}>
      <summary
        onClick={(event) => {
          event.preventDefault()
          onToggle()
        }}
      >
        <span className="clinical-visit-date">
          {day === 'search'
            ? 'Search results'
            : day === 'pending'
              ? 'Appointment date still being checked'
              : day === 'undated'
                ? 'Appointment date not available'
                : formatVisitDay(day)}
        </span>
        <span className="clinical-visit-count">
          {patients.length} {patients.length === 1 ? 'patient' : 'patients'}
        </span>
      </summary>
      <div>
        {patients.slice(0, visibleRows || PATIENTS_PER_GROUP).map((patient) => (
          <article className="clinical-patient-row" key={patient.id}>
            <button className="clinical-patient-open" onClick={() => onOpen(patient)}>
              <span>
                <strong>{name(patient)}</strong>
                <small>Date of birth: {patient.birth_date || 'Needs confirmation'}</small>
                <small>
                  {accountLabel(patient.account_status)}
                  {patient.ecw_account_number && ` · ACCT ${patient.ecw_account_number}`}
                </small>
              </span>
              <span
                className={`staff-status ${patient.card_status === 'missing' ? 'staff-status-attention' : ''}`}
              >
                {patient.card_status === 'received' ? 'Card received' : 'Card missing'}
              </span>
              <ChevronRight size={19} aria-hidden="true" />
            </button>
            <button onClick={() => onRequestCard(patient)}>
              <Mail size={17} /> Request insurance card
            </button>
          </article>
        ))}
        {patients.length > (visibleRows || PATIENTS_PER_GROUP) && (
          <button onClick={onShowMore}>Show more patients</button>
        )}
      </div>
    </details>
  )
}
