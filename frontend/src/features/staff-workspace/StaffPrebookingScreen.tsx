import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft } from 'lucide-react'
import { ApiError } from '../../services/api/client'
import {
  checkPrebookingEstimate, getPrebookingContexts, getPrebookingOptions, planPrebookingServices,
  type PrebookingDetails, type PrebookingEstimate, type PrebookingPlan, type PrebookingTerms,
} from '../../services/api/prebooking-eligibility'

type Payer = Awaited<ReturnType<typeof getPrebookingOptions>>['payers'][number]
type VisitType = 'new' | 'established' | 'unknown'
const empty: PrebookingDetails = { payer_id: '', given_name: '', family_name: '', birth_date: '', member_id: '' }
const money = (cents: number | null | undefined) => cents == null ? 'Not reported'
  : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)

function BenefitTerms({ terms }: { terms: PrebookingTerms }) {
  return <dl className="prebooking-benefits">
    <div><dt>Copay</dt><dd>{money(terms.copay_cents)}</dd></div>
    <div><dt>Coinsurance</dt><dd>{terms.coinsurance_percent == null ? 'Not reported' : `${terms.coinsurance_percent}%`}</dd></div>
    <div><dt>Annual deductible</dt><dd>{money(terms.deductible_annual_cents)}</dd></div>
    <div><dt>Deductible remaining</dt><dd>{money(terms.deductible_remaining_cents)}</dd></div>
    <div><dt>Annual out-of-pocket max</dt><dd>{money(terms.oop_annual_cents)}</dd></div>
    <div><dt>Out-of-pocket remaining</dt><dd>{money(terms.oop_remaining_cents)}</dd></div>
  </dl>
}

