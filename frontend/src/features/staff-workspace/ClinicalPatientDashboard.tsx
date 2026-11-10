import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Mail, Plus, RefreshCw, Users } from 'lucide-react'
import { deleteStaffIntake } from '../../services/api/staff-intake'
import {
  deleteStaffPatient,
  getClinicalPatients,
  getPatientAppointments,
  getTodayAppointments,
  retryStaffDeletionCleanup,
  type ClinicalDashboard,
  type ClinicalPatient,
  type TodayAppointments,
} from '../../services/api/staff-patients'

import ClinicalPatientDetail from './ClinicalPatientDetail'
import ClinicalPatientSections from './ClinicalPatientSections'
import { ecwVisitDays, localVisitDay } from './ecwVisitDates'
import { name, accountLabel } from './clinical-patient-labels'

interface Props {
  initialPatientId?: string
  onOpenIntake: (id: string) => void
  onRecheckEstimate: (intakeId: string, appointmentId: string) => void
  onRequestCard: (patient?: ClinicalPatient) => void
  onAddCard: () => void
  onPullEcw: () => void
}
type VisitResult = { day: string; time: number | null }
const CACHE_AGE_MS = 15 * 60_000
const DATE_CHECK_BATCH_SIZE = 200
const PATIENTS_PER_GROUP = 50
export default function ClinicalPatientDashboard({
  initialPatientId,
  onOpenIntake,
  onRecheckEstimate,
  onRequestCard,
  onAddCard,
  onPullEcw,
}: Props) {
  const [dashboard, setDashboard] = useState<ClinicalDashboard | null>(null)
  const [query, setQuery] = useState('')
  const [cardStatus, setCardStatus] = useState('')
  const [selected, setSelected] = useState<ClinicalPatient | null>(null)
  const [selectedDay, setSelectedDay] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [cleanupPending, setCleanupPending] = useState(false)
  const [notice, setNotice] = useState('')
  const [visitResults, setVisitResults] = useState<Record<string, VisitResult[]>>({})
  const [visitProgress, setVisitProgress] = useState({ done: 0, total: 0, failed: 0 })
  const [todaySnapshot, setTodaySnapshot] = useState<TodayAppointments | null>(null)
  const [todayReadFinished, setTodayReadFinished] = useState(false)
  const [todayReadFailed, setTodayReadFailed] = useState(false)
  const [remainingDateChecks, setRemainingDateChecks] = useState(0)
  const [dateCheckBatch, setDateCheckBatch] = useState(0)
  const [visibleRows, setVisibleRows] = useState<Record<string, number>>({})
  const visitResultsRef = useRef<Record<string, VisitResult[]>>({})
  const checkedPatientIds = useRef(new Set<string>())
  const visitRun = useRef(0)
  const forceVisitRefresh = useRef(false)
  const [period, setPeriod] = useState<'upcoming' | 'past' | null>(null)
  const [datePage, setDatePage] = useState(1)
  const [expandedDay, setExpandedDay] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    heading.current?.focus()
  }, [selected])
  useEffect(() => {
    let active = true
    getClinicalPatients()
      .then((result) => {
        if (active) {
          checkedPatientIds.current.clear()
          setDashboard(result)
          if (initialPatientId) {
            setSelectedDay(null)
            setSelected(
              result.patients.find((patient) => patient.clinic_patient_id === initialPatientId) ||
                null,
            )
          }
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
    let active = true
    getTodayAppointments()
      .then((result) => {
        if (active) setTodaySnapshot(result)
      })
      .catch(() => {
        if (active) setTodayReadFailed(true)
      })
      .finally(() => {
        if (active) setTodayReadFinished(true)
      })
    return () => {
      active = false
    }
  }, [dashboard])
  useEffect(() => {
    if (!dashboard) return
    const results = { ...visitResultsRef.current }
    const now = Date.now()
    const todayVisits = new Map<string, VisitResult>()
    if (todaySnapshot) {
      for (const visit of todaySnapshot.visits) {
        const result = ecwVisitDays({
          ecw_status: 'complete',
          appointments: [{ ...visit, from_ecw: true }],
        })[0]
        if (!result) continue
        const prior = todayVisits.get(visit.clinic_patient_id)
        if (!prior || (result.time !== null && (prior.time === null || result.time < prior.time)))
          todayVisits.set(visit.clinic_patient_id, result)
      }
    }
    const withToday = (patient: ClinicalPatient, visits: VisitResult[]) => {
      if (!todaySnapshot) return visits
      const otherDays = visits.filter((visit) => visit.day !== todaySnapshot.date)
      const todayVisit = todayVisits.get(patient.clinic_patient_id || '')
      return todayVisit ? [...otherDays, todayVisit] : otherDays
    }
    const linked = dashboard.patients.filter((patient) => {
      if (!patient.ecw_linked || !patient.clinic_patient_id) {
        delete results[patient.id]
        return false
      }
      if (
        patient.ecw_visit_cache &&
        patient.ecw_visit_cached_at &&
        !checkedPatientIds.current.has(patient.id)
      ) {
        results[patient.id] = ecwVisitDays({
          ecw_status: 'complete',
          appointments: patient.ecw_visit_cache.map((visit) => ({ ...visit, from_ecw: true })),
        })
      }
      if (results[patient.id] !== undefined)
        results[patient.id] = withToday(patient, results[patient.id])
      else if (todayVisits.has(patient.clinic_patient_id))
        results[patient.id] = [todayVisits.get(patient.clinic_patient_id)!]
      return (
        !checkedPatientIds.current.has(patient.id) &&
        (forceVisitRefresh.current ||
          !patient.ecw_visit_cached_at ||
          !Number.isFinite(Date.parse(patient.ecw_visit_cached_at)) ||
          now - Date.parse(patient.ecw_visit_cached_at) >= CACHE_AGE_MS)
      )
    })
    visitResultsRef.current = results
    setVisitResults(results)
    if (!todayReadFinished) return
    forceVisitRefresh.current = false
    linked.sort((first, second) => {
      const priority = (patient: ClinicalPatient) =>
        todayVisits.has(patient.clinic_patient_id || '') ? 0 : patient.ecw_visit_cache ? 2 : 1
      return priority(first) - priority(second)
    })
    const batch = linked.slice(0, DATE_CHECK_BATCH_SIZE)
    setRemainingDateChecks(linked.length - batch.length)
    setVisitProgress({ done: 0, total: batch.length, failed: 0 })
    const run = ++visitRun.current
    let next = 0
    let done = 0
    let failed = 0
    async function loadNext() {
      while (run === visitRun.current && next < batch.length) {
        const patient = batch[next++]
        try {
          const history = await getPatientAppointments(patient.clinic_patient_id!, true)
          if (run !== visitRun.current) return
          if (history.ecw_status !== 'complete') throw new Error('eCW history incomplete')
          results[patient.id] = withToday(patient, ecwVisitDays(history))
        } catch {
          failed += 1
        }
        if (run !== visitRun.current) return
        checkedPatientIds.current.add(patient.id)
        done += 1
        if (
          done % 10 === 0 ||
          done === batch.length ||
          results[patient.id]?.some((visit) => visit.day === localVisitDay(new Date()))
        ) {
          visitResultsRef.current = { ...results }
          setVisitResults({ ...results })
          setVisitProgress({ done, total: batch.length, failed })
        }
      }
    }
    void Promise.all(Array.from({ length: Math.min(3, batch.length) }, () => loadNext()))
    return () => {
      if (visitRun.current === run) visitRun.current += 1
    }
  }, [dashboard, dateCheckBatch, todaySnapshot, todayReadFinished])
  async function refresh(reloadVisits = true) {
    setBusy(true)
    setError('')
    try {
      const updated = await getClinicalPatients()
      if (reloadVisits) {
        visitRun.current += 1
        forceVisitRefresh.current = true
        checkedPatientIds.current.clear()
        visitResultsRef.current = {}
      }
      setTodaySnapshot(null)
      setTodayReadFinished(false)
      setTodayReadFailed(false)
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
        setSelectedDay(null)
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
  const patients = useMemo(
    () =>
      dashboard?.patients.filter(
        (patient) =>
          (!cardStatus || patient.card_status === cardStatus) &&
          `${name(patient)} ${patient.email || ''} ${patient.birth_date} ${patient.ecw_account_number || ''}`
            .toLowerCase()
            .includes(query.trim().toLowerCase()),
      ) || [],
    [dashboard, cardStatus, query],
  )
  const groups = useMemo(() => {
    const grouped = new Map<string, ClinicalPatient[]>()
    const groupTimes = new Map<string, number | null>()
    for (const patient of patients) {
      const visits =
        visitResults[patient.id] === undefined && patient.ecw_linked
          ? [{ day: 'pending', time: null }]
          : visitResults[patient.id]?.length
            ? visitResults[patient.id]
            : [{ day: 'undated', time: null }]
      for (const visit of visits) {
        const group = grouped.get(visit.day)
        if (group) group.push(patient)
        else grouped.set(visit.day, [patient])
        groupTimes.set(`${patient.id}:${visit.day}`, visit.time)
      }
    }
    for (const [day, group] of grouped)
      group.sort(
        (first, second) =>
          (groupTimes.get(`${first.id}:${day}`) ?? Infinity) -
            (groupTimes.get(`${second.id}:${day}`) ?? Infinity) ||
          name(first).localeCompare(name(second)),
      )
    return grouped
  }, [patients, visitResults])
  const today = todaySnapshot?.date || localVisitDay(new Date())
  const upcomingGroups = [...groups]
    .filter(([day]) => day > today && day !== 'undated' && day !== 'pending')
    .sort(([first], [second]) => first.localeCompare(second))
  const todayGroup = groups.get(today) || []
  const pastGroups = [...groups]
    .filter(([day]) => day < today)
    .sort(([first], [second]) => second.localeCompare(first))
  if (groups.has('undated')) pastGroups.push(['undated', groups.get('undated')!])
  const pendingGroup = groups.get('pending') || []
  const formatDay = (day: string) =>
    new Date(`${day}T12:00:00`).toLocaleDateString(undefined, {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
      year: 'numeric',
    })
  const renderGroup = (day: string, group: ClinicalPatient[]) => (
    <details className="clinical-visit-group" key={day} open={day === today || day === expandedDay}>
      <summary
        onClick={(event) => {
          event.preventDefault()
          setExpandedDay((current) => (current === day ? null : day))
        }}
      >
        <span className="clinical-visit-date">
          {day === 'pending'
            ? 'Appointment date still being checked'
            : day === 'undated'
              ? 'Appointment date not available'
              : formatDay(day)}
        </span>
        <span className="clinical-visit-count">
          {group.length} {group.length === 1 ? 'patient' : 'patients'}
        </span>
      </summary>
      <div>
        {group.slice(0, visibleRows[day] || PATIENTS_PER_GROUP).map((patient) => (
          <article className="clinical-patient-row" key={patient.id}>
            <button
              className="clinical-patient-open"
              onClick={() => {
                setSelected(patient)
                setSelectedDay(day)
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
        {group.length > (visibleRows[day] || PATIENTS_PER_GROUP) && (
          <button
            onClick={() =>
              setVisibleRows((current) => ({
                ...current,
                [day]: (current[day] || PATIENTS_PER_GROUP) + PATIENTS_PER_GROUP,
              }))
            }
          >
            Show more patients
          </button>
        )}
      </div>
    </details>
  )
  if (selected)
    return (
      <ClinicalPatientDetail
        patient={selected}
        returnLabel={
          selectedDay && selectedDay !== 'undated'
            ? `Back to ${formatDay(selectedDay)}`
            : 'Clinical patients'
        }
        heading={heading}
        deleting={deleting}
        cleanupPending={cleanupPending}
        error={error}
        onBack={() => {
          setSelected(null)
          setError('')
          setCleanupPending(false)
        }}
        onDelete={() => void deletePatient()}
        onOpenIntake={onOpenIntake}
        onRecheckEstimate={onRecheckEstimate}
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
              setDatePage(1)
            }}
          />
        </label>
        <label className="staff-field">
          <span>Insurance card status</span>
          <select
            value={cardStatus}
            onChange={(event) => {
              setCardStatus(event.target.value)
              setDatePage(1)
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
              Each appointment date links to the same patient profile. Today’s schedule is checked
              first; older dates update as patient histories finish.
            </p>
            {!todayReadFinished && (
              <p className="staff-small staff-muted" role="status">
                Checking today’s appointments first…
              </p>
            )}
            {todayReadFailed && (
              <p className="staff-small staff-muted" role="alert">
                Today’s schedule could not be checked together. Patient dates will appear as
                individual histories load.
              </p>
            )}
            {visitProgress.total > 0 && (
              <p className="staff-small staff-muted" role="status">
                {visitProgress.done < visitProgress.total
                  ? `Checking eCW visits: ${visitProgress.done} of ${visitProgress.total} patient histories loaded… Dates may change when the refresh finishes.`
                  : `eCW visit checks finished for ${visitProgress.total} patients.`}
              </p>
            )}
            {visitProgress.failed > 0 && visitProgress.done === visitProgress.total && (
              <p className="staff-small staff-muted" role="alert">
                {visitProgress.failed} patient{' '}
                {visitProgress.failed === 1 ? 'history was' : 'histories were'} unavailable. Some
                dates may be missing; select Refresh patients to retry.
              </p>
            )}
            {remainingDateChecks > 0 && visitProgress.done === visitProgress.total && (
              <button onClick={() => setDateCheckBatch((current) => current + 1)}>
                Check appointment dates for {Math.min(remainingDateChecks, DATE_CHECK_BATCH_SIZE)}{' '}
                more patients
              </button>
            )}
            <ClinicalPatientSections
              selected={period}
              onSelect={setPeriod}
              page={datePage}
              onPageChange={setDatePage}
              groups={{
                today: todayGroup.length ? [[today, todayGroup]] : [],
                upcoming: upcomingGroups,
                past: pastGroups,
              }}
              pending={pendingGroup.length ? ['pending', pendingGroup] : null}
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
