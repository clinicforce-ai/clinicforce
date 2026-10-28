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
  payer_connection_id: '',
  provider_network_id: '',
  given_name: '',
  family_name: '',
  birth_date: '',
  member_id: '',
  relationship: 'self',
  subscriber_given_name: '',
  subscriber_family_name: '',
  subscriber_birth_date: '',
}

export default function StaffPrebookingScreen({ onBack }: { onBack: () => void }) {
  const [payers, setPayers] = useState<Payer[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState<PrebookingDetails>(empty)
  const [result, setResult] = useState<PrebookingResult | null>(null)
  const payer = payers.find((item) => item.id === form.payer_connection_id)
  const today = new Date().toLocaleDateString('en-CA')

  useEffect(() => {
    let active = true
    getPrebookingOptions()
      .then((value) => {
        if (active) setPayers(value.payers)
      })
      .catch((caught) => {
        if (active)
          setError(caught instanceof Error ? caught.message : 'Payer options are unavailable.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  function change(key: keyof PrebookingDetails, value: string) {
    setForm((current) => ({ ...current, [key]: value }))
    setResult(null)
    setError('')
  }

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
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
          <p className="staff-muted">
            Check general medical coverage today, before creating a patient or appointment in eCW.
          </p>
        </div>
      </header>
      {error && (
        <p className="staff-alert staff-error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">Loading clinic payers…</p>
      ) : !payers.length ? (
        error ? null : (
          <p className="staff-alert staff-warning">
            No approved medical payer route is configured for this clinic. Ask a clinic
            administrator to set one up.
          </p>
        )
      ) : (
        <form className="staff-panel" onSubmit={(event) => void check(event)} autoComplete="off">
          <h2>Insurance and clinic provider</h2>
          <div className="staff-two-columns">
            <label className="staff-field">
              <span>Insurance company *</span>
              <select
                value={form.payer_connection_id}
                required
                disabled={busy}
                onChange={(event) => {
                  const selected = payers.find((item) => item.id === event.target.value)
                  setForm((current) => ({
                    ...current,
                    payer_connection_id: event.target.value,
                    provider_network_id:
                      selected?.providers.length === 1 ? selected.providers[0].id : '',
                  }))
                  setResult(null)
                  setError('')
                }}
              >
                <option value="">Choose insurer</option>
                {payers.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="staff-field">
              <span>Provider for inquiry *</span>
              <select
                value={form.provider_network_id}
                required
                disabled={busy || !payer}
                onChange={(event) => change('provider_network_id', event.target.value)}
              >
                <option value="">Choose provider</option>
                {payer?.providers.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · NPI {item.npi}
                  </option>
                ))}
              </select>
              {payer && !payer.providers.length && (
                <small>This insurer needs a configured clinic provider before checking.</small>
              )}
            </label>
          </div>
          <h2>Person seeking an appointment</h2>
          <div className="staff-two-columns">
            <label className="staff-field">
              <span>Legal first name *</span>
              <input
                value={form.given_name}
                required
                maxLength={100}
                disabled={busy}
                onChange={(event) => change('given_name', event.target.value)}
              />
            </label>
            <label className="staff-field">
              <span>Legal last name *</span>
              <input
                value={form.family_name}
                required
                maxLength={100}
                disabled={busy}
                onChange={(event) => change('family_name', event.target.value)}
              />
            </label>
            <label className="staff-field">
              <span>Date of birth *</span>
              <input
                type="date"
                value={form.birth_date}
                required
                min="1850-01-01"
                max={today}
                disabled={busy}
                onChange={(event) => change('birth_date', event.target.value)}
              />
            </label>
            <label className="staff-field">
              <span>Insurance member ID *</span>
              <input
                value={form.member_id}
                required
                maxLength={80}
                disabled={busy}
                onChange={(event) => change('member_id', event.target.value)}
              />
            </label>
            <label className="staff-field">
              <span>Coverage relationship *</span>
              <select
                value={form.relationship}
                disabled={busy}
                onChange={(event) => change('relationship', event.target.value)}
              >
                <option value="self">This person has their own member ID</option>
                <option value="dependent">Covered under another person's member ID</option>
              </select>
            </label>
          </div>
          {form.relationship === 'dependent' && (
            <>
              <h2>Policyholder</h2>
              <p className="staff-small staff-muted">
                Use the policyholder's member ID above. Enter their identity separately from the
                person seeking care.
              </p>
              <div className="staff-two-columns">
                <label className="staff-field">
                  <span>Policyholder first name *</span>
                  <input
                    value={form.subscriber_given_name}
                    required
                    maxLength={100}
                    disabled={busy}
                    onChange={(event) => change('subscriber_given_name', event.target.value)}
                  />
                </label>
                <label className="staff-field">
                  <span>Policyholder last name *</span>
                  <input
                    value={form.subscriber_family_name}
                    required
                    maxLength={100}
                    disabled={busy}
                    onChange={(event) => change('subscriber_family_name', event.target.value)}
                  />
                </label>
                <label className="staff-field">
                  <span>Policyholder date of birth *</span>
                  <input
                    type="date"
                    value={form.subscriber_birth_date}
                    required
                    min="1850-01-01"
                    max={today}
                    disabled={busy}
                    onChange={(event) => change('subscriber_birth_date', event.target.value)}
                  />
                </label>
              </div>
            </>
          )}
          <button className="staff-primary" disabled={busy || !payer?.providers.length}>
            {busy ? 'Checking with payer…' : 'Check coverage today'}
          </button>
          <p className="staff-small staff-muted">
            ClinicForce does not save this inquiry. The result disappears when you leave this
            screen; Stedi retains its own transaction record.
          </p>
        </form>
      )}
      {result && (
        <section
          className={`staff-alert ${result.status === 'active_today' ? 'staff-success' : 'staff-warning'}`}
          role="status"
          aria-live="polite"
        >
          <h2>
            {result.status === 'active_today'
              ? 'General medical coverage active today'
              : result.status === 'inactive_today'
                ? 'No active general medical coverage reported today'
                : 'Coverage needs review'}
          </h2>
          <p>{result.message}</p>
          <p className="staff-small">
            {result.payer} · Checked {result.checked_on}
            {result.coverage_start && ` · Coverage starts ${result.coverage_start}`}
            {result.coverage_end && ` · Coverage ends ${result.coverage_end}`}
            {result.check_id && ` · Stedi check ${result.check_id}`}
          </p>
          <p className="staff-small">
            This check does not establish this clinic's network status, coverage for a specific
            service, or eligibility on a future appointment date.
          </p>
        </section>
      )}
    </div>
  )
}
