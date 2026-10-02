import {
  AlertTriangle, ArrowLeft, ArrowRight, Check, CheckCircle2, Clipboard, FileSpreadsheet,
  LoaderCircle, Mail, RefreshCw, ShieldCheck, Sparkles,
} from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import StaffLibraryPicker from './StaffLibraryPicker'
import {
  confirmV2Services, createV2Case, getV2Case, getV2Configuration, getV2Requirements,
  getV2Appointments, latestV2Case, refreshV2Transaction, retryV2Transaction, submitV2Transaction,
  saveV2ManualReview, suggestV2Services, uploadV2Document,
  type V2Appointment, type V2Case, type V2Configuration, type V2ManualReview, type V2Resource, type V2Transaction,
} from '../services/api'

interface Props { onBack: () => void; staffMode?: boolean; onUseDocuments?: () => void }
type Choice = { selected: boolean; quantity: number; service_status: 'scheduled' | 'possible' }

const money = (value?: number | null) => value == null
  ? 'Not returned'
  : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value / 100)
const readable = (value?: string) => value?.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ') || 'Not returned'
const dateTime = (value?: string) => value ? new Date(value).toLocaleString() : 'Not returned'
const resources: Array<{ id: V2Resource; label: string; action: string; help: string }> = [
  { id: 'coverage', label: 'Eligibility and benefits', action: 'Check eligibility', help: 'Confirm active coverage and return payer benefit details.' },
  { id: 'referral', label: 'Referral', action: 'Submit referral request', help: 'Use when staff intend to initiate a referral review.' },
  { id: 'authorization', label: 'Prior authorization', action: 'Submit authorization request', help: 'Use when staff intend to initiate a prior authorization review.' },
  { id: 'estimate', label: 'Scheduled visit estimate', action: 'Get scheduled visit estimate', help: 'Estimate only the services marked Scheduled.' },
  { id: 'procedure_estimate', label: 'Possible procedure estimate', action: 'Get possible procedure estimate', help: 'Keep a possible follow-up procedure separate from today’s visit.' },
]

function TransactionStatus({ transaction }: { transaction: V2Transaction }) {
  const complete = transaction.outcome === 'complete'
  return <div className={`v2-response ${complete ? 'is-complete' : ''}`}>
    <div className="v2-response-heading">
      {complete ? <CheckCircle2 size={18} aria-hidden="true" /> : <AlertTriangle size={18} aria-hidden="true" />}
      <strong>{readable(transaction.outcome)}</strong>
      {transaction.mock_response && <span className="v2-tag">Synthetic</span>}
    </div>
    {transaction.message && <p>{transaction.message}</p>}
    <dl className="v2-facts">
      <div><dt>{transaction.source === 'stedi' ? 'Stedi check ID' : 'Availity result ID'}</dt><dd>{transaction.resource_id || 'Not returned'}</dd></div>
      <div><dt>Transaction ID</dt><dd>{transaction.transaction_id || 'Not returned'}</dd></div>
      <div><dt>Last checked</dt><dd>{dateTime(transaction.checked_at)}</dd></div>
    </dl>
  </div>
}

function PriceExplanation({ transaction }: { transaction?: V2Transaction }) {
  const lines = transaction?.result?.lines
  if (!lines?.length) return null
  return <details className="v2-price-explanation">
    <summary>Why Availity returned this amount</summary>
    {lines.map((line, index) => <div key={`${line.code}-${index}`}>
      <strong>{line.code || 'Service'} · patient {money(line.patient_cents)}</strong>
      <small>Allowed {money(line.allowed_cents)} · deductible {money(line.deductible_cents)} · copay {money(line.copay_cents)} · coinsurance {money(line.coinsurance_cents)}</small>
    </div>)}
  </details>
}

