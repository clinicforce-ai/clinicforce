import type { FormEvent } from 'react'
import { beginGoogleDriveConnection, beginGoogleSignIn } from '../../services/api/patient-google'
import { beginIdmeAuthorization, disconnectIdme, getIdmeStatus } from '../../services/api/idme'
import { chooseStorageProvider, uploadPatientPdfs } from '../../services/api/patient-storage'
import { generatePatientLink } from '../../services/api/patient-sharing'
import { getPatientProfile, savePatientProfile } from '../../services/api/patient-profile'
import { loginPatient, registerPatient } from '../../services/api/patient-session'
import { savePatientRecord } from '../../services/api/patient-records'
import { type ManualRecordInput } from '../../services/api/patient-document-types'
import { categoryMeta } from './record-meta'
import { emptyForm, type FormState } from './manual-record-form'
import { messageFrom } from './error-text'
import { profileIsComplete, type ProfileFormState } from './profile-form'
import type { PortalState } from './portal-state'

export function createPortalAccountActions(state: PortalState) {
  const {
    mode,
    patientId,
    password,
    session,
    setSession,
    setIdmeStatus,
    profile,
    profileForm,
    files,
    setFiles,
    category,
    form,
    setForm,
    sharePasscode,
    setLink,
    epicRecord,
    setBusy,
    setError,
    setNotice,
    fileInput,
    applyProfile,
    clearFeedback,
    signOut,
  } = state
  const authenticate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedId = patientId.trim()
    if (!normalizedId || !password) {
      setError('Enter both your patient ID and password.')
      return
    }
    if (mode === 'register' && password.length < 12) {
      setError('Use a password with at least 12 characters.')
      return
    }
    clearFeedback()
    setBusy('auth')
    try {
      const workspace =
        mode === 'login'
          ? await loginPatient(normalizedId, password)
          : await registerPatient(normalizedId, password)
      setSession(workspace)
      applyProfile(await getPatientProfile())
      void getIdmeStatus()
        .then(setIdmeStatus)
        .catch(() => setIdmeStatus(null))
      setNotice(
        mode === 'login'
          ? 'Your workspace is ready.'
          : 'Your account is ready. Add a report or health detail to get started.',
      )
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to open your workspace.'))
    } finally {
      setBusy(null)
    }
  }

  const chooseFiles = (nextFiles: File[]) => {
    const invalid = nextFiles.find((file) => !file.name.toLowerCase().endsWith('.pdf'))
    if (invalid) {
      setFiles([])
      if (fileInput.current) fileInput.current.value = ''
      setError(`${invalid.name} is not a PDF. Choose PDF reports only.`)
      return
    }
    const oversized = nextFiles.find((file) => file.size > 15 * 1024 * 1024)
    if (oversized) {
      setFiles([])
      if (fileInput.current) fileInput.current.value = ''
      setError(`${oversized.name} exceeds the 15 MB limit.`)
      return
    }
    clearFeedback()
    setFiles(nextFiles)
  }

  const upload = async () => {
    if (!session || !files.length) {
      setError('Choose at least one PDF report before uploading.')
      return
    }
    clearFeedback()
    setBusy('upload')
    try {
      setSession(await uploadPatientPdfs(session.patient_id, password, files))
      setFiles([])
      if (fileInput.current) fileInput.current.value = ''
      setNotice(
        `${files.length === 1 ? 'Report' : `${files.length} reports`} added to ${session.storage_provider === 'google_drive' ? 'your Google Drive folder' : 'your secure workspace'}.`,
      )
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to upload the selected reports.'))
    } finally {
      setBusy(null)
    }
  }

  const connectGoogleDrive = async () => {
    if (!session) return
    clearFeedback()
    setBusy('drive')
    try {
      window.location.assign(await beginGoogleDriveConnection(session.patient_id, password))
    } catch (caught) {
      setBusy(null)
      setError(messageFrom(caught, 'Unable to start the Google Drive connection.'))
    }
  }

  const signInWithGoogle = async () => {
    clearFeedback()
    setBusy('google')
    try {
      window.location.assign(await beginGoogleSignIn())
    } catch (caught) {
      setBusy(null)
      setError(messageFrom(caught, 'Unable to start Google sign-in.'))
    }
  }

  const continueWithIdme = async (intent: 'login' | 'link') => {
    clearFeedback()
    setBusy('idme')
    try {
      window.location.assign(await beginIdmeAuthorization(intent))
    } catch (caught) {
      setBusy(null)
      setError(
        messageFrom(caught, `Unable to start ID.me ${intent === 'login' ? 'sign-in' : 'linking'}.`),
      )
    }
  }

  const unlinkIdme = async () => {
    if (!window.confirm('Disconnect ID.me and sign out of ClinicForce on every device?')) return
    clearFeedback()
    setBusy('idme-unlink')
    try {
      await disconnectIdme()
      signOut(false)
      setNotice(
        'ID.me was disconnected and all ClinicForce sessions were signed out. Sign in with your remaining method to continue.',
      )
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to disconnect ID.me.'))
    } finally {
      setBusy(null)
    }
  }

  const selectStorage = async (storageProvider: 's3' | 'google_drive') => {
    if (!session) return
    if (storageProvider === 'google_drive' && !session.drive_connected) {
      await connectGoogleDrive()
      return
    }
    clearFeedback()
    setBusy('drive')
    try {
      setSession(await chooseStorageProvider(session.patient_id, password, storageProvider))
      setNotice(
        storageProvider === 'google_drive'
          ? 'New reports will be stored in your ClinicForce Google Drive folder.'
          : 'New reports will be stored in ClinicForce secure storage.',
      )
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to change document storage.'))
    } finally {
      setBusy(null)
    }
  }

  const recordPayload = (): ManualRecordInput | null => {
    const trimmed = Object.fromEntries(
      Object.entries(form).map(([key, value]) => [key, value.trim()]),
    ) as FormState
    if (category === 'hospital' && !trimmed.hospital) {
      setError('Enter the hospital or clinic for this care visit.')
      return null
    }
    if ((category === 'lab' || category === 'vital') && (!trimmed.name || !trimmed.value)) {
      setError('Enter both a measurement name and its result.')
      return null
    }
    if (['condition', 'allergy', 'medication'].includes(category) && !trimmed.name) {
      setError(`Enter the ${categoryMeta[category].label.toLowerCase()} name.`)
      return null
    }
    return { category, ...trimmed }
  }

  const saveRecord = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!session) return
    const payload = recordPayload()
    if (!payload) return
    clearFeedback()
    setBusy('record')
    try {
      setSession(await savePatientRecord(session.patient_id, password, payload))
      setForm(emptyForm)
      setNotice('Health detail added to your workspace.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to save this health detail.'))
    } finally {
      setBusy(null)
    }
  }

  const saveProfile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!session) return
    const submitted: ProfileFormState = {
      ...profileForm,
      given_name: profileForm.given_name.trim(),
      family_name: profileForm.family_name.trim(),
    }
    if (!submitted.given_name || !submitted.family_name || !submitted.birth_date) {
      setError(
        'Enter your legal first name, legal last name, and date of birth before creating an EHR QR code.',
      )
      return
    }
    clearFeedback()
    setBusy('profile')
    try {
      applyProfile(await savePatientProfile(session.patient_id, password, submitted))
      setNotice('Demographic profile saved. Your health QR can now be matched by a receiving EHR.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to save your demographic profile.'))
    } finally {
      setBusy(null)
    }
  }

  const generateLink = async (shareMode: 'standard' | 'ecw_compat' = 'standard') => {
    if (!session) return
    if (!profileIsComplete(profile)) {
      setError('Complete and save your demographic profile before creating an EHR QR code.')
      document
        .getElementById('demographics-heading')
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      return
    }
    if (shareMode === 'standard' && !/^\d{4}$/.test(sharePasscode)) {
      setError('Choose a four-digit passcode for the clinician.')
      return
    }
    if (!session.documents.length && !session.records.length && !epicRecord?.bundle.entry.length) {
      setError(
        'Add a PDF report or health detail, or connect MyChart, before creating a share link.',
      )
      return
    }
    clearFeedback()
    setBusy('share')
    try {
      const nextLink = await generatePatientLink(
        session.patient_id,
        password,
        shareMode === 'standard' ? sharePasscode : undefined,
        shareMode,
      )
      setLink(nextLink)
      setNotice(
        shareMode === 'ecw_compat'
          ? 'A 3-hour, no-passcode eClinicalMobile compatibility QR is ready for a live scan test.'
          : nextLink.epic_included
            ? 'One QR code is ready with your workspace and the current MyChart record.'
            : 'One QR code is ready with the health information in your workspace.',
      )
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to generate the secure link.'))
    } finally {
      setBusy(null)
    }
  }

  return {
    authenticate,
    chooseFiles,
    upload,
    connectGoogleDrive,
    signInWithGoogle,
    continueWithIdme,
    unlinkIdme,
    selectStorage,
    saveRecord,
    saveProfile,
    generateLink,
  }
}
