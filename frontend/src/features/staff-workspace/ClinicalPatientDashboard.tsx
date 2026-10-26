import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Mail, Plus, RefreshCw, Users } from 'lucide-react'
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
import { compareVisitDays, ecwVisitDates, localVisitDay } from './ecwVisitDates'
import { name, accountLabel } from './clinical-patient-labels'

interface Props {
  initialPatientId?: string
  onOpenIntake: (id: string) => void
  onRequestCard: (patient?: ClinicalPatient) => void
  onAddCard: () => void
  onPullEcw: () => void
}
type Cohort = 'all' | 'accounts_with_cards' | 'accounts_without_cards' | 'card_only' | 'other'
const GROUPS_PER_PAGE = 5
type VisitResult = { status: 'complete' | 'unavailable'; dates: string[] }
const undatedLabels: Record<string, string> = {
  loading: 'Checking eCW visits',
  unavailable: 'eCW visits unavailable',
  none: 'No upcoming or completed eCW visit',
  unlinked: 'No confirmed eCW link',
}
const cohortLabel: Record<Cohort, string> = {
  all: 'All patients',
  accounts_with_cards: 'Accounts with cards',
  accounts_without_cards: 'Accounts missing cards',
  card_only: 'Card-only records',
  other: 'Other clinic patients',
}
export default function ClinicalPatientDashboard({
  initialPatientId,
  onOpenIntake,
  onRequestCard,
  onAddCard,
  onPullEcw,
}: Props) {
  const [dashboard, setDashboard] = useState<ClinicalDashboard | null>(null)
  const [cohort, setCohort] = useState<Cohort>('all')
  const [query, setQuery] = useState('')
  const [cardStatus, setCardStatus] = useState('')
  const [page, setPage] = useState(1)
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
        let result: VisitResult = { status: 'unavailable', dates: [] }
        try {
          const history = await getPatientAppointments(patient.clinic_patient_id!, true)
          if (history.ecw_status === 'complete')
            result = {
              status: 'complete',
              dates: ecwVisitDates(history, localVisitDay(new Date())),
            }
        } catch {
          // Keep the patient visible in the unavailable group when the read fails.
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
  function inCohort(patient: ClinicalPatient) {
    if (cohort === 'all') return true
    if (cohort === 'accounts_with_cards')
      return patient.account_status === 'portal' && patient.card_status === 'received'
    if (cohort === 'accounts_without_cards')
      return patient.account_status === 'portal' && patient.card_status === 'missing'
    if (cohort === 'card_only')
      return patient.account_status !== 'portal' && patient.card_status === 'received'
    return patient.account_status !== 'portal' && patient.card_status === 'missing'
  }
  const patients =
    dashboard?.patients.filter(
      (patient) =>
        inCohort(patient) &&
        (!cardStatus || patient.card_status === cardStatus) &&
        `${name(patient)} ${patient.email || ''} ${patient.birth_date} ${patient.ecw_account_number || ''}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
    ) || []
  const groups = new Map<string, ClinicalPatient[]>()
  for (const patient of patients) {
    const result = visitResults[patient.id]
    const days = !patient.ecw_linked
      ? ['unlinked']
      : !result
        ? ['loading']
        : result.status === 'unavailable'
          ? ['unavailable']
          : result.dates.length
            ? result.dates
            : ['none']
    for (const day of days) {
      const group = groups.get(day)
      if (group) group.push(patient)
      else groups.set(day, [patient])
    }
  }
  const today = localVisitDay(new Date())
  const datedGroups = [...groups]
    .filter(([day]) => !undatedLabels[day])
    .sort(([first], [second]) => compareVisitDays(first, second, today))
  const undatedGroups = [...groups].filter(([day]) => undatedLabels[day])
  const pageCount = Math.max(1, Math.ceil(datedGroups.length / GROUPS_PER_PAGE))
  const currentPage = Math.min(page, pageCount)
  const pageStart = (currentPage - 1) * GROUPS_PER_PAGE
  const visibleGroups = [
    ...datedGroups.slice(pageStart, pageStart + GROUPS_PER_PAGE),
    ...undatedGroups,
  ]
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
      <nav className="clinical-cohorts" aria-label="Patient groups">
        {(Object.keys(cohortLabel) as Cohort[])
          .filter((value) => value !== 'other' || !!dashboard?.counts.other)
          .map((value) => (
            <button
              key={value}
              aria-pressed={cohort === value}
              onClick={() => {
                setCohort(value)
                setPage(1)
              }}
            >
              <strong>{dashboard?.counts[value] ?? '—'}</strong>
              <span>{cohortLabel[value]}</span>
            </button>
          ))}
      </nav>
      <div className="staff-search clinical-search">
        <label className="staff-field">
          <span>Find a clinical patient</span>
          <input
            type="search"
            placeholder="Name, ACCT, email, or date of birth"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setPage(1)
            }}
          />
        </label>
        <label className="staff-field">
          <span>Insurance card status</span>
          <select
            value={cardStatus}
            onChange={(event) => {
              setCardStatus(event.target.value)
              setPage(1)
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
          <h2>{cohortLabel[cohort]}</h2>
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
            <h3>
              {query || cardStatus || cohort !== 'all'
                ? 'No matching patients'
                : 'No clinic patients yet'}
            </h3>
            <p>
              {query || cardStatus || cohort !== 'all'
                ? 'Try a different name or patient group.'
                : 'Scan a card, request a patient upload, or open a patient’s ClinicForce QR to get started.'}
            </p>
          </div>
        ) : (
          <div className="clinical-patient-list">
            <p className="staff-small staff-muted clinical-visit-note">
              Today and upcoming eCW visits appear first, followed by past completed visits.
              Patients with visits on several dates appear in each date group. Records without a
              confirmed eCW link stay in a separate group.
            </p>
            {visitProgress.total > 0 && (
              <p className="staff-small staff-muted" role="status">
                {visitProgress.done < visitProgress.total
                  ? `Checking eCW visits: ${visitProgress.done} of ${visitProgress.total} patient histories loaded…`
                  : `eCW visit checks finished for ${visitProgress.total} patients.`}
              </p>
            )}
            {visibleGroups.map(([day, group]) => (
              <details className="clinical-visit-group" key={day}>
                <summary>
                  <span className="clinical-visit-date">
                    {undatedLabels[day] ||
                      new Date(`${day}T12:00:00`).toLocaleDateString(undefined, {
                        weekday: 'long',
                        month: 'long',
                        day: 'numeric',
                        year: 'numeric',
                      })}
                  </span>
                  {!undatedLabels[day] && day > today && (
                    <span className="clinical-visit-upcoming">Upcoming</span>
                  )}
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
            ))}
          </div>
        )}
        {datedGroups.length > GROUPS_PER_PAGE && (
          <nav className="clinical-pagination" aria-label="Visit date pages">
            <span className="staff-small staff-muted" role="status">
              Showing {pageStart + 1}–{Math.min(pageStart + GROUPS_PER_PAGE, datedGroups.length)} of{' '}
              {datedGroups.length} date groups
            </span>
            <div className="clinical-page-buttons">
              <button
                aria-label="Previous visit date page"
                disabled={currentPage === 1}
                onClick={() => setPage(currentPage - 1)}
              >
                <ChevronLeft size={18} aria-hidden="true" />
              </button>
              {Array.from({ length: pageCount }, (_, index) => index + 1).map((number) => (
                <button
                  key={number}
                  aria-label={`Visit date page ${number}`}
                  aria-current={number === currentPage ? 'page' : undefined}
                  onClick={() => setPage(number)}
                >
                  {number}
                </button>
              ))}
              <button
                aria-label="Next visit date page"
                disabled={currentPage === pageCount}
                onClick={() => setPage(currentPage + 1)}
              >
                <ChevronRight size={18} aria-hidden="true" />
              </button>
            </div>
          </nav>
        )}
      </section>
      <p className="staff-small staff-muted">
        This dashboard is visible only to clinic staff. Patient accounts appear here after they are
        shared or connected to your clinic.
      </p>
    </>
  )
}
