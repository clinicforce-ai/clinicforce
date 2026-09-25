import { useState } from 'react'
import type { PrevisitEstimate, VisitCode, VisitPricedLine, VisitReview } from '../services/api/commercial-estimator'
const money = (value?: number | null) => value == null ? 'Needs review' : new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value / 100)
const label = (value?: string) => ({ '27': 'Per visit', VISIT: 'Per visit', SERVICE: 'Per service', PROCEDURE: 'Per procedure', valid: 'Received and valid', not_applicable: 'Not applicable', not_required: 'Not required', not_verified: 'Not verified' }[value || ''] || (value || 'Not verified').replaceAll('_', ' '))
const stamp = (value?: string | null) => value ? new Date(value).toLocaleString() : 'Time not supplied'
type Save = (review: VisitReview) => Promise<void>

function Verification({ line, kind, result, busy, save }: { line: VisitPricedLine; kind: 'referral' | 'authorization' | 'benefits'; result: PrevisitEstimate; busy: boolean; save: Save }) {
  const [method, setMethod] = useState('copay')
  const title = kind === 'benefits' ? 'Benefit applicability' : kind === 'referral' ? 'Referral verification' : 'Prior authorization verification'
  const options = (line.benefits.reported_options || []).filter(row => row.kind === 'copay' || row.kind === 'coinsurance')
  return <details className="visit-review-form"><summary>{title}</summary><form onSubmit={event => {
    event.preventDefault(); const data = new FormData(event.currentTarget)
    void save({ verification: { line_id: line.id, kind, status: String(data.get('status') || 'valid'), reference: String(data.get('reference')), notes: String(data.get('notes')), valid_from: String(data.get('valid_from')), valid_to: String(data.get('valid_to')), units: Number(data.get('units')), ...(kind === 'benefits' ? { cost_share: method, deductible_applies: data.get('deductible') === 'yes', selected_ids: [data.get('copay'), data.get('coinsurance')].filter(Boolean).map(String) } : {}) } })
  }}>
    <p>Record evidence for CPT {line.code}, this patient, provider and visit. Saving rechecks eCW and recalculates using the saved payer response.</p>
    {kind !== 'benefits' ? <label>Verified outcome<select name="status" disabled={busy}><option value="valid">Received and valid for this service</option>{line.benefits.requirements[kind].required !== 'required' && <option value="not_required">Verified not required</option>}<option value="pending">Pending</option><option value="denied">Denied or invalid</option></select></label> : <>
      <label>Applicable cost sharing<select value={method} onChange={e => setMethod(e.target.value)} disabled={busy}><option value="copay">Copay</option><option value="coinsurance">Coinsurance</option><option value="cumulative">Copay and coinsurance</option></select></label>
      {(['copay', 'coinsurance'] as const).filter(kind => method === kind || method === 'cumulative').map(type => <label key={type}>Returned {type} term<select name={type} required disabled={busy} defaultValue=""><option value="" disabled>Select the verified payer term</option>{options.filter(row => row.kind === type && row.id).map(row => <option key={row.id} value={row.id}>{type === 'copay' ? money(row.amount_cents) : `${row.percent}%`} · {row.procedure_code || row.service_type_code || 'Scope not stated'} · {label(row.network)} · {row.payer_note || 'No additional note'}</option>)}</select></label>)}
      <label>Does the deductible apply?<select name="deductible" required disabled={busy} defaultValue=""><option value="" disabled>Verify with the plan evidence</option><option value="yes">Yes</option><option value="no">No — evidence confirms a waiver</option></select></label>
    </>}
    <div className="visit-form-grid"><label>Valid from<input name="valid_from" type="date" defaultValue={result.service_date} required disabled={busy} /></label><label>Valid through<input name="valid_to" type="date" defaultValue={result.service_date} required disabled={busy} /></label><label>{['95885', '95886'].includes(line.code) ? 'Limbs covered by this approval' : 'Visits or procedures covered'}<input name="units" type="number" min="1" max="10000" step="1" defaultValue={Number(line.units) || 1} required disabled={busy} /></label></div>
    <label>Evidence or approval reference<input name="reference" minLength={3} maxLength={500} placeholder="eCW document, approval number or payer reference" required disabled={busy} /></label>
    <label>What was verified?<textarea name="notes" minLength={10} maxLength={2000} placeholder="Document the service, provider and any applicable benefit conditions." required disabled={busy} /></label>
    <button className="visit-primary" disabled={busy || !line.units}>Save verification and recalculate</button>
  </form></details>
}

