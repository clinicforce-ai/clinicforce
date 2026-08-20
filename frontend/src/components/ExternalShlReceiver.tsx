import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, CheckCircle2, FileCheck2, Globe2, KeyRound, LoaderCircle, ShieldAlert, ShieldCheck } from 'lucide-react'
import { ApiError, getCurrentPatientSession, importExternalShl, type PatientSession } from '../services/api'
import { receiveExternalShl, type ExternalShlReview } from '../services/externalShl'
import type { SmartHealthLink } from '../services/shl'

interface ExternalShlReceiverProps {
  link: SmartHealthLink
  onCancel: () => void
  onImported: () => void
}

function messageFrom(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export default function ExternalShlReceiver({ link, onCancel, onImported }: ExternalShlReceiverProps) {
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [review, setReview] = useState<ExternalShlReview | null>(null)
  const [session, setSession] = useState<PatientSession | null>(null)
  const [error, setError] = useState('')
  const [imported, setImported] = useState(false)
  const host = new URL(link.manifestUrl).host

  useEffect(() => {
    let active = true
    void getCurrentPatientSession().then((current) => { if (active) setSession(current) }).catch(() => { if (active) setSession(null) })
    return () => { active = false }
  }, [])

  const retrieve = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (link.requiresPasscode && !pin.trim()) {
      setError('Ask the sharing patient for this link’s passcode.')
      return
    }
    setBusy(true)
    setError('')
    try {
      setReview(await receiveExternalShl(link, link.requiresPasscode ? pin : undefined, session))
    } catch (caught) {
      setError(messageFrom(caught, 'ClinicForce could not open this external Smart Health Link.'))
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!review || !review.canImport) return
    setBusy(true)
    setError('')
    try {
      await importExternalShl({ resourceType: 'Bundle', type: 'collection', entry: review.resources.map((resource) => ({ resource })) }, review.sourceHost)
      setImported(true)
    } catch (caught) {
      const fallback = caught instanceof ApiError && caught.status === 401
        ? 'Sign in to the ClinicForce patient workspace that should receive this data, then scan again.'
        : 'ClinicForce could not save the confirmed external records.'
      setError(messageFrom(caught, fallback))
    } finally {
      setBusy(false)
    }
  }

  if (imported) {
    return <section className="narrow-panel animate-enter" aria-labelledby="external-imported-heading">
      <div className="panel status-panel">
        <CheckCircle2 aria-hidden="true" className="external-success-icon" />
        <p className="eyebrow">External records saved</p>
        <h1 id="external-imported-heading">Your reviewed records were added.</h1>
        <p className="lead-small">They are marked as external Smart Health Link data for reconciliation in ClinicForce.</p>
        <button className="button button-primary" type="button" onClick={onImported}>Return to ClinicForce</button>
      </div>
    </section>
  }

  if (review) {
    return <section className="narrow-panel animate-enter" aria-labelledby="external-review-heading">
      <button className="back-control" type="button" onClick={onCancel}><ArrowLeft aria-hidden="true" className="size-4" /> Cancel import</button>
      <div className="panel external-review-panel">
        <p className="eyebrow"><FileCheck2 aria-hidden="true" className="size-4" /> Review before saving</p>
        <h1 id="external-review-heading">Nothing has been saved yet.</h1>
        <p className="lead-small">From <strong>{review.sourceHost}</strong>{review.label ? ` · ${review.label}` : ''}</p>
        {review.apiAccessOffers.length > 0 && <div className="external-warning" role="status"><ShieldAlert aria-hidden="true" /><p>This link offers live SMART API access to <strong>{review.apiAccessOffers.join(', ')}</strong>. ClinicForce will not connect or save anything from that offer in this version.</p></div>}
        <div className="external-review-block">
          <h2>Records ready for review</h2>
          {review.resourceCounts.length ? <ul>{review.resourceCounts.map(({ type, count }) => <li key={type}><strong>{count}</strong> {type}</li>)}</ul> : <p>No importable FHIR R4 records were supplied.</p>}
        </div>
        <div className="external-review-block">
          <h2>Source and trust</h2>
          <ul>{review.provenance.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
        <div className="external-review-block">
          <h2>Potential conflicts</h2>
          <ul>{review.conflicts.map((item) => <li key={item}>{item}</li>)}</ul>
          {!review.conflicts.length && <p>No matching patient-entered record labels were found.</p>}
        </div>
        {!session && <div className="external-warning"><ShieldAlert aria-hidden="true" /><p>Sign in to the ClinicForce patient workspace that should receive these records before you can save them.</p></div>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="external-actions">
          <button className="button button-secondary" type="button" onClick={onCancel}>Discard</button>
          <button className="button button-primary" type="button" onClick={save} disabled={!review.canImport || busy}>
            {busy ? <LoaderCircle aria-hidden="true" className="size-4 animate-spin" /> : <ShieldCheck aria-hidden="true" className="size-4" />}
            Save reviewed records
          </button>
        </div>
      </div>
    </section>
  }

  return <section className="narrow-panel animate-enter" aria-labelledby="external-confirm-heading">
    <button className="back-control" type="button" onClick={onCancel}><ArrowLeft aria-hidden="true" className="size-4" /> Back to scan</button>
    <div className="panel external-confirm-panel">
      <p className="eyebrow"><Globe2 aria-hidden="true" className="size-4" /> External Smart Health Link</p>
      <h1 id="external-confirm-heading">Confirm this issuer before opening it.</h1>
      <div className="external-host"><Globe2 aria-hidden="true" /><div><span>Manifest host</span><strong>{host}</strong></div></div>
      <p className="lead-small">{link.label ? `“${link.label}”` : 'This QR did not include a label.'} ClinicForce will retrieve and decrypt this link in this browser only after you confirm.</p>
      {link.legacyPayloadEncoding && <div className="external-warning" role="status"><ShieldAlert aria-hidden="true" /><p>This QR uses a legacy padded encoding. ClinicForce normalized only that QR envelope; the issuer URL, key, encryption, and FHIR data are still validated before anything can be saved.</p></div>}
      <form className="stack-form" onSubmit={retrieve} noValidate>
        {link.requiresPasscode && <><label htmlFor="external-pin">Passcode from the sharing patient</label><div className="passcode-field"><KeyRound aria-hidden="true" /><input id="external-pin" autoFocus value={pin} onChange={(event) => setPin(event.target.value)} autoComplete="one-time-code" /></div></>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button button-primary w-full" type="submit" disabled={busy}>
          {busy ? <LoaderCircle aria-hidden="true" className="size-4 animate-spin" /> : <ShieldCheck aria-hidden="true" className="size-4" />}
          Retrieve for review
        </button>
      </form>
      <p className="scanner-privacy"><ShieldCheck aria-hidden="true" /> The QR key and passcode are never sent to ClinicForce.</p>
    </div>
  </section>
}