function PrebookingResultView({ result }: { result: PrebookingEstimate }) {
  const patient = result.patient_min_cents != null && result.patient_max_cents != null
    ? result.patient_min_cents === result.patient_max_cents ? money(result.patient_max_cents)
      : `${money(result.patient_min_cents)}–${money(result.patient_max_cents)}` : null
  const priced = result.estimate_status !== 'needs_review' && result.allowed_total_cents != null && patient != null
  const patientPercent = priced && result.allowed_total_cents! > 0
    ? Math.min(100, Math.max(0, result.patient_max_cents! / result.allowed_total_cents! * 100)) : 0
  const networkLabel = result.network_status === 'in_network' ? 'In network'
    : result.network_status === 'out_of_network' ? 'Out of network' : 'Needs review'

  return <section className="staff-panel prebooking-result" aria-labelledby="prebooking-result-title">
    <header className="prebooking-result-header"><div><p className="staff-eyebrow">Check before booking</p>
      <h2 id="prebooking-result-title">Prospective visit estimate</h2>
      <p className="staff-small staff-muted">{result.payer}{result.plan_name ? ` · ${result.plan_name}` : ''} · Checked for {result.checked_on}</p>
    </div><span className={`prebooking-result-badge ${priced ? 'is-ready' : 'needs-review'}`}>
      {priced ? result.estimate_status === 'conditional' ? 'Conditional estimate' : 'Estimate ready' : 'Needs review'}
    </span></header>
    <div className="prebooking-status-row">
      <div><span>Coverage</span><strong>{result.coverage.status === 'active_today' ? 'Active for checked date' : result.coverage.status === 'inactive_today' ? 'Inactive for checked date' : 'Needs review'}</strong><small>{result.coverage.message}</small></div>
      <div><span>NMCT network</span><strong>{networkLabel}</strong><small>{result.network_evidence?.reason || 'Provider participation is not established for this check.'}</small></div>
    </div>
    <div className="prebooking-cost-grid">
      <div className="prebooking-cost-card"><span>Total estimated visit cost</span><strong>{priced ? money(result.allowed_total_cents) : 'Needs review'}</strong><small>Contract-allowed cost for selected services</small></div>
      <div className="prebooking-cost-card is-patient"><span>Estimated patient share</span><strong>{priced ? patient : 'Needs review'}</strong><small>Not an amount to collect before the visit</small></div>
    </div>
    {priced && result.plan_total_cents != null && <div className="prebooking-share">
      <div className="prebooking-share-labels"><span>Patient {patient}</span><span>Plan {money(result.plan_total_cents)}</span></div>
      <div className="prebooking-share-track" role="img" aria-label={`Estimated visit cost: patient ${patient}, plan ${money(result.plan_total_cents)}`}><span style={{ width: `${patientPercent}%` }} /></div>
    </div>}
    {result.benefits && <section className="prebooking-benefit-section" aria-labelledby="prebooking-benefit-title"><h3 id="prebooking-benefit-title">General medical benefits</h3><BenefitTerms terms={result.benefits} /></section>}
    {result.lines.length > 0 && <section aria-labelledby="prebooking-services-title"><h3 id="prebooking-services-title">Clinician-confirmed planned services</h3>
      <div className="prebooking-service-list">{result.lines.map((line) => <article className="prebooking-service-row" key={line.code}>
        <div className="prebooking-service-name"><strong>{line.code} × {line.units}</strong><span>{line.role !== 'visit' ? 'Possible procedure or test' : 'Office visit'}</span></div>
        <div><span>Allowed cost</span><strong>{money(line.allowed_cents)}</strong></div>
        <div><span>Patient share</span><strong>{money(line.patient_cents)}</strong></div>
        <small className="prebooking-rate-source">{line.rate_source ? `Rate source: ${line.rate_source}` : 'Rate source needs review'}</small>
        {(line.issues.length > 0 || line.conditions.length > 0) && <ul className="prebooking-line-issues">{[...line.issues, ...line.conditions].map((issue, index) => <li key={index}>{issue}</li>)}</ul>}
        <details className="prebooking-line-benefits"><summary>Service benefits</summary><BenefitTerms terms={line.benefits} /></details>
      </article>)}</div>
    </section>}
    {result.issues.length > 0 && <section className="prebooking-review" aria-labelledby="prebooking-review-title"><h3 id="prebooking-review-title">What to verify</h3><ul>{result.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></section>}
    {result.estimate_status === 'conditional' && <p className="staff-small staff-muted">Resolve the listed benefit or approval conditions before using this estimate.</p>}
    <p className="staff-small staff-muted prebooking-result-footnote">Estimate for selected services and checked date. Final responsibility depends on services performed and payer adjudication. Recheck after booking.{result.check_ids.length ? ` Stedi check: ${result.check_ids.join(', ')}.` : ''}</p>
  </section>
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
  const [manualCode, setManualCode] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [result, setResult] = useState<PrebookingEstimate | null>(null)
  const today = new Date().toLocaleDateString('en-CA')

  useEffect(() => {
    getPrebookingContexts().then(({ contexts: choices }) => {
      setContexts(choices)
      if (choices.length === 1) setContextId(choices[0].id)
    }).catch(() => setContexts([]))
  }, [])
  useEffect(() => {
    if (insurer.trim().length < 2 || form.payer_id) return
    let active = true
    const timer = window.setTimeout(() => {
      getPrebookingOptions(insurer.trim()).then((value) => {
        if (active) { setPayers(value.payers); setOptionsState('ready') }
      }).catch(() => {
        if (active) { setOptionsState('failed'); setError('Insurance companies are temporarily unavailable.') }
      })
    }, 300)
    return () => { active = false; window.clearTimeout(timer) }
  }, [insurer, form.payer_id])

  function change(key: keyof PrebookingDetails, value: string) {
    setForm((current) => ({ ...current, [key]: value }))
    setResult(null); setError('')
  }
  function changeReason(value: string) {
    setReason(value); setPlan(null); setSelected({}); setConfirmed(false); setResult(null)
  }
  async function preparePlan() {
    if (busy || reason.trim().length < 3) { setError('Enter a visit reason of at least three characters.'); return }
    setBusy('planning'); setPlan(null); setSelected({}); setConfirmed(false); setResult(null); setError('')
    try { setPlan(await planPrebookingServices(reason.trim(), visitType)) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Possible services could not be found.') }
    finally { setBusy(null) }
  }
  function toggle(code: string, group?: PrebookingPlan['groups'][number]) {
    setSelected((current) => {
      const next = { ...current }
      if (code in next) delete next[code]
      else { if (group?.selection_mode === 'single') group.codes.forEach((item) => { delete next[item.code] }); next[code] = 1 }
      return next
    })
    setConfirmed(false); setResult(null)
  }
  function addManualCode() {
    const code = manualCode.trim().toUpperCase()
    if (!/^(?:[0-9]{5}|[A-Z][0-9]{4}|[0-9]{4}[A-Z])$/.test(code)) { setError('Enter one valid five-character CPT or HCPCS code.'); return }
    setSelected((current) => ({ ...current, [code]: current[code] || 1 }))
    setManualCode(''); setConfirmed(false); setResult(null); setError('')
  }
  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !plan) return
    if (!form.payer_id || !confirmed || !Object.keys(selected).length) {
      setError('Choose an insurer and confirm at least one planned service.'); return
    }
    if (relationship === 'dependent' && (!form.member_id || !subscriber.given || !subscriber.family || !subscriber.birth)) {
      setError('Enter the policyholder name, birth date and member ID for a dependent.'); return
    }
    setBusy('checking'); setResult(null); setError('')
    try {
      setResult(await checkPrebookingEstimate({ ...form, reason: reason.trim(), visit_type: visitType,
        service_date: serviceDate || null, network_context_id: contextId || null,
        relationship, subscriber_given_name: subscriber.given, subscriber_family_name: subscriber.family,
        subscriber_birth_date: subscriber.birth || null,
        selected_services: Object.entries(selected).map(([code, units]) => ({ code, units })) }))
    } catch (caught) {
      setError(caught instanceof ApiError && [0, 408].includes(caught.status)
        ? 'The request may have reached Stedi. Check Stedi activity before sending another inquiry.'
        : caught instanceof Error ? caught.message : 'This estimate could not be completed.')
    } finally { setBusy(null) }
  }
  return <div className="staff-narrow">
    <button type="button" className="staff-text-button" onClick={onBack} disabled={Boolean(busy)}><ArrowLeft size={18} /> Back to clinical patients</button>
    <header className="staff-header"><div><div className="staff-eyebrow">Prospective patient</div>
      <h1>Plan care and estimate cost before booking</h1>
      <p className="staff-muted">Describe the visit, confirm possible services, then check coverage and estimated patient share.</p>
    </div></header>
    {error && <p className="staff-alert staff-error" role="alert">{error}</p>}
    <form className="staff-panel" onSubmit={(event) => void check(event)} autoComplete="off">
      <h2>1. Patient and visit</h2>
      <div className="staff-two-columns">
        <label className="staff-field"><span>Patient's first name *</span><input value={form.given_name} required maxLength={100} disabled={Boolean(busy)} onChange={(event) => change('given_name', event.target.value)} /></label>
        <label className="staff-field"><span>Patient's last name *</span><input value={form.family_name} required maxLength={100} disabled={Boolean(busy)} onChange={(event) => change('family_name', event.target.value)} /></label>
        <label className="staff-field"><span>Patient's birth date *</span><input type="date" value={form.birth_date} required min="1850-01-01" max={today} disabled={Boolean(busy)} onChange={(event) => change('birth_date', event.target.value)} /></label>
        <div className="staff-field"><label htmlFor="prebooking-insurer">Insurance company *</label>
          <input id="prebooking-insurer" value={insurer} required maxLength={200} disabled={Boolean(busy)} aria-describedby="prebooking-insurer-help" placeholder="Search Aetna, BCBS, Cigna…" onChange={(event) => {
            const value = event.target.value; setInsurer(value); setPayers([])
            setOptionsState(value.trim().length >= 2 ? 'loading' : 'idle'); change('payer_id', '')
          }} />
          {payers.length > 0 && !form.payer_id && <div className="prebooking-payer-options" role="listbox" aria-label="Matching insurance companies">
            {payers.map((payer) => <button key={payer.id} type="button" role="option" aria-selected={false} disabled={Boolean(busy)} onClick={() => {
              setInsurer(`${payer.name} (${payer.id})`); setPayers([]); setOptionsState('idle'); change('payer_id', payer.id)
            }}>{payer.name} ({payer.id})</button>)}
          </div>}
          <small id="prebooking-insurer-help" aria-live="polite">{form.payer_id ? 'Insurer selected.' : optionsState === 'loading' ? 'Searching insurance companies…' : optionsState === 'ready' && !payers.length ? 'No matching insurer found.' : optionsState === 'failed' ? 'Search unavailable.' : 'Type at least two letters and choose the exact insurer.'}</small>
        </div>
        <label className="staff-field"><span>Insurance member ID (needed for a priced estimate)</span><input value={form.member_id} maxLength={80} disabled={Boolean(busy)} onChange={(event) => change('member_id', event.target.value)} /></label>
        <label className="staff-field"><span>Patient is</span><select value={relationship} disabled={Boolean(busy)} onChange={(event) => { setRelationship(event.target.value as 'self' | 'dependent'); setResult(null) }}><option value="self">Policyholder</option><option value="dependent">Dependent</option></select></label>
        {relationship === 'dependent' && <>
          <label className="staff-field"><span>Policyholder's first name *</span><input value={subscriber.given} required maxLength={100} disabled={Boolean(busy)} onChange={(event) => { setSubscriber({ ...subscriber, given: event.target.value }); setResult(null) }} /></label>
          <label className="staff-field"><span>Policyholder's last name *</span><input value={subscriber.family} required maxLength={100} disabled={Boolean(busy)} onChange={(event) => { setSubscriber({ ...subscriber, family: event.target.value }); setResult(null) }} /></label>
          <label className="staff-field"><span>Policyholder's birth date *</span><input type="date" value={subscriber.birth} required min="1850-01-01" max={today} disabled={Boolean(busy)} onChange={(event) => { setSubscriber({ ...subscriber, birth: event.target.value }); setResult(null) }} /></label>
        </>}
        <label className="staff-field"><span>Patient visit status</span><select value={visitType} disabled={Boolean(busy)} onChange={(event) => { setVisitType(event.target.value as VisitType); changeReason(reason) }}><option value="unknown">Unknown</option><option value="new">New patient</option><option value="established">Established patient</option></select></label>
        <label className="staff-field"><span>Possible service date</span><input type="date" value={serviceDate} min={today} disabled={Boolean(busy)} onChange={(event) => { setServiceDate(event.target.value); setResult(null) }} /><small>Leave blank to check today. Recheck after booking.</small></label>
        <label className="staff-field"><span>Clinic provider and location</span><select value={contextId} disabled={Boolean(busy)} onChange={(event) => { setContextId(event.target.value); setResult(null) }}><option value="">Select if known</option>{contexts.map((context) => <option key={context.id} value={context.id}>{context.label}</option>)}</select><small>{contexts.length ? 'Needed for an exact network and contracted rate match.' : 'No configured provider/location found; a patient amount may be unavailable.'}</small></label>
      </div>
      <label className="staff-field"><span>Reason for visit and anticipated care *</span><textarea value={reason} required rows={3} maxLength={800} disabled={Boolean(busy)} onChange={(event) => changeReason(event.target.value)} placeholder="Example: Follow-up for hand numbness; EMG/NCS may be considered" /><small>Possible tests stay unconfirmed until selected below.</small></label>
      <button type="button" className="staff-primary" disabled={Boolean(busy) || reason.trim().length < 3} onClick={() => void preparePlan()}>{busy === 'planning' ? 'Finding possible services…' : 'Find possible services'}</button>
      {plan && <section className="prebooking-plan" aria-labelledby="prebooking-plan-title"><h2 id="prebooking-plan-title">2. Confirm anticipated services</h2>
        <p className="staff-muted">{plan.message}</p>
        {plan.groups.map((group, index) => <div key={`${group.label}:${index}`} className="prebooking-service-group"><h3>{group.label}</h3>
          {group.codes.map((candidate) => <label key={candidate.code} className="prebooking-service-choice"><input type="checkbox" checked={candidate.code in selected} disabled={Boolean(busy)} onChange={() => toggle(candidate.code, group)} /><span><strong>{candidate.code}</strong>{candidate.role !== 'consultation' && <em className="prebooking-procedure-label">Potential extra procedure or test</em>} · {candidate.reason}</span></label>)}
        </div>)}
        {plan.notes.map((note, index) => <p key={index} className="staff-small staff-muted">{note}</p>)}
        <div className="prebooking-manual-code"><label className="staff-field"><span>Add a clinician-confirmed CPT/HCPCS code</span><input value={manualCode} maxLength={5} disabled={Boolean(busy)} onChange={(event) => setManualCode(event.target.value.toUpperCase())} /></label><button type="button" className="staff-text-button" disabled={Boolean(busy)} onClick={addManualCode}>Add code</button></div>
        {Object.keys(selected).length > 0 && <div className="prebooking-selected-services">{Object.entries(selected).map(([code, units]) => <div key={code}><strong>{code}</strong><label>Units <input type="number" min="1" max="4" value={units} disabled={Boolean(busy)} onChange={(event) => { setSelected({ ...selected, [code]: Number(event.target.value) }); setConfirmed(false); setResult(null) }} /></label><button type="button" className="staff-text-button" onClick={() => toggle(code)}>Remove</button></div>)}</div>}
        <label className="prebooking-confirm"><input type="checkbox" checked={confirmed} disabled={Boolean(busy) || !Object.keys(selected).length} onChange={(event) => setConfirmed(event.target.checked)} /> I reviewed these anticipated services and units with the clinician.</label>
        <button className="staff-primary" disabled={Boolean(busy) || !confirmed || !form.payer_id || !Object.keys(selected).length}>{busy === 'checking' ? 'Checking with payer…' : 'Check benefits and estimate cost'}</button>
        <p className="staff-small staff-muted">This may send several service-specific Stedi inquiries. ClinicForce does not save this result; Stedi retains its transaction records.</p>
      </section>}
    </form>
    {result && <PrebookingResultView result={result} />}
  </div>
}
