import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../services/api/client'
import {
  getStaffInvitations,
  type StaffInvitation,
  type SavedStaffInvitation,
} from '../../services/api/staff-clinic'
import {
  getStaffIntake,
  getStaffIntakes,
  type EcwPatientSuggestion,
  type EcwPatientMatch,
} from '../../services/api/staff-intake'
import { getStaffLibrary, type StaffLibraryDocument } from '../../services/api/staff-library'
import { getStaffSession } from '../../services/api/staff-session'
import {
  type StaffIntake,
  type StaffPatient,
  type StaffPatientFields,
  type StaffSession,
} from '../../services/api/staff-contracts'
import type { StaffWorkspaceProps } from './staff-workspace-props'
import { emptyFields, patientFieldsFrom } from './staff-workspace-utils'

export function useStaffWorkspaceState({
  initialIntakeId,
  initialEcwLookup = false,
}: StaffWorkspaceProps) {
  const [historyPatientId, setHistoryPatientId] = useState<string>()
  const [linkPatient, setLinkPatient] = useState<{
    id?: string
    intakeId?: string
    name: string
    email?: string
  } | null>(null)
  const [recordBack, setRecordBack] = useState<'patients' | 'inbox'>('patients')
  const [linkBack, setLinkBack] = useState<'patients' | 'inbox' | 'record'>('patients')
  const [invitations, setInvitations] = useState<SavedStaffInvitation[] | null>(null)
  const [invitation, setInvitation] = useState<StaffInvitation | null>(null)
  const [session, setSession] = useState<StaffSession | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [screen, setScreen] = useState<
    | 'patients'
    | 'inbox'
    | 'lookup'
    | 'prebooking'
    | 'add'
    | 'review'
    | 'match'
    | 'record'
    | 'members'
    | 'library'
    | 'links'
  >('patients')
  const [tab, setTab] = useState<'overview' | 'documents' | 'activity'>('overview')
  const [intakes, setIntakes] = useState<StaffIntake[]>([])
  const [intake, setIntake] = useState<StaffIntake | null>(null)
  const [fields, setFields] = useState<StaffPatientFields>(emptyFields)
  const [candidates, setCandidates] = useState<StaffPatient[]>([])
  const [match, setMatch] = useState('')
  const [identityConfirmed, setIdentityConfirmed] = useState(false)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [lookupDetails, setLookupDetails] = useState({
    given_name: '',
    middle_initial: '',
    family_name: '',
    birth_date: '',
    ecw_account_number: '',
  })
  const [ecwSuggestions, setEcwSuggestions] = useState<EcwPatientSuggestion[] | null>(null)
  const [ecwSelection, setEcwSelection] = useState('')
  const [ecwConfirmed, setEcwConfirmed] = useState(false)
  const [ecwMatch, setEcwMatch] = useState<EcwPatientMatch | null>(null)
  const [camera, setCamera] = useState(false)
  const [library, setLibrary] = useState<StaffLibraryDocument[] | null>(null)
  const [busy, setBusy] = useState('')
  const [listLoading, setListLoading] = useState(false)
  const [inboxLoaded, setInboxLoaded] = useState(false)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [appliedSearch, setAppliedSearch] = useState({ query: '', status: '' })
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  const errorBox = useRef<HTMLDivElement>(null)
  const uploadInFlight = useRef(false)
  const staffId = session?.id
  const usesAutomaticRecord = intake?.auto_patient?.identity_status === 'unverified'
  const selectedEcwPatient = ecwSuggestions?.find((row) => row.id === ecwSelection)

  useEffect(() => {
    let active = true
    getStaffSession()
      .then(({ staff }) => {
        if (active) setSession(staff)
      })
      .catch((caught) => {
        if (active && !(caught instanceof ApiError && caught.status === 401))
          setError(
            caught instanceof Error ? caught.message : 'Unable to restore your staff session.',
          )
      })
      .finally(() => {
        if (active) setRestoring(false)
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (!session) return
    let active = true
    queueMicrotask(() => {
      if (active) setListLoading(true)
    })
    getStaffIntakes()
      .then((result) => {
        if (active) {
          setIntakes(result.intakes)
          setInboxLoaded(true)
          setNextOffset(result.has_more ? result.next_offset : null)
          setAppliedSearch({ query: '', status: '' })
        }
      })
      .catch((caught) => {
        if (active)
          setError(caught instanceof Error ? caught.message : 'Unable to load the insurance inbox.')
      })
      .finally(() => {
        if (active) setListLoading(false)
      })
    return () => {
      active = false
    }
  }, [session])

  useEffect(() => {
    if (!staffId || !initialIntakeId) return
    let active = true
    queueMicrotask(() => {
      if (active) setBusy('open')
    })
    getStaffIntake(initialIntakeId)
      .then((record) => {
        if (!active) return
        setIntake(record)
        setTab('overview')
        if (initialEcwLookup) {
          setLookupDetails({
            given_name: record.patient?.given_name || record.fields.given_name || '',
            middle_initial: '',
            family_name: record.patient?.family_name || record.fields.family_name || '',
            birth_date: record.patient?.birth_date || record.fields.birth_date || '',
            ecw_account_number: record.patient?.ecw_account_number || '',
          })
          setScreen('lookup')
        } else if (!record.patient) {
          setFields(patientFieldsFrom(record))
          setIdentityConfirmed(false)
          setMatch('')
          setScreen('review')
        } else setScreen('record')
      })
      .catch((caught) => {
        if (active)
          setError(caught instanceof Error ? caught.message : 'Unable to open this intake.')
      })
      .finally(() => {
        if (active) setBusy('')
      })
    return () => {
      active = false
    }
  }, [staffId, initialIntakeId, initialEcwLookup])

  useEffect(() => {
    if (screen !== 'members' || session?.role !== 'admin') return
    let active = true
    getStaffInvitations()
      .then((result) => {
        if (active) setInvitations(result.invitations)
      })
      .catch((caught) => {
        if (active)
          setError(
            caught instanceof Error ? caught.message : 'Staff invitations could not be loaded.',
          )
      })
    return () => {
      active = false
    }
  }, [screen, session, invitation])

  useEffect(() => {
    if (
      screen !== 'library' ||
      !library?.some(
        (doc) =>
          doc.kind !== 'era_835' &&
          (!doc.extraction_summary ||
            ['reading', 'read', 'needs_retry'].includes(doc.extraction_summary.status)),
      )
    )
      return
    let cancelled = false
    const timer = window.setTimeout(() => {
      void getStaffLibrary()
        .then((result) => {
          if (!cancelled) setLibrary(result.documents)
        })
        .catch(() => {
          if (!cancelled)
            setError('Document status could not be refreshed. The original files remain saved.')
        })
    }, 5000)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [screen, library])
  useEffect(() => {
    heading.current?.focus()
  }, [screen, session, restoring, camera])
  useEffect(() => {
    if (error) errorBox.current?.focus()
  }, [error])
  return {
    historyPatientId,
    setHistoryPatientId,
    linkPatient,
    setLinkPatient,
    recordBack,
    setRecordBack,
    linkBack,
    setLinkBack,
    invitations,
    setInvitations,
    invitation,
    setInvitation,
    session,
    setSession,
    restoring,
    setRestoring,
    screen,
    setScreen,
    tab,
    setTab,
    intakes,
    setIntakes,
    intake,
    setIntake,
    fields,
    setFields,
    candidates,
    setCandidates,
    match,
    setMatch,
    identityConfirmed,
    setIdentityConfirmed,
    query,
    setQuery,
    status,
    setStatus,
    files,
    setFiles,
    lookupDetails,
    setLookupDetails,
    ecwSuggestions,
    setEcwSuggestions,
    ecwSelection,
    setEcwSelection,
    ecwConfirmed,
    setEcwConfirmed,
    ecwMatch,
    setEcwMatch,
    camera,
    setCamera,
    library,
    setLibrary,
    busy,
    setBusy,
    listLoading,
    setListLoading,
    inboxLoaded,
    setInboxLoaded,
    nextOffset,
    setNextOffset,
    appliedSearch,
    setAppliedSearch,
    error,
    setError,
    notice,
    setNotice,
    heading,
    errorBox,
    uploadInFlight,
    staffId,
    usesAutomaticRecord,
    selectedEcwPatient,
  }
}
