import { type FormEvent } from 'react'
import { LoaderCircle } from 'lucide-react'
import { ApiError } from '../../services/api/client'
import {
  confirmStaffPatient,
  createStaffEcwLookup,
  createStaffIntake,
  findStaffPatients,
  getStaffIntake,
  getStaffIntakes,
  type EcwPatientSuggestion,
} from '../../services/api/staff-intake'
import { getStaffLibrary } from '../../services/api/staff-library'
import { type ClinicalPatient } from '../../services/api/staff-patients'
import { type StaffIntake, type StaffPatientFields } from '../../services/api/staff-contracts'
import type { StaffWorkspaceProps } from './staff-workspace-props'
import { emptyFields, emptyCardDetails, today, patientFieldsFrom } from './staff-workspace-utils'
import { useStaffWorkspaceState } from './useStaffWorkspaceState'

export function useStaffWorkspaceModel(props: StaffWorkspaceProps) {
  const { onOpenInsurance } = props
  const state = useStaffWorkspaceState(props)
  const {
    setHistoryPatientId,
    setLinkPatient,
    setRecordBack,
    setLinkBack,
    setInvitations,
    setInvitation,
    session,
    setSession,
    screen,
    setScreen,
    setTab,
    setIntakes,
    intake,
    setIntake,
    fields,
    setFields,
    setCandidates,
    match,
    setMatch,
    identityConfirmed,
    setIdentityConfirmed,
    query,
    status,
    setFiles,
    lookupDetails,
    setLookupDetails,
    setEcwSuggestions,
    setEcwSelection,
    setEcwConfirmed,
    setEcwMatch,
    setCamera,
    setLibrary,
    busy,
    setBusy,
    setInboxLoaded,
    nextOffset,
    setNextOffset,
    appliedSearch,
    setAppliedSearch,
    error,
    setError,
    notice,
    setNotice,
    errorBox,
    uploadInFlight,
    usesAutomaticRecord,
  } = state
  async function perform(name: string, action: () => Promise<void>) {
    setBusy(name)
    setError('')
    setNotice('')
    try {
      await action()
    } catch (caught) {
      if (session && caught instanceof ApiError && caught.status === 401) {
        setSession(null)
        setInvitation(null)
        setInvitations(null)
        setLinkPatient(null)
        setIntakes([])
        setIntake(null)
        setFiles([])
        setFields(emptyFields)
        setCandidates([])
        setEcwSuggestions(null)
        setEcwSelection('')
        setEcwConfirmed(false)
        setCamera(false)
        setLibrary(null)
        setInboxLoaded(false)
        setNextOffset(null)
        setError('Your staff session has ended. Sign in again to continue.')
      } else if (
        name === 'upload' &&
        caught instanceof ApiError &&
        [0, 408, 502, 504].includes(caught.status)
      ) {
        setError(
          'Upload confirmation was interrupted. The card may already be saved. Open the insurance inbox and refresh before uploading it again.',
        )
      } else
        setError(
          caught instanceof Error
            ? caught.message
            : 'This action could not be completed. Please try again.',
        )
    } finally {
      setBusy('')
    }
  }

  function navigate(next: typeof screen) {
    setEcwSuggestions(null)
    setEcwSelection('')
    setEcwConfirmed(false)
    if (next === 'patients') setHistoryPatientId(undefined)
    setScreen(next)
    setError('')
    setNotice('')
    setCamera(false)
  }
  function editPatient(record: StaffIntake) {
    setFields(patientFieldsFrom(record))
    setIdentityConfirmed(false)
    setMatch('')
    navigate('review')
  }
  function retryEcwLookup(record: StaffIntake) {
    setLookupDetails({
      given_name: record.fields.given_name || '',
      middle_initial: record.fields.middle_initial || '',
      family_name: record.fields.family_name || '',
      birth_date: record.fields.birth_date || '',
      ecw_account_number: record.patient?.ecw_account_number || '',
    })
    setEcwSuggestions(null)
    setEcwSelection('')
    setEcwConfirmed(false)
    navigate('lookup')
  }
  function changeEcwSearch(key: keyof typeof lookupDetails, value: string) {
    setEcwSuggestions(null)
    setEcwSelection('')
    setEcwConfirmed(false)
    setLookupDetails((current) => ({ ...current, [key]: value }))
  }
  async function refreshInbox(append = false) {
    const search = append ? appliedSearch : { query, status }
    const result = await getStaffIntakes(search.query, search.status, append ? nextOffset || 0 : 0)
    setIntakes((current) =>
      append
        ? [
            ...current,
            ...result.intakes.filter(
              (record) => !current.some((existing) => existing.id === record.id),
            ),
          ]
        : result.intakes,
    )
    setAppliedSearch(search)
    setNextOffset(result.has_more ? result.next_offset : null)
    setInboxLoaded(true)
  }
  function requestPatientCard(patient?: ClinicalPatient) {
    setLinkBack('patients')
    setLinkPatient(
      patient
        ? {
            ...(patient.clinic_patient_id
              ? { id: patient.clinic_patient_id }
              : { intakeId: patient.latest_intake_id || undefined }),
            name: `${patient.given_name} ${patient.family_name}`,
            email: patient.email || undefined,
          }
        : null,
    )
    navigate('links')
  }
  function openLibrary() {
    navigate('library')
    void perform('library', async () => {
      const result = await getStaffLibrary()
      setLibrary(result.documents)
    })
  }
  function openIntake(id: string) {
    setRecordBack(screen === 'inbox' ? 'inbox' : 'patients')
    void perform('open', async () => {
      const record = await getStaffIntake(id)
      setEcwMatch(null)
      setIntake(record)
      setTab('overview')
      if (
        screen === 'patients' &&
        record.patient &&
        (record.patient.ecw_patient_id || record.fields.ecw_patient_id)
      ) {
        onOpenInsurance(record.id, 'commercial')
        return
      }
      navigate('record')
    })
  }
  function checkFiles(next: File[]) {
    if (!next.length || next.length > 2)
      return 'Choose one or two files for the front and back of the card.'
    if (next.some((file) => !['image/jpeg', 'image/png', 'application/pdf'].includes(file.type)))
      return 'Use a JPG, PNG, or PDF file.'
    if (next.some((file) => file.size > 10 * 1024 * 1024 || file.size === 0))
      return 'Each file must contain data and be no larger than 10 MB.'
    return ''
  }
  function saveCard(next: File[]) {
    if (uploadInFlight.current || busy) return
    const problem = checkFiles(next)
    if (problem) {
      setError(problem)
      return
    }
    uploadInFlight.current = true
    setFiles(next)
    setCamera(false)
    void perform('upload', async () => {
      const record = await createStaffIntake(next, emptyCardDetails)
      setIntake(record)
      setIntakes((current) => [record, ...current.filter((row) => row.id !== record.id)])
      setFiles([])
      editPatient(record)
      setNotice('Insurance card saved. Confirm the patient details before linking a clinic record.')
    }).finally(() => {
      uploadInFlight.current = false
    })
  }
  function startEcwLookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void lookupEcwPatient()
  }
  async function lookupEcwPatient(selected?: EcwPatientSuggestion) {
    await perform('lookup', async () => {
      if (!selected) setEcwSuggestions(null)
      const response = await createStaffEcwLookup(
        selected
          ? {
              given_name: selected.given_name,
              middle_initial: '',
              family_name: selected.family_name,
              birth_date: selected.birth_date,
              ecw_account_number: selected.ecw_account_number || '',
              selected_patient_id: selected.id,
              identity_confirmed: true,
            }
          : lookupDetails,
      )
      if ('candidates' in response) {
        setEcwSuggestions(response.candidates)
        setEcwSelection('')
        setEcwConfirmed(false)
        return
      }
      const { ecw_match, ...record } = response
      setEcwSuggestions(null)
      setIntake(record)
      setIntakes((current) => [record, ...current.filter((row) => row.id !== record.id)])
      setEcwMatch(ecw_match)
      setFields(patientFieldsFrom(record))
      if (record.patient) {
        onOpenInsurance(record.id, 'commercial', false)
        return
      }
      const result = await findStaffPatients({
        given_name: record.fields.given_name || '',
        family_name: record.fields.family_name || '',
        birth_date: record.fields.birth_date || '',
      })
      // eCW already matched this exact name and date of birth. When one clinic
      // record (or none) has the same identity, open the estimate directly.
      // Ambiguous records still require an explicit staff choice.
      if (result.patients.length <= 1) {
        try {
          const confirmed = await confirmStaffPatient(record.id, {
            ...patientFieldsFrom(record),
            ...(result.patients[0] ? { patient_id: result.patients[0].id } : {}),
            identity_confirmed: true,
          })
          setIntake(confirmed)
          setIntakes((current) => current.map((row) => (row.id === confirmed.id ? confirmed : row)))
          onOpenInsurance(confirmed.id, 'commercial', false)
          return
        } catch {
          // A concurrent eCW link or changed clinic record is handled by the
          // existing explicit match screen below.
        }
      }
      setCandidates(result.patients)
      setMatch(result.patients.length ? '' : 'new')
      setIdentityConfirmed(false)
      setLookupDetails({
        given_name: '',
        middle_initial: '',
        family_name: '',
        birth_date: '',
        ecw_account_number: '',
      })
      navigate('match')
    })
  }
  function findMatches(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (intake?.source === 'ecw_lookup' && !intake.fields.ecw_patient_id) {
      setError('This intake was saved before eCW verification. Start a new eCW lookup.')
      return
    }
    void perform('matching', async () => {
      const result = await findStaffPatients({
        given_name: fields.given_name.trim(),
        family_name: fields.family_name.trim(),
        birth_date: fields.birth_date,
      })
      const matches = result.patients.filter(
        (patient) =>
          patient.id !== intake?.expected_patient_id &&
          !(usesAutomaticRecord && patient.id === intake?.auto_patient_id),
      )
      setCandidates(matches)
      setMatch(matches.length ? '' : 'new')
      setIdentityConfirmed(false)
      navigate('match')
    })
  }
  function savePatient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!intake || !identityConfirmed || !match) return
    void perform('saving', async () => {
      const record = await confirmStaffPatient(intake.id, {
        ...fields,
        ...(match === 'new' ? {} : { patient_id: match }),
        identity_confirmed: true,
      })
      setIntake(record)
      setIntakes((current) => current.map((row) => (row.id === record.id ? record : row)))
      setTab('overview')
      if (record.source === 'ecw_lookup') {
        onOpenInsurance(record.id, 'commercial', false)
        return
      }
      navigate('record')
      setNotice(
        'Patient confirmed. Insurance is saved to this clinic without creating a patient account.',
      )
    })
  }
  function input(
    label: string,
    key: keyof StaffPatientFields,
    options: { required?: boolean; type?: string; help?: string } = {},
  ) {
    return (
      <label className="staff-field" key={key} htmlFor={`staff-${key}`}>
        <span>
          {label}
          {options.required ? ' *' : ''}
        </span>
        <input
          id={`staff-${key}`}
          value={fields[key]}
          onChange={(event) => setFields((current) => ({ ...current, [key]: event.target.value }))}
          type={options.type || 'text'}
          required={options.required}
          max={options.type === 'date' ? today() : undefined}
          min={options.type === 'date' ? '1900-01-01' : undefined}
          aria-describedby={options.help ? `staff-${key}-help` : undefined}
          disabled={Boolean(busy)}
        />
        {options.help && <small id={`staff-${key}-help`}>{options.help}</small>}
      </label>
    )
  }
  const alerts = (
    <>
      {error && (
        <div className="staff-alert staff-error" role="alert" tabIndex={-1} ref={errorBox}>
          {error}
        </div>
      )}
      {notice && (
        <div className="staff-alert staff-success" role="status">
          {notice}
        </div>
      )}
      {busy && (
        <div className="staff-progress" role="status">
          <LoaderCircle className="staff-spin" size={18} aria-hidden="true" />
          {busy === 'upload'
            ? 'Saving card and reading its details. Keep this page open.'
            : busy === 'matching'
              ? 'Checking clinic patient records…'
              : 'Working…'}
        </div>
      )}
    </>
  )
  return {
    ...props,
    ...state,
    perform,
    navigate,
    editPatient,
    retryEcwLookup,
    changeEcwSearch,
    refreshInbox,
    requestPatientCard,
    openLibrary,
    openIntake,
    checkFiles,
    saveCard,
    startEcwLookup,
    lookupEcwPatient,
    findMatches,
    savePatient,
    input,
    alerts,
  }
}
export type StaffWorkspaceModel = ReturnType<typeof useStaffWorkspaceModel>
