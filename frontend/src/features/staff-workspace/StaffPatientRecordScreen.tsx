import type { StaffWorkspaceModel } from './useStaffWorkspaceModel'
import { ArrowLeft, ChevronRight, FileText } from 'lucide-react'
import InsuranceCards from '../insurance-card/InsuranceCards'
import {
  downloadStaffDocument,
  downloadStaffWorkflowDocument,
} from '../../services/api/staff-downloads'
import { patientName, statusLabel, dateLabel } from './staff-workspace-utils'

export default function StaffPatientRecordScreen({
  model,
  session,
  intake,
}: {
  model: StaffWorkspaceModel
  session: NonNullable<StaffWorkspaceModel['session']>
  intake: NonNullable<StaffWorkspaceModel['intake']>
}) {
  const {
    onOpenInsurance,
    setHistoryPatientId,
    setLinkPatient,
    recordBack,
    setLinkBack,
    tab,
    setTab,
    busy,
    heading,
    perform,
    navigate,
    editPatient,
    retryEcwLookup,
    refreshInbox,
  } = model
  return (
    <>
      <button
        className="staff-text-button"
        onClick={() => {
          navigate(recordBack)
          if (recordBack === 'inbox') void perform('refresh', refreshInbox)
        }}
        disabled={Boolean(busy)}
      >
        <ArrowLeft size={18} />{' '}
        {recordBack === 'patients' ? 'Clinical patients' : 'Insurance inbox'}
      </button>
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Patient insurance workspace</div>
          <h1 tabIndex={-1} ref={heading}>
            {patientName(intake)}
          </h1>
          <p className="staff-muted">
            {intake.fields.payer || 'Insurance company needs review'} · Received{' '}
            {dateLabel(intake.created_at)}
          </p>
        </div>
        <span className="staff-status">{statusLabel(intake.status)}</span>
      </header>
      {intake.source === 'patient_upload' && (
        <p className="staff-alert staff-warning">
          Patient-submitted card and details
          {intake.patient
            ? ' · Confirmed by clinic staff.'
            : ' · Awaiting staff identity confirmation.'}
        </p>
      )}
      {!intake.patient && intake.expected_patient && (
        <p className="staff-alert staff-warning">
          Requested for {intake.expected_patient.given_name} {intake.expected_patient.family_name} ·
          Date of birth: {intake.expected_patient.birth_date}. Confirm the uploaded details match.
        </p>
      )}
      {intake.patient && (
        <div className="staff-actions">
          <button
            onClick={() => {
              setLinkBack('record')
              setLinkPatient({ id: intake.patient!.id, name: patientName(intake) })
              navigate('links')
            }}
          >
            Request an updated insurance card
          </button>
        </div>
      )}
      <nav className="staff-tabs" aria-label="Patient insurance sections">
        {(['overview', 'documents', 'activity'] as const).map((value) => (
          <button
            key={value}
            onClick={() => setTab(value)}
            aria-current={tab === value ? 'page' : undefined}
          >
            {value === 'overview' ? 'Overview' : value === 'documents' ? 'Documents' : 'Activity'}
          </button>
        ))}
      </nav>
      {tab === 'overview' && (
        <>
          <div className="staff-two-columns">
            <section className="staff-panel">
              <h2>{intake.patient ? 'Continue insurance review' : 'Confirm this patient first'}</h2>
              <p className="staff-muted">
                {intake.patient
                  ? 'Choose the information available for this visit. Each path stays attached to this patient intake.'
                  : 'Confirm the name and date of birth before linking patient details or opening the insurance workflow.'}
              </p>
              {intake.patient ? (
                <div className="staff-workflow-actions">
                  {intake.source !== 'ecw_lookup' && (
                    <button
                      className="staff-primary"
                      onClick={() => onOpenInsurance(intake.id, 'v1')}
                    >
                      Review uploaded documents <ChevronRight size={18} />
                    </button>
                  )}
                  {intake.source === 'ecw_lookup' && !intake.fields.ecw_patient_id ? (
                    <button className="staff-primary" onClick={() => retryEcwLookup(intake)}>
                      Search this patient in eCW <ChevronRight size={18} />
                    </button>
                  ) : (
                    <button onClick={() => onOpenInsurance(intake.id, 'commercial')}>
                      Check visit estimate <ChevronRight size={18} />
                    </button>
                  )}
                </div>
              ) : (
                <button className="staff-primary" onClick={() => editPatient(intake)}>
                  Confirm patient details
                </button>
              )}
              <p className="staff-small staff-muted">
                Visit estimates use eClinicalWorks appointments, Stedi benefits, and your clinic’s
                contracted rates.
              </p>
            </section>
            <section className="staff-panel">
              <h2>Patient and policy</h2>
              <InsuranceCards key={intake.id} intakeId={intake.id} />
              <dl className="staff-facts">
                <div>
                  <dt>Date of birth</dt>
                  <dd>
                    {intake.patient?.birth_date || intake.fields.birth_date || 'Not confirmed'}
                  </dd>
                </div>
                <div>
                  <dt>Member ID</dt>
                  <dd>{intake.fields.member_id || 'Not confirmed'}</dd>
                </div>
                <div>
                  <dt>Policyholder</dt>
                  <dd>
                    {intake.fields.subscriber_name || intake.fields.member_name || 'Not confirmed'}
                  </dd>
                </div>
                <div>
                  <dt>Relationship</dt>
                  <dd>{intake.fields.relationship || 'Not confirmed'}</dd>
                </div>
              </dl>
              <p className="staff-small staff-muted">
                Saved to {session.practice_name}. A patient account is optional.
              </p>
            </section>
          </div>
          <section className="staff-panel">
            <h2>Insurance information sources</h2>
            <div className="staff-document">
              <span>
                <FileText size={18} />{' '}
                {intake.source === 'clinicforce_qr'
                  ? 'ClinicForce QR intake'
                  : 'Original card files'}
              </span>
              <span className="staff-small">
                {intake.documents.length} card files saved · {dateLabel(intake.created_at)}
              </span>
            </div>
            <p className="staff-small staff-muted">
              Uploaded benefits PDFs, clinic fee schedules, and insurance contracts are reviewed in
              the document workflow. Coverage, authorization, and estimates retain their source
              information.
            </p>
            {intake.warning && <div className="staff-alert staff-warning">{intake.warning}</div>}
          </section>
          <section className="staff-panel">
            <h2>Appointment history</h2>
            <p className="staff-muted">
              Previous appointments and their saved estimates are in this patient’s clinical record.
            </p>
            {intake.patient && (
              <button
                onClick={() => {
                  navigate('patients')
                  setHistoryPatientId(intake.patient!.id)
                }}
              >
                View patient appointments <ChevronRight size={17} />
              </button>
            )}
          </section>
        </>
      )}
      {tab === 'documents' && (
        <>
          <section className="staff-panel">
            <h2>Original insurance card</h2>
            <p className="staff-muted">
              {intake.documents.length
                ? intake.source === 'patient_upload'
                  ? 'Original files submitted by the patient or their representative.'
                  : 'Files captured or uploaded by clinic staff.'
                : 'This intake started from a ClinicForce QR. No separate card file has been uploaded.'}
            </p>
            {intake.documents.map((doc) => (
              <div className="staff-document" key={doc.id}>
                <span>
                  <FileText size={18} />
                  <span>
                    <strong>{doc.filename}</strong>
                    <small>
                      {dateLabel(doc.created_at)} · {Math.max(1, Math.round(doc.size_bytes / 1024))}{' '}
                      KB
                    </small>
                  </span>
                </span>
                <button
                  disabled={Boolean(busy)}
                  onClick={() =>
                    void perform('download', () =>
                      downloadStaffDocument(intake.id, doc.id, doc.filename),
                    )
                  }
                >
                  Download
                </button>
              </div>
            ))}
            {intake.patient && (
              <div className="staff-actions">
                <button onClick={() => onOpenInsurance(intake.id, 'v1')}>
                  Open document review <ChevronRight size={18} />
                </button>
              </div>
            )}
          </section>
          <section className="staff-panel">
            <h2>Supporting insurance documents</h2>
            <p className="staff-muted">
              Benefits, contracts, fees, and supporting files saved during this patient’s insurance
              review.
            </p>
            {intake.workflow_documents?.length ? (
              intake.workflow_documents.map((doc) => (
                <div className="staff-document" key={doc.id}>
                  <span>
                    <FileText size={18} />
                    <span>
                      <strong>{doc.filename}</strong>
                      <small>
                        {doc.kind.replaceAll('_', ' ')} ·{' '}
                        {doc.version === 'v1' ? 'Document review' : 'Connected insurance'}
                      </small>
                      <small>
                        {doc.observed_at
                          ? `Source recorded ${dateLabel(doc.observed_at)}`
                          : 'Source date unavailable'}
                      </small>
                    </span>
                  </span>
                  <button
                    disabled={Boolean(busy)}
                    onClick={() =>
                      void perform('download', () => downloadStaffWorkflowDocument(intake.id, doc))
                    }
                  >
                    Download
                  </button>
                </div>
              ))
            ) : (
              <p className="staff-small staff-muted">
                No supporting documents have been saved in this intake’s insurance workflows yet.
              </p>
            )}
          </section>
        </>
      )}
      {tab === 'activity' && (
        <section className="staff-panel">
          <h2>Insurance activity</h2>
          {intake.events?.length ? (
            <ol className="staff-timeline">
              {intake.events.map((event) => (
                <li key={event.id}>
                  <strong>{event.action.replaceAll('_', ' ')}</strong>
                  <small>
                    {dateLabel(event.created_at)}
                    {event.actor ? ` · ${event.actor}` : ''}
                  </small>
                </li>
              ))}
            </ol>
          ) : (
            <p className="staff-muted">No additional activity has been recorded.</p>
          )}
        </section>
      )}
    </>
  )
}
