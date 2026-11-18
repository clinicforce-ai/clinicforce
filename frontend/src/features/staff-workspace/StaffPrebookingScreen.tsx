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
export default function StaffPrebookingScreen({
  onBack,
  practiceId,
}: {
  onBack: () => void
  practiceId: string
}) {
  const [payers, setPayers] = useState<Payer[]>([])
  const [insurer, setInsurer] = useState('')
  const [optionsState, setOptionsState] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle')
  const [contexts, setContexts] = useState<Array<{ id: string; label: string }>>([])
  const [contextId, setContextId] = useState('')
  const [busy, setBusy] = useState<'planning' | 'checking' | null>(null)
  const [error, setError] = useState('')
  const [payerCheckUnavailable, setPayerCheckUnavailable] = useState(false)
  const [form, setForm] = useState<PrebookingDetails>(empty)
  const [reason, setReason] = useState('')
  const [visitType, setVisitType] = useState<VisitType>('new')
  const [serviceDate, setServiceDate] = useState('')
  const [relationship, setRelationship] = useState<'self' | 'dependent'>('self')
  const [subscriber, setSubscriber] = useState({ given: '', family: '', birth: '' })
  const [plan, setPlan] = useState<PrebookingPlan | null>(null)
  const [selected, setSelected] = useState<Record<string, number>>({})
  const [result, setResult] = useState<PrebookingEstimate | null>(null)
  const today = new Date().toLocaleDateString('en-CA')
  const nmct = ['primary', 'f378bc13-af1d-4ea5-bfbd-8cd102e42ddc'].includes(practiceId)
  const selectedContext = contexts.find((context) => context.id === contextId)
  const payerNotListed =
    optionsState === 'ready' && payers.length === 0 && insurer.trim().length >= 2

  useEffect(() => {
    getPrebookingContexts()
      .then(({ contexts: choices }) => {
        setContexts(choices)
        if (choices.length) setContextId(choices[0].id)
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
            setError(
              'ClinicForce did not receive the insurer list from Stedi. No insurer was checked.',
            )
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
    setPayerCheckUnavailable(false)
  }
  function changeReason(value: string) {
    setReason(value)
    setPlan(null)
    setSelected({})
    setResult(null)
    setPayerCheckUnavailable(false)
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
    setResult(null)
  }
  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setPayerCheckUnavailable(false)
    if (!form.payer_id) {
      setError('Choose the insurer from the search results.')
      return
    }
    if (!form.member_id.trim()) {
      setError('Enter the member ID from the insurance card to match a policy and estimate cost.')
      return
    }
    if (
      relationship === 'dependent' &&
      (!form.member_id || !subscriber.given || !subscriber.family || !subscriber.birth)
    ) {
      setError('Enter the policyholder name, birth date and member ID for a dependent.')
      return
    }
    setBusy(plan ? 'checking' : 'planning')
    setResult(null)
    setError('')
    let estimateRequested = false
    try {
      const planned = plan || (await planPrebookingServices(reason.trim(), visitType))
      if (!plan) setPlan(planned)
      const included = plan
        ? selected
        : Object.fromEntries(
            planned.groups.flatMap((group) =>
              group.codes
                .filter((candidate) => candidate.selected_by_default)
                .map(({ code }) => [code, 1]),
            ),
          )
      if (!Object.keys(included).length) {
        setError(
          'An office visit estimate could not be prepared. Try again or change the visit type.',
        )
        return
      }
      if (!plan) setSelected(included)
      setBusy('checking')
      estimateRequested = true
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
          selected_services: Object.entries(included).map(([code, units]) => ({ code, units })),
        }),
      )
    } catch (caught) {
      setPayerCheckUnavailable(
        estimateRequested &&
          (!(caught instanceof ApiError) ||
            caught.status === 0 ||
            caught.status === 408 ||
            caught.status >= 500),
      )
      setError(
        caught instanceof ApiError && [0, 408].includes(caught.status)
          ? 'The request may have reached Stedi. Check Stedi activity before sending another inquiry.'
          : caught instanceof Error
            ? caught.message
            : 'The insurer check could not be completed.',
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
          <h1>Check insurance before booking</h1>
          <p className="staff-muted">
            Enter the insurance card and visit reason. ClinicForce will build a starting visit
            estimate and check the payer.
          </p>
        </div>
      </header>
      {error && (
        <p className="staff-alert staff-error" role="alert">
          {error}
        </p>
      )}
      {nmct &&
        !result &&
        (payerCheckUnavailable || optionsState === 'failed' || payerNotListed) && (
          <div className="prebooking-cost-card is-patient" role="status">
            <span>Collect on arrival</span>
            <strong>$0.00</strong>
            <small>
              {payerNotListed
                ? 'Stedi did not list an insurer matching this search, so no payer benefit amount was obtained. '
                : ''}
              NMCT policy: collect $0 at arrival; bill after coverage and claim review. This does
              not mean the visit costs $0.
            </small>
          </div>
        )}
      <form className="staff-panel" onSubmit={(event) => void check(event)} autoComplete="off">
        <h2>Patient and insurance</h2>
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
            <span>Member ID from insurance card *</span>
            <input
              value={form.member_id}
              required
              maxLength={80}
              disabled={Boolean(busy)}
              onChange={(event) => change('member_id', event.target.value)}
            />
          </label>
          <label className="staff-field">
            <span>Office visit type</span>
            <select
              value={visitType}
              disabled={Boolean(busy)}
              onChange={(event) => {
                setVisitType(event.target.value as VisitType)
                changeReason(reason)
              }}
            >
              <option value="new">New to NMCT</option>
              <option value="established">Returning to NMCT</option>
            </select>
            <small>
              Starts with a common office-visit level; the final billed level may differ.
            </small>
          </label>
        </div>
        <label className="staff-field">
          <span>Reason for visit *</span>
          <textarea
            value={reason}
            required
            rows={2}
            maxLength={800}
            disabled={Boolean(busy)}
            onChange={(event) => changeReason(event.target.value)}
            placeholder="Example: Headache consultation, or hand numbness with EMG planned"
          />
        </label>
        <details className="prebooking-more-options">
          <summary>Other details: dependent, visit date, provider</summary>
          <div className="staff-two-columns">
            <label className="staff-field">
              <span>Insurance belongs to</span>
              <select
                value={relationship}
                disabled={Boolean(busy)}
                onChange={(event) => {
                  setRelationship(event.target.value as 'self' | 'dependent')
                  setResult(null)
                }}
              >
                <option value="self">Patient</option>
                <option value="dependent">Someone else (dependent)</option>
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
              <small>Blank checks today. Some payers reject future dates.</small>
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
                <option value="">No configured provider selected</option>
                {contexts.map((context) => (
                  <option key={context.id} value={context.id}>
                    {context.label}
                  </option>
                ))}
              </select>
              <small>
                {contexts.length
                  ? 'The first configured NMCT context is selected automatically.'
                  : 'No configured provider/location found; a contract price may be unavailable.'}
              </small>
            </label>
          </div>
        </details>
        {contexts.length > 0 && (
          <p className="staff-small staff-muted">
            {`Clinic provider and office used for pricing: ${selectedContext?.label || 'None selected'}. Change it under Other details.`}
          </p>
        )}
        {plan && (
          <StaffPrebookingPlan
            plan={plan}
            selected={selected}
            busy={Boolean(busy)}
            onToggle={toggle}
            onUnits={(code, units) => {
              setSelected({ ...selected, [code]: units })
              setResult(null)
            }}
          />
        )}
        <button className="staff-primary prebooking-submit" disabled={Boolean(busy)}>
          {busy === 'planning'
            ? 'Finding visit services…'
            : busy === 'checking'
              ? 'Checking with insurer…'
              : result
                ? 'Update estimate'
                : 'Check insurance and estimate visit'}
        </button>
        <p className="staff-small staff-muted">
          The estimate uses the selected office visit and any added services. ClinicForce does not
          save this check or create a patient record.
        </p>
      </form>
      {result && plan && <StaffPrebookingResult result={result} plan={plan} />}
    </div>
  )
}
