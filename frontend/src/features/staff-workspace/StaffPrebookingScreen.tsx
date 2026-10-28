import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft } from 'lucide-react'
import { ApiError } from '../../services/api/client'
import {
  checkPrebookingEligibility,
  getPrebookingOptions,
  type PrebookingDetails,
  type PrebookingResult,
} from '../../services/api/prebooking-eligibility'

type Payer = Awaited<ReturnType<typeof getPrebookingOptions>>['payers'][number]

const empty: PrebookingDetails = {
  payer_id: '',
  given_name: '',
  family_name: '',
  birth_date: '',
  member_id: '',
}

export default function StaffPrebookingScreen({ onBack }: { onBack: () => void }) {
  const [payers, setPayers] = useState<Payer[]>([])
  const [insurer, setInsurer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState<PrebookingDetails>(empty)
  const [result, setResult] = useState<PrebookingResult | null>(null)
  const today = new Date().toLocaleDateString('en-CA')

  useEffect(() => {
    if (insurer.trim().length < 2 || form.payer_id) return
    let active = true
    const timer = window.setTimeout(() => {
      getPrebookingOptions(insurer.trim())
        .then((value) => { if (active) setPayers(value.payers) })
        .catch(() => { if (active) setError('Insurance companies are temporarily unavailable.') })
    }, 300)
    return () => { active = false; window.clearTimeout(timer) }
  }, [insurer, form.payer_id])

  function change(key: keyof PrebookingDetails, value: string) {
    setForm((current) => ({ ...current, [key]: value }))
    setResult(null)
    setError('')
  }

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    if (!form.payer_id) {
      setError('Choose an insurance company from the suggestions.')
      return
    }
    setBusy(true)
    setResult(null)
    setError('')
    try {
      setResult(await checkPrebookingEligibility(form))
    } catch (caught) {
      setError(
        caught instanceof ApiError && [0, 408].includes(caught.status)
          ? 'The request may have reached Stedi. Check Stedi activity before sending another inquiry.'
          : caught instanceof Error
            ? caught.message
            : 'This check could not be completed.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="staff-narrow">
      <button type="button" className="staff-text-button" onClick={onBack} disabled={busy}>
        <ArrowLeft size={18} /> Back to clinical patients
      </button>
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Prospective patient</div>
          <h1>Check coverage before booking</h1>
          <p className="staff-muted">Check general medical coverage today, before creating a patient or appointment in eCW.</p>
        </div>
      </header>
      {error && <p className="staff-alert staff-error" role="alert">{error}</p>}
      <form className="staff-panel" onSubmit={(event) => void check(event)} autoComplete="off">
        <div className="staff-two-columns">
          <label className="staff-field">
            <span>Patient's first name *</span>
            <input value={form.given_name} required maxLength={100} disabled={busy}
              onChange={(event) => change('given_name', event.target.value)} />
          </label>
          <label className="staff-field">
            <span>Patient's last name *</span>
            <input value={form.family_name} required maxLength={100} disabled={busy}
              onChange={(event) => change('family_name', event.target.value)} />
          </label>
          <label className="staff-field">
            <span>Patient's date of birth *</span>
            <input type="date" value={form.birth_date} required min="1850-01-01" max={today} disabled={busy}
              onChange={(event) => change('birth_date', event.target.value)} />
          </label>
          <label className="staff-field">
            <span>Insurance company *</span>
            <input list="prebooking-payers" value={insurer} required maxLength={200} disabled={busy}
              placeholder="Search Aetna, BCBS, Cigna…"
              onChange={(event) => {
                const value = event.target.value
                const selected = payers.find((payer) => `${payer.name} (${payer.id})` === value)
                if (!selected) setPayers([])
                setInsurer(value)
                change('payer_id', selected?.id ?? '')
              }} />
            <datalist id="prebooking-payers">
              {payers.map((payer) => <option key={payer.id} value={`${payer.name} (${payer.id})`} />)}
            </datalist>
            <small>Choose the exact insurer from the suggestions.</small>
          </label>
          <label className="staff-field">
            <span>Insurance member ID (optional)</span>
            <input value={form.member_id} maxLength={80} disabled={busy}
              onChange={(event) => change('member_id', event.target.value)} />
          </label>
        </div>
        <button className="staff-primary" disabled={busy || !form.payer_id}>
          {busy ? 'Checking with payer…' : 'Check coverage today'}
        </button>
        <p className="staff-small staff-muted">
          Some insurers need a member ID to find coverage. ClinicForce does not save this inquiry;
          Stedi retains its own transaction record.
        </p>
      </form>
      {result && (
        <section className={`staff-alert ${result.status === 'active_today' ? 'staff-success' : 'staff-warning'}`}
          role="status" aria-live="polite">
          <h2>{result.status === 'active_today' ? 'General medical coverage active today'
            : result.status === 'inactive_today' ? 'No active general medical coverage reported today'
            : 'Coverage needs review'}</h2>
          <p>{result.message}</p>
          <p className="staff-small">
            {result.payer} · Checked {result.checked_on}
            {result.coverage_start && ` · Coverage starts ${result.coverage_start}`}
            {result.coverage_end && ` · Coverage ends ${result.coverage_end}`}
            {result.check_id && ` · Stedi check ${result.check_id}`}
          </p>
          <p className="staff-small">
            This check does not establish this clinic's network status, coverage for a specific service,
            or eligibility on a future appointment date.
          </p>
        </section>
      )}
    </div>
  )
}
