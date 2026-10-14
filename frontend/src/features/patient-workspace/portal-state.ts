import { useCallback, useRef, useState } from 'react'
import { logoutPatient } from '../../services/api/patient-session'
import {
  type EcwRecord,
  type EpicEndpoint,
  type EpicRecord,
} from '../../services/api/patient-provider-types'
import { type IdmeStatus } from '../../services/api/idme'
import { type LinkResponse } from '../../services/api/patient-link-types'
import { type PatientProfile, type PatientSession } from '../../services/api/patient-session-types'
import { type RecordCategory } from '../../services/api/patient-document-types'
import { emptyForm, type FormState } from './manual-record-form'
import { emptyProfileForm, profileFormFrom, type ProfileFormState } from './profile-form'
import { type AuthMode, type WorkspaceTab } from './portal-types'

export function idmeResultFromLocation(): string | null {
  return new URLSearchParams(window.location.search).get('idme')
}

function idmeCallbackError(result: string | null): string {
  if (result === 'not-linked')
    return 'This ID.me account is not linked to ClinicForce yet. Sign in with your patient ID or Google, then open Care connections and link ID.me.'
  if (result === 'error')
    return 'ID.me could not complete sign-in or linking. No ID.me account was linked.'
  return ''
}

export function usePortalState() {
  const [mode, setMode] = useState<AuthMode>('login')
  const [patientId, setPatientId] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [session, setSession] = useState<PatientSession | null>(null)
  const [idmeStatus, setIdmeStatus] = useState<IdmeStatus | null>(null)
  const [idmeStatusLoading, setIdmeStatusLoading] = useState(true)
  const [profile, setProfile] = useState<PatientProfile | null>(null)
  const [profileForm, setProfileForm] = useState<ProfileFormState>(emptyProfileForm)
  const [files, setFiles] = useState<File[]>([])
  const [category, setCategory] = useState<RecordCategory>('hospital')
  const [form, setForm] = useState<FormState>(emptyForm)
  const [sharePasscode, setSharePasscode] = useState('')
  const [link, setLink] = useState<LinkResponse | null>(null)
  const [ecwCompatibilityConfirmOpen, setEcwCompatibilityConfirmOpen] = useState(false)
  const [epicRecord, setEpicRecord] = useState<EpicRecord | null>(null)
  const [epicEndpoints, setEpicEndpoints] = useState<EpicEndpoint[]>([])
  const [epicQuery, setEpicQuery] = useState('')
  const [selectedEpicEndpointId, setSelectedEpicEndpointId] = useState('')
  const [epicSearching, setEpicSearching] = useState(false)
  const [epicDirectoryError, setEpicDirectoryError] = useState('')
  const [ecwPracticeCode, setEcwPracticeCode] = useState('')
  const [ecwPracticeCodeError, setEcwPracticeCodeError] = useState('')
  const [ecwRecord, setEcwRecord] = useState<EcwRecord | null>(null)
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTab>('overview')
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState<
    | 'auth'
    | 'profile'
    | 'upload'
    | 'record'
    | 'share'
    | 'download'
    | 'delete'
    | 'bulk-download'
    | 'bulk-delete'
    | 'import'
    | 'drive'
    | 'google'
    | 'idme'
    | 'idme-unlink'
    | 'epic-connect'
    | 'epic-load'
    | 'epic-document'
    | 'ecw-connect'
    | 'ecw-load'
    | 'ecw-import'
    | 'ecw-disconnect'
    | null
  >(null)
  const [restoringSession, setRestoringSession] = useState(true)
  const [error, setError] = useState(() => idmeCallbackError(idmeResultFromLocation()))
  const [notice, setNotice] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const workspacePanel = useRef<HTMLDivElement>(null)
  const epicPopup = useRef<Window | null>(null)
  const epicPopupWatch = useRef<number | null>(null)
  const epicConnectionHandled = useRef(false)
  const ecwPopup = useRef<Window | null>(null)
  const ecwPopupWatch = useRef<number | null>(null)
  const ecwConnectionHandled = useRef(false)

  const applyProfile = useCallback((nextProfile: PatientProfile) => {
    setProfile(nextProfile)
    setProfileForm(profileFormFrom(nextProfile))
  }, [])
  const setField = (key: keyof FormState, value: string) =>
    setForm((current) => ({ ...current, [key]: value }))
  const clearFeedback = () => {
    setError('')
    setNotice('')
  }

  const signOut = (notifyServer = true) => {
    if (notifyServer) void logoutPatient()
    setSession(null)
    setPassword('')
    setFiles([])
    setLink(null)
    setProfile(null)
    setProfileForm(emptyProfileForm)
    setEpicRecord(null)
    setEcwRecord(null)
    setIdmeStatus((current) => (current ? { ...current, linked: false } : current))
    setSelectedDocumentIds(new Set())
    setSharePasscode('')
    clearFeedback()
  }

  const idmeReady = Boolean(idmeStatus?.enabled && idmeStatus.configured)
  const idmeCanLink = Boolean(idmeReady && idmeStatus?.auth_recent)

  return {
    mode,
    setMode,
    patientId,
    setPatientId,
    password,
    setPassword,
    showPassword,
    setShowPassword,
    session,
    setSession,
    idmeStatus,
    setIdmeStatus,
    idmeStatusLoading,
    setIdmeStatusLoading,
    profile,
    setProfile,
    profileForm,
    setProfileForm,
    files,
    setFiles,
    category,
    setCategory,
    form,
    setForm,
    sharePasscode,
    setSharePasscode,
    link,
    setLink,
    ecwCompatibilityConfirmOpen,
    setEcwCompatibilityConfirmOpen,
    epicRecord,
    setEpicRecord,
    epicEndpoints,
    setEpicEndpoints,
    epicQuery,
    setEpicQuery,
    selectedEpicEndpointId,
    setSelectedEpicEndpointId,
    epicSearching,
    setEpicSearching,
    epicDirectoryError,
    setEpicDirectoryError,
    ecwPracticeCode,
    setEcwPracticeCode,
    ecwPracticeCodeError,
    setEcwPracticeCodeError,
    ecwRecord,
    setEcwRecord,
    workspaceTab,
    setWorkspaceTab,
    selectedDocumentIds,
    setSelectedDocumentIds,
    busy,
    setBusy,
    restoringSession,
    setRestoringSession,
    error,
    setError,
    notice,
    setNotice,
    fileInput,
    workspacePanel,
    epicPopup,
    epicPopupWatch,
    epicConnectionHandled,
    ecwPopup,
    ecwPopupWatch,
    ecwConnectionHandled,
    applyProfile,
    setField,
    clearFeedback,
    signOut,
    idmeReady,
    idmeCanLink,
  }
}

export type PortalState = ReturnType<typeof usePortalState>
