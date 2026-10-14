import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Copy, Mail } from 'lucide-react'
import { emailStaffLink, getStaffEmailConfiguration } from '../../services/api/staff-clinic'

interface Props {
  kind: 'upload-links' | 'invitations'
  id: string
  url: string
  expiresAt: string
  clinicName: string
  initialEmail?: string
}
export default function StaffShareLink({
  kind,
  id,
  url,
  expiresAt,
  clinicName,
  initialEmail = '',
}: Props) {
  const [email, setEmail] = useState(initialEmail)
  const [configuration, setConfiguration] = useState<{
    available: boolean
    reason?: string
  } | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const field = useRef<HTMLInputElement>(null)
  useEffect(() => {
    let active = true
    getStaffEmailConfiguration()
      .then((result) => {
        if (active) setConfiguration(result)
      })
      .catch(() => {
        if (active)
          setConfiguration({
            available: false,
            reason:
              'Email availability could not be checked. Copy the link or open an email draft.',
          })
      })
    return () => {
      active = false
    }
  }, [])
  const subject =
    kind === 'upload-links'
      ? `Insurance card upload for ${clinicName}`
      : `Join ${clinicName} on ClinicForce`
  const body =
    kind === 'upload-links'
      ? `${clinicName} invites you to send your insurance card securely. No patient account is required.\n\n${url}\n\nThis private link expires ${new Date(expiresAt).toLocaleString()}. Do not forward it. Please contact the clinic if you need a new link.`
      : `You have been invited to join ${clinicName} on ClinicForce. Create your staff login using this invitation:\n\n${url}\n\nThis private link expires ${new Date(expiresAt).toLocaleString()}. Do not forward it.`
  async function copy() {
    try {
      await navigator.clipboard.writeText(url)
      setNotice('Link copied. Share it only with the intended recipient.')
      setError('')
    } catch {
      field.current?.focus()
      field.current?.select()
      setNotice('The link is selected. Copy it using your device’s copy command.')
    }
  }
  async function send(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await emailStaffLink(kind, id, email, url)
      if (result.status === 'sent')
        setNotice('Email accepted for sending. Delivery is not yet confirmed.')
      else setError(result.detail)
    } catch (caught) {
      setError(
        `${caught instanceof Error ? caught.message : 'Email could not be confirmed.'} Check before sending again; it may already have been accepted.`,
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="staff-link-result">
      <label className="staff-field staff-link-output">
        <span>Private {kind === 'upload-links' ? 'patient upload' : 'staff invitation'} link</span>
        <input ref={field} value={url} readOnly onFocus={(event) => event.target.select()} />
        <small>
          Expires {new Date(expiresAt).toLocaleString()}. Keep this link private. It is shown only
          when created.
        </small>
      </label>
      {notice && (
        <p className="staff-alert staff-success" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="staff-alert staff-error" role="alert">
          {error}
        </p>
      )}
      <button type="button" onClick={() => void copy()}>
        <Copy size={18} /> Copy link
      </button>
      <form onSubmit={send}>
        <label className="staff-field patient-card-input">
          <span>Recipient email{configuration?.available ? ' *' : ' (optional for draft)'}</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required={configuration?.available}
            autoComplete="off"
            disabled={busy}
          />
        </label>
        <div className="staff-inline-actions">
          <a
            className="staff-mail-link"
            href={`mailto:${encodeURIComponent(email)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`}
          >
            <Mail size={18} /> Open email draft
          </a>
          {configuration?.available && (
            <button className="staff-primary" disabled={busy}>
              {busy ? 'Sending…' : 'Send email'}
            </button>
          )}
        </div>
        <p className="staff-small staff-muted patient-expiry">
          {configuration === null
            ? 'Checking clinic email availability…'
            : configuration.available
              ? 'Send email uses the clinic’s configured sender. Nothing is sent until you select Send email.'
              : configuration.reason ||
                'Direct email sending is not configured. Copy the link or use your email app.'}
        </p>
      </form>
    </div>
  )
}
