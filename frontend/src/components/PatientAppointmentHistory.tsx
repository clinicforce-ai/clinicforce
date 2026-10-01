import { useEffect, useState } from 'react'
import { getPatientAppointments, type PatientAppointmentHistory as History } from '../services/api/staff'
import SavedInsuranceChecks from './SavedInsuranceChecks'

const PAGE_SIZE = 7
export default function PatientAppointmentHistory({ patientId, patientName }: { patientId: string; patientName: string }) {
  const [history, setHistory] = useState<History | null>(null)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  function refresh() { setLoading(true); setError(''); setAttempt(value => value + 1) }
  useEffect(() => {
    let active = true
    getPatientAppointments(patientId).then(async saved => {
      if (!active) return
      setHistory(saved)
      try {
        const updated = await getPatientAppointments(patientId, true)
        if (active) setHistory(updated)
      } catch { /* Saved appointment results remain readable if eCW cannot refresh. */ }
    }).catch(() => { if (active) setError('Saved appointments could not be loaded. Please retry.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [patientId, attempt])
  const visits = history?.appointments || []
  const pageCount = Math.max(1, Math.ceil(visits.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount)
  return <section className="staff-panel patient-appointment-history" aria-label="Appointment and estimate history">
    <div className="staff-section-heading"><h2>Appointments and saved estimates</h2><button disabled={loading} onClick={refresh}>Refresh history</button></div>
    <p className="staff-muted">Open an appointment to see what Clinicforce estimated for that visit. Saved results keep their original benefits, amounts, and recorded versions.</p>
    {history?.ecw_account_number && <p><strong>eCW ACCT: {history.ecw_account_number}</strong></p>}
    {loading && <p className="staff-small staff-muted" role="status">Loading appointment history…</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && history?.ecw_status === 'incomplete' && <p className="staff-small staff-muted">Showing saved visits. Refresh history to include other eCW appointments.</p>}
    {!loading && history && !visits.length && <p className="staff-muted">No appointments with saved estimates yet.{history.ecw_status === 'not_linked' ? ' Confirm this patient’s eCW link to include their other appointments.' : ''}</p>}
    {visits.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE).map(visit => <article className="patient-appointment" key={visit.id}>
      <header><h3>{visit.date || visit.start?.slice(0, 10) || 'Visit date not retained'}{visit.visit_type && visit.visit_type !== 'not_returned' && ` · ${visit.visit_type === 'new' ? 'New patient' : visit.visit_type === 'established' ? 'Established / follow-up patient' : visit.visit_type}`}</h3><span className="staff-small staff-muted">{visit.status === 'not_returned' ? 'Recorded visit' : visit.status.replaceAll('_', ' ')}</span></header>
      {visit.reason && <p>{visit.reason}</p>}
      {visit.start?.includes('T') && <p className="staff-small staff-muted">Appointment: {new Date(visit.start).toLocaleString()}</p>}
      {visit.checks.length ? <SavedInsuranceChecks intakeId={visit.checks[0].intake_id!} patientName={patientName} checksForVisit={visit.checks} currentAccountNumber={history?.ecw_account_number} onChange={async () => refresh()} /> : <p className="staff-small staff-muted">No saved Clinicforce estimate is linked to this appointment.</p>}
    </article>)}
    {pageCount > 1 && <nav className="clinical-pagination" aria-label="Appointment pages"><button disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>Previous appointments</button><span>Page {currentPage} of {pageCount}</span><button disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)}>Next appointments</button></nav>}
  </section>
}