const groupFor = (code: string) => /^992(0[2-5]|1[2-5])$/.test(code) ? 'visit' : /^959(0[7-9]|1[0-3])$/.test(code) ? 'nerve' : /^9588[5-7]$/.test(code) ? 'muscle' : 'other'
const clinicalName = (row: VisitCode) => {
  const nerve = ['1–2 studies', '3–4 studies', '5–6 studies', '7–8 studies', '9–10 studies', '11–12 studies', '13 or more studies']
  if (groupFor(row.code) === 'nerve') return nerve[Number(row.code) - 95907]
  if (row.code === '95885') return 'Limited muscle examination'
  if (row.code === '95886') return 'Complete muscle examination'
  if (row.code === '95887') return 'Non-limb muscle examination'
  if (groupFor(row.code) === 'visit') return `${Number(row.code) < 99210 ? 'New patient' : 'Established patient'} · ${['Straightforward', 'Low complexity', 'Moderate complexity', 'High complexity'][Number(row.code) % 10 - 2]}`
  return row.description || `Procedure ${row.code}`
}

export function ServicePlanner({ result, busy, save }: { result: PrevisitEstimate; busy: boolean; save: Save }) {
  const available = result.available_codes || result.codes
  const groups = result.service_plan?.groups || ['visit', 'nerve', 'muscle', 'other'].map(id => ({ id, label: { visit: 'Office consultation', nerve: 'Nerve conduction study', muscle: 'Needle EMG', other: 'Other services' }[id], description: 'Select the services anticipated for this appointment.', role: id === 'muscle' ? 'Additional services' : 'Main service', selection_mode: ['visit', 'nerve'].includes(id) ? 'single' : 'multiple', ids: available.filter(row => groupFor(row.code) === id).map(row => row.id!) })).filter(group => group.ids.length)
  const defaults = result.selected_ids?.length ? result.selected_ids : result.service_plan?.recommended_ids || available.filter(row => row.source === 'eCW scheduled service').map(row => row.id!)
  const [selected, setSelected] = useState<string[]>(defaults)
  const [quantities, setQuantities] = useState<Record<string, number>>(() => Object.fromEntries(available.map(row => [row.id!, Number(row.units) || (row.code === 'J0585' ? 0 : 1)])))
  const [cashSelection, setCashSelection] = useState(result.cash_quote?.selection || '')
  const [search, setSearch] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [error, setError] = useState('')
  const ids = groups.flatMap(group => group.ids).filter(id => selected.includes(id))
  const chosen = ids.map(id => available.find(row => row.id === id)!).filter(Boolean)
  const missingCompanion = chosen.some(row => groupFor(row.code) === 'muscle') && !chosen.some(row => groupFor(row.code) === 'nerve')
  const total = (key: 'unit_allowed_cents' | 'unit_cash_cents') => chosen.length && chosen.every(row => row[key] != null) ? chosen.reduce((sum, row) => sum + row[key]! * quantities[row.id!], 0) : null
  const toggle = (id: string, group: typeof groups[number]) => setSelected(current => current.includes(id) ? current.filter(value => value !== id) : [...current.filter(value => group.selection_mode !== 'single' || !group.ids.includes(value)), id])
  return <section className="visit-planner" aria-labelledby="service-plan-title">
    <div className="visit-section-heading"><div><p className="visit-label">Before the patient arrives</p><h2 id="service-plan-title">Plan the scope of care</h2></div><span className="visit-badge">{result.service_plan?.kind === 'emg_ncs' ? 'EMG / nerve study' : result.visit_type === 'new' ? 'New patient visit' : result.visit_type === 'established' ? 'Follow-up visit' : 'Scheduled care'}</span></div>
    <p className="visit-muted">Review the anticipated procedures. ClinicForce puts the main service and supporting services in clinical sequence.</p>
    {result.service_plan?.recommendation_basis && <p className="visit-notice">{result.service_plan.recommendation_basis}</p>}
    <form onSubmit={event => { event.preventDefault(); if (missingCompanion) { setError('Choose the nerve study that accompanies the muscle examination.'); return } setError(''); void save({ ...(result.cash_schedule ? { cash_selection: cashSelection } : {}), selected_ids: ids, confirmed_units: Object.fromEntries(ids.map(id => [id, quantities[id]])) }) }}>
      <div className="visit-planner-tools"><label className="visit-search">Find a procedure<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Procedure name or CPT" /></label><button type="button" className="visit-text-button" aria-pressed={showAll} onClick={() => setShowAll(!showAll)}>{showAll ? 'Show appointment matches' : 'Browse all clinic services'}</button></div>
      {groups.map(group => {
        const rows = available.filter(row => group.ids.includes(row.id!) && (search ? `${row.code} ${row.description} ${clinicalName(row)}`.toLowerCase().includes(search.toLowerCase()) : showAll || selected.includes(row.id!) || result.codes.some(code => code.id === row.id) || (group.id === 'visit' && (result.visit_type === 'new' ? /^9920[2-5]$/.test(row.code) : result.visit_type === 'established' && /^9921[2-5]$/.test(row.code))) || (result.service_plan?.kind === 'emg_ncs' && ['nerve', 'muscle'].includes(group.id))))
        if (!rows.length) return null
        return <fieldset className="visit-procedure-group" key={group.id} disabled={busy}><legend><span>{group.role}</span>{group.label}</legend><p>{group.description}</p><div className="visit-procedure-options">{rows.map(row => <div className={`visit-procedure-option${selected.includes(row.id!) ? ' is-selected' : ''}`} key={row.id}>
          <label><input type={group.selection_mode === 'single' ? 'radio' : 'checkbox'} name={group.id} checked={selected.includes(row.id!)} onChange={() => toggle(row.id!, group)} /><span><strong>{clinicalName(row)}</strong><small>CPT {row.code}{row.source === 'eCW scheduled service' ? ' · Planned in eCW' : result.service_plan?.recommended_ids.includes(row.id!) ? ' · Suggested from eCW history' : ''}</small></span><span className="visit-option-price"><strong>{row.unit_allowed_cents == null ? 'Match rate' : money(row.unit_allowed_cents)}</strong><small>{['95885', '95886'].includes(row.code) ? 'contracted / limb' : 'contracted price'}</small></span></label>
          {selected.includes(row.id!) && !['visit', 'nerve'].includes(group.id) && <label className="visit-clinical-count">{['95885', '95886'].includes(row.code) ? 'Limbs to examine' : row.code === '95887' ? 'Sites to examine' : row.code === 'J0585' ? 'Botox drug units planned' : 'Times planned'}<input aria-label={`${clinicalName(row)} ${['95885', '95886'].includes(row.code) ? 'limbs to examine' : 'times planned'}`} type="number" min="1" max={['95885', '95886'].includes(row.code) ? 4 : 10000} step="1" required value={quantities[row.id!] || ''} onChange={event => setQuantities(current => ({ ...current, [row.id!]: Number(event.target.value) }))} /><small>{row.source === 'eCW scheduled service' && row.units ? 'Loaded from the eCW service' : 'Planning assumption — adjust for this visit'}</small></label>}
        </div>)}</div>{group.selection_mode === 'single' && group.ids.some(id => selected.includes(id)) && <button type="button" className="visit-text-button" onClick={() => setSelected(current => current.filter(id => !group.ids.includes(id)))}>Clear {group.label}</button>}</fieldset>
      })}
      {result.cash_schedule && <fieldset className="visit-procedure-group" disabled={busy}><legend><span>NMCT direct-pay price</span>Cash option for this scope of care</legend>
        <p>Office visit: $250 new patient or $150 established patient. Botox: $450 per session + $7.50 per drug unit.</p>
        <label>Cash pricing choice<select aria-label="Cash pricing choice" value={cashSelection} onChange={event => setCashSelection(event.target.value)}><option value="">Standard office / Botox pricing, or choose an EMG package</option>
          {chosen.some(row => ['nerve', 'muscle'].includes(groupFor(row.code))) && result.cash_schedule.packages.map(row => <option key={row.id} value={row.id}>{row.label} · {money(row.amount_cents)}</option>)}
          {chosen.length > 0 && chosen.every(row => /^9920[2-5]$/.test(row.code)) && <option value="friends_family">Friends & Family · new patient · $100</option>}
          {cashSelection && !result.cash_schedule.packages.some(row => row.id === cashSelection && chosen.some(item => ['nerve', 'muscle'].includes(groupFor(item.code)))) && !(cashSelection === 'friends_family' && chosen.length > 0 && chosen.every(row => /^9920[2-5]$/.test(row.code))) && <option value={cashSelection}>Previous cash choice — review for these services</option>}
        </select></label><p>{result.cash_schedule.package_includes}</p><p className="visit-muted">Select the package matching symptoms and the intended examination. The example CPT codes do not determine a package. Expanded testing requires a revised quote.</p>
        <details><summary>Source and proposed services</summary><p>{result.cash_schedule.source} · Effective {result.cash_schedule.effective_from}</p><p>{result.cash_schedule.proposed}</p></details>
      </fieldset>}
      {!available.length && <p className="visit-notice">Add the clinic’s procedure prices for this payer, provider and location to start a priced visit plan.</p>}
      {(error || missingCompanion) && <p role="alert" className="visit-notice">{error || 'Include a nerve conduction study with the selected needle EMG.'}</p>}
      <div className="visit-plan-footer"><div><strong>{chosen.length ? `${chosen.length} planned ${chosen.length === 1 ? 'service' : 'services'}` : 'Choose anticipated services'}</strong><span>Contracted {total('unit_allowed_cents') == null ? '— match clinic rates' : money(total('unit_allowed_cents'))} · Cash {result.cash_schedule ? '— calculated from the NMCT choice' : total('unit_cash_cents') == null ? '— add cash schedule' : money(total('unit_cash_cents'))}</span></div><button className="visit-primary" disabled={busy || !ids.length || missingCompanion || !result.check_id || !result.context_token}>{busy ? 'Updating…' : 'See patient estimate'}</button></div>
      <p className="visit-muted">These are planning choices. The clinician confirms the services actually provided after care.</p>
    </form>
  </section>
}

