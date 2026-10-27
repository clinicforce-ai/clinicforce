import { ApiError } from '../../services/api/client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { deleteStaffInsuranceCheck } from '../../services/api/staff-history'
import { getStaffIntake } from '../../services/api/staff-intake'
import { getStaffSession } from '../../services/api/staff-session'
import { type StaffPatient, type StaffSession } from '../../services/api/staff-contracts'
import {
  commercialAppointments,
  commercialHistory,
  commercialPrevisit,
  commercialSavedPrevisit,
  commercialSelectCoverageCandidate,
  commercialSelectPayerRoute,
  commercialVisitReview,
  type VisitReview,
  type CommercialAppointment,
  type CommercialPastVisit,
  type PrevisitEstimate,
} from '../../services/api/commercial-estimator'
import { visitDate } from './format'
import type { CommercialEstimatorProps } from './commercial-props'

type DetailPanel = 'coverage' | 'notes' | 'calculation' | 'sources' | 'services'
export function useCommercialEstimateActions({
  onBack,
  onResolvePatient,
  intakeId,
  refreshBenefits = false,
  saved,
  currentAccountNumber,
}: CommercialEstimatorProps) {
  const readOnly = !!saved
  const [session, setSession] = useState<StaffSession | null>(null)
  const [patient, setPatient] = useState<StaffPatient | null>(saved?.patient || null)
  const [appointments, setAppointments] = useState<CommercialAppointment[]>([])
  const [history, setHistory] = useState<CommercialPastVisit[] | null>(null)
  const [result, setResult] = useState<PrevisitEstimate | null>(saved?.result || null)
  const [error, setError] = useState('')
  const [patientResolutionNeeded, setPatientResolutionNeeded] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [historyBusy, setHistoryBusy] = useState(false)
  const [selectedVisit, setSelectedVisit] = useState('')
  const [candidateSuggestion, setCandidateSuggestion] = useState('')
  const [payerSuggestion, setPayerSuggestion] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState<'visits' | 'estimate' | 'delete' | 'review' | 'coverage' | null>(
    readOnly ? null : 'visits',
  )
  const [deleteError, setDeleteError] = useState('')
  const [cleanupPending, setCleanupPending] = useState(false)
  const [notice, setNotice] = useState('')
  const [panel, setPanel] = useState<DetailPanel | null>(null)
  const requestId = useRef(0)
  const automaticEstimate = useRef('')

  const load = useCallback(async () => {
    const current = ++requestId.current
    automaticEstimate.current = ''
    setBusy('visits')
    setError('')
    setPatientResolutionNeeded(false)
    setDeleteError('')
    setCleanupPending(false)
    setNotice('')
    setResult(null)
    setPatient(null)
    const [staff, schedule, record] = await Promise.allSettled([
      getStaffSession(),
      commercialAppointments(intakeId),
      intakeId ? getStaffIntake(intakeId) : Promise.resolve(null),
    ])
    if (current !== requestId.current) return
    if (staff.status === 'fulfilled') setSession(staff.value.staff)
    if (record.status === 'fulfilled') setPatient(record.value?.patient || null)
    else {
      setError('Patient details could not be loaded. Return to the patient record and try again.')
      setAppointments([])
      setSelectedVisit('')
      setLoaded(false)
      setBusy(null)
      return
    }
    if (schedule.status === 'fulfilled') {
      setAppointments(schedule.value.appointments)
      setSelectedVisit((previous) =>
        schedule.value.appointments.some((visit) => visit.id === previous)
          ? previous
          : schedule.value.appointments[0]?.id || '',
      )
      setLoaded(true)
    } else {
      setPatientResolutionNeeded(
        schedule.reason instanceof ApiError &&
          schedule.reason.code === 'ecw_patient_selection_required',
      )
      setError(
        schedule.reason instanceof Error
          ? schedule.reason.message
          : 'Scheduled visits could not be loaded.',
      )
      setAppointments([])
      setSelectedVisit('')
      setLoaded(false)
    }
    setBusy(null)
  }, [intakeId])
  useEffect(() => {
    if (readOnly) return
    const requests = requestId
    const timer = window.setTimeout(() => {
      void load()
    }, 0)
    return () => {
      window.clearTimeout(timer)
      requests.current++
    }
  }, [load, readOnly])

  const recovering = !readOnly && result?.recovery?.state === 'pending'
  useEffect(() => {
    if (!recovering || !result?.check_id) return
    const id = result.check_id
    const current = requestId.current
    let stopped = false
    let attempts = 0
    let timer: number
    const poll = async () => {
      try {
        const updated = await commercialSavedPrevisit(id, intakeId)
        if (stopped || current !== requestId.current) return
        setResult(updated)
        if (updated.recovery?.state === 'pending' && ++attempts < 180)
          timer = window.setTimeout(() => void poll(), 3000)
        else if (updated.recovery?.state === 'pending')
          setNotice(
            'Recovery is taking longer than expected. The request is saved. Reopen this visit to check its status.',
          )
      } catch (caught) {
        if (!stopped && current === requestId.current)
          setError(
            caught instanceof Error
              ? caught.message
              : 'The recovery result could not be loaded. Reopen this visit to check its status.',
          )
      }
    }
    timer = window.setTimeout(() => void poll(), 2000)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [recovering, result?.check_id, intakeId])

  const estimate = useCallback(
    async (refresh = refreshBenefits) => {
      if (!selectedVisit || busy) return
      const current = ++requestId.current
      setPatientResolutionNeeded(false)
      setError('')
      setNotice('')
      setDeleteError('')
      setBusy('estimate')
      try {
        const response = await commercialPrevisit(selectedVisit, refresh, intakeId)
        if (response.appointment_id !== selectedVisit)
          throw new Error(
            'The returned estimate does not match this visit. Refresh visits and try again.',
          )
        if (current === requestId.current) setResult(response)
      } catch (caught) {
        if (current === requestId.current) {
          setPatientResolutionNeeded(
            caught instanceof ApiError && caught.code === 'ecw_patient_selection_required',
          )
          setError(caught instanceof Error ? caught.message : 'This visit could not be checked.')
        }
      } finally {
        if (current === requestId.current) setBusy(null)
      }
    },
    [selectedVisit, busy, refreshBenefits, intakeId],
  )
  useEffect(() => {
    if (!loaded || !selectedVisit || result || error || busy) return
    if (automaticEstimate.current === selectedVisit) return
    automaticEstimate.current = selectedVisit
    void estimate()
  }, [loaded, selectedVisit, result, error, busy, estimate])
  const saveReview = async (review: VisitReview) => {
    if (!result || busy) return false
    const current = ++requestId.current
    setBusy('review')
    setError('')
    setNotice('')
    try {
      const updated = await commercialVisitReview(result, review, intakeId)
      if (current === requestId.current) {
        setResult(updated)
        if (
          review.separate_em_planned === true &&
          !result.requested_service_types?.includes('98')
        ) {
          let refreshed: PrevisitEstimate
          try {
            refreshed = await commercialPrevisit(updated.appointment_id, true, intakeId)
          } catch (caught) {
            // The gateway can time out after Stedi succeeds; read the saved case before offering another paid inquiry.
            const saved = await commercialSavedPrevisit(updated.check_id!, intakeId)
            if (!saved.requested_service_types?.includes('98')) throw caught
            refreshed = saved
          }
          if (current === requestId.current) setResult(refreshed)
        }
        setNotice('')
        return true
      }
      return false
    } catch (caught) {
      if (current === requestId.current)
        setError(caught instanceof Error ? caught.message : 'Verification could not be saved.')
      return false
    } finally {
      if (current === requestId.current) setBusy(null)
    }
  }
  const selectCoverage = async (candidateId: string, payerRoute = false) => {
    if (!result || busy) return
    const current = ++requestId.current
    setBusy('coverage')
    setError('')
    setNotice('')
    try {
      const updated = await (payerRoute
        ? commercialSelectPayerRoute(result, candidateId, intakeId)
        : commercialSelectCoverageCandidate(result, candidateId, intakeId))
      if (current === requestId.current) {
        setResult(updated)
        setNotice('Checking the selected policy with Stedi…')
      }
    } catch (caught) {
      if (current === requestId.current)
        setError(
          caught instanceof Error ? caught.message : 'The selected policy could not be checked.',
        )
    } finally {
      if (current === requestId.current) setBusy(null)
    }
  }
  const deleteCheck = async () => {
    if (!intakeId || !result?.check_id || busy) return
    const patientName = patient ? `${patient.given_name} ${patient.family_name}` : 'this patient'
    if (
      !cleanupPending &&
      !window.confirm(
        `Delete the saved insurance check for ${patientName} on ${visitDate(result.service_date)}? The patient and eCW appointment stay unchanged. This cannot be undone.`,
      )
    )
      return
    setBusy('delete')
    setDeleteError('')
    try {
      const deleted = await deleteStaffInsuranceCheck(intakeId, result.check_id)
      if (deleted.cleanup_pending) {
        setCleanupPending(true)
        setDeleteError(
          'The saved check was removed. Some stored files still need cleanup. Select Retry file cleanup to finish.',
        )
      } else {
        setResult(null)
        setHistory(null)
        setCleanupPending(false)
        setNotice('Insurance check deleted. The patient and scheduled visit are still available.')
      }
    } catch (caught) {
      setDeleteError(
        caught instanceof Error ? caught.message : 'The insurance check could not be deleted.',
      )
    } finally {
      setBusy(null)
    }
  }
  const loadHistory = async () => {
    if (historyBusy) return
    setHistoryBusy(true)
    setHistoryError('')
    try {
      setHistory((await commercialHistory(intakeId)).visits)
    } catch (caught) {
      setHistoryError(caught instanceof Error ? caught.message : 'Past visits could not be loaded.')
    } finally {
      setHistoryBusy(false)
    }
  }

  const chooseVisit = (id: string) => {
    if (selectedVisit !== id) {
      setPanel(null)
      setSelectedVisit(id)
      setResult(null)
      setError('')
      setDeleteError('')
      setCleanupPending(false)
      setNotice('')
    }
  }
  return {
    onBack,
    onResolvePatient,
    intakeId,
    saved,
    currentAccountNumber,
    readOnly,
    session,
    patient,
    appointments,
    history,
    result,
    error,
    patientResolutionNeeded,
    historyError,
    historyBusy,
    selectedVisit,
    candidateSuggestion,
    setCandidateSuggestion,
    payerSuggestion,
    setPayerSuggestion,
    loaded,
    busy,
    deleteError,
    cleanupPending,
    notice,
    setNotice,
    panel,
    setPanel,
    recovering,
    load,
    estimate,
    saveReview,
    selectCoverage,
    deleteCheck,
    loadHistory,
    chooseVisit,
  }
}
export type CommercialEstimateActions = ReturnType<typeof useCommercialEstimateActions>
