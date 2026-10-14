import { PatientUploadReceipt } from './PatientUploadReceipt'
import { UnavailableUpload } from './UnavailableUpload'
import PatientUploadForm from './PatientUploadForm'
import ClinicLookupForm from './ClinicLookupForm'
import { patientCardFileProblem } from './card-files'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft } from 'lucide-react'
import InsuranceCardScanner from '../insurance-card/InsuranceCardScanner'
import { ApiError } from '../../services/api/client'
import {
  getInsuranceUploadAccount,
  getPatientUpload,
  getUploadClinic,
  readInsuranceUploadLocation,
  readPatientInsuranceCard,
  startPatientUpload,
  submitPatientUpload,
  type InsuranceCardFields,
  type UploadClinic,
  type UploadReceipt,
} from '../../services/api/insurance-upload'
import '../staff-workspace/foundation.css'
import '../staff-workspace/inbox.css'
import '../staff-workspace/intake.css'
import './upload.css'

interface Props {
  clinicCode?: string
  uploadCode?: string
  onBack: () => void
}
import type { PatientField } from './patient-upload-types'
type PatientFields = Partial<Record<PatientField, string>>
function splitMemberName(value = ''): { given_name: string; family_name: string } {
  const parts = value.trim().split(/\s+/)
  if (parts.length < 2) return { given_name: '', family_name: '' }
  if (value.includes(','))
    return {
      given_name: parts.slice(1).join(' ').replace(',', ''),
      family_name: parts[0].replace(',', ''),
    }
  return { given_name: parts.slice(0, -1).join(' '), family_name: parts.at(-1) || '' }
}
export default function PatientInsuranceUpload({
  clinicCode = '',
  uploadCode = '',
  onBack,
}: Props) {
  const [account, setAccount] = useState<{
    available: boolean
    given_name?: string
    family_name?: string
    birth_date?: string
  }>({ available: false })
  const [clinic, setClinic] = useState<UploadClinic | null>(null)
  const [token, setToken] = useState(uploadCode)
  const [expiresAt, setExpiresAt] = useState('')
  const [lookup, setLookup] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [cardFields, setCardFields] = useState<InsuranceCardFields>({})
  const [editedFields, setEditedFields] = useState<PatientFields>({})
  const [cardMessage, setCardMessage] = useState('')
  const [camera, setCamera] = useState(false)
  const [busy, setBusy] = useState(Boolean(clinicCode))
  const [busyMessage, setBusyMessage] = useState(clinicCode ? 'Opening clinic upload…' : '')
  const [error, setError] = useState('')
  const [received, setReceived] = useState(false)
  const [receipt, setReceipt] = useState<UploadReceipt | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const submissionId = useRef(crypto.randomUUID())
  const inFlight = useRef(false)
  const heading = useRef<HTMLHeadingElement>(null)
  const errorBox = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let active = true
    getInsuranceUploadAccount()
      .then((result) => {
        if (active) setAccount(result)
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [])
  useEffect(() => {
    heading.current?.focus()
  }, [clinic, received, camera])
  useEffect(() => {
    if (error) errorBox.current?.focus()
  }, [error])
  useEffect(() => {
    if (!clinicCode) return
    let active = true
    const load = async () => {
      try {
        if (uploadCode) {
          const metadata = await getPatientUpload(clinicCode, uploadCode)
          if (active) {
            setClinic({ npi: clinicCode, name: metadata.clinic_name })
            setExpiresAt(metadata.expires_at)
            setReceived(metadata.status === 'received')
          }
        } else {
          const result = await getUploadClinic(clinicCode)
          if (active) setClinic(result.clinic)
        }
      } catch (caught) {
        if (active) {
          setError(
            caught instanceof Error ? caught.message : 'This upload link could not be opened.',
          )
          setUnavailable(true)
        }
      } finally {
        if (active) setBusy(false)
      }
    }
    void load()
    return () => {
      active = false
    }
  }, [clinicCode, uploadCode])

  async function findClinic(event: FormEvent) {
    event.preventDefault()
    const location = readInsuranceUploadLocation(lookup.trim())
    if (!location) {
      setError(
        'Enter the clinic’s 10-digit NPI or a ClinicForce upload link provided by the clinic.',
      )
      return
    }
    setBusy(true)
    setBusyMessage('Opening clinic upload…')
    setError('')
    try {
      if (location.token) {
        window.location.assign(
          `/${encodeURIComponent(location.npi)}/${encodeURIComponent(location.token)}`,
        )
        return
      }
      const result = await getUploadClinic(location.npi)
      setClinic(result.clinic)
      setUnavailable(false)
      window.history.replaceState({}, '', `/${encodeURIComponent(result.clinic.npi)}`)
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'Clinic not found. Check the NPI with your clinic.',
      )
    } finally {
      setBusy(false)
    }
  }
  async function ensureToken() {
    if (token) return token
    if (!clinic) throw new Error('Choose a clinic before adding your card.')
    const created = await startPatientUpload(clinic.npi)
    setToken(created.upload_code)
    setExpiresAt(created.expires_at)
    window.history.replaceState({}, '', new URL(created.url, window.location.origin).pathname)
    return created.upload_code
  }
  async function chooseFiles(next: File[]) {
    const validation = patientCardFileProblem(next)
    setFiles(next)
    setCardFields({})
    setCardMessage('')
    setError(validation)
    if (validation) return
    setBusy(true)
    setBusyMessage('Reading your insurance card…')
    try {
      const activeToken = await ensureToken()
      const body = new FormData()
      next.forEach((file) => body.append('files', file))
      const result = await readPatientInsuranceCard(clinic!.npi, activeToken, body)
      setCardFields(result.fields)
      setCardMessage(
        Object.keys(result.fields).length
          ? 'Available details were filled from your card. Review them before sending.'
          : 'No patient details were found on the card. Enter them below.',
      )
    } catch (caught) {
      setCardMessage(
        caught instanceof Error
          ? caught.message
          : 'We could not read the card automatically. Enter the details below.',
      )
    } finally {
      setBusy(false)
    }
  }
  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!clinic || inFlight.current) return
    if (patientCardFileProblem(files)) {
      setError('Choose one or two JPG, PNG, or PDF card files, each no larger than 10 MB.')
      return
    }
    const body = new FormData(event.currentTarget)
    files.forEach((file) => body.append('files', file))
    body.set('consent', 'true')
    body.set('submission_id', submissionId.current)
    inFlight.current = true
    setBusy(true)
    setBusyMessage('Sending securely. Keep this page open…')
    setError('')
    try {
      const activeToken = await ensureToken()
      const result = await submitPatientUpload(clinic.npi, activeToken, body)
      setReceipt(result)
      setReceived(true)
      setFiles([])
    } catch (caught) {
      setError(
        caught instanceof ApiError && [0, 408, 502, 504].includes(caught.status)
          ? 'We could not confirm receipt. Keep this page open and try Send to clinic again; your retry uses the same submission.'
          : caught instanceof Error
            ? caught.message
            : 'Your card could not be submitted. Please try again.',
      )
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  const cardName = splitMemberName(cardFields.member_name)
  const suggestedFields: Record<PatientField, string> = {
    given_name: (account.available ? account.given_name : cardName.given_name) || '',
    family_name: (account.available ? account.family_name : cardName.family_name) || '',
    birth_date: (account.available ? account.birth_date : cardFields.birth_date) || '',
    member_id: cardFields.member_id || '',
    email: '',
  }
  const field = (name: PatientField) => editedFields[name] ?? suggestedFields[name]
  const edit = (name: PatientField, value: string) =>
    setEditedFields((current) => ({ ...current, [name]: value }))
  const alerts = (
    <>
      {error && (
        <div className="staff-alert staff-error" role="alert" tabIndex={-1} ref={errorBox}>
          {error}
        </div>
      )}
      {busy && (
        <p className="staff-progress" role="status">
          {busyMessage}
        </p>
      )}
    </>
  )
  return (
    <>
      {camera && (
        <section className="staff-workspace staff-scan-focus">
          <div className="staff-scan-content">
            <h1 className="staff-sr-only" tabIndex={-1}>
              Scan your insurance card
            </h1>
            <InsuranceCardScanner
              onCaptured={(next) => {
                void chooseFiles(next)
                setCamera(false)
              }}
              onCancel={() => setCamera(false)}
            />
          </div>
        </section>
      )}
      <section className="staff-workspace patient-upload" hidden={camera}>
        <div className="patient-upload-content">
          <button className="staff-text-button" onClick={onBack} disabled={busy}>
            <ArrowLeft size={18} /> ClinicForce home
          </button>
          <header className="staff-header">
            <div>
              <div className="staff-eyebrow">Patient insurance · No account needed</div>
              <h1 ref={heading} tabIndex={-1}>
                {received ? 'Your card has been received' : 'Upload your insurance card'}
              </h1>
              <p className="staff-muted">
                {clinic
                  ? `Send directly to ${clinic.name}.`
                  : 'Use the clinic NPI or private link your clinic provided.'}
              </p>
            </div>
          </header>
          {alerts}
          {received ? (
            <PatientUploadReceipt clinic={clinic} receipt={receipt} />
          ) : unavailable ? (
            <UnavailableUpload />
          ) : !clinic ? (
            !busy && (
              <ClinicLookupForm
                lookup={lookup}
                busy={busy}
                onLookupChange={setLookup}
                onSubmit={findClinic}
              />
            )
          ) : (
            <PatientUploadForm
              clinic={clinic}
              account={account}
              files={files}
              busy={busy}
              cardMessage={cardMessage}
              expiresAt={expiresAt}
              field={field}
              edit={edit}
              onSubmit={send}
              onChooseFiles={(next) => void chooseFiles(next)}
              onOpenCamera={() => setCamera(true)}
            />
          )}
          <p className="staff-small staff-muted">
            A clinic patient record is created automatically. No password or sign-in is needed.
          </p>
        </div>
      </section>
    </>
  )
}
