import * as Dialog from '@radix-ui/react-dialog'
import { animate, stagger } from 'animejs'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ArrowLeft,
  Building2,
  CheckCircle2,
  ChevronRight,
  ClipboardPlus,
  Cloud,
  Download,
  Eye,
  EyeOff,
  FileText,
  FolderOpen,
  KeyRound,
  LockKeyhole,
  LoaderCircle,
  LogOut,
  Plus,
  QrCode,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Upload,
  UserRound,
  X,
} from 'lucide-react'
import {
  ApiError,
  beginEcwAuthorization,
  beginGoogleDriveConnection,
  beginEpicAuthorization,
  beginGoogleSignIn,
  chooseStorageProvider,
  deletePatientDocument,
  deletePatientDocuments,
  disconnectEcw,
  disconnectEpic,
  downloadEpicDocument,
  getEpicRecord,
  getEcwRecord,
  downloadPatientDocument,
  downloadPatientDocumentsArchive,
  generatePatientLink,
  getCurrentPatientSession,
  getPatientProfile,
  loginPatient,
  importGoogleDriveChanges,
  importEcwRecord,
  logoutPatient,
  registerPatient,
  searchEpicEndpoints,
  savePatientRecord,
  savePatientProfile,
  type LinkResponse,
  type EpicEndpoint,
  type EpicRecord,
  type EcwRecord,
  type ManualRecordInput,
  type PatientDocument,
  type PatientProfile,
  type PatientProfileInput,
  type PatientSession,
  type RecordCategory,
  uploadPatientPdfs,
} from '../services/api'

interface PatientPortalProps {
  onBack: () => void
}

type AuthMode = 'login' | 'register'
type WorkspaceTab = 'overview' | 'records' | 'connections'
type FormState = Record<Exclude<keyof ManualRecordInput, 'category'>, string>
type ProfileFormState = PatientProfileInput

const emptyForm: FormState = {
  hospital: '', name: '', value: '', unit: '', date: '', details: '', dosage: '',
}

const emptyProfileForm: ProfileFormState = {
  given_name: '', family_name: '', birth_date: '', administrative_gender: 'unknown',
}

function profileFormFrom(profile: PatientProfile): ProfileFormState {
  return {
    given_name: profile.given_name ?? profile.suggested_given_name ?? '',
    family_name: profile.family_name ?? profile.suggested_family_name ?? '',
    birth_date: profile.birth_date ?? '',
    administrative_gender: profile.administrative_gender ?? 'unknown',
  }
}

function profileIsComplete(profile: PatientProfile | null): boolean {
  return Boolean(profile?.given_name && profile.family_name && profile.birth_date && profile.administrative_gender)
}

const categoryMeta: Record<RecordCategory, { label: string; help: string; icon: string }> = {
  hospital: { label: 'Care visit', help: 'A hospital, clinic, or care-team visit', icon: '01' },
  lab: { label: 'Lab result', help: 'A test name and result', icon: '02' },
  vital: { label: 'Vital sign', help: 'A measurement such as blood pressure', icon: '03' },
  condition: { label: 'Condition', help: 'A diagnosis or ongoing condition', icon: '04' },
  allergy: { label: 'Allergy', help: 'An allergy or intolerance', icon: '05' },
  medication: { label: 'Medication', help: 'A medication you currently take', icon: '06' },
}

const externalImportMeta = { label: 'External SHL import', icon: 'SHL' }
const ecwImportMeta = { label: 'eClinicalWorks / healow import', icon: 'eCW' }
const providerLogos = {
  google: { src: 'https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg', alt: 'Google' },
  mychart: { src: 'https://media.epic.com/mychartdotorg/site/en-us/images/logo/site-logo.svg', alt: 'MyChart' },
  ecw: { src: 'https://www.eclinicalworks.com/wp-content/uploads/2014/08/main-logo.png', alt: 'eClinicalWorks' },
  healow: { src: 'https://plus.healow.com/wp-content/uploads/2022/03/healow-logo@2x.png', alt: 'healow' },
} as const

function ProviderLogo({ provider }: { provider: keyof typeof providerLogos }) {
  const logo = providerLogos[provider]
  return <img className={`provider-logo provider-logo-${provider}`} src={logo.src} alt={logo.alt} />
}
const ecwImportableResourceTypes = new Set([
  'AllergyIntolerance', 'CarePlan', 'CareTeam', 'Condition', 'Coverage',
  'DiagnosticReport', 'Device', 'DocumentReference', 'Encounter', 'Goal',
  'Immunization', 'MedicationDispense', 'MedicationRequest', 'Observation',
  'Procedure', 'ServiceRequest',
])

function recordMeta(category: PatientSession['records'][number]['category']): { label: string; icon: string } {
  if (category === 'external-shl') return externalImportMeta
  if (category === 'ecw') return ecwImportMeta
  return categoryMeta[category]
}

