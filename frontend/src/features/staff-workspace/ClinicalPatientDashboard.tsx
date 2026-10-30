import { useEffect, useRef, useState } from 'react'
import { ChevronRight, Mail, Plus, RefreshCw, Users } from 'lucide-react'
import { deleteStaffIntake } from '../../services/api/staff-intake'
import {
  deleteStaffPatient,
  getClinicalPatients,
  getPatientAppointments,
  retryStaffDeletionCleanup,
  type ClinicalDashboard,
  type ClinicalPatient,
} from '../../services/api/staff-patients'

import ClinicalPatientDetail from './ClinicalPatientDetail'
import ClinicalPatientSections from './ClinicalPatientSections'
import { ecwVisit, localVisitDay } from './ecwVisitDates'
import { name, accountLabel } from './clinical-patient-labels'

interface Props {
  initialPatientId?: string
  onOpenIntake: (id: string) => void
  onRequestCard: (patient?: ClinicalPatient) => void
  onAddCard: () => void
  onPullEcw: () => void
}
type VisitResult = { day: string | null; time: number | null }
export default function ClinicalPatientDashboard({
  initialPatientId,
  onOpenIntake,
  onRequestCard,
  onAddCard,
  onPullEcw,
}: Props) {
  const [dashboard, setDashboard] = useState<ClinicalDashboard | null>(null)
  const [query, setQuery] = useState('')
  const [cardStatus, setCardStatus] = useState('')
  const [selected, setSelected] = useState<ClinicalPatient | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [cleanupPending, setCleanupPending] = useState(false)
  const [notice, setNotice] = useState('')
  const [visitResults, setVisitResults] = useState<Record<string, VisitResult>>({})
  const [visitProgress, setVisitProgress] = useState({ done: 0, total: 0 })
  const visitResultsRef = useRef<Record<string, VisitResult>>({})
  const visitRun = useRef(0)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    heading.current?.focus()
  }, [selected])
  useEffect(() => {
    let active = true
    getClinicalPatients()
      .then((result) => {
        if (active) {
          setDashboard(result)
          if (initialPatientId)
            setSelected(
              result.patients.find((patient) => patient.clinic_patient_id === initialPatientId) ||
                null,
            )
        }
      })
      .catch((caught) => {
        if (active)
          setError(caught instanceof Error ? caught.message : 'Patients could not be loaded.')
      })
      .finally(() => {
        if (active) setBusy(false)
      })
    return () => {
      active = false
    }
  }, [initialPatientId])
  useEffect(() => {
    if (!dashboard) return
    const linked = dashboard.patients.filter(
      (patient) =>
        patient.ecw_linked && patient.clinic_patient_id && !visitResultsRef.current[patient.id],
    )
    setVisitProgress({ done: 0, total: linked.length })
    const run = ++visitRun.current
    let next = 0
    let done = 0
    async function loadNext() {
      while (run === visitRun.current && next < linked.length) {
        const patient = linked[next++]
        let result: VisitResult = { day: null, time: null }
        try {
          const history = await getPatientAppointments(patient.clinic_patient_id!, true)
          result = ecwVisit(history, localVisitDay(new Date()))
        } catch {
          // Keep the patient visible when the appointment read fails.
        }
        if (run !== visitRun.current) return
        visitResultsRef.current[patient.id] = result
        setVisitResults((current) => ({ ...current, [patient.id]: result }))
        done += 1
        setVisitProgress({ done, total: linked.length })
      }
    }
    void Promise.all(Array.from({ length: Math.min(3, linked.length) }, () => loadNext()))
    return () => {
      if (visitRun.current === run) visitRun.current += 1
    }
  }, [dashboard])
  async function refresh(reloadVisits = true) {
    setBusy(true)
    setError('')
    try {
      const updated = await getClinicalPatients()
      if (reloadVisits) {
        visitRun.current += 1
        visitResultsRef.current = {}
        setVisitResults({})
      }
      setDashboard(updated)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Patients could not be loaded.')
    } finally {
      setBusy(false)
    }
  }
  async function deletePatient() {
    if (!selected || (!selected.clinic_patient_id && !selected.latest_intake_id) || deleting) return
    if (
      !cleanupPending &&
      !window.confirm(
        `Delete ${name(selected)} from this clinic’s ClinicForce workspace? Their clinic intakes, insurance checks, and associated files will be removed. eCW records and their own patient account stay unchanged. This cannot be undone.`,
      )
    )
      return
    setDeleting(true)
    setError('')
    try {
      const result = await (selected.clinic_patient_id
        ? deleteStaffPatient(selected.clinic_patient_id)
        : deleteStaffIntake(selected.latest_intake_id!))
      if (result.cleanup_pending) {
        setCleanupPending(true)
        setError(
          'The clinic record was removed. Some stored files still need cleanup. Select Retry file cleanup to finish.',
        )
      } else {
        setSelected(null)
        setCleanupPending(false)
        setNotice('Patient and linked insurance checks deleted from this clinic.')
        await refresh()
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The patient could not be deleted.')
    } finally {
      setDeleting(false)
    }
  }
  async function retryCleanup() {
    setDeleting(true)
    setError('')
    setNotice('')
    try {
      const result = await retryStaffDeletionCleanup()
      await refresh()
      if (result.pending_file_cleanup)
        setError('Some files could not be removed. Retry file cleanup when storage is available.')
      else setNotice('Deleted records’ file cleanup is complete.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'File cleanup could not be completed.')
    } finally {
      setDeleting(false)
    }
  }
  const patients =
    dashboard?.patients.filter(
      (patient) =>
        (!cardStatus || patient.card_status === cardStatus) &&
        `${name(patient)} ${patient.email || ''} ${patient.birth_date} ${patient.ecw_account_number || ''}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
    ) || []
  const groups = new Map<string, ClinicalPatient[]>()
  for (const patient of patients) {
    const day = visitResults[patient.id]?.day || 'undated'
    const group = groups.get(day)
    if (group) group.push(patient)
    else groups.set(day, [patient])
  }
  for (const group of groups.values())
    group.sort(
      (first, second) =>
        (visitResults[first.id]?.time ?? Infinity) - (visitResults[second.id]?.time ?? Infinity) ||
        name(first).localeCompare(name(second)),
    )
  const today = localVisitDay(new Date())
  const upcomingGroups = [...groups]
    .filter(([day]) => day > today && day !== 'undated')
    .sort(([first], [second]) => first.localeCompare(second))
  const todayGroup = groups.get(today) || []
  const pastGroups = [...groups]
    .filter(([day]) => day < today)
    .sort(([first], [second]) => second.localeCompare(first))
  if (groups.has('undated')) pastGroups.push(['undated', groups.get('undated')!])
  const formatDay = (day: string) =>
    new Date(`${day}T12:00:00`).toLocaleDateString(undefined, {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
      year: 'numeric',
    })
  const renderGroup = (day: string, group: ClinicalPatient[]) => (
    <details className="clinical-visit-group" key={day} open={day === today}>
      <summary>
        <span className="clinical-visit-date">
          {day === 'undated' ? 'Appointment date not available' : formatDay(day)}
        </span>
        <span className="clinical-visit-count">
          {group.length} {group.length === 1 ? 'patient' : 'patients'}
        </span>
      </summary>
      <div>
        {group.map((patient) => (
          <article className="clinical-patient-row" key={patient.id}>
            <button
              className="clinical-patient-open"
              onClick={() => {
                setSelected(patient)
                setError('')
                setNotice('')
                setCleanupPending(false)
              }}
            >
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
      </div>
    </details>
  )
  if (selected)
    return (
      <ClinicalPatientDetail
        patient={selected}
        heading={heading}
        deleting={deleting}
        cleanupPending={cleanupPending}
        error={error}
        onBack={() => {
          setSelected(null)
          setError('')
          setCleanupPending(false)
          void refresh(false)
        }}
        onDelete={() => void deletePatient()}
        onOpenIntake={onOpenIntake}
        onRequestCard={onRequestCard}
      />
    )
  return (
    <>
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
          <button onClick={() => onRequestCard()}>
            <Mail size={18} /> Request insurance by email
          </button>
          <button onClick={onPullEcw}>Pull patient from eCW</button>
          <button className="staff-primary" onClick={onAddCard}>
            <Plus size={18} /> Scan or upload card
          </button>
        </div>
      </header>
      {error && (
        <p role="alert" className="staff-alert staff-error">
          {error}
        </p>
      )}
      {notice && (
        <p className="staff-alert staff-success" role="status">
          {notice}
        </p>
      )}
      {!!dashboard?.pending_file_cleanup && (
        <p className="staff-alert" role="status">
          {dashboard.pending_file_cleanup} deleted{' '}
          {dashboard.pending_file_cleanup === 1 ? 'record has' : 'records have'} files awaiting
          cleanup.{' '}
          <button disabled={deleting || busy} onClick={() => void retryCleanup()}>
            {deleting ? 'Cleaning up…' : 'Retry file cleanup'}
          </button>
        </p>
      )}
      <div className="staff-search clinical-search">
        <label className="staff-field">
          <span>Find a clinical patient</span>
          <input
            type="search"
            placeholder="Name, ACCT, email, or date of birth"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
            }}
          />
        </label>
        <label className="staff-field">
          <span>Insurance card status</span>
          <select
            value={cardStatus}
            onChange={(event) => {
              setCardStatus(event.target.value)
            }}
          >
            <option value="">All card statuses</option>
            <option value="received">Card received</option>
            <option value="missing">Card missing</option>
          </select>
        </label>
        <button onClick={() => void refresh()} disabled={busy}>
          <RefreshCw size={17} /> Refresh patients
        </button>
      </div>
      <section className="staff-panel">
        <div className="staff-section-heading">
          <h2>All patients</h2>
          <span className="staff-small staff-muted" role="status">
            {busy
              ? 'Loading patients…'
              : !dashboard
                ? 'Not loaded'
                : `${patients.length} ${patients.length === 1 ? 'patient' : 'patients'}`}
          </span>
        </div>
        {!dashboard && !busy ? (
          <p className="staff-muted">
            Patients could not be loaded. Select Refresh patients to try again.
          </p>
        ) : dashboard && !busy && !patients.length ? (
          <div className="staff-empty">
            <Users size={30} aria-hidden="true" />
            <h3>{query || cardStatus ? 'No matching patients' : 'No clinic patients yet'}</h3>
            <p>
              {query || cardStatus
                ? 'Try a different name or card status.'
                : 'Scan a card, request a patient upload, or open a patient’s ClinicForce QR to get started.'}
            </p>
          </div>
        ) : (
          <div className="clinical-patient-list">
            <p className="staff-small staff-muted clinical-visit-note">
              Each patient appears once: today, on their next appointment date, or on their most
              recent past visit date. Refresh patients to check for newly booked appointments.
            </p>
            {visitProgress.total > 0 && (
              <p className="staff-small staff-muted" role="status">
                {visitProgress.done < visitProgress.total
                  ? `Checking eCW visits: ${visitProgress.done} of ${visitProgress.total} patient histories loaded…`
                  : `eCW visit checks finished for ${visitProgress.total} patients.`}
              </p>
            )}
            <ClinicalPatientSections
              key={JSON.stringify([query, cardStatus])}
              groups={{
                today: todayGroup.length ? [[today, todayGroup]] : [],
                upcoming: upcomingGroups,
                past: pastGroups,
              }}
              renderGroup={renderGroup}
            />
          </div>
        )}
      </section>
      <p className="staff-small staff-muted">
        This dashboard is visible only to clinic staff. Patient accounts appear here after they are
        shared or connected to your clinic.
      </p>
    </>
  )
}
