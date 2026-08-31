import type { FormEvent } from 'react'
import { LockKeyhole } from 'lucide-react'

interface AccessPinProps { pin: string; error: string; onBack: () => void; onPinChange: (pin: string) => void; onSubmit: (event: FormEvent<HTMLFormElement>) => void }

export function AccessPin({ pin, error, onBack, onPinChange, onSubmit }: AccessPinProps) {
  return <section className="narrow-panel animate-enter" aria-labelledby="pin-heading"><button className="back-control" type="button" onClick={onBack}>Back to scan</button><div className="panel"><p className="eyebrow">Clinician access · Step 2 of 2</p><h1 id="pin-heading">Enter the patient’s passcode</h1><p className="lead-small">The QR code was read. Ask the patient for the four-digit passcode they set when sharing this record.</p><form className="stack-form" onSubmit={onSubmit} noValidate><label htmlFor="pin">Four-digit passcode</label><input id="pin" className="pin-input" inputMode="numeric" autoComplete="one-time-code" maxLength={4} pattern="[0-9]{4}" placeholder="0000" value={pin} onChange={(event) => onPinChange(event.target.value.replace(/\D/g, ''))} autoFocus aria-describedby={error ? 'pin-error' : undefined} />{error && <p id="pin-error" className="form-error" role="alert">{error}</p>}<button className="button button-primary w-full" type="submit"><LockKeyhole aria-hidden="true" className="size-4" /> Open shared record</button></form></div></section>
}
