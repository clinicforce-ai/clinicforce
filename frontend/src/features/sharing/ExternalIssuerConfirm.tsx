import type { FormEvent } from 'react'
import { ArrowLeft, Globe2, KeyRound, LoaderCircle, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { SmartHealthLink } from '../../services/shl'

interface Props {
  link: SmartHealthLink
  host: string
  pin: string
  onPinChange: (value: string) => void
  busy: boolean
  error: string
  onCancel: () => void
  onRetrieve: (event: FormEvent<HTMLFormElement>) => void
}
export default function ExternalIssuerConfirm({
  link,
  host,
  pin,
  onPinChange,
  busy,
  error,
  onCancel,
  onRetrieve,
}: Props) {
  return (
    <section className="narrow-panel animate-enter" aria-labelledby="external-confirm-heading">
      <button className="back-control" type="button" onClick={onCancel}>
        <ArrowLeft aria-hidden="true" className="size-4" /> Back to scan
      </button>
      <div className="panel external-confirm-panel">
        <p className="eyebrow">
          <Globe2 aria-hidden="true" className="size-4" /> External Smart Health Link
        </p>
        <h1 id="external-confirm-heading">Confirm this issuer before opening it.</h1>
        <div className="external-host">
          <Globe2 aria-hidden="true" />
          <div>
            <span>Manifest host</span>
            <strong>{host}</strong>
          </div>
        </div>
        <p className="lead-small">
          {link.label ? `“${link.label}”` : 'This QR did not include a label.'} ClinicForce will
          retrieve and decrypt this link in this browser only after you confirm.
        </p>
        {link.legacyPayloadEncoding && (
          <div className="external-warning" role="status">
            <ShieldAlert aria-hidden="true" />
            <p>
              This QR uses a legacy padded encoding. ClinicForce normalized only that QR envelope;
              the issuer URL, key, encryption, and FHIR data are still validated before anything can
              be saved.
            </p>
          </div>
        )}
        <form className="stack-form" onSubmit={onRetrieve} noValidate>
          {link.requiresPasscode && (
            <>
              <label htmlFor="external-pin">Passcode from the sharing patient</label>
              <div className="passcode-field">
                <KeyRound aria-hidden="true" />
                <input
                  id="external-pin"
                  autoFocus
                  value={pin}
                  onChange={(event) => onPinChange(event.target.value)}
                  autoComplete="one-time-code"
                />
              </div>
            </>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button className="button button-primary w-full" type="submit" disabled={busy}>
            {busy ? (
              <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            ) : (
              <ShieldCheck aria-hidden="true" className="size-4" />
            )}
            Retrieve for review
          </button>
        </form>
        <p className="scanner-privacy">
          <ShieldCheck aria-hidden="true" /> The QR key and passcode are never sent to ClinicForce.
        </p>
      </div>
    </section>
  )
}
