import type { StaffWorkspaceModel } from './useStaffWorkspaceModel'
import { ChevronRight, Inbox, Plus, RefreshCw } from 'lucide-react'
import { patientName, statusLabel, dateLabel } from './staff-workspace-utils'

export default function StaffInboxScreen({
  model,
  session,
}: {
  model: StaffWorkspaceModel
  session: NonNullable<StaffWorkspaceModel['session']>
}) {
  const {
    setLinkPatient,
    setLinkBack,
    intakes,
    query,
    setQuery,
    status,
    setStatus,
    setFiles,
    busy,
    listLoading,
    inboxLoaded,
    nextOffset,
    heading,
    perform,
    navigate,
    refreshInbox,
    openIntake,
  } = model
  return (
    <>
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Staff workspace</div>
          <h1 tabIndex={-1} ref={heading}>
            Insurance inbox
          </h1>
          <p className="staff-muted">Card scans and PDFs submitted to {session.practice_name}.</p>
        </div>
        <div className="staff-inbox-actions">
          <button disabled={Boolean(busy)} onClick={() => navigate('prebooking')}>
            Check before booking
          </button>
          <button
            disabled={Boolean(busy)}
            onClick={() => {
              setLinkBack('inbox')
              setLinkPatient(null)
              navigate('links')
            }}
          >
            Request patient upload
          </button>
          <button
            className="staff-primary"
            disabled={Boolean(busy)}
            onClick={() => {
              setFiles([])
              navigate('add')
            }}
          >
            <Plus size={18} /> Add patient insurance
          </button>
        </div>
      </header>
      <form
        className="staff-search"
        onSubmit={(event) => {
          event.preventDefault()
          void perform('search', refreshInbox)
        }}
      >
        <label className="staff-field">
          <span>Find a patient</span>
          <input
            type="search"
            placeholder="Name or member ID"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className="staff-field">
          <span>Status</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All submissions</option>
            <option value="needs_confirmation">Confirm patient</option>
            <option value="ready">Ready to review</option>
            <option value="in_progress">Review in progress</option>
            <option value="reviewed">Staff review complete</option>
          </select>
        </label>
        <button disabled={Boolean(busy)}>Search</button>
      </form>
      <section className="staff-panel">
        <div className="staff-section-heading">
          <h2>Received insurance</h2>
          <button
            className="staff-text-button"
            aria-label="Refresh insurance inbox"
            disabled={Boolean(busy) || listLoading}
            onClick={() => void perform('refresh', refreshInbox)}
          >
            <RefreshCw size={17} /> Refresh
          </button>
        </div>
        {listLoading ? (
          <p role="status">Loading submissions…</p>
        ) : !inboxLoaded ? (
          <p className="staff-muted">
            Submissions could not be loaded. Select Refresh to try again.
          </p>
        ) : !intakes.length ? (
          <div className="staff-empty">
            <Inbox size={30} aria-hidden="true" />
            <h3>{query || status ? 'No matching submissions' : 'Your inbox is ready'}</h3>
            <p>
              {query || status
                ? 'Try another name or choose all submissions.'
                : 'Scan an insurance card or upload a card PDF to get started.'}
            </p>
          </div>
        ) : (
          <div className="staff-intake-list">
            {intakes.map((record) => (
              <button
                className="staff-intake-row"
                key={record.id}
                disabled={Boolean(busy)}
                onClick={() => openIntake(record.id)}
              >
                <span>
                  <strong>{patientName(record)}</strong>
                  <small>
                    {record.patient?.birth_date ||
                      record.fields.birth_date ||
                      'Date of birth needed'}
                  </small>
                </span>
                <span>
                  {record.fields.payer || 'Insurance company needs review'}
                  <small>
                    {record.source === 'clinicforce_qr'
                      ? 'ClinicForce QR'
                      : record.source === 'patient_upload'
                        ? record.patient
                          ? 'Patient upload'
                          : 'Patient upload · Pending staff confirmation'
                        : `${record.documents.length} card ${record.documents.length === 1 ? 'file' : 'files'}`}{' '}
                    · {dateLabel(record.created_at)}
                  </small>
                </span>
                <span
                  className={`staff-status ${record.status === 'needs_confirmation' ? 'staff-status-attention' : ''}`}
                >
                  {statusLabel(record.status)}
                </span>
                <ChevronRight size={19} aria-hidden="true" />
              </button>
            ))}
          </div>
        )}
        {inboxLoaded && intakes.length > 0 && (
          <div className="staff-pagination">
            <span className="staff-small staff-muted" role="status">
              Showing {intakes.length} {intakes.length === 1 ? 'submission' : 'submissions'}
            </span>
            {nextOffset !== null && (
              <button
                disabled={Boolean(busy)}
                onClick={() => void perform('load-more', () => refreshInbox(true))}
              >
                Load more submissions
              </button>
            )}
          </div>
        )}
      </section>
    </>
  )
}