function dateLabel(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function patientInitials(patientId: string): string {
  return patientId.slice(0, 2).toUpperCase() || 'PT'
}

function messageFrom(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

function codedText(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as { text?: unknown; coding?: unknown }
  if (typeof candidate.text === 'string' && candidate.text.trim()) return candidate.text.trim()
  if (Array.isArray(candidate.coding)) {
    for (const coding of candidate.coding) {
      if (!coding || typeof coding !== 'object') continue
      const display = (coding as { display?: unknown; code?: unknown }).display ?? (coding as { code?: unknown }).code
      if (typeof display === 'string' && display.trim()) return display.trim()
    }
  }
  return null
}

/** Human-readable labels and display order for grouped Epic resource types. */
const epicResourceGroupMeta: Record<string, { label: string; singular: string; order: number }> = {
  Patient: { label: 'Patient demographics', singular: 'Patient', order: 0 },
  Condition: { label: 'Conditions & diagnoses', singular: 'Condition', order: 1 },
  AllergyIntolerance: { label: 'Allergies & intolerances', singular: 'Allergy', order: 2 },
  MedicationRequest: { label: 'Medications', singular: 'Medication', order: 3 },
  Immunization: { label: 'Immunizations', singular: 'Immunization', order: 4 },
  Observation: { label: 'Lab results & vitals', singular: 'Observation', order: 5 },
  DiagnosticReport: { label: 'Diagnostic reports', singular: 'Diagnostic report', order: 6 },
  Encounter: { label: 'Encounters & visits', singular: 'Encounter', order: 7 },
  CarePlan: { label: 'Care plans', singular: 'Care plan', order: 8 },
  CareTeam: { label: 'Care teams', singular: 'Care team', order: 9 },
  Coverage: { label: 'Insurance coverage', singular: 'Coverage', order: 10 },
  Goal: { label: 'Health goals', singular: 'Goal', order: 11 },
  Device: { label: 'Devices', singular: 'Device', order: 12 },
  DocumentReference: { label: 'Documents', singular: 'Document', order: 13 },
}

function groupEpicResources(resources: Array<Record<string, unknown>>): Array<{ type: string; meta: typeof epicResourceGroupMeta[string]; resources: Array<Record<string, unknown>> }> {
  const groups: Record<string, Array<Record<string, unknown>>> = {}
  for (const resource of resources) {
    const type = typeof resource.resourceType === 'string' ? resource.resourceType : 'Unknown'
    ;(groups[type] ??= []).push(resource)
  }
  return Object.entries(groups)
    .map(([type, items]) => ({
      type,
      meta: epicResourceGroupMeta[type] ?? { label: type, singular: type, order: 99 },
      resources: items,
    }))
    .sort((a, b) => a.meta.order - b.meta.order)
}

/** Extract a structured summary object from any FHIR resource Epic returns. */
function epicResourceDetail(resource: Record<string, unknown>): { title: string; details: string[] } {
  const resourceType = typeof resource.resourceType === 'string' ? resource.resourceType : 'FHIR resource'

  if (resourceType === 'Condition') {
    const name = codedText(resource.code) ?? (typeof resource.description === 'string' ? resource.description : null)
    const status = codedText(resource.clinicalStatus)
    const severity = codedText(resource.severity)
    const category = Array.isArray(resource.category) ? resource.category.map((cat) => codedText(cat)).filter(Boolean).join(', ') : null
    const onset = typeof resource.onsetDateTime === 'string' ? `Onset: ${dateLabel(resource.onsetDateTime)}` : null
    return { title: name ?? 'Unnamed condition', details: [status, severity, category, onset].filter(Boolean) as string[] }
  }

  if (resourceType === 'AllergyIntolerance') {
    const substance = codedText(resource.code)
    const status = codedText(resource.clinicalStatus)
    const verification = codedText(resource.verificationStatus)
    const criticality = typeof resource.criticality === 'string' ? `Criticality: ${resource.criticality}` : null
    const categoryArr = Array.isArray(resource.category) ? resource.category.filter((c): c is string => typeof c === 'string').join(', ') : null
    const reactions = Array.isArray(resource.reaction)
      ? resource.reaction.map((r) => {
          if (!r || typeof r !== 'object') return null
          const manifestations = Array.isArray((r as Record<string, unknown>).manifestation)
            ? ((r as Record<string, unknown>).manifestation as unknown[]).map((m) => codedText(m)).filter(Boolean).join(', ')
            : null
          return manifestations
        }).filter(Boolean).join('; ')
      : null
    return {
      title: substance ?? 'Unknown allergen',
      details: [status, verification, categoryArr ? `Type: ${categoryArr}` : null, criticality, reactions ? `Reactions: ${reactions}` : null].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'MedicationRequest') {
    const medName = codedText(resource.medicationCodeableConcept) ?? (typeof resource.medicationReference === 'object' && resource.medicationReference ? codedText((resource.medicationReference as Record<string, unknown>).display ? { text: (resource.medicationReference as Record<string, unknown>).display } : resource.medicationReference) : null)
    const status = typeof resource.status === 'string' ? resource.status : null
    const intent = typeof resource.intent === 'string' ? resource.intent : null
    const authored = typeof resource.authoredOn === 'string' ? `Prescribed: ${dateLabel(resource.authoredOn)}` : null
    const dosageInstructions = Array.isArray(resource.dosageInstruction)
      ? resource.dosageInstruction.map((d) => {
          if (!d || typeof d !== 'object') return null
          const text = typeof (d as Record<string, unknown>).text === 'string' ? (d as Record<string, unknown>).text as string : null
          return text
        }).filter(Boolean).join('; ')
      : null
    const requester = resource.requester && typeof resource.requester === 'object' ? (typeof (resource.requester as Record<string, unknown>).display === 'string' ? `Prescribed by: ${(resource.requester as Record<string, unknown>).display}` : null) : null
    return {
      title: medName ?? 'Unnamed medication',
      details: [status, intent !== 'order' ? intent : null, dosageInstructions, authored, requester].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Immunization') {
    const vaccine = codedText(resource.vaccineCode)
    const status = typeof resource.status === 'string' ? resource.status : null
    const date = typeof resource.occurrenceDateTime === 'string' ? `Date: ${dateLabel(resource.occurrenceDateTime)}` : (typeof resource.occurrenceString === 'string' ? `Date: ${resource.occurrenceString}` : null)
    const site = codedText(resource.site)
    const primarySource = typeof resource.primarySource === 'boolean' ? (resource.primarySource ? 'Primary source' : 'Historical record') : null
    return {
      title: vaccine ?? 'Unnamed vaccine',
      details: [status, date, site ? `Site: ${site}` : null, primarySource].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Observation') {
    const name = codedText(resource.code)
    const status = typeof resource.status === 'string' && resource.status !== 'final' ? resource.status : null
    const effectiveDate = typeof resource.effectiveDateTime === 'string' ? dateLabel(resource.effectiveDateTime) : null
    let valueStr: string | null = null
    if (resource.valueQuantity && typeof resource.valueQuantity === 'object') {
      const q = resource.valueQuantity as { value?: unknown; unit?: unknown }
      if (q.value !== undefined) valueStr = `${q.value}${typeof q.unit === 'string' ? ` ${q.unit}` : ''}`
    } else if (typeof resource.valueString === 'string') {
      valueStr = resource.valueString
    } else if (resource.valueCodeableConcept) {
      valueStr = codedText(resource.valueCodeableConcept)
    }
    const interpretation = Array.isArray(resource.interpretation) ? resource.interpretation.map((i) => codedText(i)).filter(Boolean).join(', ') : null
    const refRange = Array.isArray(resource.referenceRange)
      ? resource.referenceRange.map((r) => {
          if (!r || typeof r !== 'object') return null
          const rr = r as { low?: { value?: unknown; unit?: unknown }; high?: { value?: unknown; unit?: unknown }; text?: unknown }
          if (typeof rr.text === 'string') return rr.text
          const low = rr.low?.value !== undefined ? `${rr.low.value}` : null
          const high = rr.high?.value !== undefined ? `${rr.high.value}` : null
          const unit = typeof rr.low?.unit === 'string' ? ` ${rr.low.unit}` : (typeof rr.high?.unit === 'string' ? ` ${rr.high.unit}` : '')
          if (low && high) return `Ref: ${low}–${high}${unit}`
          if (low) return `Ref: ≥${low}${unit}`
          if (high) return `Ref: ≤${high}${unit}`
          return null
        }).filter(Boolean).join('; ')
      : null
    // Handle component observations (e.g. blood pressure systolic/diastolic)
    const components = Array.isArray(resource.component)
      ? resource.component.map((c) => {
          if (!c || typeof c !== 'object') return null
          const comp = c as Record<string, unknown>
          const cName = codedText(comp.code)
          let cValue: string | null = null
          if (comp.valueQuantity && typeof comp.valueQuantity === 'object') {
            const q = comp.valueQuantity as { value?: unknown; unit?: unknown }
            if (q.value !== undefined) cValue = `${q.value}${typeof q.unit === 'string' ? ` ${q.unit}` : ''}`
          } else if (typeof comp.valueString === 'string') { cValue = comp.valueString }
          return cName && cValue ? `${cName}: ${cValue}` : null
        }).filter(Boolean).join(' / ')
      : null
    return {
      title: name ?? 'Unnamed observation',
      details: [valueStr ? `Value: ${valueStr}` : null, components, interpretation ? `Interpretation: ${interpretation}` : null, refRange, effectiveDate, status].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'DiagnosticReport') {
    const name = codedText(resource.code)
    const status = typeof resource.status === 'string' ? resource.status : null
    const date = typeof resource.effectiveDateTime === 'string' ? dateLabel(resource.effectiveDateTime) : null
    const conclusion = typeof resource.conclusion === 'string' ? resource.conclusion : null
    const category = Array.isArray(resource.category) ? resource.category.map((cat) => codedText(cat)).filter(Boolean).join(', ') : null
    return {
      title: name ?? 'Unnamed report',
      details: [status, category, date, conclusion ? `Conclusion: ${conclusion}` : null].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Encounter') {
    const encounterClass = resource.class && typeof resource.class === 'object'
      ? ((resource.class as { display?: unknown }).display as string) ?? ((resource.class as { code?: unknown }).code as string) ?? null
      : null
    const status = typeof resource.status === 'string' ? resource.status : null
    const typeArr = Array.isArray(resource.type) ? resource.type.map((t) => codedText(t)).filter(Boolean) : []
    const period = resource.period && typeof resource.period === 'object'
      ? (() => { const p = resource.period as { start?: string; end?: string }; return typeof p.start === 'string' ? `${dateLabel(p.start)}${typeof p.end === 'string' ? ` – ${dateLabel(p.end)}` : ''}` : null })()
      : null
    const reasonArr = Array.isArray(resource.reasonCode) ? resource.reasonCode.map((r) => codedText(r)).filter(Boolean) : []
    return {
      title: typeArr[0] ?? encounterClass ?? 'Clinical encounter',
      details: [status, encounterClass && typeArr[0] ? `Class: ${encounterClass}` : null, period, reasonArr.length ? `Reason: ${reasonArr.join(', ')}` : null].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'CarePlan') {
    const title = typeof resource.title === 'string' ? resource.title : codedText(Array.isArray(resource.category) ? resource.category[0] : null)
    const status = typeof resource.status === 'string' ? resource.status : null
    const intent = typeof resource.intent === 'string' ? resource.intent : null
    const description = typeof resource.description === 'string' ? resource.description : null
    return { title: title ?? 'Care plan', details: [status, intent, description].filter(Boolean) as string[] }
  }

  if (resourceType === 'CareTeam') {
    const name = typeof resource.name === 'string' ? resource.name : null
    const status = typeof resource.status === 'string' ? resource.status : null
    const participants = Array.isArray(resource.participant)
      ? resource.participant.slice(0, 5).map((p) => {
          if (!p || typeof p !== 'object') return null
          const member = (p as Record<string, unknown>).member
          return member && typeof member === 'object' && typeof (member as Record<string, unknown>).display === 'string' ? (member as Record<string, unknown>).display as string : null
        }).filter(Boolean).join(', ')
      : null
    return { title: name ?? 'Care team', details: [status, participants ? `Members: ${participants}` : null].filter(Boolean) as string[] }
  }

  if (resourceType === 'Coverage') {
    const payor = Array.isArray(resource.payor) ? resource.payor.map((p) => (p && typeof p === 'object' && typeof (p as Record<string, unknown>).display === 'string' ? (p as Record<string, unknown>).display as string : null)).filter(Boolean).join(', ') : null
    const status = typeof resource.status === 'string' ? resource.status : null
    const subscriberId = typeof resource.subscriberId === 'string' ? `Subscriber ID: ${resource.subscriberId}` : null
    const relationship = codedText(resource.relationship)
    return { title: payor ?? 'Insurance coverage', details: [status, subscriberId, relationship ? `Relationship: ${relationship}` : null].filter(Boolean) as string[] }
  }

  if (resourceType === 'Goal') {
    const description = codedText(resource.description) ?? (typeof resource.description === 'string' ? resource.description : null)
    const lifecycleStatus = typeof resource.lifecycleStatus === 'string' ? resource.lifecycleStatus : null
    const startDate = typeof resource.startDate === 'string' ? `Start: ${dateLabel(resource.startDate)}` : null
    const targetDate = Array.isArray(resource.target) ? resource.target.map((t) => (t && typeof t === 'object' && typeof (t as Record<string, unknown>).dueDate === 'string' ? `Target: ${dateLabel((t as Record<string, unknown>).dueDate as string)}` : null)).filter(Boolean).join(', ') : null
    return { title: description ?? 'Health goal', details: [lifecycleStatus, startDate, targetDate].filter(Boolean) as string[] }
  }

  if (resourceType === 'Device') {
    const deviceName = Array.isArray(resource.deviceName) ? resource.deviceName.map((d) => (d && typeof d === 'object' && typeof (d as Record<string, unknown>).name === 'string' ? (d as Record<string, unknown>).name as string : null)).filter(Boolean)[0] : null
    const type = codedText(resource.type)
    const status = typeof resource.status === 'string' ? resource.status : null
    const manufacturer = typeof resource.manufacturer === 'string' ? `Manufacturer: ${resource.manufacturer}` : null
    const model = typeof resource.modelNumber === 'string' ? `Model: ${resource.modelNumber}` : null
    return { title: deviceName ?? type ?? 'Medical device', details: [status, manufacturer, model].filter(Boolean) as string[] }
  }

  if (resourceType === 'DocumentReference') {
    const content = Array.isArray(resource.content) ? resource.content : []
    const attachment = content.find((item) => item && typeof item === 'object' && 'attachment' in item)
    const title = attachment && typeof attachment === 'object' && 'attachment' in attachment && attachment.attachment && typeof attachment.attachment === 'object'
      ? (attachment.attachment as { title?: unknown }).title : null
    const docType = codedText(resource.type)
    const desc = typeof resource.description === 'string' ? resource.description : null
    const docDate = typeof resource.date === 'string' ? dateLabel(resource.date) : null
    return {
      title: docType ?? desc ?? (typeof title === 'string' ? title : null) ?? 'Clinical document',
      details: [docDate, desc && docType ? desc : null].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Patient') {
    const names = Array.isArray(resource.name) ? resource.name : []
    const firstName = names.find((name) => name && typeof name === 'object') as { text?: unknown; given?: unknown; family?: unknown } | undefined
    const name = typeof firstName?.text === 'string' ? firstName.text : [
      ...(Array.isArray(firstName?.given) ? firstName.given.filter((part): part is string => typeof part === 'string') : []),
      typeof firstName?.family === 'string' ? firstName.family : '',
    ].filter(Boolean).join(' ')
    const birthDate = typeof resource.birthDate === 'string' ? `Born: ${dateLabel(resource.birthDate)}` : null
    const gender = typeof resource.gender === 'string' ? `Sex: ${resource.gender}` : null
    return { title: name || 'Patient', details: [birthDate, gender].filter(Boolean) as string[] }
  }

  // Generic fallback
  const fallbackName = codedText(resource.code) ?? (typeof resource.description === 'string' ? resource.description : null) ?? (typeof resource.status === 'string' ? resource.status : null)
  return { title: fallbackName ?? 'Clinical data retrieved', details: [] }
}

/** Legacy one-line summary kept for eCW review list compatibility. */
function epicResourceSummary(resource: Record<string, unknown>): string {
  const detail = epicResourceDetail(resource)
  return detail.details.length ? `${detail.title} · ${detail.details.join(' · ')}` : detail.title
}

function hasEpicPdfLink(resource: Record<string, unknown>): boolean {
  if (resource.resourceType !== 'DocumentReference' || !Array.isArray(resource.content)) return false
  return resource.content.some((item) => {
    if (!item || typeof item !== 'object' || !('attachment' in item) || !item.attachment || typeof item.attachment !== 'object') return false
    const attachment = item.attachment as { contentType?: unknown; url?: unknown }
    return typeof attachment.url === 'string' && attachment.url.trim().length > 0 && String(attachment.contentType ?? '').toLowerCase().split(';', 1)[0] === 'application/pdf'
  })
}

function epicAvailabilityMessage(availability: Record<string, string>): string {
  const unavailable = Object.entries(availability)
    .filter(([, value]) => value !== 'ok')
    .map(([type, value]) => {
      if (value === 'empty') return `${type}: Epic returned no records`
      if (value === 'forbidden') return `${type}: access not granted`
      if (value === 'unavailable') return `${type}: not available from this hospital`
      return `${type}: ${value}`
    })
  return unavailable.join(' · ') || 'All requested supported resource families responded.'
}

export default function PatientPortal({ onBack }: PatientPortalProps) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [patientId, setPatientId] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [session, setSession] = useState<PatientSession | null>(null)
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
  const [busy, setBusy] = useState<'auth' | 'profile' | 'upload' | 'record' | 'share' | 'download' | 'delete' | 'bulk-download' | 'bulk-delete' | 'import' | 'drive' | 'google' | 'epic-connect' | 'epic-load' | 'epic-document' | 'ecw-connect' | 'ecw-load' | 'ecw-import' | 'ecw-disconnect' | null>(null)
  const [restoringSession, setRestoringSession] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const workspacePanel = useRef<HTMLDivElement>(null)
  const epicPopup = useRef<Window | null>(null)
  const epicPopupWatch = useRef<number | null>(null)
  const epicConnectionHandled = useRef(false)
  const ecwPopup = useRef<Window | null>(null)
  const ecwPopupWatch = useRef<number | null>(null)
  const ecwConnectionHandled = useRef(false)

  const applyProfile = (nextProfile: PatientProfile) => {
    setProfile(nextProfile)
    setProfileForm(profileFormFrom(nextProfile))
  }

  useEffect(() => {
    let active = true
    void getCurrentPatientSession()
      .then((workspace) => {
        if (active) {
          setSession(workspace)
          void getPatientProfile()
            .then((currentProfile) => { if (active) applyProfile(currentProfile) })
            .catch((caught) => { if (active) setError(messageFrom(caught, 'Unable to load your demographic profile.')) })
          setNotice(workspace.drive_sync_message ?? 'Your Google Drive workspace is ready.')
          if (new URLSearchParams(window.location.search).get('epic') === 'connected') {
            void getEpicRecord().then((record) => {
              if (!active) return
              setEpicRecord(record)
              setNotice(`Live Epic record connected from ${record.source.organization_name}.`)
            }).catch((caught) => { if (active) setError(messageFrom(caught, 'Epic connected, but the live record could not be loaded.')) })
            window.history.replaceState({}, '', window.location.pathname)
          }
        }
      })
      .catch((caught) => {
        if (active && !(caught instanceof ApiError && caught.status === 401)) {
          setError(messageFrom(caught, 'Unable to restore your workspace.'))
        }
      })
      .finally(() => { if (active) setRestoringSession(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    const panel = workspacePanel.current
    if (!panel || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const animation = animate(Array.from(panel.querySelectorAll<HTMLElement>('[data-workspace-panel]:not([hidden])')), {
      opacity: [0, 1],
      translateY: [10, 0],
      duration: 240,
      delay: stagger(45),
      ease: 'outQuad',
    })
    return () => { animation.cancel() }
  }, [workspaceTab, session?.patient_id])

  useEffect(() => {
    const stopWatchingPopup = () => {
      if (ecwPopupWatch.current !== null) window.clearInterval(ecwPopupWatch.current)
      ecwPopupWatch.current = null
      ecwPopup.current = null
    }
    const onMessage = (event: MessageEvent<unknown>) => {
      if (event.origin !== window.location.origin || !event.data || typeof event.data !== 'object' || !('type' in event.data)) return
      const type = (event.data as { type?: unknown }).type
      if (type !== 'clinicforce:ecw-connected' && type !== 'clinicforce:ecw-not-connected') return
      stopWatchingPopup()
      setBusy(null)
      ecwConnectionHandled.current = true
      if (type === 'clinicforce:ecw-not-connected') {
        setError('eClinicalWorks did not complete authorization. Check the six-character practice code and try again.')
        return
      }
      setBusy('ecw-load')
      void getEcwRecord()
        .then((record) => {
          setEcwRecord(record)
          setError('')
          setNotice(`eClinicalWorks data is ready to review from ${record.source.organization_name}. Nothing has been imported yet.`)
        })
        .catch((caught) => {
          setEcwRecord(null)
          setError(messageFrom(caught, 'eClinicalWorks connected, but the authorized data could not be loaded. Reconnect healow and try again.'))
        })
        .finally(() => setBusy(null))
    }
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('message', onMessage)
      stopWatchingPopup()
    }
  }, [])

  useEffect(() => {
    const query = epicQuery.trim()
    if (!session || query.length < 2) return

    let active = true
    const timer = window.setTimeout(() => {
      setEpicSearching(true)
      setEpicDirectoryError('')
      void searchEpicEndpoints(query)
        .then((endpoints) => {
          if (!active) return
          setEpicEndpoints(endpoints)
          setSelectedEpicEndpointId((current) => endpoints.some((endpoint) => endpoint.id === current) ? current : '')
        })
        .catch((caught) => {
          if (!active) return
          setEpicEndpoints([])
          setSelectedEpicEndpointId('')
          setEpicDirectoryError(messageFrom(caught, 'Unable to load Epic hospital suggestions.'))
        })
        .finally(() => { if (active) setEpicSearching(false) })
    }, 250)
    return () => { active = false; window.clearTimeout(timer) }
  }, [epicQuery, session])

  useEffect(() => {
    const stopWatchingPopup = () => {
      if (epicPopupWatch.current !== null) window.clearInterval(epicPopupWatch.current)
      epicPopupWatch.current = null
      epicPopup.current = null
    }
    const onMessage = (event: MessageEvent<unknown>) => {
      if (event.origin !== window.location.origin || !event.data || typeof event.data !== 'object' || !('type' in event.data)) return
      const type = (event.data as { type?: unknown }).type
      if (type !== 'clinicforce:epic-connected' && type !== 'clinicforce:epic-not-connected') return
      stopWatchingPopup()
      setBusy(null)
      if (type === 'clinicforce:epic-not-connected') {
        epicConnectionHandled.current = true
        setError('Epic did not complete authorization. If Epic showed an OAuth2 error before its sign-in page, the hospital has not activated ClinicForce yet—try again after activation completes. Otherwise, complete or retry MyChart sign-in. No Epic data was imported.')
        return
      }
      epicConnectionHandled.current = true
      void getEpicRecord().then((record) => {
        setEpicRecord(record)
        setError('')
        setNotice(`Live Epic record connected from ${record.source.organization_name}.`)
      }).catch((caught) => setError(messageFrom(caught, 'Epic connected, but the live record could not be loaded.')))
    }
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('message', onMessage)
      stopWatchingPopup()
    }
  }, [])

  const setField = (key: keyof FormState, value: string) => setForm((current) => ({ ...current, [key]: value }))
  const clearFeedback = () => { setError(''); setNotice('') }

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
      const workspace = mode === 'login' ? await loginPatient(normalizedId, password) : await registerPatient(normalizedId, password)
      setSession(workspace)
      applyProfile(await getPatientProfile())
      setNotice(mode === 'login' ? 'Your workspace is ready.' : 'Your account is ready. Add a report or health detail to get started.')
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
      setNotice(`${files.length === 1 ? 'Report' : `${files.length} reports`} added to ${session.storage_provider === 'google_drive' ? 'your Google Drive folder' : 'your secure workspace'}.`)
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
      setNotice(storageProvider === 'google_drive' ? 'New reports will be stored in your ClinicForce Google Drive folder.' : 'New reports will be stored in ClinicForce secure storage.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to change document storage.'))
    } finally {
      setBusy(null)
    }
  }

  const recordPayload = (): ManualRecordInput | null => {
    const trimmed = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value.trim()])) as FormState
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
      setError('Enter your legal first name, legal last name, and date of birth before creating an EHR QR code.')
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
      document.getElementById('demographics-heading')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      return
    }
    if (shareMode === 'standard' && !/^\d{4}$/.test(sharePasscode)) {
      setError('Choose a four-digit passcode for the clinician.')
      return
    }
    if (!session.documents.length && !session.records.length && !epicRecord?.bundle.entry.length) {
      setError('Add a PDF report or health detail, or connect MyChart, before creating a share link.')
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
      setNotice(shareMode === 'ecw_compat'
        ? 'A 15-minute, no-passcode eClinicalMobile compatibility QR is ready for a live scan test.'
        : nextLink.epic_included
        ? 'One QR code is ready with your workspace and the current MyChart record.'
        : 'One QR code is ready with the health information in your workspace.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to generate the secure link.'))
    } finally {
      setBusy(null)
    }
  }

  const chooseEpicHospital = (value: string) => {
    const normalized = value.trim().toLocaleLowerCase()
    const endpoint = epicEndpoints.find((candidate) => candidate.name.toLocaleLowerCase() === normalized)
    setEpicQuery(value)
    setSelectedEpicEndpointId(endpoint?.id ?? '')
    setEpicDirectoryError('')
    if (normalized.length < 2) {
      setEpicEndpoints([])
      setEpicSearching(false)
    }
  }

  const selectEpicHospital = (endpoint: EpicEndpoint) => {
    setEpicQuery(endpoint.name)
    setSelectedEpicEndpointId(endpoint.id)
    setEpicEndpoints([])
    setEpicDirectoryError('')
  }

  const connectEpic = async (endpoint: EpicEndpoint) => {
    if (!session) return
    clearFeedback()
    const popup = window.open('', 'clinicforce-epic-authorize', 'popup,width=540,height=760')
    if (!popup) {
      setError('Your browser blocked the secure MyChart window. Allow pop-ups for ClinicForce and try again.')
      return
    }
    epicPopup.current = popup
    epicConnectionHandled.current = false
    setBusy('epic-connect')
    try {
      popup.location.replace(await beginEpicAuthorization(session.patient_id, password, endpoint.id, true))
      epicPopupWatch.current = window.setInterval(() => {
        if (!popup.closed) return
        if (epicPopupWatch.current !== null) window.clearInterval(epicPopupWatch.current)
        epicPopupWatch.current = null
        epicPopup.current = null
        if (epicConnectionHandled.current) return
        // A popup may close before its postMessage reaches the opener. Verify
        // the server-side, HttpOnly Epic session before treating that close as
        // a failed authorization; this prevents a successful sign-in from
        // leaving a false red error banner behind.
        epicConnectionHandled.current = true
        setBusy('epic-load')
        void getEpicRecord()
          .then((record) => {
            setEpicRecord(record)
            setError('')
            setNotice(`Live Epic record connected from ${record.source.organization_name}.`)
          })
          .catch(() => setError('Epic authorization was not completed. Close the MyChart window only after sign-in finishes, then try again.'))
          .finally(() => setBusy(null))
      }, 500)
    } catch (caught) {
      popup.close()
      epicPopup.current = null
      setBusy(null)
      setError(messageFrom(caught, 'Unable to start the Epic connection.'))
    }
  }

  const validateEcwPracticeCode = (value: string): string | null => {
    const normalized = value.trim().toUpperCase()
    return /^[A-Z0-9]{6}$/.test(normalized) ? null : 'Enter the six-character eClinicalWorks practice code (letters and numbers only).'
  }

  const connectEcw = async () => {
    if (!session) return
    const normalizedCode = ecwPracticeCode.trim().toUpperCase()
    const validationError = validateEcwPracticeCode(normalizedCode)
    setEcwPracticeCode(normalizedCode)
    setEcwPracticeCodeError(validationError ?? '')
    if (validationError) return
    clearFeedback()
    const popup = window.open('', 'clinicforce-ecw-authorize', 'popup,width=540,height=760')
    if (!popup) {
      setError('Your browser blocked the secure eClinicalWorks window. Allow pop-ups for ClinicForce and try again.')
      return
    }
    ecwPopup.current = popup
    ecwConnectionHandled.current = false
    setBusy('ecw-connect')
    try {
      popup.location.replace(await beginEcwAuthorization(session.patient_id, password, normalizedCode, true))
      ecwPopupWatch.current = window.setInterval(() => {
        if (!popup.closed) return
        if (ecwPopupWatch.current !== null) window.clearInterval(ecwPopupWatch.current)
        ecwPopupWatch.current = null
        ecwPopup.current = null
        if (ecwConnectionHandled.current) return
        ecwConnectionHandled.current = true
        // A secure callback can close before its postMessage reaches this
        // window. Ask the HttpOnly session endpoint once before calling the
        // authorization unsuccessful, as we do for Epic.
        setBusy('ecw-load')
        void getEcwRecord()
          .then((record) => {
            setEcwRecord(record)
            setError('')
            setNotice(`eClinicalWorks data is ready to review from ${record.source.organization_name}. Nothing has been imported yet.`)
          })
          .catch(() => setError('eClinicalWorks authorization was not completed. Close the healow window only after sign-in finishes, then try again.'))
          .finally(() => setBusy(null))
      }, 500)
    } catch (caught) {
      popup.close()
      ecwPopup.current = null
      setBusy(null)
      setError(messageFrom(caught, 'Unable to start the eClinicalWorks connection.'))
    }
  }

  const cancelEpicConnection = () => {
    epicPopup.current?.close()
    if (epicPopupWatch.current !== null) window.clearInterval(epicPopupWatch.current)
    epicPopup.current = null
    epicPopupWatch.current = null
    epicConnectionHandled.current = true
    setBusy(null)
    setNotice('Epic connection cancelled. No Epic data was imported.')
  }

  const importConfirmedEcw = async () => {
    clearFeedback()
    setBusy('ecw-import')
    try {
      const workspace = await importEcwRecord()
      setSession(workspace)
      setEcwRecord(null)
      setNotice('Your confirmed eClinicalWorks data was imported with its eCW/healow source label. The live connection has been closed.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to import the reviewed eClinicalWorks data. Reconnect healow and try again.'))
    } finally {
      setBusy(null)
    }
  }

  const discardEcwConnection = async () => {
    clearFeedback()
    setBusy('ecw-disconnect')
    try {
      await disconnectEcw()
      setEcwRecord(null)
      setNotice('eClinicalWorks disconnected. No reviewed data was imported.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to discard the eClinicalWorks connection.'))
    } finally {
      setBusy(null)
    }
  }

  const refreshEpic = async () => {
    clearFeedback()
    setBusy('epic-load')
    try {
      const record = await getEpicRecord()
      setEpicRecord(record)
      setNotice(`Live Epic data refreshed from ${record.source.organization_name}.`)
    } catch (caught) {
      setEpicRecord(null)
      setError(messageFrom(caught, 'Your Epic session needs to be reconnected.'))
    } finally {
      setBusy(null)
    }
  }

  const endEpicSession = async () => {
    clearFeedback()
    try {
      await disconnectEpic()
      setEpicRecord(null)
      setEpicEndpoints([])
      setEpicQuery('')
      setSelectedEpicEndpointId('')
      setEpicDirectoryError('')
      setNotice('Epic disconnected. ClinicForce no longer has live access to this record.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to end the Epic connection.'))
    }
  }

  const openEpicDocument = async (documentId: string) => {
    clearFeedback()
    setBusy('epic-document')
    try {
      const url = URL.createObjectURL(await downloadEpicDocument(documentId))
      window.open(url, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to open this live Epic PDF.'))
    } finally {
      setBusy(null)
    }
  }

  const openDocument = async (document: PatientDocument) => {
    if (!session) return
    clearFeedback()
    setBusy('download')
    try {
      const url = URL.createObjectURL(await downloadPatientDocument(session.patient_id, password, document.id))
      window.open(url, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to open this report.'))
    } finally {
      setBusy(null)
    }
  }

  const importDriveChanges = async () => {
    if (!session) return
    clearFeedback()
    setBusy('import')
    try {
      const workspace = await importGoogleDriveChanges(session.patient_id, password)
      setSession(workspace)
      setNotice(workspace.drive_sync_message ?? 'Drive changes were imported securely.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to import the detected Drive files.'))
    } finally {
      setBusy(null)
    }
  }

  const deleteDocument = async (document: PatientDocument) => {
    if (!session || !window.confirm(`Delete ${document.filename}? This removes it from ClinicForce and its selected storage.`)) return
    clearFeedback()
    setBusy('delete')
    try {
      const workspace = await deletePatientDocument(session.patient_id, password, document.id)
      setSession(workspace)
      setSelectedDocumentIds((current) => {
        const next = new Set(current)
        next.delete(document.id)
        return next
      })
      setNotice(workspace.drive_sync_message ?? 'Report deleted.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to delete this report.'))
    } finally {
      setBusy(null)
    }
  }

  const toggleDocumentSelection = (documentId: number) => {
    setSelectedDocumentIds((current) => {
      const next = new Set(current)
      if (next.has(documentId)) next.delete(documentId)
      else next.add(documentId)
      return next
    })
  }

  const toggleAllDocumentSelection = () => {
    if (!session) return
    setSelectedDocumentIds((current) => current.size === session.documents.length
      ? new Set()
      : new Set(session.documents.map((document) => document.id)))
  }

  const downloadSelectedDocuments = async () => {
    if (!session || selectedDocumentIds.size === 0) return
    clearFeedback()
    setBusy('bulk-download')
    try {
      const archive = await downloadPatientDocumentsArchive(session.patient_id, password, Array.from(selectedDocumentIds))
      const url = URL.createObjectURL(archive)
      const linkElement = document.createElement('a')
      linkElement.href = url
      linkElement.download = 'clinicforce-reports.zip'
      document.body.appendChild(linkElement)
      linkElement.click()
      linkElement.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
      setNotice(`${selectedDocumentIds.size} selected report${selectedDocumentIds.size === 1 ? '' : 's'} downloaded as a ZIP file.`)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to download the selected reports.'))
    } finally {
      setBusy(null)
    }
  }

  const deleteSelectedDocuments = async () => {
    if (!session || selectedDocumentIds.size === 0) return
    const count = selectedDocumentIds.size
    if (!window.confirm(`Delete ${count} selected report${count === 1 ? '' : 's'}? This removes them from ClinicForce and their selected storage.`)) return
    clearFeedback()
    setBusy('bulk-delete')
    try {
      const workspace = await deletePatientDocuments(session.patient_id, password, Array.from(selectedDocumentIds))
      setSession(workspace)
      setSelectedDocumentIds(new Set())
      setNotice(workspace.drive_sync_message ?? 'Selected reports deleted.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to delete the selected reports.'))
    } finally {
      setBusy(null)
    }
  }

  const signOut = () => {
    void logoutPatient()
    setSession(null)
    setPassword('')
    setFiles([])
    setLink(null)
    setProfile(null)
    setProfileForm(emptyProfileForm)
    setEpicRecord(null)
    setEcwRecord(null)
    setSelectedDocumentIds(new Set())
    setSharePasscode('')
    clearFeedback()
  }

  if (!session) {
    return (
      <section className="auth-layout animate-enter" aria-labelledby="patient-heading">
        <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to home</button>
        <div className="auth-aside">
          <p className="eyebrow"><ShieldCheck aria-hidden="true" className="size-4" /> Patient workspace</p>
          <h1 id="patient-heading">Build a record you can carry into care.</h1>
          <p className="lead-small">Add source reports and the health details you know. When you are ready, create a QR code for your clinician to scan.</p>
          <div className="auth-note"><LockKeyhole aria-hidden="true" /><p><strong>You remain in control.</strong><br />Share only after you choose a passcode and show the QR code.</p></div>
        </div>
        <div className="panel auth-panel">
          <div className="mode-tabs" role="tablist" aria-label="Patient account options">
            <button className={mode === 'login' ? 'active' : ''} role="tab" aria-selected={mode === 'login'} type="button" onClick={() => { setMode('login'); clearFeedback() }}>Sign in</button>
            <button className={mode === 'register' ? 'active' : ''} role="tab" aria-selected={mode === 'register'} type="button" onClick={() => { setMode('register'); clearFeedback() }}>Create account</button>
          </div>
          <h2>{mode === 'login' ? 'Welcome back' : 'Create your workspace'}</h2>
          <p className="text-sm leading-6 text-[var(--muted-ink)]">{mode === 'login' ? 'Enter your account details to open your health record.' : 'Use a memorable patient ID and a strong password.'}</p>
          {restoringSession ? <div className="status-panel"><LoaderCircle className="size-5 animate-spin" aria-hidden="true" /><p>Opening your secure workspace…</p></div> : <form className="stack-form" onSubmit={authenticate} noValidate>
            <label htmlFor="patient-id">Patient ID</label>
            <input id="patient-id" autoComplete="username" value={patientId} onChange={(event) => setPatientId(event.target.value)} placeholder="e.g. alex-morgan" />
            <label htmlFor="patient-password">Password</label>
            <div className="password-field">
              <input id="patient-password" type={showPassword ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={(event) => setPassword(event.target.value)} aria-describedby={mode === 'register' ? 'password-help' : undefined} />
              <button className="icon-button" type="button" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}</button>
            </div>
            {mode === 'register' && <p id="password-help" className="field-help">Use at least 12 characters.</p>}
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="button button-primary w-full" type="submit" disabled={busy === 'auth'}>{busy === 'auth' ? <><LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> Opening workspace</> : <>{mode === 'login' ? 'Open my workspace' : 'Create my workspace'} <ChevronRight aria-hidden="true" className="size-4" /></>}</button>
          </form>}
          {!restoringSession && <><div className="divider"><span>or</span></div><button className="button google-signin-button w-full" type="button" disabled={busy === 'google'} onClick={() => void signInWithGoogle()}>{busy === 'google' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <ProviderLogo provider="google" />} Continue with Google</button><p className="field-help">Google sign-in opens your patient workspace and stores new PDFs in your ClinicForce Drive folder.</p></>}
        </div>
      </section>
    )
  }

  const recordsAvailable = session.documents.length + session.records.length
  const needsName = category !== 'hospital'
  const needsResult = category === 'lab' || category === 'vital'
  const epicResources = (epicRecord?.bundle.entry ?? []).map((entry) => entry.resource).filter(Boolean)
  const epicCounts = epicResources.reduce<Record<string, number>>((counts, resource) => {
    const type = typeof resource.resourceType === 'string' ? resource.resourceType : 'FHIR resource'
    counts[type] = (counts[type] ?? 0) + 1
    return counts
  }, {})
  const ecwResources = (ecwRecord?.bundle.entry ?? []).map((entry) => entry.resource).filter(Boolean)
  const ecwCounts = ecwResources.reduce<Record<string, number>>((counts, resource) => {
    const type = typeof resource.resourceType === 'string' ? resource.resourceType : 'FHIR resource'
    counts[type] = (counts[type] ?? 0) + 1
    return counts
  }, {})
  const ecwUnavailable = Object.entries(ecwRecord?.availability ?? {})
    .filter(([, result]) => result !== 'ok')
    .map(([resourceType, result]) => {
      if (result === 'empty') return `${resourceType}: no records returned`
      if (result === 'forbidden') return `${resourceType}: permission was not granted`
      if (result === 'unavailable') return `${resourceType}: not available from this practice`
      return `${resourceType}: ${result}`
    })
  const ecwImportableCount = ecwResources.filter((resource) => ecwImportableResourceTypes.has(String(resource.resourceType))).length
  const canCreateShare = recordsAvailable > 0 || epicResources.length > 0
  const demographicProfileComplete = profileIsComplete(profile)
  const selectedEpicEndpoint = epicEndpoints.find((endpoint) => endpoint.id === selectedEpicEndpointId)

  return (
    <section className="workspace animate-enter" aria-labelledby="workspace-heading">
      <div className="workspace-banner">
        <div className="avatar" aria-hidden="true">{patientInitials(session.patient_id)}</div>
        <div><p className="eyebrow">Patient workspace</p><h1 id="workspace-heading">Your health record</h1><p>Signed in as <strong>{session.patient_id}</strong></p></div>
        <button className="button button-ghost signout-button" type="button" onClick={signOut}><LogOut aria-hidden="true" className="size-4" /> Sign out</button>
      </div>

      {(error || notice) && <div className={error ? 'feedback feedback-error' : 'feedback feedback-success'} role={error ? 'alert' : 'status'}>{error ? <X aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}<span>{error || notice}</span><button type="button" onClick={clearFeedback} aria-label="Dismiss message"><X aria-hidden="true" className="size-4" /></button></div>}

      <div className="workspace-stats" aria-label="Workspace summary">
        <div><FileText aria-hidden="true" /><span><strong>{session.documents.length}</strong> report{session.documents.length === 1 ? '' : 's'} stored</span></div>
        <div><ClipboardPlus aria-hidden="true" /><span><strong>{session.records.length}</strong> health detail{session.records.length === 1 ? '' : 's'} added</span></div>
        <div><ShieldCheck aria-hidden="true" /><span>{recordsAvailable ? 'Ready to share' : 'Add your first item'}</span></div>
      </div>

      <div className="workspace-grid" ref={workspacePanel}>
        <nav className="workspace-tabs" aria-label="Health record sections">
          <button className={workspaceTab === 'overview' ? 'active' : ''} aria-pressed={workspaceTab === 'overview'} type="button" onClick={() => setWorkspaceTab('overview')}><UserRound aria-hidden="true" /><span>Profile & sharing</span></button>
          <button className={workspaceTab === 'records' ? 'active' : ''} aria-pressed={workspaceTab === 'records'} type="button" onClick={() => setWorkspaceTab('records')}><FileText aria-hidden="true" /><span>Reports & details</span></button>
          <button className={workspaceTab === 'connections' ? 'active' : ''} aria-pressed={workspaceTab === 'connections'} type="button" onClick={() => setWorkspaceTab('connections')}><Building2 aria-hidden="true" /><span>Care connections</span></button>
        </nav>
        <p className="workspace-tabs-helper" role="status">
          {workspaceTab === 'overview' && <><strong>Start here.</strong> Confirm your profile, then share.</>}
          {workspaceTab === 'records' && <><strong>Add information.</strong> Upload a report or add a detail.</>}
          {workspaceTab === 'connections' && <><strong>Connect care.</strong> Authorize in a secure window.</>}
        </p>
        <div className="workspace-main">
          <section className="section-card demographics-card" data-workspace-panel aria-labelledby="demographics-heading" hidden={workspaceTab !== 'overview'}>
            <div className="section-heading"><div><p className="section-kicker">Required for EHR QR sharing</p><h2 id="demographics-heading">Confirm your demographics</h2></div><UserRound aria-hidden="true" className="section-icon" /></div>
            <p className="demographics-intro">Receiving EHRs use these details to match your QR to the right chart. Google profile names, when available, are only suggestions—confirm your legal name before sharing.</p>
            <form className="demographics-form" onSubmit={saveProfile} noValidate>
              <div className="form-grid">
                <div className="field"><label htmlFor="given-name">Legal first name <em>Required</em></label><input id="given-name" autoComplete="given-name" value={profileForm.given_name} onChange={(event) => setProfileForm((current) => ({ ...current, given_name: event.target.value }))} /></div>
                <div className="field"><label htmlFor="family-name">Legal last name <em>Required</em></label><input id="family-name" autoComplete="family-name" value={profileForm.family_name} onChange={(event) => setProfileForm((current) => ({ ...current, family_name: event.target.value }))} /></div>
                <div className="field"><label htmlFor="birth-date">Date of birth <em>Required</em></label><input id="birth-date" type="date" autoComplete="bday" value={profileForm.birth_date} onChange={(event) => setProfileForm((current) => ({ ...current, birth_date: event.target.value }))} /></div>
                <div className="field"><label htmlFor="administrative-gender">Administrative sex/gender <em>Required</em></label><select id="administrative-gender" autoComplete="sex" value={profileForm.administrative_gender} onChange={(event) => setProfileForm((current) => ({ ...current, administrative_gender: event.target.value as ProfileFormState['administrative_gender'] }))}><option value="female">Female</option><option value="male">Male</option><option value="other">Other</option><option value="unknown">Unknown</option></select></div>
              </div>
              <div className="demographics-actions"><button className="button button-secondary" type="submit" disabled={busy === 'profile'}>{busy === 'profile' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <ShieldCheck className="size-4" aria-hidden="true" />}{demographicProfileComplete ? 'Update demographic profile' : 'Save demographic profile'}</button>{demographicProfileComplete && <span className="demographics-complete"><CheckCircle2 aria-hidden="true" /> Ready for EHR QR sharing</span>}</div>
            </form>
          </section>

          <section className="section-card" data-workspace-panel aria-labelledby="epic-heading" hidden={workspaceTab !== 'connections'}>
            <div className="section-heading"><div><p className="section-kicker">Live patient-authorized connection</p><h2 id="epic-heading">Epic / MyChart</h2></div><div className="provider-lockup"><ProviderLogo provider="mychart" /></div></div>
            {!epicRecord ? <>
              <p className="text-sm leading-6 text-[var(--muted-ink)]">Connect a hospital you have used to view its current Epic record in this browser. ClinicForce does not save Epic records, access tokens, or attached PDFs.</p>
              <div className="storage-choice mt-4"><div className="flex-1"><label htmlFor="epic-hospital"><strong>Hospital or health system</strong></label><input id="epic-hospital" value={epicQuery} onChange={(event) => chooseEpicHospital(event.target.value)} placeholder="Start typing, e.g. Houston Methodist" autoComplete="organization" aria-describedby="epic-hospital-help epic-hospital-options" /><div id="epic-hospital-options" className="epic-endpoint-options" role="listbox" aria-label="Matching hospitals">{epicEndpoints.map((endpoint) => <button key={endpoint.id} className="epic-endpoint-option" role="option" type="button" aria-selected={endpoint.id === selectedEpicEndpointId} onClick={() => selectEpicHospital(endpoint)}><strong>{endpoint.name}</strong>{endpoint.aliases.length > 0 && <span>{endpoint.aliases.slice(0, 2).join(' · ')}</span>}</button>)}</div><p id="epic-hospital-help" className="field-help mt-2" aria-live="polite">{epicSearching ? 'Looking up Epic hospitals…' : selectedEpicEndpoint ? `Selected: ${selectedEpicEndpoint.name}. Continue to MyChart to authorize ClinicForce.` : epicDirectoryError || (epicQuery.trim().length >= 2 && epicEndpoints.length === 0 ? 'No matching Epic hospital found. Try the hospital or health-system name.' : 'Type at least two letters, then select a hospital.')}</p></div><button className="button button-primary" type="button" disabled={!selectedEpicEndpoint || busy === 'epic-connect'} onClick={() => selectedEpicEndpoint && void connectEpic(selectedEpicEndpoint)}>{busy === 'epic-connect' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : 'Continue to MyChart'}</button></div>
              {busy === 'epic-connect' && <div className="feedback feedback-success mt-3" role="status"><LoaderCircle className="size-4 animate-spin" aria-hidden="true" /><span>MyChart opened in a secure window. Complete sign-in there. If Epic shows an error, close that window or cancel here to return safely.</span><button className="button button-secondary" type="button" onClick={cancelEpicConnection}>Cancel connection</button></div>}
            </> : <>
              <div className="storage-choice mt-4"><div><strong>Live from {epicRecord.source.organization_name}</strong><p>Retrieved {dateLabel(epicRecord.source.retrieved_at)}. This connection ends when you disconnect, its token expires, or the service restarts.</p></div><div className="storage-actions"><button className="button button-secondary" type="button" onClick={() => void refreshEpic()} disabled={busy === 'epic-load'}>{busy === 'epic-load' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="size-4" aria-hidden="true" />} Refresh</button><button className="button button-secondary" type="button" onClick={() => void endEpicSession()}>Disconnect</button></div></div>
              <div className="workspace-stats mt-4" aria-label="Live Epic data counts">{Object.entries(epicCounts).map(([type, count]) => <div key={type}><FileText aria-hidden="true" /><span><strong>{count}</strong> {epicResourceGroupMeta[type]?.singular ?? type}{count !== 1 ? 's' : ''}</span></div>)}</div>
              <p className="field-help mt-2">Availability: {epicAvailabilityMessage(epicRecord.availability)}</p>
              {epicRecord.availability.Condition === 'empty' && <div className="feedback feedback-error mt-3" role="status"><ShieldAlert aria-hidden="true" /><span>Epic did not return any condition records. The hospital may need to authorize ClinicForce to read the problem list shown in MyChart.</span></div>}
              <div className="epic-grouped-records mt-4">
                {groupEpicResources(epicResources).map((group) => (
                  <details key={group.type} className="epic-resource-group" open>
                    <summary className="epic-group-header">
                      <span className="epic-group-label">{group.meta.label}</span>
                      <span className="epic-group-count">{group.resources.length}</span>
                    </summary>
                    <ul className="epic-resource-list">
                      {group.resources.map((resource, index) => {
                        const detail = epicResourceDetail(resource)
                        return (
                          <li key={`${resource.resourceType ?? 'resource'}-${resource.id ?? index}`} className="epic-resource-item">
                            <div className="epic-resource-content">
                              <strong className="epic-resource-title">{detail.title}</strong>
                              {detail.details.length > 0 && (
                                <div className="epic-resource-details">
                                  {detail.details.map((line, i) => <span key={i} className="epic-detail-tag">{line}</span>)}
                                </div>
                              )}
                              {resource.resourceType === 'DocumentReference' && typeof resource.id === 'string' && hasEpicPdfLink(resource) && (
                                <button className="button button-secondary mt-3" type="button" disabled={busy === 'epic-document'} onClick={() => void openEpicDocument(String(resource.id))}>Open source PDF</button>
                              )}
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                  </details>
                ))}
              </div>
            </>}
          </section>

          <section className="section-card" data-workspace-panel aria-labelledby="ecw-heading" hidden={workspaceTab !== 'connections'}>
            <div className="section-heading"><div><p className="section-kicker">Live patient-authorized connection</p><h2 id="ecw-heading">eClinicalWorks / healow</h2></div><div className="provider-lockup provider-lockup-dual"><ProviderLogo provider="ecw" /><ProviderLogo provider="healow" /></div></div>
            {!ecwRecord ? <>
              <p className="text-sm leading-6 text-[var(--muted-ink)]">Enter the six-character OEM code for the eClinicalWorks practice where you receive care. ClinicForce derives that practice’s FHIR endpoint and opens its secure healow sign-in; it never asks you for an eCW password.</p>
              <div className="storage-choice mt-4"><div className="flex-1"><label htmlFor="ecw-practice-code"><strong>eClinicalWorks practice OEM code</strong></label><input id="ecw-practice-code" value={ecwPracticeCode} onChange={(event) => { setEcwPracticeCode(event.target.value.toUpperCase()); if (ecwPracticeCodeError) setEcwPracticeCodeError('') }} onBlur={(event) => setEcwPracticeCodeError(validateEcwPracticeCode(event.target.value.trim().toUpperCase()) ?? '')} placeholder="e.g. BEDBAA" autoComplete="off" spellCheck="false" maxLength={6} aria-describedby="ecw-practice-code-help" aria-invalid={Boolean(ecwPracticeCodeError)} />{ecwPracticeCodeError ? <p id="ecw-practice-code-help" className="field-help mt-2" role="alert">{ecwPracticeCodeError}</p> : <p id="ecw-practice-code-help" className="field-help mt-2">This is supplied by the practice. It may contain letters, such as BEDBAA.</p>}</div><button className="button button-primary" type="button" disabled={busy === 'ecw-connect' || busy === 'ecw-load'} onClick={() => void connectEcw()}>{busy === 'ecw-connect' || busy === 'ecw-load' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : 'Continue to healow'}</button></div>
              {(busy === 'ecw-connect' || busy === 'ecw-load') && <div className="feedback feedback-success mt-3" role="status"><LoaderCircle className="size-4 animate-spin" aria-hidden="true" /><span>{busy === 'ecw-connect' ? 'healow opened in a secure window. Complete sign-in there, then return here.' : 'Retrieving the resource families healow approved for your review…'}</span></div>}
            </> : <>
              <div className="storage-choice mt-4"><div><strong>Review eClinicalWorks data before import</strong><p>Retrieved {dateLabel(ecwRecord.source.retrieved_at)} from practice {ecwRecord.source.practice_code}. Nothing is saved until you confirm below.</p></div><div className="storage-actions"><button className="button button-secondary" type="button" disabled={busy === 'ecw-disconnect' || busy === 'ecw-import'} onClick={() => void discardEcwConnection()}>{busy === 'ecw-disconnect' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : 'Discard connection'}</button><button className="button button-primary" type="button" disabled={busy === 'ecw-disconnect' || busy === 'ecw-import' || ecwImportableCount === 0} onClick={() => void importConfirmedEcw()}>{busy === 'ecw-import' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : `Import ${ecwImportableCount} confirmed resources`}</button></div></div>
              <div className="workspace-stats mt-4" aria-label="eClinicalWorks data counts">{Object.entries(ecwCounts).slice(0, 6).map(([type, count]) => <div key={type}><FileText aria-hidden="true" /><span><strong>{count}</strong> {type}</span></div>)}</div>
              <p className="field-help mt-3">Every imported clinical resource is labeled “eClinicalWorks / healow practice {ecwRecord.source.practice_code}”. Patient identity and Provenance entries remain in this review only; DocumentReference entries include metadata only, never PDF bytes or provider download URLs.</p>
              {ecwUnavailable.length > 0 && <div className="feedback feedback-error mt-3" role="status"><ShieldAlert aria-hidden="true" /><span>Some requested families could not be retrieved: {ecwUnavailable.join(' · ')}. You can import the displayed data, or ask the practice to grant the missing read permissions and reconnect.</span></div>}
              <section className="mt-4" aria-label="eClinicalWorks resource review"><p className="document-name">Review ({ecwResources.length} retrieved resources)</p><ul className="record-list mt-3">{ecwResources.slice(0, 20).map((resource, index) => <li key={`${resource.resourceType ?? 'resource'}-${resource.id ?? index}`}><div><strong>{String(resource.resourceType ?? 'FHIR resource')}</strong><p>{epicResourceSummary(resource)}</p></div></li>)}</ul>{ecwResources.length > 20 && <p className="field-help mt-2">Showing the first 20 resources. Confirming imports all {ecwImportableCount} supported clinical resources shown in the counts above.</p>}</section>
            </>}
          </section>

          <section className="section-card" data-workspace-panel aria-labelledby="reports-heading" hidden={workspaceTab !== 'records'}>
            <div className="section-heading"><div><p className="section-kicker">Source documents</p><h2 id="reports-heading">PDF reports</h2></div><FileText aria-hidden="true" className="section-icon" /></div>
            <div className="storage-choice" aria-label="Choose where new reports are stored">
              <div><strong>{session.storage_provider === 'google_drive' ? 'Your Google Drive is selected' : 'ClinicForce secure storage is selected'}</strong><p>{session.storage_provider === 'google_drive' ? 'Drive contains encrypted ClinicForce files. Open reports here to read them.' : 'New PDFs stay in ClinicForce encrypted document storage.'}</p></div>
              <div className="storage-actions">
                <button className="button button-secondary" type="button" disabled={busy === 'drive' || session.storage_provider === 's3'} onClick={() => void selectStorage('s3')}>Use ClinicForce storage</button>
                <button className="button button-secondary" type="button" disabled={busy === 'drive' || session.storage_provider === 'google_drive'} onClick={() => void selectStorage('google_drive')}><ProviderLogo provider="google" /> {session.drive_connected ? 'Use my Drive' : 'Connect Google Drive'}</button>
              </div>
            </div>
            {session.storage_provider === 'google_drive' && session.drive_access_upgrade_required && <div className="feedback feedback-error" role="status"><Cloud aria-hidden="true" /><span>Reconnect Google Drive to enable secure folder import. Your existing reports remain available.</span><button className="button button-secondary" type="button" disabled={busy === 'drive'} onClick={() => void connectGoogleDrive()}>Reconnect Drive</button></div>}
            {session.storage_provider === 'google_drive' && session.drive_pending_import_count > 0 && !session.drive_access_upgrade_required && <div className="feedback feedback-success" role="status"><ShieldCheck aria-hidden="true" /><span>{session.drive_pending_import_count} Drive PDF{session.drive_pending_import_count === 1 ? '' : 's'} need secure import. Plaintext originals will be replaced with encrypted ClinicForce files.</span><button className="button button-secondary" type="button" disabled={busy === 'import'} onClick={() => void importDriveChanges()}>{busy === 'import' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <LockKeyhole aria-hidden="true" className="size-4" />} Import securely</button></div>}
            {session.documents.length ? <>
              <div className="document-selection-toolbar">
                <label className="select-all-control">
                  <input
                    type="checkbox"
                    checked={selectedDocumentIds.size === session.documents.length}
                    onChange={toggleAllDocumentSelection}
                    disabled={busy === 'bulk-download' || busy === 'bulk-delete'}
                  />
                  <span>Select all reports</span>
                </label>
                {selectedDocumentIds.size > 0 && <div className="document-bulk-actions" role="status">
                  <span>{selectedDocumentIds.size} selected</span>
                  <button className="button button-secondary" type="button" onClick={() => void downloadSelectedDocuments()} disabled={busy === 'bulk-download' || busy === 'bulk-delete'}>
                    {busy === 'bulk-download' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Download className="size-4" aria-hidden="true" />} Download ZIP
                  </button>
                  <button className="button button-danger" type="button" onClick={() => void deleteSelectedDocuments()} disabled={busy === 'bulk-download' || busy === 'bulk-delete'}>
                    {busy === 'bulk-delete' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Trash2 className="size-4" aria-hidden="true" />} Delete
                  </button>
                </div>}
              </div>
              <ul className="item-list">{session.documents.map((document) => <li key={document.id}>
                <label className="document-select" aria-label={`Select ${document.filename}`}>
                  <input type="checkbox" checked={selectedDocumentIds.has(document.id)} onChange={() => toggleDocumentSelection(document.id)} disabled={busy === 'bulk-download' || busy === 'bulk-delete'} />
                </label>
                <div className="file-mark"><FileText aria-hidden="true" /></div>
                <div className="min-w-0 flex-1"><button type="button" className="document-name" onClick={() => void openDocument(document)} disabled={busy === 'download'}>{document.filename}</button><p>{dateLabel(document.uploaded_at)} · {document.resource_count} extracted item{document.resource_count === 1 ? '' : 's'}</p></div>
                <button className="item-action" type="button" onClick={() => void openDocument(document)} disabled={busy === 'download'} aria-label={`Open ${document.filename}`}>{busy === 'download' ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Eye aria-hidden="true" />}</button>
                <button className="item-action" type="button" onClick={() => void deleteDocument(document)} disabled={busy === 'delete'} aria-label={`Delete ${document.filename}`}>{busy === 'delete' ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}</button>
              </li>)}</ul>
            </> : <div className="empty-block"><FolderOpen aria-hidden="true" /><p><strong>No reports yet</strong><br />Upload a PDF to keep it with this workspace.</p></div>}
            <div className="upload-zone">
              <input ref={fileInput} id="medical-pdf" type="file" accept="application/pdf,.pdf" multiple onChange={(event) => chooseFiles(Array.from(event.target.files ?? []))} />
              <label htmlFor="medical-pdf"><Upload aria-hidden="true" /><span><strong>{session.storage_provider === 'google_drive' ? 'Upload to my Drive' : 'Choose PDF reports'}</strong><small>PDF only · up to 15 MB each</small></span></label>
              {files.length > 0 && <div className="selected-files"><span>{files.length} report{files.length === 1 ? '' : 's'} ready to add</span><button className="button button-secondary" type="button" disabled={busy === 'upload'} onClick={() => void upload()}>{busy === 'upload' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Upload aria-hidden="true" className="size-4" />} Add reports</button></div>}
            </div>
          </section>

          <section className="section-card" data-workspace-panel aria-labelledby="detail-heading" hidden={workspaceTab !== 'records'}>
            <div className="section-heading"><div><p className="section-kicker">Structured details</p><h2 id="detail-heading">Health details</h2></div><ClipboardPlus aria-hidden="true" className="section-icon" /></div>
            {session.records.length ? <ul className="record-list">{session.records.map((record) => <li key={record.id}><span className="record-number">{recordMeta(record.category).icon}</span><div><strong>{record.label}</strong><p>{recordMeta(record.category).label} · added {dateLabel(record.created_at)}</p></div></li>)}</ul> : <div className="empty-block compact"><ClipboardPlus aria-hidden="true" /><p><strong>Add a health detail</strong><br />Include care visits, lab results, medications, and more.</p></div>}
            <form className="record-form" onSubmit={saveRecord} noValidate>
              <fieldset><legend>Add a detail</legend><div className="category-picker">{(Object.keys(categoryMeta) as RecordCategory[]).map((key) => <button className={category === key ? 'selected' : ''} type="button" key={key} onClick={() => { setCategory(key); setError('') }}><span>{categoryMeta[key].icon}</span>{categoryMeta[key].label}</button>)}</div></fieldset>
              <p className="field-help mb-4">{categoryMeta[category].help}</p>
              <div className="form-grid">
                {category === 'hospital' && <div className="field full"><label htmlFor="hospital">Hospital or clinic <em>Required</em></label><input id="hospital" value={form.hospital} onChange={(event) => setField('hospital', event.target.value)} placeholder="e.g. City General Hospital" /></div>}
                {needsName && <div className="field"><label htmlFor="record-name">{category === 'lab' ? 'Test name' : category === 'vital' ? 'Measurement' : 'Name'} <em>Required</em></label><input id="record-name" value={form.name} onChange={(event) => setField('name', event.target.value)} placeholder={category === 'lab' ? 'e.g. Hemoglobin A1c' : category === 'vital' ? 'e.g. Blood pressure' : 'Enter a name'} /></div>}
                {needsResult && <div className="field"><label htmlFor="record-value">Result <em>Required</em></label><input id="record-value" value={form.value} onChange={(event) => setField('value', event.target.value)} placeholder="e.g. 5.8 or Negative" /></div>}
                {needsResult && <div className="field"><label htmlFor="record-unit">Unit <span>Optional</span></label><input id="record-unit" value={form.unit} onChange={(event) => setField('unit', event.target.value)} placeholder="e.g. %, mg/dL" /></div>}
                {category === 'medication' && <div className="field"><label htmlFor="dosage">Dosage <span>Optional</span></label><input id="dosage" value={form.dosage} onChange={(event) => setField('dosage', event.target.value)} placeholder="e.g. Take once daily" /></div>}
                <div className="field"><label htmlFor="record-date">Date <span>Optional</span></label><input id="record-date" type="date" value={form.date} onChange={(event) => setField('date', event.target.value)} /></div>
                <div className="field full"><label htmlFor="details">Notes <span>Optional</span></label><textarea id="details" value={form.details} onChange={(event) => setField('details', event.target.value)} placeholder="Anything you want your clinician to know" /></div>
              </div>
              <button className="button button-secondary" type="submit" disabled={busy === 'record'}>{busy === 'record' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" className="size-4" />} Add health detail</button>
            </form>
          </section>
        </div>

        <aside className="share-card" data-workspace-panel aria-labelledby="share-heading" hidden={workspaceTab !== 'overview'}>
          <div className="share-icon"><QrCode aria-hidden="true" /></div>
          <p className="eyebrow">Share with your clinician</p>
          <h2 id="share-heading">Create a secure QR code</h2>
          <p>{epicRecord ? `This one QR combines ${recordsAvailable} workspace item${recordsAvailable === 1 ? '' : 's'} with ${epicResources.length} current MyChart resource${epicResources.length === 1 ? '' : 's'}.` : `It includes the ${recordsAvailable} item${recordsAvailable === 1 ? '' : 's'} in this workspace. You do not need to connect MyChart to share with eClinicalWorks.`} Give the clinician your passcode separately.</p>
          {!demographicProfileComplete && <p className="share-requirement"><UserRound aria-hidden="true" /> Save the demographic profile first. It is required so the receiving EHR can match the patient safely.</p>}
          <label htmlFor="share-passcode">Four-digit passcode</label>
          <div className="passcode-field"><KeyRound aria-hidden="true" /><input id="share-passcode" type="password" inputMode="numeric" autoComplete="off" maxLength={4} value={sharePasscode} onChange={(event) => setSharePasscode(event.target.value.replace(/\D/g, ''))} placeholder="0000" /></div>
          <button className="button button-primary w-full" type="button" onClick={() => void generateLink()} disabled={busy === 'share' || !canCreateShare || !demographicProfileComplete}>{busy === 'share' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <QrCode aria-hidden="true" className="size-4" />} Generate combined QR code</button>
          <p className="share-footnote"><ShieldCheck aria-hidden="true" /> The clinician needs both this QR code and your passcode.</p>
          <div className="ecw-test-warning" role="note">
            <ShieldAlert aria-hidden="true" />
            <p><strong>eClinicalMobile compatibility test</strong> creates a bare SHL with no passcode. Anyone who photographs it can retrieve this snapshot until it expires in 15 minutes.</p>
          </div>
          <button className="button button-secondary ecw-test-button w-full" type="button" onClick={() => setEcwCompatibilityConfirmOpen(true)} disabled={busy === 'share' || !canCreateShare || !demographicProfileComplete}>
            {busy === 'share' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <QrCode aria-hidden="true" className="size-4" />} Generate eClinicalMobile test QR
          </button>
        </aside>
      </div>

      <Dialog.Root open={Boolean(link)} onOpenChange={(open) => { if (!open) setLink(null) }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content" aria-describedby="qr-description">
            <Dialog.Close className="dialog-close" aria-label="Close QR code"><X aria-hidden="true" /></Dialog.Close>
            <p className="eyebrow">Secure link created</p>
            <Dialog.Title>{link?.share_mode === 'ecw_compat' ? 'eClinicalMobile compatibility QR' : 'Show this QR code to your clinician'}</Dialog.Title>
            <Dialog.Description id="qr-description">{link?.share_mode === 'ecw_compat'
              ? 'This is a bare SMART Health Link with no passcode, created only to test eClinicalMobile compatibility. It expires in 15 minutes.'
              : 'This is your one combined record. Your clinician can scan it with ClinicForce, then enter the four-digit passcode you selected.'}</Dialog.Description>
            {link && <img className="qr-image" src={`data:image/png;base64,${link.qr_code_base64}`} alt="Smart Health Link QR code for the current health record" />}
            {link?.share_mode === 'ecw_compat'
              ? <div className="dialog-note dialog-note-warning"><ShieldAlert aria-hidden="true" /><span>No passcode: use only for the live eClinicalMobile compatibility test.</span></div>
              : <div className="dialog-note"><KeyRound aria-hidden="true" /><span>Passcode: <strong>{sharePasscode}</strong></span></div>}
            <Dialog.Close className="button button-secondary w-full">Done</Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={ecwCompatibilityConfirmOpen} onOpenChange={setEcwCompatibilityConfirmOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content" aria-describedby="ecw-compatibility-description">
            <Dialog.Close className="dialog-close" aria-label="Close eClinicalMobile compatibility confirmation"><X aria-hidden="true" /></Dialog.Close>
            <p className="eyebrow">Compatibility test</p>
            <Dialog.Title>Generate a no-passcode health QR?</Dialog.Title>
            <Dialog.Description id="ecw-compatibility-description">This QR uses the bare SMART Health Link format required for this eClinicalMobile scan test. It expires in 15 minutes, but anyone with a copy can retrieve the encrypted record during that time.</Dialog.Description>
            <div className="dialog-note dialog-note-warning"><ShieldAlert aria-hidden="true" /><span>Use this only while testing with a clinician you trust.</span></div>
            <div className="dialog-actions">
              <Dialog.Close className="button button-secondary">Cancel</Dialog.Close>
              <button className="button button-primary" type="button" onClick={() => { setEcwCompatibilityConfirmOpen(false); void generateLink('ecw_compat') }}>Generate test QR</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  )
}