export default function VisitServices({ result, busy, save, approvals = false }: { result: PrevisitEstimate; busy: boolean; save: Save; approvals?: boolean }) {
  const lines = result.price_range?.lines || []
  const canReview = !!result.check_id && !!result.context_token && !result.eligibility_error
  const alternatives = result.price_range?.mode !== 'services'
  return <section className="visit-services" aria-labelledby="visit-services-title">
    <div className="visit-section-heading"><h2 id="visit-services-title">{approvals ? 'Resolve service approvals' : 'How the price is calculated'}</h2><span>{alternatives ? 'Compare individual alternatives' : 'Main service → supporting services'}</span></div>
    {lines.map((line, index) => <article className="visit-cost-card" key={line.id}>
      <header><span className="visit-sequence">{index + 1}</span><div><h3>{clinicalName(line)}</h3><p>CPT {line.code}{Number(line.units) > 1 ? ` · ${line.units} ${['95885', '95886'].includes(line.code) ? 'limbs' : 'planned services'}` : ''} · {line.source === 'eCW scheduled service' ? 'eCW appointment' : 'Anticipated care'}</p></div></header>
      {!approvals && <><div className="visit-cost-values"><div><span>Cash price</span><strong>{line.cash_included ? 'Included in cash package' : line.cash_cents == null ? (result.cash_schedule ? 'See cash quote above' : 'Add cash rate') : money(line.cash_cents)}</strong></div><div><span>Contracted amount</span><strong>{line.allowed_cents == null ? 'Match clinic rate' : money(line.allowed_cents)}</strong></div><div><span>Patient pays</span><strong>{line.calculation ? money(line.calculation.patient_cents) : 'Resolve benefit terms'}</strong></div><div><span>Insurance pays</span><strong>{line.calculation ? money(line.calculation.plan_cents) : 'Resolve benefit terms'}</strong></div></div>
      {line.calculation && <div className="visit-math"><p>The patient’s {money(line.calculation.patient_cents)} = <strong>{money(line.calculation.deductible_cents)}</strong> toward the deductible + <strong>{money(line.calculation.copay_cents)}</strong> copay + <strong>{money(line.calculation.coinsurance_cents)}</strong> coinsurance{line.calculation.oop_adjustment_cents > 0 ? ` − ${money(line.calculation.oop_adjustment_cents)} because of the out-of-pocket limit` : ''}.</p><p>Insurance covers the remaining <strong>{money(line.calculation.plan_cents)}</strong> of the contracted amount.</p></div>}
      <dl className="visit-benefit-values"><div><dt>Copay rule</dt><dd>{line.benefits.copay_cents != null ? `${money(line.benefits.copay_cents)} · ${label(line.benefits.copay_period)}` : line.calculation?.cost_share === 'coinsurance' ? 'Coinsurance applies' : 'Select the applicable payer term'}</dd></div><div><dt>Coinsurance rule</dt><dd>{line.benefits.coinsurance_percent != null ? `${line.benefits.coinsurance_percent}% patient share` : line.calculation?.cost_share === 'copay' ? 'Copay applies' : 'Select the applicable payer term'}</dd></div></dl></>}
      {!!line.conditions?.length && <div className="visit-notice"><strong>Planning conditions</strong><ul>{line.conditions.map(item => <li key={item}>{item}</li>)}</ul></div>}
      {!!line.issues.length && <div className="visit-notice"><strong>To complete this estimate</strong><ul>{line.issues.map(issue => <li key={issue}>{issue}</li>)}</ul></div>}
      {approvals ? <><dl className="visit-benefit-values">{(['referral', 'authorization'] as const).map(kind => { const item = line.benefits.requirements[kind]; return <div key={kind}><dt>{kind === 'referral' ? 'Referral' : 'Prior authorization'}</dt><dd>{item.cleared ? label(item.status) : item.required === 'required' ? 'Required · verify approval' : 'Check requirement in plan / eCW'}{item.reference && <small>{item.reference} · {stamp(item.verified_at)}</small>}</dd></div> })}</dl>{canReview && (['referral', 'authorization', 'benefits'] as const).map(kind => <Verification key={kind} line={line} kind={kind} result={result} busy={busy} save={save} />)}</> : <details className="visit-line-details"><summary>Evidence for this calculation</summary><div className="visit-detail-body"><p>Benefits: Stedi · {line.benefit_basis} · {stamp(result.checked_at)}</p><p>Contract: {line.rate_source}{line.rate_version && ` · Version ${line.rate_version}`}{line.rate_effective_from && ` · Effective ${line.rate_effective_from}`}</p>{line.cash_source && <p>Cash: {line.cash_source}</p>}{line.benefit_verification && <p>Benefit applicability: {line.benefit_verification.reference} · {line.benefit_verification.verified_by} · {stamp(line.benefit_verification.verified_at)}<br />{line.benefit_verification.notes}</p>}</div></details>}
    </article>)}
    {!lines.length && <p className="visit-notice">Choose the anticipated services in Plan visit to see the service-level breakdown.</p>}
    {!approvals && canReview && result.price_range?.complete && !result.price_range.conditional && !alternatives && <details className="visit-review-form"><summary>Confirm payments already received</summary><form onSubmit={event => { event.preventDefault(); const data = new FormData(event.currentTarget); void save({ payments_received_cents: Math.round(Number(data.get('amount')) * 100), payment_reference: String(data.get('reference')) }) }}><p>Check the clinic payment ledger for this visit. This records verification; it does not charge the patient.</p><label>Payments received for this visit ($)<input name="amount" type="number" min="0" max="1000000" step="0.01" required disabled={busy} /></label><label>Payment ledger reference<input name="reference" minLength={3} maxLength={500} required disabled={busy} /></label><button className="visit-primary" disabled={busy}>Update collection recommendation</button></form></details>}
  </section>
}
