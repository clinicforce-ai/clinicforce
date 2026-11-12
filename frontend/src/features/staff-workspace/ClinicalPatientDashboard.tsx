import { useEffect, useMemo, useRef, useState } from 'react'
import { deleteStaffIntake } from '../../services/api/staff-intake'
import {
  deleteStaffPatient,
  getAppointmentDate,
  getClinicalPatients,
  getPatientAppointments,
  getTodayAppointments,
  retryStaffDeletionCleanup,
  type ClinicalDashboard,
  type ClinicalPatient,
  type DateAppointments,
} from '../../services/api/staff-patients'

import ClinicalPatientDateGroup from './ClinicalPatientDateGroup'
import ClinicalPatientHeader from './ClinicalPatientHeader'
import ClinicalPatientEmpty from './ClinicalPatientEmpty'
import ClinicalPatientFilters from './ClinicalPatientFilters'
import ClinicalPatientListStatus from './ClinicalPatientListStatus'
import ClinicalPatientDetail from './ClinicalPatientDetail'
import ClinicalPatientSections from './ClinicalPatientSections'
import { ecwVisitDays, localVisitDay } from './ecwVisitDates'
import { name } from './clinical-patient-labels'
import {
  filterClinicalPatients,
  formatVisitDay,
  groupPatientDates,
  partitionPatientDates,
  type VisitResult,
} from './clinical-patient-groups'

