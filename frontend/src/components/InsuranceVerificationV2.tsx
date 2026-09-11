import { AlertTriangle, ArrowLeft, ArrowRight, Check, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createV2Case, getV2Case, getV2Configuration, getV2Requirements, latestV2Case, refreshV2Transaction,
  submitV2Transaction, retryV2Transaction,
  type V2Case, type V2Configuration, type V2Resource, type V2Transaction,
} from '../services/api'

interface Props { onBack: () => void }
const money = (value?: number | null) => value == null ? 'Not returned' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value / 100)
const readable = (value?: string) => value?.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ') || 'Not returned'
const dateTime = (value?: string) => value ? new Date(value).toLocaleString() : 'Not returned'
const resources: Array<{ id: V2Resource; label: string; action: string }> = [
  { id: 'coverage', label: 'Eligibility & benefits', action: 'Confirm extracted details & check eligibility' },
  { id: 'estimate', label: 'Patient-cost estimate', action: 'Request Availity estimate' },
  { id: 'authorization', label: 'Prior authorization', action: 'Submit outpatient authorization request' },
  { id: 'referral', label: 'Referral', action: 'Submit referral request' },
]
function TransactionStatus({ transaction }: { transaction: V2Transaction }) {
  return <div className={`v2-response ${transaction.outcome === 'complete' ? 'is-complete' : ''}`}>
    <div className="v2-response-heading">{transaction.outcome === 'complete' ? <Check aria-hidden="true" size={18} /> : <AlertTriangle aria-hidden="true" size={18} />}<strong>{readable(transaction.outcome)}</strong>{transaction.mock_response && <span className="v2-tag">Synthetic response</span>}</div>
    {transaction.message && <p>{transaction.message}</p>}
    {['submitting', 'indeterminate'].includes(transaction.outcome) && <p>The submission may exist at Availity. Refresh this case and contact support with the case and transaction IDs if it remains unresolved. Do not start a duplicate inquiry.</p>}
    <dl className="v2-facts"><div><dt>Availity result ID</dt><dd>{transaction.resource_id || 'Not returned'}</dd></div><div><dt>Transaction ID</dt><dd>{transaction.transaction_id || 'Not returned'}</dd></div><div><dt>Last checked</dt><dd>{dateTime(transaction.checked_at)}</dd></div></dl>
    {!!transaction.result?.notes?.length && <ul>{transaction.result.notes.map((note, n) => <li key={n}>{note}</li>)}</ul>}
  </div>
}

