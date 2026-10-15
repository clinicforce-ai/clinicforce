import type { RefObject } from 'react'
import { ArrowLeft, ChevronRight, Mail, Trash2 } from 'lucide-react'
import { type ClinicalPatient } from '../../services/api/staff-patients'
import PatientAppointmentHistory from './PatientAppointmentHistory'
import { name, accountLabel, stateLabel } from './clinical-patient-labels'

interface Props {
  patient: ClinicalPatient
  heading: RefObject<HTMLHeadingElement | null>
  deleting: boolean
  cleanupPending: boolean
  error: string
  onBack: () => void
  onDelete: () => void
  onOpenIntake: (id: string) => void
  onRequestCard: (patient: ClinicalPatient) => void
}

export default function ClinicalPatientDetail({
  patient: selected,
  heading,
  deleting,
  cleanupPending,
  error,
  onBack,
  onDelete,
  onOpenIntake,
  onRequestCard,
}: Props) {
  return (
    <div className="staff-narrow">
      <button className="staff-text-button" disabled={deleting} onClick={onBack}>
        <ArrowLeft size={18} /> Clinical patients
      </button>
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Clinical patient record</div>
          <h1 ref={heading} tabIndex={-1}>
            {name(selected)}
          </h1>
          <p className="staff-muted">
            Date of birth: {selected.birth_date || 'Needs confirmation'} ·{' '}
            {accountLabel(selected.account_status)}
            {selected.ecw_account_number && ` · eCW ACCT ${selected.ecw_account_number}`}
          </p>
        </div>
        <div className="staff-actions">
          {selected.latest_intake_id && (
            <button
              disabled={deleting || cleanupPending}
              onClick={() => onOpenIntake(selected.latest_intake_id!)}
            >
              Open insurance review <ChevronRight size={17} />
            </button>
          )}
          {(selected.clinic_patient_id || selected.latest_intake_id) && (
            <button
              className="staff-text-button staff-delete-button"
              disabled={deleting}
              onClick={onDelete}
            >
              <Trash2 size={17} aria-hidden="true" />
              {deleting
                ? 'Deleting…'
                : cleanupPending
                  ? 'Retry file cleanup'
                  : selected.clinic_patient_id
                    ? 'Delete patient'
                    : 'Delete unconfirmed upload'}
            </button>
          )}
          <button
            className="staff-primary"
            disabled={deleting || cleanupPending}
            onClick={() => onRequestCard(selected)}
          >
            <Mail size={18} /> Request insurance card
          </button>
        </div>
      </header>
      {error && (
        <p className="staff-alert staff-error" role="alert">
          {error}
        </p>
      )}
      <>
        {selected.clinic_patient_id && selected.identity_status === 'confirmed' && (
          <PatientAppointmentHistory
            key={selected.clinic_patient_id}
            patientId={selected.clinic_patient_id}
            patientName={name(selected)}
          />
        )}
        <details className="staff-panel">
          <summary>Insurance submissions</summary>
          <p className="staff-muted">
            Original card submissions and insurance review entry points.
          </p>
          {selected.email && (
            <p className="staff-small staff-muted">Contact email: {selected.email}</p>
          )}
          {!selected.intakes.length ? (
            <p className="staff-muted">
              No insurance submissions yet. Request a card to get started.
            </p>
          ) : (
            selected.intakes.map((intake) => (
              <div className="staff-document" key={intake.id}>
                <span>
                  <span>
                    <strong>
                      {intake.source === 'clinicforce_qr'
                        ? 'ClinicForce shared records'
                        : intake.source === 'patient_upload'
                          ? 'Patient-submitted insurance'
                          : intake.source === 'manual_entry'
                            ? 'Manual insurance details'
                            : 'Clinic insurance intake'}
                    </strong>
                    <small>
                      {new Date(intake.created_at).toLocaleString()} · {stateLabel(intake.status)}
                    </small>
                  </span>
                </span>
                <button
                  disabled={deleting || cleanupPending}
                  onClick={() => onOpenIntake(intake.id)}
                >
                  {intake.id === selected.latest_intake_id
                    ? 'Open insurance review'
                    : 'View submission history'}{' '}
                  <ChevronRight size={17} />
                </button>
              </div>
            ))
          )}
        </details>
      </>
      {selected.account_status === 'automatic' && (
        <p className="staff-small staff-muted">
          This clinic record was created from submitted insurance details.{' '}
          {selected.identity_status === 'confirmed'
            ? 'Patient identity has been confirmed by clinic staff.'
            : 'Patient identity must be confirmed by staff before insurance review.'}{' '}
          No patient login was created.
        </p>
      )}
    </div>
  )
}