interface Props {
  initialPatientId?: string
  onOpenIntake: (id: string) => void
  onRecheckEstimate: (intakeId: string, appointmentId: string) => void
  onRequestCard: (patient?: ClinicalPatient) => void
  onAddCard: () => void
  onPullEcw: () => void
}
const CACHE_AGE_MS = 15 * 60_000
const DATE_CHECK_BATCH_SIZE = 200
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
  const [todaySnapshot, setTodaySnapshot] = useState<DateAppointments | null>(null)
  const [todayReadFinished, setTodayReadFinished] = useState(false)
  const [todayReadFailed, setTodayReadFailed] = useState(false)
  const [exactDates, setExactDates] = useState<Record<string, DateAppointments>>({})
  const [lookupDay, setLookupDay] = useState('')
  const [selectedLookupDay, setSelectedLookupDay] = useState<string | null>(null)
  const [dateLookupLoading, setDateLookupLoading] = useState(false)
  const [dateLookupError, setDateLookupError] = useState('')
  const [remainingDateChecks, setRemainingDateChecks] = useState(0)
  const [dateCheckBatch, setDateCheckBatch] = useState(0)
  const [visibleRows, setVisibleRows] = useState<Record<string, number>>({})
  const visitResultsRef = useRef<Record<string, VisitResult[]>>({})
  const checkedPatientIds = useRef(new Set<string>())
  const dateLookupRun = useRef(0)
  const visitRun = useRef(0)
  const forceVisitRefresh = useRef(false)
  const [period, setPeriod] = useState<'upcoming' | 'past' | null>(null)
  const [datePage, setDatePage] = useState(1)
  const [expandedDay, setExpandedDay] = useState<string | null>(null)
  const [searchExpanded, setSearchExpanded] = useState(true)
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
      dateLookupRun.current += 1
      setExactDates({})
      setSelectedLookupDay(null)
      setDateLookupLoading(false)
      setDateLookupError('')
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
  async function loadExactDate(day: string) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return
    const run = ++dateLookupRun.current
    setDateLookupLoading(true)
    setDateLookupError('')
    try {
      const result = await getAppointmentDate(day)
      if (run !== dateLookupRun.current) return
      setExactDates((current) => ({ ...current, [result.date]: result }))
      setSelectedLookupDay(result.date)
      setExpandedDay(result.date)
      setDatePage(1)
    } catch {
      if (run === dateLookupRun.current)
        setDateLookupError('The selected eCW appointment date could not be verified. Try again.')
    } finally {
      if (run === dateLookupRun.current) setDateLookupLoading(false)
    }
  }
  const patients = useMemo(
    () => filterClinicalPatients(dashboard?.patients || [], query, cardStatus),
    [dashboard, cardStatus, query],
  )
  const filtered = Boolean(query.trim() || cardStatus)
  const groups = useMemo(
    () => groupPatientDates(patients, visitResults, exactDates),
    [patients, visitResults, exactDates],
  )
  const today = todaySnapshot?.date || localVisitDay(new Date())
  const {
    today: todayGroup,
    upcoming: upcomingGroups,
    past: pastGroups,
    pending: pendingGroup,
  } = partitionPatientDates(groups, today, selectedLookupDay)
  const renderGroup = (day: string, group: ClinicalPatient[]) => (
    <ClinicalPatientDateGroup
      key={day}
      day={day}
      patients={group}
      expanded={day === 'search' ? searchExpanded : day === today || day === expandedDay}
      visibleRows={visibleRows[day] || 0}
      onToggle={() =>
        day === 'search'
          ? setSearchExpanded((current) => !current)
          : setExpandedDay((current) => (current === day ? null : day))
      }
      onShowMore={() =>
        setVisibleRows((current) => ({ ...current, [day]: (current[day] || 50) + 50 }))
      }
      onOpen={(patient) => {
        setSelected(patient)
        setSelectedDay(day === 'search' ? null : day)
        setError('')
        setNotice('')
        setCleanupPending(false)
      }}
      onRequestCard={onRequestCard}
    />
  )
  if (selected)
    return (
      <ClinicalPatientDetail
        patient={selected}
        returnLabel={
          selectedDay && selectedDay !== 'undated'
            ? `Back to ${formatVisitDay(selectedDay)}`
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
      <ClinicalPatientHeader
        heading={heading}
        onRequestCard={onRequestCard}
        onPullEcw={onPullEcw}
        onAddCard={onAddCard}
      />
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
      <ClinicalPatientFilters
        query={query}
        cardStatus={cardStatus}
        busy={busy}
        onQuery={(value) => {
          setQuery(value)
          setSearchExpanded(true)
          setDatePage(1)
        }}
        onCardStatus={(value) => {
          setCardStatus(value)
          setSearchExpanded(true)
          setDatePage(1)
        }}
        onRefresh={() => void refresh()}
      />
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
          <p className="staff-muted">Patients could not be loaded. Select Refresh patients.</p>
        ) : dashboard && !busy && !patients.length ? (
          <ClinicalPatientEmpty filtered={filtered} />
        ) : (
          <div className="clinical-patient-list">
            <ClinicalPatientListStatus
              todayReadFinished={todayReadFinished}
              todayReadFailed={todayReadFailed}
              progress={visitProgress}
              remainingDateChecks={remainingDateChecks}
              onNextBatch={() => setDateCheckBatch((current) => current + 1)}
              period={period}
              lookupDay={lookupDay}
              today={today}
              dateLookupLoading={dateLookupLoading}
              dateLookupError={dateLookupError}
              selectedLookupDay={selectedLookupDay}
              selectedDayIsEmpty={
                !!selectedLookupDay &&
                !!exactDates[selectedLookupDay] &&
                !groups.has(selectedLookupDay)
              }
              onLookupDay={setLookupDay}
              onLoadDate={() => void loadExactDate(lookupDay)}
            />
            {filtered ? (
              renderGroup('search', patients)
            ) : (
              <ClinicalPatientSections
                selected={period}
                onSelect={(next) => {
                  setPeriod(next)
                  if (next) {
                    const adjacent = new Date(`${today}T12:00:00`)
                    adjacent.setDate(adjacent.getDate() + (next === 'past' ? -1 : 1))
                    const day = localVisitDay(adjacent)
                    setLookupDay(day)
                    if (!exactDates[day]) void loadExactDate(day)
                  }
                }}
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
            )}
          </div>
        )}
      </section>
      <p className="staff-small staff-muted">Clinic staff only.</p>
    </>
  )
}