export default function InsuranceVerificationV2({ onBack }: Props) {
  const [config, setConfig] = useState<V2Configuration | null>(null)
  const [insuranceCase, setCase] = useState<V2Case | null>(null)
  const [busy, setBusy] = useState('Loading Availity settings…')
  const [error, setError] = useState('')
  const active = useRef(false)
  const pollStarted = useRef<Record<string, number>>({})
  const [newCasePrompt, setNewCasePrompt] = useState(false)

  useEffect(() => {
    let canceled = false
    Promise.all([getV2Configuration(), latestV2Case()]).then(([settings, latest]) => {
      if (!canceled) { setConfig(settings); setCase(latest.case) }
    }).catch((caught: unknown) => {
      if (!canceled) setError(caught instanceof Error ? caught.message : 'Unable to open Availity V2.')
    }).finally(() => { if (!canceled) setBusy('') })
    return () => { canceled = true }
  }, [])

  const perform = useCallback(async (label: string, operation: () => Promise<V2Case>) => {
    if (active.current) return
    active.current = true
    setBusy(label); setError('')
    try { setCase(await operation()) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The request could not be completed. Refresh the saved case before resubmitting.') }
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
      void perform('Checking the saved Availity transaction…', () => refreshV2Transaction(insuranceCase.id, pending.id))
    }, delay)
    return () => window.clearTimeout(timer)
  }, [insuranceCase, busy, error, perform])

  const start = () => {
    setNewCasePrompt(false)
    void perform('Starting a saved Availity case…', () => createV2Case())
  }
  const demo = config?.environment === 'demo'
  const coverageDone = insuranceCase?.transactions.coverage?.outcome === 'complete'
  const coverageActive = demo || insuranceCase?.transactions.coverage?.result?.active === true
  const ecw = insuranceCase?.sources.ecw

  if (config && (!config.credentials_configured || (!demo && (!config.production_enabled || !config.ecw_configured)))) {
    return <section className="insurance-beta insurance-v2" aria-labelledby="insurance-v2-unavailable">
      <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to clinical summary</button>
      <div className="panel">
        <p className="eyebrow"><ShieldCheck aria-hidden="true" size={17} /> Insurance verification</p>
        <h1 id="insurance-v2-unavailable">Insurance verification is currently unavailable</h1>
        <p>You can continue reviewing the patient’s shared record.</p>
      </div>
    </section>
  }

  return <section className="insurance-beta insurance-v2" aria-labelledby="insurance-v2-heading">
    <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to clinical summary</button>
    <header className="insurance-beta-hero">
      <div><p className="eyebrow"><ShieldCheck aria-hidden="true" size={17} /> Insurance beta V2 · eCW + Availity</p><h1 id="insurance-v2-heading">From appointment to payer response.</h1><p>eClinicalWorks supplies the scheduled patient and coded visit. Availity supplies eligibility, benefits, cost estimates and service-review responses.</p></div>
      <div className="beta-session"><span><i aria-hidden="true" /> {config ? `${demo ? 'Demo' : 'Production'} environment` : 'Connecting'}</span><strong>{demo ? 'Synthetic patient only' : 'Patient-specific API inquiries'}</strong><small>{demo ? 'Not live coverage or patient pricing' : 'Review extracted details before submission'}</small></div>
    </header>
    <div className="v2-layout">
      <aside className="v2-rail" aria-label="Workflow stages">
        <p className="section-kicker">Patient → Availity</p>
        <ol><li><a href="#v2-intake">eCW appointment</a></li><li><a href="#v2-payer">Payer requirements</a></li>{resources.map(r => <li key={r.id}><a href={`#v2-${r.id}`}>{r.label}</a><small>{readable(insuranceCase?.transactions[r.id]?.outcome ?? 'not_started')}</small></li>)}</ol>
        {insuranceCase && <><p className="section-kicker">Saved case</p><p className="v2-case-id">{insuranceCase.id}</p><small>Expires {dateTime(insuranceCase.expires_at)}. Reopening this QR session resumes its saved case.</small><button className="button button-secondary" type="button" disabled={!!busy} onClick={() => void perform('Refreshing saved case…', () => getV2Case(insuranceCase.id))}><RefreshCw size={15} aria-hidden="true" /> Refresh case</button></>}
      </aside>
      <main className="v2-main">
        <div className="v2-live-status" role="status" aria-live="polite">{busy ? <><LoaderCircle size={16} className="animate-spin" aria-hidden="true" /> {busy}</> : 'Case changes are saved on the server.'}</div>
        {error && <div className="form-error" role="alert"><AlertTriangle size={18} aria-hidden="true" /><span>{error}</span></div>}
        <section id="v2-intake" className="v2-section">
          <p className="section-kicker">01 · Request inputs</p><h2>{demo ? 'Use Availity’s synthetic patient' : 'Load the scheduled visit from eClinicalWorks'}</h2><p>{config?.message}</p>
          {!insuranceCase && <button className="button button-primary" type="button" disabled={!!busy || !config?.credentials_configured || (!demo && (!config?.production_enabled || !config?.ecw_configured))} onClick={start}>{demo ? 'Start demo case' : 'Load eCW appointment'}<ArrowRight size={17} aria-hidden="true" /></button>}
          {insuranceCase && <>
            {!demo && ecw && <p className="v2-source-file"><Check size={15} aria-hidden="true" /> eClinicalWorks · appointment {ecw.appointment_date || 'date not returned'} · retrieved {dateTime(ecw.retrieved_at)}</p>}
            {insuranceCase.missing.length > 0 && <details className="v2-callout" open><summary>API or practice data still needed ({insuranceCase.missing.length})</summary><ul>{insuranceCase.missing.map((item, n) => <li key={n}>{item}</li>)}</ul></details>}
            <h3>{demo ? 'Synthetic request preview' : 'eCW request preview · read only'}</h3>
            <dl className="v2-facts"><div><dt>{demo ? 'Demo patient' : 'QR patient'}</dt><dd>{[insuranceCase.inputs.patient.patient_first_name, insuranceCase.inputs.patient.patient_last_name].filter(Boolean).join(' ') || 'Missing profile'}</dd></div><div><dt>Birth date</dt><dd>{insuranceCase.inputs.patient.patient_birth_date || 'Missing'}</dd></div><div><dt>Member / payer ID</dt><dd>{insuranceCase.inputs.member_id || 'Missing'} / {insuranceCase.inputs.payer_id || 'Missing'}</dd></div><div><dt>Billing provider</dt><dd>{insuranceCase.inputs.provider.lastName || 'Not configured'}<br />NPI {insuranceCase.inputs.provider.npi || 'Missing'}</dd></div><div><dt>Service date</dt><dd>{insuranceCase.inputs.service_date || 'Missing from encounter'}</dd></div><div><dt>Diagnosis codes from order</dt><dd>{insuranceCase.inputs.diagnoses.join(', ') || 'Missing from encounter'}</dd></div></dl>
            {insuranceCase.inputs.reason && <p><strong>Visit context:</strong> {insuranceCase.inputs.reason}</p>}
            {!!insuranceCase.inputs.lines.length && <div className="v2-table-wrap"><table><caption>Services supplied by {demo ? 'the synthetic example' : 'eClinicalWorks'}</caption><thead><tr><th>Code / modifiers</th><th>Units</th><th>Billed line charge</th></tr></thead><tbody>{insuranceCase.inputs.lines.map((line, n) => <tr key={n}><td>{line.code} {line.modifiers.join(', ')}</td><td>{line.units || 'Missing'}</td><td>{line.charge == null ? 'Missing' : `$${line.charge}`}</td></tr>)}</tbody></table></div>}
          </>}
        </section>
        <section id="v2-payer" className="v2-section">
          <p className="section-kicker">02 · Payer requirements</p><h2>Check what this payer supports</h2><p>Availity’s payer directory and configuration responses identify routing, enrollment and required fields. Available products differ by payer and subscription.</p>
          {demo && <p className="v2-exclusion">Demo scenarios can run without payer configuration access. A missing demo entitlement does not establish production payer support.</p>}
          <button type="button" className="button button-secondary" disabled={!!busy || !insuranceCase?.inputs.payer_id} onClick={() => insuranceCase && void perform('Fetching Availity payer requirements…', () => getV2Requirements(insuranceCase.id))}>Fetch payer requirements</button>
          {insuranceCase?.discovery.checked_at && <><p>Checked {dateTime(insuranceCase.discovery.checked_at)}</p>{insuranceCase.discovery.message && <p className="v2-callout">{insuranceCase.discovery.message}</p>}<div className="v2-capabilities">{resources.map(r => {
            const requirement = insuranceCase.discovery.requirements?.[r.id]
            return <div key={r.id}><strong>{r.label}</strong><span>{requirement?.available ? 'Payer configuration returned' : 'Configuration unavailable'}</span>{requirement?.message && <small>{requirement.message}</small>}{requirement?.configuration?.elements && <details><summary>Required source fields</summary><ul>{Object.entries(requirement.configuration.elements).filter(([, rule]) => rule.required).map(([key, rule]) => <li key={key}>{rule.label || key}</li>)}</ul><small>The payer may enforce additional conditional requirements.</small></details>}</div>
          })}</div></>}
        </section>
        {resources.map((resource, index) => {
          const transaction = insuranceCase?.transactions[resource.id]
          const result = transaction?.result
          const stageMissing = insuranceCase ? (resource.id === 'coverage' ? insuranceCase.inputs.coverage_missing : resource.id === 'estimate' ? insuranceCase.missing : insuranceCase.inputs.review_missing) : []
          const canSubmit = insuranceCase && (demo || stageMissing.length === 0) && (resource.id === 'coverage' || (coverageDone && coverageActive))
          return <section id={`v2-${resource.id}`} className="v2-section" key={resource.id}>
            <p className="section-kicker">0{index + 3} · Availity response</p><h2>{resource.label}</h2>
            <p>{resource.id === 'coverage' ? 'Eligibility is returned for the requested patient and date. In-network benefit tables alone do not prove this clinic is in network.' : resource.id === 'estimate' ? 'Professional outpatient estimate for the coded services. Patient responsibility and allowed amounts must come from Availity; missing values remain unavailable.' : 'Submit only when staff intend to request this review. Certification may apply to specific procedures, units and dates; additional clinical information may be required by the payer.'}</p>
            {!transaction && <button className="button button-primary" type="button" disabled={!!busy || !canSubmit} onClick={() => insuranceCase && void perform(`Submitting ${resource.label.toLowerCase()} to Availity…`, () => submitV2Transaction(insuranceCase.id, resource.id))}>{demo ? `Run demo ${resource.label.toLowerCase()}` : resource.action}</button>}
            {!transaction && !canSubmit && <p className="v2-next-step">{!insuranceCase ? 'Start a case above.' : resource.id !== 'coverage' && !coverageDone ? 'Complete eligibility first.' : resource.id !== 'coverage' && !coverageActive ? 'Availity did not confirm active coverage. Review the payer response before continuing.' : 'Resolve the missing eClinicalWorks or practice data listed above.'}</p>}
            {!demo && !transaction && ['authorization', 'referral'].includes(resource.id) && stageMissing.length > 0 && <details className="v2-callout"><summary>Source information needed for this review</summary><ul>{stageMissing.map((item, n) => <li key={n}>{item}</li>)}</ul></details>}
            {transaction && <TransactionStatus transaction={transaction} />}
            {resource.id === 'coverage' && transaction?.outcome === 'complete' && <p className="v2-exclusion">Coverage decision: {result?.active === true ? 'Active coverage confirmed' : result?.active === false ? 'Inactive coverage returned' : 'Active coverage was not confirmed'}</p>}
            {transaction && !transaction.resource_id && [401, 403, 429].includes(transaction.http_status ?? 0) && <button className="button button-secondary" type="button" disabled={!!busy} onClick={() => insuranceCase && void perform('Retrying the rejected transaction…', () => retryV2Transaction(insuranceCase.id, resource.id))}>Retry after access / rate-limit issue is resolved</button>}
            {transaction && ['pending', 'unavailable'].includes(transaction.outcome) && transaction.resource_id && <><p>The saved result is checked automatically for up to one minute. You can continue checking it here; this does not submit another request.</p><button className="button button-secondary" type="button" disabled={!!busy} onClick={() => insuranceCase && void perform('Checking the existing Availity result…', () => refreshV2Transaction(insuranceCase.id, resource.id))}><RefreshCw size={16} aria-hidden="true" /> Check existing result</button></>}
            {resource.id === 'coverage' && result?.plans?.map((plan, n) => <details className="v2-plan" key={n} open={n === 0}><summary>{plan.name || plan.planName || `Plan ${n + 1}`} · {plan.status || 'Status not returned'}</summary><p>{plan.insuranceType} · Group {plan.groupNumber || 'not returned'} · Effective {plan.effectiveDate || 'not returned'} · Ends {plan.expirationDate || 'not returned'}</p><div className="v2-table-wrap"><table><caption>Benefit dimensions exactly as returned by Availity</caption><thead><tr><th>Benefit / service</th><th>Network / level</th><th>Payer amount</th><th>Remaining / total</th><th>Conditions</th></tr></thead><tbody>{plan.benefits.map((b, j) => <tr key={j}><td>{readable(b.category)}<small>{b.service}</small></td><td>{readable(b.network)}<small>{b.level}</small></td><td>{b.amount ?? 'Not returned'}<small>{b.amountTimePeriod}</small></td><td>{b.remaining ?? 'Not returned'} / {b.total ?? 'Not returned'}<small>{b.remainingTimePeriod} / {b.totalTimePeriod}</small></td><td>{b.description}{b.authorizationRequired === true && <small>Authorization required</small>}{b.authorizationRequiredUnknown && <small>Authorization requirement unknown</small>}{b.referralNumber && <small>Referral: {b.referralNumber}</small>}</td></tr>)}</tbody></table></div></details>)}
            {resource.id === 'estimate' && result && <><div className="v2-price"><span>{demo ? 'Synthetic patient responsibility' : 'Estimated patient responsibility'}</span><strong>{result.estimate_available ? money(result.patient_cents) : 'Unavailable'}</strong><p>{result.message}</p><small>Provider network: {result.provider_network || 'Not returned by Availity'}</small></div>{!!result.lines?.length && <div className="v2-table-wrap"><table><caption>{demo ? 'Canned Availity service lines — may differ from the demo request' : 'Payer-returned line estimates'}</caption><thead><tr><th>Procedure</th><th>Allowed</th><th>Deductible</th><th>Copay</th><th>Coinsurance</th><th>Patient estimate</th></tr></thead><tbody>{result.lines.map((line, n) => <tr key={n}><td>{line.code}<small>{line.description}</small></td><td>{money(line.allowed_cents)}</td><td>{money(line.deductible_cents)}</td><td>{money(line.copay_cents)}</td><td>{money(line.coinsurance_cents)}</td><td>{money(line.patient_cents)}</td></tr>)}</tbody></table></div>}<p className="estimate-disclaimer">{result.disclaimer}</p></>}
            {['authorization', 'referral'].includes(resource.id) && result && <><h3>{result.status}</h3><dl className="v2-facts"><div><dt>Certification / referral reference</dt><dd>{result.certificationNumber || 'Not returned'}</dd></div><div><dt>Effective → expiration</dt><dd>{result.certificationEffectiveDate || 'Not returned'} → {result.certificationExpirationDate || 'Not returned'}</dd></div></dl>{result.procedures?.map((line, n) => <div className="v2-review-line" key={n}><strong>{line.code || 'Procedure not returned'} · {line.status || line.statusCode || 'No decision'}</strong><p>Units: {line.quantity || 'not returned'} {line.quantityType} · Reference: {line.certificationNumber || 'not returned'}</p><p>{line.certificationEffectiveDate || line.fromDate || 'No start date'} → {line.certificationExpirationDate || line.toDate || 'No end date'}</p>{line.notes.map((note, j) => <p key={j}>{note}</p>)}</div>)}<p className="estimate-disclaimer">{result.disclaimer}</p></>}
          </section>
        })}
        {insuranceCase && <section className="v2-section"><h2>Response history</h2><ol className="v2-history">{insuranceCase.events.map((event, n) => <li key={n}><span>{readable(event.stage.replaceAll('-', ' '))}</span><time dateTime={event.at}>{dateTime(event.at)}</time></li>)}</ol><p>Only Availity-returned amounts are shown as insurance estimates. A failed or unsupported API response never falls back to the old PDF calculation.</p>{newCasePrompt ? <div className="v2-callout"><p>Start a separate case only after resolving existing pending or uncertain submissions. ClinicForce will reload the current eCW appointment; the existing case remains saved until expiry.</p><button className="button button-secondary" type="button" disabled={!!busy} onClick={() => setNewCasePrompt(false)}>Keep this case</button><button className="button button-primary" type="button" disabled={!!busy} onClick={start}>Reload eCW appointment</button></div> : <button className="button button-secondary" type="button" disabled={!!busy} onClick={() => setNewCasePrompt(true)}>Start another case</button>}</section>}
      </main>
    </div>
  </section>
}
