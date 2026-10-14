import type { FormEvent } from 'react'

interface Props {
  lookup: string
  busy: boolean
  onLookupChange: (value: string) => void
  onSubmit: (event: FormEvent) => void
}

export default function ClinicLookupForm({ lookup, busy, onLookupChange, onSubmit }: Props) {
  return (
    <form className="staff-panel" onSubmit={onSubmit}>
      <label className="staff-field">
        <span>Clinic NPI or upload link</span>
        <input
          value={lookup}
          onChange={(event) => onLookupChange(event.target.value)}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          required
          placeholder="10-digit NPI or link from clinic"
          disabled={busy}
        />
        <small>Check the clinic name on the next screen before sending your card.</small>
      </label>
      <button className="staff-primary" disabled={busy}>
        Find my clinic
      </button>
    </form>
  )
}
