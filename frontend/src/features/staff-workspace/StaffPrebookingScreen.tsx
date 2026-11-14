import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft } from 'lucide-react'
import { ApiError } from '../../services/api/client'
import StaffPrebookingResult from './StaffPrebookingResult'
import StaffPrebookingPlan from './StaffPrebookingPlan'
import {
  checkPrebookingEstimate,
  getPrebookingContexts,
  getPrebookingOptions,
  planPrebookingServices,
  type PrebookingDetails,
  type PrebookingEstimate,
  type PrebookingPlan,
} from '../../services/api/prebooking-eligibility'

type Payer = Awaited<ReturnType<typeof getPrebookingOptions>>['payers'][number]
type VisitType = 'new' | 'established' | 'unknown'
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
  const [optionsState, setOptionsState] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle')
  const [contexts, setContexts] = useState<Array<{ id: string; label: string }>>([])
  const [contextId, setContextId] = useState('')
  const [busy, setBusy] = useState<'planning' | 'checking' | null>(null)
  const [error, setError] = useState('')
  const [form, setForm] = useState<PrebookingDetails>(empty)
  const [reason, setReason] = useState('')
  const [visitType, setVisitType] = useState<VisitType>('unknown')
  const [serviceDate, setServiceDate] = useState('')
  const [relationship, setRelationship] = useState<'self' | 'dependent'>('self')
  const [subscriber, setSubscriber] = useState({ given: '', family: '', birth: '' })
  const [plan, setPlan] = useState<PrebookingPlan | null>(null)
  const [selected, setSelected] = useState<Record<string, number>>({})
  const [confirmed, setConfirmed] = useState(false)
  const [result, setResult] = useState<PrebookingEstimate | null>(null)
  const today = new Date().toLocaleDateString('en-CA')

  useEffect(() => {
    getPrebookingContexts()
      .then(({ contexts: choices }) => {
        setContexts(choices)
        if (choices.length === 1) setContextId(choices[0].id)
      })
      .catch(() => setContexts([]))
  }, [])
  useEffect(() => {
    if (insurer.trim().length < 2 || form.payer_id) return
    let active = true
    const timer = window.setTimeout(() => {
      getPrebookingOptions(insurer.trim())
        .then((value) => {
          if (active) {
            setPayers(value.payers)
            setOptionsState('ready')
          }
        })
        .catch(() => {
          if (active) {
            setOptionsState('failed')
            setError('Insurance companies are temporarily unavailable.')
          }
        })
    }, 300)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [insurer, form.payer_id])

  function change(key: keyof PrebookingDetails, value: string) {
    setForm((current) => ({ ...current, [key]: value }))
    setResult(null)
    setError('')
  }
  function changeReason(value: string) {
    setReason(value)
    setPlan(null)
    setSelected({})
    setConfirmed(false)
    setResult(null)
  }
  async function preparePlan() {
    if (busy || reason.trim().length < 3) {
      setError('Enter a visit reason of at least three characters.')
      return
    }
    setBusy('planning')
    setPlan(null)
    setSelected({})
    setConfirmed(false)
    setResult(null)
    setError('')
    try {
      setPlan(await planPrebookingServices(reason.trim(), visitType))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Possible services could not be found.')
    } finally {
      setBusy(null)
    }
  }
  function toggle(code: string, group?: PrebookingPlan['groups'][number]) {
    setSelected((current) => {
      const next = { ...current }
      if (code in next) delete next[code]
      else {
        if (group?.selection_mode === 'single')
          group.codes.forEach((item) => {
            delete next[item.code]
          })
        next[code] = 1
      }
      return next
    })
    setConfirmed(false)
    setResult(null)
  }
  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !plan) return
    if (!form.payer_id || !confirmed || !Object.keys(selected).length) {
      setError('Choose an insurer and confirm at least one planned service.')
      return
    }
    if (
      relationship === 'dependent' &&
      (!form.member_id || !subscriber.given || !subscriber.family || !subscriber.birth)
    ) {
      setError('Enter the policyholder name, birth date and member ID for a dependent.')
      return
    }
    setBusy('checking')
    setResult(null)
    setError('')
    try {
      setResult(
        await checkPrebookingEstimate({
          ...form,
          reason: reason.trim(),
          visit_type: visitType,
          service_date: serviceDate || null,
          network_context_id: contextId || null,
          relationship,
          subscriber_given_name: subscriber.given,
          subscriber_family_name: subscriber.family,
          subscriber_birth_date: subscriber.birth || null,
          selected_services: Object.entries(selected).map(([code, units]) => ({ code, units })),
        }),
      )
    } catch (caught) {
      setError(
        caught instanceof ApiError && [0, 408].includes(caught.status)
          ? 'The request may have reached Stedi. Check Stedi activity before sending another inquiry.'
          : caught instanceof Error
            ? caught.message
            : 'This estimate could not be completed.',
      )
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="staff-narrow">
      <button type="button" className="staff-text-button" onClick={onBack} disabled={Boolean(busy)}>
        <ArrowLeft size={18} /> Back to clinical patients
      </button>
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Prospective patient</div>
          <h1>Plan care and estimate cost before booking</h1>
          <p className="staff-muted">
            Describe the visit, confirm possible services, then check coverage and estimated patient
            share.
          </p>
        </div>
      </header>
      {error && (
        <p className="staff-alert staff-error" role="alert">
          {error}
        </p>
      )}
      <form className="staff-panel" onSubmit={(event) => void check(event)} autoComplete="off">
        <h2>1. Patient and visit</h2>
        <div className="staff-two-columns">
          <label className="staff-field">
            <span>Patient's first name *</span>
            <input
              value={form.given_name}
              required
              maxLength={100}
              disabled={Boolean(busy)}
              onChange={(event) => change('given_name', event.target.value)}
            />
          </label>
          <label className="staff-field">
            <span>Patient's last name *</span>
            <input
              value={form.family_name}
              required
              maxLength={100}
              disabled={Boolean(busy)}
              onChange={(event) => change('family_name', event.target.value)}
            />
          </label>
          <label className="staff-field">
            <span>Patient's birth date *</span>
            <input
              type="date"
              value={form.birth_date}
              required
              min="1850-01-01"
              max={today}
              disabled={Boolean(busy)}
              onChange={(event) => change('birth_date', event.target.value)}
            />
          </label>
          <div className="staff-field">
            <label htmlFor="prebooking-insurer">Insurance company *</label>
            <input
              id="prebooking-insurer"
              value={insurer}
              required
              maxLength={200}
              disabled={Boolean(busy)}
              aria-describedby="prebooking-insurer-help"
              placeholder="Search Aetna, BCBS, Cigna…"
              onChange={(event) => {
                const value = event.target.value
                setInsurer(value)
                setPayers([])
                setOptionsState(value.trim().length >= 2 ? 'loading' : 'idle')
                change('payer_id', '')
              }}
            />
            {payers.length > 0 && !form.payer_id && (
              <div
                className="prebooking-payer-options"
                role="listbox"
                aria-label="Matching insurance companies"
              >
                {payers.map((payer) => (
                  <button
                    key={payer.id}
                    type="button"
                    role="option"
                    aria-selected={false}
                    disabled={Boolean(busy)}
                    onClick={() => {
                      setInsurer(`${payer.name} (${payer.id})`)
                      setPayers([])
                      setOptionsState('idle')
                      change('payer_id', payer.id)
                    }}
                  >
                    {payer.name} ({payer.id})
                  </button>
                ))}
              </div>
            )}
            <small id="prebooking-insurer-help" aria-live="polite">
              {form.payer_id
                ? 'Insurer selected.'
                : optionsState === 'loading'
                  ? 'Searching insurance companies…'
                  : optionsState === 'ready' && !payers.length
                    ? 'No matching insurer found.'
                    : optionsState === 'failed'
                      ? 'Search unavailable.'
                      : 'Type at least two letters and choose the exact insurer.'}
            </small>
          </div>
          <label className="staff-field">
            <span>Insurance member ID (needed for a priced estimate)</span>
            <input
              value={form.member_id}
              maxLength={80}
              disabled={Boolean(busy)}
              onChange={(event) => change('member_id', event.target.value)}
            />
          </label>
          <label className="staff-field">
            <span>Patient is</span>
            <select
              value={relationship}
              disabled={Boolean(busy)}
              onChange={(event) => {
                setRelationship(event.target.value as 'self' | 'dependent')
                setResult(null)
              }}
            >
              <option value="self">Policyholder</option>
              <option value="dependent">Dependent</option>
            </select>
          </label>
          {relationship === 'dependent' && (
            <>
              <label className="staff-field">
                <span>Policyholder's first name *</span>
                <input
                  value={subscriber.given}
                  required
                  maxLength={100}
                  disabled={Boolean(busy)}
                  onChange={(event) => {
                    setSubscriber({ ...subscriber, given: event.target.value })
                    setResult(null)
                  }}
                />
              </label>
              <label className="staff-field">
                <span>Policyholder's last name *</span>
                <input
                  value={subscriber.family}
                  required
                  maxLength={100}
                  disabled={Boolean(busy)}
                  onChange={(event) => {
                    setSubscriber({ ...subscriber, family: event.target.value })
                    setResult(null)
                  }}
                />
              </label>
              <label className="staff-field">
                <span>Policyholder's birth date *</span>
                <input
                  type="date"
                  value={subscriber.birth}
                  required
                  min="1850-01-01"
                  max={today}
                  disabled={Boolean(busy)}
                  onChange={(event) => {
                    setSubscriber({ ...subscriber, birth: event.target.value })
                    setResult(null)
                  }}
                />
              </label>
            </>
          )}
          <label className="staff-field">
            <span>Patient visit status</span>
            <select
              value={visitType}
              disabled={Boolean(busy)}
              onChange={(event) => {
                setVisitType(event.target.value as VisitType)
                changeReason(reason)
              }}
            >
              <option value="unknown">Unknown</option>
              <option value="new">New patient</option>
              <option value="established">Established patient</option>
            </select>
          </label>
          <label className="staff-field">
            <span>Possible service date</span>
            <input
              type="date"
              value={serviceDate}
              min={today}
              disabled={Boolean(busy)}
              onChange={(event) => {
                setServiceDate(event.target.value)
                setResult(null)
              }}
            />
            <small>Leave blank to check today. Recheck after booking.</small>
          </label>
          <label className="staff-field">
            <span>Clinic provider and location</span>
            <select
              value={contextId}
              disabled={Boolean(busy)}
              onChange={(event) => {
                setContextId(event.target.value)
                setResult(null)
              }}
            >
              <option value="">Select if known</option>
              {contexts.map((context) => (
                <option key={context.id} value={context.id}>
                  {context.label}
                </option>
              ))}
            </select>
            <small>
              {contexts.length
                ? 'Needed for an exact network and contracted rate match.'
                : 'No configured provider/location found; a patient amount may be unavailable.'}
            </small>
          </label>
        </div>
        <label className="staff-field">
          <span>Reason for visit and any care already discussed *</span>
          <textarea
            value={reason}
            required
            rows={3}
            maxLength={800}
            disabled={Boolean(busy)}
            onChange={(event) => changeReason(event.target.value)}
            placeholder="Example: Follow-up for hand numbness; EMG/NCS may be considered"
          />
          <small>
            Use everyday words. Include any tests or treatments mentioned by the clinician; no CPT
            code is needed.
          </small>
        </label>
        <button
          type="button"
          className="staff-primary"
          disabled={Boolean(busy) || reason.trim().length < 3}
          onClick={() => void preparePlan()}
        >
          {busy === 'planning' ? 'Finding possible care…' : 'Find possible care'}
        </button>
        {plan && (
          <StaffPrebookingPlan
            plan={plan}
            selected={selected}
            busy={Boolean(busy)}
            checking={busy === 'checking'}
            confirmed={confirmed}
            payerSelected={Boolean(form.payer_id)}
            onToggle={toggle}
            onUnits={(code, units) => {
              setSelected({ ...selected, [code]: units })
              setConfirmed(false)
              setResult(null)
            }}
            onConfirm={setConfirmed}
          />
        )}
      </form>
      {result && plan && <StaffPrebookingResult result={result} plan={plan} />}
    </div>
  )
}