export default function InsuranceVerificationV2({ onBack, staffMode = false, onUseDocuments }: Props) {
  const [config, setConfig] = useState<V2Configuration | null>(null)
  const [insuranceCase, setCase] = useState<V2Case | null>(null)
  const [appointments, setAppointments] = useState<V2Appointment[]>([])
  const [appointmentId, setAppointmentId] = useState('')
  const [busy, setBusy] = useState('Loading insurance settings…')
  const [error, setError] = useState('')
  const [choices, setChoices] = useState<Record<string, Choice>>({})
  const [copied, setCopied] = useState(false)
  const active = useRef(false)
  const pollStarted = useRef<Record<string, number>>({})

  useEffect(() => {
    let canceled = false
    Promise.all([getV2Configuration(), latestV2Case(), staffMode ? getV2Appointments() : Promise.resolve({ appointments: [] })]).then(([settings, latest, visits]) => {
      if (!canceled) { setConfig(settings); setCase(latest.case); setAppointments(visits.appointments) }
    }).catch((caught: unknown) => {
      if (!canceled) setError(caught instanceof Error ? caught.message : 'Unable to open insurance verification.')
    }).finally(() => { if (!canceled) setBusy('') })
    return () => { canceled = true }
  }, [])

  const perform = useCallback(async (label: string, operation: () => Promise<V2Case>) => {
    if (active.current) return
    active.current = true
    setBusy(label); setError('')
    try { setCase(await operation()) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The request could not be completed. Refresh the saved case before trying again.') }
    finally { active.current = false; setBusy('') }
  }, [])

  useEffect(() => {
    if (!insuranceCase || busy || error) return
    const pending = resources.find(({ id }) => insuranceCase.transactions[id]?.outcome === 'pending')
    if (!pending) return
    const key = `${insuranceCase.id}:${pending.id}`
    const started = pollStarted.current[key] ?? Date.now()
    pollStarted.current[key] = started
    if (Date.now() - started >= 60_000) return
    const transaction = insuranceCase.transactions[pending.id]!
    const delay = Math.max(3000, (transaction.next_poll_at ?? 0) * 1000 - Date.now())
    if (Date.now() + delay - started > 60_000) return
    const timer = window.setTimeout(() => {
      void perform('Checking the saved Availity response…', () => refreshV2Transaction(insuranceCase.id, pending.id))
    }, delay)
    return () => window.clearTimeout(timer)
  }, [insuranceCase, busy, error, perform])

  const demo = config?.environment === 'demo'
  const ecwPreview = staffMode && demo
  const coverage = insuranceCase?.transactions.coverage
  const coverageDone = coverage?.outcome === 'complete'
  const coverageActive = !ecwPreview && (demo || coverage?.result?.active === true)
  const stediCoverage = coverage?.source === 'stedi'
  const servicesLocked = !!insuranceCase && (['estimate', 'procedure_estimate', 'authorization', 'referral'] as V2Resource[]).some(resource => !!insuranceCase.transactions[resource])
  const hasPending = !!insuranceCase && resources.some(resource => ['submitting', 'pending'].includes(insuranceCase.transactions[resource.id]?.outcome || ''))

  const run = (resource: V2Resource) => {
    if (!insuranceCase || ecwPreview) return
    void perform(`Sending ${resources.find(item => item.id === resource)?.label.toLowerCase()} to the payer…`, async () => {
      let current = insuranceCase
      if (!demo && !current.discovery.checked_at) current = await getV2Requirements(current.id)
      return submitV2Transaction(current.id, resource)
    })
  }

  const getAnswer = () => {
    if (!insuranceCase || ecwPreview) return
    void perform('Checking the selected appointment with the payer…', async () => {
      let current = insuranceCase
      if (!demo && !current.discovery.checked_at) current = await getV2Requirements(current.id)
      if (!current.transactions.coverage) current = await submitV2Transaction(current.id, 'coverage')
      const coverageResult = current.transactions.coverage?.result
      const canEstimate = current.transactions.coverage?.outcome === 'complete' && coverageResult?.active === true &&
        coverageResult.pce_available !== false && current.missing_by_resource.estimate.length === 0 &&
        current.inputs.lines.some(line => (line.service_status || 'scheduled') === 'scheduled')
      return canEstimate && !current.transactions.estimate ? submitV2Transaction(current.id, 'estimate') : current
    })
  }

  const uploadFees = (file: File) => {
    if (!insuranceCase) return
    void perform('Reading clinic prices and suggesting services…', async () => {
      const updated = await uploadV2Document(insuranceCase.id, 'fees', file)
      return updated.inputs.reason ? suggestV2Services(updated.id) : updated
    })
  }

  const toggle = (code: string, groupCodes: string[], single: boolean) => {
    setChoices(current => {
      const next = { ...current }
      if (single) groupCodes.forEach(item => { if (item !== code) delete next[item] })
      next[code] = { selected: !current[code]?.selected, quantity: current[code]?.quantity || 1, service_status: current[code]?.service_status || 'scheduled' }
      return next
    })
  }

  const confirmServices = () => {
    if (!insuranceCase) return
    const lines = Object.entries(choices).filter(([, choice]) => choice.selected).map(([code, choice]) => ({
      code, quantity: choice.quantity, service_status: choice.service_status,
    }))
    if (!lines.length) { setError('Choose at least one clinician-reviewed service.'); return }
    void perform('Saving reviewed services…', () => confirmV2Services(insuranceCase.id, lines))
  }

  const recordManualReview = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!insuranceCase) return
    const values = new FormData(event.currentTarget)
    const review: V2ManualReview = {
      source: values.get('source') as V2ManualReview['source'],
      network_status: values.get('network_status') as V2ManualReview['network_status'],
      network_reference: String(values.get('network_reference') || ''),
      referral_status: values.get('referral_status') as V2ManualReview['referral_status'],
      referral_reference: String(values.get('referral_reference') || ''),
      authorization_status: values.get('authorization_status') as V2ManualReview['authorization_status'],
      authorization_reference: String(values.get('authorization_reference') || ''),
      price_path: values.get('price_path') as V2ManualReview['price_path'],
    }
    void perform('Saving manual payer verification…', () => saveV2ManualReview(insuranceCase.id, review))
  }

  const copySummary = async () => {
    if (!insuranceCase) return
    const summary = insuranceCase.summary
    const text = [summary.heading, `Coverage: ${summary.coverage}`, `Network: ${summary.scheduled.network || 'not returned'}`,
      `Scheduled insurance estimate: ${money(summary.scheduled.insurance_cents)}`,
      `Scheduled cash price: ${money(summary.scheduled.cash_cents)}`,
      `Referral: ${summary.referral.status || (summary.referral.required ? 'required — no determination' : 'not identified as required')}`,
      `Prior authorization: ${summary.authorization.status || (summary.authorization.required ? 'required — no determination' : 'not identified as required')}`].join('\n')
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 2000) }
    catch { setError('The browser could not copy the summary. Select the result and copy it manually.') }
  }

  if (config && (staffMode && demo ? !config.ecw_configured : !config.credentials_configured || (!demo && (!config.production_enabled || !config.ecw_configured)))) {
    return <section className="insurance-beta insurance-v2" aria-labelledby="insurance-v2-unavailable">
      <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> {staffMode ? 'Back to patient insurance' : 'Back to clinical summary'}</button>
      <div className="panel"><p className="eyebrow"><ShieldCheck aria-hidden="true" size={17} /> Insurance verification</p><h1 id="insurance-v2-unavailable">Live insurance verification is unavailable</h1><p>{staffMode ? 'Your patient intake and card are saved. Use uploaded documents while the clinic completes its live connection setup.' : 'You can continue reviewing the patient’s shared record.'}</p>{onUseDocuments && <button className="button button-primary" type="button" onClick={onUseDocuments}>Use uploaded documents</button>}</div>
    </section>
  }

  return <section className="insurance-beta insurance-v2" aria-labelledby="insurance-v2-heading">
    <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> {staffMode ? 'Back to patient insurance' : 'Back to clinical summary'}</button>
    <header className="insurance-beta-hero v2-hero">
      <div><p className="eyebrow"><ShieldCheck aria-hidden="true" size={17} /> Front-desk insurance decision</p><h1 id="insurance-v2-heading">Know what to do before the patient is scheduled.</h1><p>{ecwPreview ? 'ClinicForce reads the live appointment and reason for visit from eClinicalWorks. Payer checks stay off until Availity production is activated.' : 'ClinicForce reads the visit from eClinicalWorks and gets coverage, review decisions, and insured pricing from Availity.'}</p></div>
      <div className="beta-session"><span><i aria-hidden="true" /> {config ? ecwPreview ? 'eCW live · Availity demo' : `${demo ? 'Demo' : 'Production'} environment` : 'Connecting'}</span><strong>{ecwPreview ? 'Live eCW appointment' : demo ? 'Synthetic Availity patient' : 'Live patient inquiry'}</strong><small>{ecwPreview ? 'No payer submission' : demo ? 'Training data only' : staffMode ? 'Saved to the clinic patient record' : 'Saved for this QR session'}</small></div>
    </header>

    <main className="v2-decision-sheet">
      <div className="v2-live-status" role="status" aria-live="polite">{busy ? <><LoaderCircle size={16} className="animate-spin" aria-hidden="true" /> {busy}</> : insuranceCase ? 'Case changes are saved.' : 'Ready to start.'}</div>
      {error && <div className="form-error" role="alert"><AlertTriangle size={18} aria-hidden="true" /><span>{error}</span>{onUseDocuments && <button className="button button-secondary" type="button" onClick={onUseDocuments}>Use uploaded documents</button>}</div>}

      {!insuranceCase && <section className="v2-start">
        <p className="section-kicker">Start here</p>
        <h2>{staffMode ? 'Choose the eCW appointment to verify' : demo ? 'Run the Availity synthetic workflow' : 'Load the patient’s next scheduled visit'}</h2>
        <p>{staffMode ? 'The insurance card is already saved with this clinic intake. Choose the visit that this verification should cover.' : config?.message}</p>
        {staffMode && <label className="v2-appointment-choice"><span>Scheduled appointment</span><select value={appointmentId} onChange={event => setAppointmentId(event.target.value)} disabled={!!busy || !appointments.length}><option value="">{appointments.length ? 'Choose an appointment' : 'No scheduled appointments found'}</option>{appointments.map(visit => <option key={visit.id} value={visit.id}>{dateTime(visit.start)} · {visit.reason || 'Reason not returned'} · {readable(visit.status)}</option>)}</select></label>}
        <button className="button button-primary" type="button" disabled={!!busy || (staffMode && !appointmentId)} onClick={() => void perform('Starting a saved insurance case…', () => createV2Case(appointmentId || undefined))}>{staffMode ? 'Verify selected appointment' : demo ? 'Start demo case' : 'Load eCW appointment'} <ArrowRight size={17} aria-hidden="true" /></button>
      </section>}

      {insuranceCase && <>
        {ecwPreview ? <section className="v2-decision v2-decision-verify" aria-labelledby="front-desk-answer">
          <p>eClinicalWorks appointment</p>
          <h2 id="front-desk-answer">Review the live visit and suggested services</h2>
          <div className="v2-decision-grid">
            <div><span>Service date</span><strong>{insuranceCase.inputs.service_date || 'Not returned'}</strong><small>{insuranceCase.sources.ecw?.appointment_status || 'Appointment status not returned'}</small></div>
            <div><span>Reason for visit</span><strong>{insuranceCase.inputs.reason || 'Not returned'}</strong><small>Exact context returned by eClinicalWorks</small></div>
          </div>
        </section> : <><section className={`v2-decision v2-decision-${insuranceCase.summary.action}`} aria-labelledby="front-desk-answer">
          <p>Front-desk answer</p>
          <h2 id="front-desk-answer">{insuranceCase.summary.heading}</h2>
          <div className="v2-decision-grid">
            <div><span>Coverage</span><strong>{readable(insuranceCase.summary.coverage)}</strong><small>{insuranceCase.summary.hmo ? 'HMO plan detected' : 'Plan type from Availity'}</small></div>
            <div><span>Network</span><strong>{insuranceCase.summary.scheduled.network || 'Not returned'}</strong><small>For the submitted provider and services</small></div>
            <div><span>Referral</span><strong>{insuranceCase.summary.referral.status || (insuranceCase.summary.referral.required ? 'Required' : 'Not identified as required')}</strong><small>{insuranceCase.summary.referral.reference ? `Ref ${insuranceCase.summary.referral.reference} · ${insuranceCase.summary.referral.effective || 'no start'} to ${insuranceCase.summary.referral.expiration || 'no end'}` : 'No reference returned'}</small></div>
            <div><span>Prior authorization</span><strong>{insuranceCase.summary.authorization.status || (insuranceCase.summary.authorization.required ? 'Required' : insuranceCase.summary.authorization.unknown ? 'Requirement unclear' : 'Not identified as required')}</strong><small>{insuranceCase.summary.authorization.reference ? `Ref ${insuranceCase.summary.authorization.reference} · ${insuranceCase.summary.authorization.effective || 'no start'} to ${insuranceCase.summary.authorization.expiration || 'no end'}` : 'No reference returned'}</small></div>
          </div>
          {insuranceCase.summary.action === 'review_oon' && <p className="v2-next-step">Do not collect this estimate automatically. Confirm OON benefits, allowed amount, and the clinic's collection policy first.</p>}
          <div className="v2-summary-actions">
            <button className="button button-secondary" type="button" onClick={() => void copySummary()}><Clipboard size={16} aria-hidden="true" /> {copied ? 'Copied' : 'Copy summary'}</button>
            <a className="button button-secondary" href="mailto:?subject=Insurance%20verification%20complete&body=ClinicForce%20insurance%20verification%20is%20complete.%20Sign%20in%20to%20ClinicForce%20to%20review%20the%20result."><Mail size={16} aria-hidden="true" /> Email completion notice</a>
          </div>
        </section>

        <section className="v2-prices" aria-label="Visit price comparison">
          <article><p>Scheduled visit</p><h3>{money(insuranceCase.summary.scheduled.insurance_cents)}</h3><span>{stediCoverage ? 'Stedi benefits require pricing review' : 'Availity patient estimate'}</span><dl><div><dt>Clinic cash price</dt><dd>{money(insuranceCase.summary.scheduled.cash_cents)}</dd></div><div><dt>Allowed amount</dt><dd>{money(insuranceCase.summary.scheduled.allowed_cents)}</dd></div></dl><small>{insuranceCase.summary.scheduled.codes.join(', ') || 'No scheduled code confirmed'}</small><PriceExplanation transaction={insuranceCase.transactions.estimate} /></article>
          <article><p>Possible procedure</p><h3>{money(insuranceCase.summary.possible.insurance_cents)}</h3><span>{stediCoverage ? 'Stedi benefits require pricing review' : 'Separate Availity patient estimate'}</span><dl><div><dt>Clinic cash price</dt><dd>{money(insuranceCase.summary.possible.cash_cents)}</dd></div><div><dt>Allowed amount</dt><dd>{money(insuranceCase.summary.possible.allowed_cents)}</dd></div></dl><small>{insuranceCase.summary.possible.codes.join(', ') || 'No possible procedure confirmed'}</small><PriceExplanation transaction={insuranceCase.transactions.procedure_estimate} /></article>
        </section></>}

        {stediCoverage && coverageDone && <section className="v2-manual-review" aria-labelledby="manual-review-heading">
          <div><p className="section-kicker">Stedi benefits-only path</p><h2 id="manual-review-heading">Record payer verification before scheduling</h2><p>Stedi did not return a patient-price estimate. Verify the payer portal or call the payer, record its confirmation reference, then choose either a pending insurance estimate or the clinic cash-price path.</p></div>
          {!staffMode ? <p className="v2-next-step">Clinic staff must record the payer verification. No insured amount will be shown for this case.</p> : <form className="v2-manual-review-form" onSubmit={recordManualReview}>
            <div className="v2-manual-review-grid">
              <label><span>Verification source</span><select name="source" defaultValue={insuranceCase.manual_review?.source || 'payer_portal'}><option value="payer_portal">Payer portal</option><option value="payer_call">Payer phone call</option></select></label>
              <label><span>Network status</span><select name="network_status" defaultValue={insuranceCase.manual_review?.network_status || 'unconfirmed'}><option value="unconfirmed">Not verified</option><option value="in_network">In network</option><option value="out_of_network">Out of network</option></select></label>
              <label><span>Network confirmation reference</span><input name="network_reference" defaultValue={insuranceCase.manual_review?.network_reference || ''} maxLength={200} /></label>
              <label><span>Referral status</span><select name="referral_status" defaultValue={insuranceCase.manual_review?.referral_status || 'unconfirmed'}><option value="unconfirmed">Not verified</option><option value="not_required">Not required</option><option value="approved">Approved</option><option value="required">Required / pending</option></select></label>
              <label><span>Referral confirmation reference</span><input name="referral_reference" defaultValue={insuranceCase.manual_review?.referral_reference || ''} maxLength={200} /></label>
              <label><span>Prior-authorization status</span><select name="authorization_status" defaultValue={insuranceCase.manual_review?.authorization_status || 'unconfirmed'}><option value="unconfirmed">Not verified</option><option value="not_required">Not required</option><option value="approved">Approved</option><option value="required">Required / pending</option></select></label>
              <label><span>Authorization confirmation reference</span><input name="authorization_reference" defaultValue={insuranceCase.manual_review?.authorization_reference || ''} maxLength={200} /></label>
              <label><span>Price path</span><select name="price_path" defaultValue={insuranceCase.manual_review?.price_path || 'pending'}><option value="pending">Insurance estimate pending</option><option value="cash">Offer clinic cash price only</option></select></label>
            </div>
            <p className="v2-next-step">A reference is required for every status other than “Not verified.” This record documents staff verification; it never creates an insured price estimate.</p>
            <button className="button button-primary" type="submit" disabled={!!busy}><Check size={16} aria-hidden="true" /> Save payer verification</button>
          </form>}
        </section>}

        <section className="v2-services" aria-labelledby="services-heading">
          <div><p className="section-kicker">Services and clinic prices</p><h2 id="services-heading">Confirm what is being estimated</h2><p>eClinicalWorks coded services are used first. Clinic staff use the applicable saved fee schedule; it supplies billed charges and cash prices, never insurance benefits.</p></div>
          {staffMode && <StaffLibraryPicker kinds={['fees']} disabled={servicesLocked || !!busy} onFile={(_kind, file) => uploadFees(file)} />}
          {!staffMode && <label className={`v2-fee-upload ${servicesLocked ? 'is-disabled' : ''}`}>
            <FileSpreadsheet size={22} aria-hidden="true" />
            <span><strong>{insuranceCase.sources.fees ? `${insuranceCase.sources.fees.rate_count} clinic prices loaded` : 'Upload clinic fee CSV'}</strong><small>Columns: CPT/HCPCS, Description, Cash Price and/or Billed Charge. Optional Service Status: Scheduled or Possible.</small></span>
            <input type="file" accept=".csv,text/csv" disabled={!!busy || servicesLocked} onChange={event => { const file = event.target.files?.[0]; if (file) uploadFees(file); event.currentTarget.value = '' }} />
          </label>}
          {!!insuranceCase.inputs.lines.length && <div className="v2-service-list">{insuranceCase.inputs.lines.map(line => <div key={`${line.code}-${line.service_status}`}><strong>{line.code}</strong><span>{line.description || 'Description not returned'}</span><small>{readable(line.service_status || 'scheduled')} · {line.units || 'missing'} unit(s) · billed {line.charge ? `$${line.charge}` : 'charge missing'} · cash {money(line.cash_cents)}</small></div>)}</div>}
          {servicesLocked && <p className="v2-next-step">Services and prices are locked because a payer request already uses them. Start a new case to make a change.</p>}
          {insuranceCase.inputs.prior_visit?.procedure_codes?.length ? <p className="v2-next-step">The most recent completed eCW visit ({insuranceCase.inputs.prior_visit.date || 'date not returned'}) and its procedures are supplied to the coding review as context only. They are never copied into this appointment.</p> : null}
          {insuranceCase.sources.fees && insuranceCase.inputs.reason && !servicesLocked && <button className="button button-secondary" type="button" disabled={!!busy} onClick={() => void perform('Finding possible services in the clinic fee list…', () => suggestV2Services(insuranceCase.id))}><Sparkles size={16} aria-hidden="true" /> {insuranceCase.suggestions ? 'Regenerate suggestions from visit context' : insuranceCase.inputs.prior_visit?.procedure_codes?.length ? 'Suggest services using visit and prior procedure' : 'Suggest services from visit reason'}</button>}
          {insuranceCase.suggestions && <div className="v2-suggestions">
            <div className="v2-suggestion-warning"><AlertTriangle size={18} aria-hidden="true" /><span><strong>Clinician review required.</strong> Bedrock can compare the current reason with a prior procedure and narrow the clinic’s own fee list. It cannot copy historical services, choose an E/M level, diagnose the patient, or establish medical necessity.</span></div>
            {!insuranceCase.suggestions.groups.length && <p>No service in this clinic fee CSV can be suggested safely from the eClinicalWorks visit reason.</p>}
            {insuranceCase.suggestions.groups.map(group => <fieldset key={group.id}>
              <legend>{group.label}</legend>{group.description && <p>{group.description}</p>}
              {group.codes.map(code => { const line = insuranceCase.suggestions!.lines.find(item => item.code === code)!; const choice = choices[code] || { selected: false, quantity: 1, service_status: 'scheduled' as const }; return <div className={`v2-suggestion ${choice.selected ? 'selected' : ''}`} key={code}>
                <label><input type={group.selection_mode === 'single' ? 'radio' : 'checkbox'} name={group.selection_mode === 'single' ? group.id : code} checked={choice.selected} onChange={() => toggle(code, group.codes, group.selection_mode === 'single')} /><span><strong>{code} · {line.description}</strong><small>{line.recommendation_reason}</small></span></label>
                {choice.selected && <div className="v2-suggestion-controls"><label>Quantity <input type="number" min="1" max="10000" value={choice.quantity} onChange={event => setChoices(current => ({ ...current, [code]: { ...choice, quantity: Number(event.target.value) } }))} /></label><label>Estimate as <select value={choice.service_status} onChange={event => setChoices(current => ({ ...current, [code]: { ...choice, service_status: event.target.value as Choice['service_status'] } }))}><option value="scheduled">Scheduled visit</option><option value="possible">Possible procedure</option></select></label></div>}
              </div> })}
            </fieldset>)}
            {!!insuranceCase.suggestions.notes.length && <ul>{insuranceCase.suggestions.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}
            {!!insuranceCase.suggestions.groups.length && <button className="button button-primary" type="button" disabled={!!busy} onClick={confirmServices}><Check size={16} aria-hidden="true" /> Confirm reviewed services</button>}
          </div>}
        </section>

        {ecwPreview ? <section className="v2-workflow" aria-labelledby="workflow-heading">
          <p className="section-kicker">Payer workflow</p><h2 id="workflow-heading">Availity production activation required</h2>
          <p>The live eCW appointment is available for service review, but eligibility, authorization, referral, and estimate requests are disabled. No patient data is sent to Availity’s demo API.</p>
        </section> : <section className="v2-workflow" aria-labelledby="workflow-heading">
          <p className="section-kicker">Payer workflow</p><h2 id="workflow-heading">Complete each needed payer check</h2>
          {!coverage && <button className="button button-primary" type="button" disabled={!!busy} onClick={getAnswer}>Get insurance answer</button>}
          {!coverage && <p className="v2-next-step">ClinicForce checks eligibility first. It requests an insured estimate only after any payer-required referral or authorization has an approved result. Referral and authorization remain separate actions because they create payer work.</p>}
          <div className="v2-action-list">{resources.map((resource, index) => {
            const transaction = insuranceCase.transactions[resource.id]
            const missing = insuranceCase.missing_by_resource[resource.id] || []
            const hasLines = resource.id === 'coverage' || insuranceCase.inputs.lines.some(line => (line.service_status || 'scheduled') === (resource.id === 'procedure_estimate' ? 'possible' : 'scheduled'))
            const canSubmit = !stediCoverage && (demo || missing.length === 0) && hasLines && (resource.id === 'coverage' || (coverageDone && coverageActive))
            return <article key={resource.id}>
              <div className="v2-action-number">{String(index + 1).padStart(2, '0')}</div>
              <div><h3>{resource.label}</h3><p>{resource.help}</p>
                {!transaction && <button className="button button-primary" type="button" disabled={!!busy || !canSubmit} onClick={() => run(resource.id)}>{resource.action}</button>}
                {!transaction && !canSubmit && <small className="v2-next-step">{stediCoverage && resource.id !== 'coverage' ? 'Stedi returned benefits only; review pricing, authorization, and referral manually.' : resource.id !== 'coverage' && !coverageDone ? 'Complete eligibility first.' : resource.id !== 'coverage' && !coverageActive ? 'Coverage is not active; stop the insured-price path.' : missing[0] || 'Confirm a matching service above.'}</small>}
                {transaction && <TransactionStatus transaction={transaction} />}
                {transaction && !transaction.resource_id && [401, 403, 429].includes(transaction.http_status ?? 0) && <button className="button button-secondary" type="button" disabled={!!busy} onClick={() => void perform('Retrying the rejected Availity request…', () => retryV2Transaction(insuranceCase.id, resource.id))}>Retry after access is fixed</button>}
                {transaction && ['pending', 'unavailable'].includes(transaction.outcome) && transaction.resource_id && <button className="button button-secondary" type="button" disabled={!!busy} onClick={() => void perform('Checking the saved Availity response…', () => refreshV2Transaction(insuranceCase.id, resource.id))}><RefreshCw size={16} aria-hidden="true" /> Check existing response</button>}
              </div>
            </article>
          })}</div>
        </section>}

        <details className="v2-technical">
          <summary>Patient, payer, benefits, and response details</summary>
          <div className="v2-technical-body">
            <h3>Request preview</h3>
            <dl className="v2-facts"><div><dt>Patient</dt><dd>{[insuranceCase.inputs.patient.patient_first_name, insuranceCase.inputs.patient.patient_last_name].filter(Boolean).join(' ')}</dd></div><div><dt>Date of birth</dt><dd>{insuranceCase.inputs.patient.patient_birth_date || 'Missing'}</dd></div><div><dt>Member / payer ID</dt><dd>{insuranceCase.inputs.member_id || 'Missing'} / {insuranceCase.inputs.payer_id || 'Missing'}</dd></div><div><dt>Service date</dt><dd>{insuranceCase.inputs.service_date || 'Missing'}</dd></div><div><dt>Reason</dt><dd>{insuranceCase.inputs.reason || 'Not returned'}</dd></div><div><dt>Diagnoses</dt><dd>{insuranceCase.inputs.diagnoses.join(', ') || 'Not returned'}</dd></div></dl>
            {insuranceCase.missing.length > 0 && <details className="v2-callout"><summary>Data still needed ({insuranceCase.missing.length})</summary><ul>{insuranceCase.missing.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}
            {coverage?.result?.plans?.map((plan, index) => <details className="v2-plan" key={index}><summary>{plan.name || plan.planName || `Plan ${index + 1}`} · {plan.status || 'Status not returned'}</summary><p>{plan.insuranceType} · Group {plan.groupNumber || 'not returned'} · Effective {plan.effectiveDate || 'not returned'} · Ends {plan.expirationDate || 'not returned'}</p><div className="v2-table-wrap"><table><thead><tr><th>Benefit</th><th>Network</th><th>Amount</th><th>Remaining / total</th><th>Conditions</th></tr></thead><tbody>{plan.benefits.map((benefit, row) => <tr key={row}><td>{readable(benefit.category)}<small>{benefit.service}</small></td><td>{readable(benefit.network)}<small>{benefit.level}</small></td><td>{benefit.amount ?? 'Not returned'}</td><td>{benefit.remaining ?? 'Not returned'} / {benefit.total ?? 'Not returned'}</td><td>{benefit.description}{benefit.authorizationRequired && <small>Authorization required</small>}{benefit.referralNumber && <small>Referral {benefit.referralNumber}</small>}</td></tr>)}</tbody></table></div></details>)}
            <h3>Response history</h3><ol className="v2-history">{insuranceCase.events.map((event, index) => <li key={index}><span>{readable(event.stage.replaceAll('-', ' '))}</span><time dateTime={event.at}>{dateTime(event.at)}</time></li>)}</ol>
            <div className="v2-technical-actions"><button className="button button-secondary" type="button" disabled={!!busy} onClick={() => void perform('Refreshing saved case…', () => getV2Case(insuranceCase.id))}><RefreshCw size={15} aria-hidden="true" /> Refresh saved case</button><button className="button button-secondary" type="button" disabled={!!busy || hasPending} onClick={() => void perform('Loading a new appointment…', () => createV2Case())}>{hasPending ? 'Finish pending response first' : ecwPreview ? 'Reload eCW appointment' : demo ? 'Start new demo case' : 'Reload eCW appointment'}</button></div>
            <small>{stediCoverage ? 'Stedi returned eligibility and benefits, not a patient-price estimate. Clinic cash prices come from the uploaded fee CSV.' : 'Only Availity-returned amounts appear as insured estimates. Clinic cash prices come from the uploaded fee CSV.'} Case {insuranceCase.id} {insuranceCase.expires_at ? `expires ${dateTime(insuranceCase.expires_at)}` : 'is saved to the clinic record'}.</small>
          </div>
        </details>
      </>}
    </main>
  </section>
}
