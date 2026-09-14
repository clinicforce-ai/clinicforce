import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft } from 'lucide-react'
import { acceptStaffInvitation, getStaffInvitation, registerStaffClinic } from '../services/api/staff'
import './StaffWorkspace.css'

interface Props { mode: 'create' | 'join'; token?: string; onBack: () => void; onComplete: () => void; onJoin: (token: string) => void }
export default function StaffOnboarding({ mode, token = '', onBack, onComplete, onJoin }: Props) {
  const [invitation, setInvitation] = useState<{ clinic_name: string; expires_at: string } | null>(null)
  const [busy, setBusy] = useState(Boolean(token))
  const [error, setError] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  const errorBox = useRef<HTMLDivElement>(null)
  useEffect(() => { heading.current?.focus() }, [mode, invitation])
  useEffect(() => { if (error) errorBox.current?.focus() }, [error])
  useEffect(() => {
    if (!token) return
    let active = true
    getStaffInvitation(token).then(result => { if (active) setInvitation(result) }).catch(caught => { if (active) setError(caught instanceof Error ? caught.message : 'This invitation could not be opened.') }).finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [token])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const data = new FormData(event.currentTarget)
    const body = { display_name: String(data.get('display_name')).trim(), email: String(data.get('email')).trim(), password: String(data.get('password')) }
    if (body.password !== data.get('confirm_password')) { setError('Passwords must match.'); return }
    setBusy(true); setError('')
    try {
      if (mode === 'create') await registerStaffClinic({ ...body, clinic_name: String(data.get('clinic_name')).trim(), npi: String(data.get('npi')).replace(/\D/g, '') })
      else await acceptStaffInvitation(token, body)
      onComplete()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Your account could not be created. Please try again.') }
    finally { setBusy(false) }
  }
  function join(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = String(new FormData(event.currentTarget).get('invitation')).trim()
    let invitationToken = value
    try {
      if (value.includes('/') || value.includes('?')) {
        const url = new URL(value, window.location.origin)
        if (url.origin !== window.location.origin) throw new Error()
        invitationToken = url.searchParams.get('join') || (/^\/join\/([^/]+)\/?$/.exec(url.pathname)?.[1] || '')
      }
      if (!/^[A-Za-z0-9_-]{20,200}$/.test(invitationToken)) throw new Error()
      onJoin(invitationToken)
    } catch { setError('Enter the ClinicForce invitation link or invitation code from your clinic administrator.') }
  }
  return <section className="staff-workspace"><div className="staff-login onboarding-form"><button type="button" className="staff-text-button" onClick={onBack} disabled={busy}><ArrowLeft size={18} /> Back to clinical access</button><div className="staff-eyebrow">Clinical access</div><h1 ref={heading} tabIndex={-1}>{mode === 'create' ? 'Create your clinic account' : invitation ? `Join ${invitation.clinic_name}` : 'Join your clinic'}</h1><p className="staff-muted">{mode === 'create' ? 'Create a separate clinic workspace and your administrator login. To access an existing clinic, ask its administrator for an invitation.' : 'Use an invitation from your clinic administrator to access the same insurance inbox.'}</p>
    {error && <div className="staff-alert staff-error" role="alert" ref={errorBox} tabIndex={-1}>{error}</div>}{busy && <p role="status">{token && !invitation ? 'Checking your invitation…' : 'Creating your account…'}</p>}
    {mode === 'join' && !token ? <form onSubmit={join}><label className="staff-field"><span>Invitation link or code</span><input name="invitation" required autoComplete="off" autoCapitalize="none" spellCheck={false} /></label><button className="staff-primary">Open invitation</button></form> : (mode === 'create' || invitation) && <form onSubmit={submit}>
      {mode === 'create' && <><label className="staff-field"><span>Clinic name *</span><input name="clinic_name" autoComplete="organization" minLength={2} maxLength={100} required disabled={busy} /></label><label className="staff-field"><span>Billing organization NPI *</span><input name="npi" pattern="\d{10}" inputMode="numeric" minLength={10} maxLength={10} required autoComplete="off" placeholder="10-digit NPI" disabled={busy} /><small>Patients use this 10-digit NPI to find the clinic upload page.</small></label></>}
      <label className="staff-field"><span>Your name *</span><input name="display_name" autoComplete="name" maxLength={100} required disabled={busy} /></label><label className="staff-field"><span>Work email *</span><input name="email" type="email" autoComplete="username" maxLength={200} required disabled={busy} /><small>This email becomes your staff sign-in username.</small></label><label className="staff-field"><span>Password *</span><input name="password" type="password" autoComplete="new-password" minLength={12} maxLength={256} required disabled={busy} /><small>Use at least 12 characters.</small></label><label className="staff-field"><span>Confirm password *</span><input name="confirm_password" type="password" autoComplete="new-password" minLength={12} maxLength={256} required disabled={busy} /></label>{invitation && <p className="staff-small staff-muted">Invitation expires {new Date(invitation.expires_at).toLocaleString()}.</p>}<button className="staff-primary staff-full" disabled={busy}>{mode === 'create' ? 'Create clinic and sign in' : 'Join clinic and sign in'}</button>
    </form>}
  </div></section>
}
