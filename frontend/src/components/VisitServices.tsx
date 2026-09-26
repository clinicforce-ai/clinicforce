import { useState } from 'react'
import { Activity, ArrowRight, Check, ChevronDown, Plus, Sparkles, Stethoscope } from 'lucide-react'
import type { PrevisitEstimate, VisitCode, VisitReview } from '../services/api/commercial-estimator'
const money = (value?: number | null) => value == null ? '—' : new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value / 100)
type Save = (review: VisitReview) => Promise<void>
const groupFor = (code: string) => /^992(0[2-5]|1[2-5])$/.test(code) ? 'visit' : /^959(0[7-9]|1[0-3])$/.test(code) ? 'nerve' : /^9588[5-7]$/.test(code) ? 'muscle' : 'other'
const clinicalName = (row: VisitCode) => {
  if (groupFor(row.code) === 'nerve') return 'Nerve conduction · ' + ['1–2 studies', '3–4 studies', '5–6 studies', '7–8 studies', '9–10 studies', '11–12 studies', '13 or more studies'][Number(row.code) - 95907]
  if (row.code === '95885') return 'Limited muscle examination'
  if (row.code === '95886') return 'Complete muscle examination'
  if (row.code === '95887') return 'Non-limb muscle examination'
  if (groupFor(row.code) === 'visit') return `${Number(row.code) < 99210 ? 'New patient' : 'Follow-up'} · ${['Straightforward', 'Low complexity', 'Moderate complexity', 'High complexity'][Number(row.code) % 10 - 2]}`
  return row.description || `Procedure ${row.code}`
}

export function ServicePlanner({ result, busy, save, viewEstimate }: { result: PrevisitEstimate; busy: boolean; save: Save; viewEstimate: (message?: string) => void }) {
  const available = result.available_codes || result.codes
  const defaults = result.selected_ids?.length ? result.selected_ids : result.service_plan?.recommended_ids || []
  const [selected, setSelected] = useState<string[]>(defaults)
  const [quantities, setQuantities] = useState<Record<string, number>>(() => Object.fromEntries(available.map(row => [row.id!, Number(row.units) || (row.code.startsWith('J') ? 0 : 1)])))
  const [cashSelection, setCashSelection] = useState(result.cash_quote?.selection || '')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState(false)
  const [changed, setChanged] = useState(false)
  const [editError, setEditError] = useState('')
  const emgVisit = result.service_plan?.kind === 'emg_ncs'
  const [emgRequested, setEmgRequested] = useState(emgVisit || available.some(row => selected.includes(row.id!) && ['nerve', 'muscle'].includes(groupFor(row.code))))
  const careOrder = emgVisit ? ['nerve', 'muscle', 'visit', 'other'] : ['visit', 'nerve', 'muscle', 'other']
  const chosen = available.filter(row => selected.includes(row.id!)).sort((a, b) => careOrder.indexOf(groupFor(a.code)) - careOrder.indexOf(groupFor(b.code)))
  const nerve = available.filter(row => groupFor(row.code) === 'nerve').sort((a, b) => a.code.localeCompare(b.code))
  const muscle = available.filter(row => ['95885', '95886'].includes(row.code))
  const office = available.filter(row => groupFor(row.code) === 'visit' && (result.visit_type === 'new' ? Number(row.code) < 99210 : result.visit_type === 'established' ? Number(row.code) > 99210 : true)).sort((a, b) => a.code.localeCompare(b.code))
  const other = available.filter(row => groupFor(row.code) === 'other' || row.code === '95887')
  const update = (ids: string[]) => { setSelected(ids); setChanged(true); setCashSelection('') }
  const pickOne = (rows: VisitCode[], id: string) => {
    let ids = selected.filter(key => !rows.some(row => row.id === key))
    if (rows === nerve && !id) ids = ids.filter(key => !available.some(row => row.id === key && groupFor(row.code) === 'muscle'))
    if (id) ids.push(id)
    update(ids)
  }
  const choosePackage = (id: string) => {
    setCashSelection(id); setChanged(true)
    const pack = result.cash_schedule?.packages.find(row => row.id === id)
    const ncs = nerve.find(row => row.code === pack?.ncs_example), emg = muscle.find(row => row.code === pack?.emg_example)
    if (pack && ncs && emg) { setSelected([ncs.id!, emg.id!]); setQuantities(current => ({ ...current, [ncs.id!]: 1, [emg.id!]: pack.limbs_example })) }
  }
  const choices = (rows: VisitCode[], title: string) => rows.length > 0 && <label className="plan-field">{title}<select value={rows.find(row => selected.includes(row.id!))?.id || ''} disabled={busy} onChange={e => pickOne(rows, e.target.value)}><option value="">Not included</option>{rows.map(row => <option key={row.id} value={row.id}>{clinicalName(row)} · {row.code}</option>)}</select></label>
  const openEstimate = () => {
    // Viewing the evidence is always available, including during coverage recovery.
    viewEstimate(changed && !selected.length ? 'Showing the saved estimate. Add a service to price a different plan.' : changed && busy ? 'Showing the saved estimate while the current check finishes.' : undefined)
    if (changed && chosen.some(row => !Number.isInteger(quantities[row.id!]) || quantities[row.id!] < 1 || quantities[row.id!] > 10000)) { const message = 'The edited plan needs a valid quantity. Showing the saved estimate.'; setEditError(message); viewEstimate(message); return }
    setEditError('')
    if (!busy && changed && selected.length && result.check_id && result.context_token) {
      void save({ selected_ids: selected, confirmed_units: Object.fromEntries(selected.map(id => [id, quantities[id]])), ...(result.cash_schedule ? { cash_selection: cashSelection } : {}) })
    }
  }
  return <section className="visit-planner" aria-labelledby="service-plan-title">
    <div className="visit-section-heading"><div><p className="visit-label">Anticipated care</p><h2 id="service-plan-title">Plan this visit</h2></div><span className="visit-badge"><Sparkles size={16} aria-hidden="true" /> {result.service_plan?.selection_source === 'clinician' ? 'Your plan' : result.service_plan?.method === 'llm' ? 'AI suggested' : 'Suggested plan'}</span></div>
    <p className="clinical-origin">{result.service_plan?.recommendation_basis || 'Based on the scheduled visit. Adjust the plan to match the expected care.'}</p>
    {result.visit_type === 'new' && choices(office, 'New patient consultation CPT')}
    <label className="plan-field"><span><input type="checkbox" checked={emgRequested} disabled={busy} onChange={event => {
      setEmgRequested(event.target.checked); setEditing(event.target.checked)
      if (!event.target.checked) update(selected.filter(id => !available.some(row => row.id === id && ['nerve', 'muscle'].includes(groupFor(row.code)))))
      else { const initial = nerve[0]; if (initial) update([...selected, initial.id!]) }
    }} /> Doctor requested EMG / nerve conduction testing for this visit</span></label>
    <div className="suggested-care">{chosen.length ? chosen.map((row, i) => <article key={row.id}><span className="care-icon">{groupFor(row.code) === 'visit' ? <Stethoscope aria-hidden="true" /> : <Activity aria-hidden="true" />}</span><div><small>{i === 0 ? 'Main service' : 'Supporting service'} · CPT {row.code}</small><h3>{clinicalName(row)}</h3>{groupFor(row.code) === 'muscle' && <span>{quantities[row.id!] || 1} {(quantities[row.id!] || 1) === 1 ? 'limb' : 'limbs'}</span>}</div><Check size={18} aria-label="Included" /></article>) : <p>The appointment has no specific procedure plan yet. You can still see its benefits and available prices.</p>}</div>
    {!!result.service_plan?.assumptions?.length && <details className="plan-assumptions"><summary>Why this plan?</summary><ul>{result.service_plan.assumptions.map(item => <li key={item}>{item}</li>)}</ul></details>}
    <button type="button" className="visit-text-button" aria-expanded={editing} onClick={() => setEditing(!editing)}>Change services or complexity <ChevronDown size={16} /></button>
    {editing && <div className="plan-editor">
      {result.cash_schedule && emgVisit && <label className="plan-field">Clinical package<select disabled={busy} value={cashSelection} onChange={e => choosePackage(e.target.value)}><option value="">Custom plan</option>{result.cash_schedule.packages.map(row => <option key={row.id} value={row.id}>{row.label} · {money(row.amount_cents)} cash</option>)}</select></label>}
      {result.visit_type !== 'new' && choices(office, 'Office consultation')}
      {emgRequested && choices(nerve, 'Nerve study range')}
      {emgRequested && muscle.length > 0 && <label className="plan-field">Muscle examination<select disabled={busy} value={emgRequested && muscle.filter(row => selected.includes(row.id!)).map(row => row.id).join(',')} onChange={e => {
        const ids = e.target.value ? e.target.value.split(',') : []
        let next = [...selected.filter(key => !muscle.some(row => row.id === key)), ...ids]
        if (ids.length && !nerve.some(row => next.includes(row.id!))) { const base = nerve.find(row => defaults.includes(row.id!)) || nerve[0]; if (base) next = [...next, base.id!] }
        update(next); setQuantities(current => ({ ...current, ...Object.fromEntries(ids.map(id => [id, 1])) }))
      }}><option value="">Not included</option>{nerve.length > 0 && muscle.map(row => <option key={row.id} value={row.id}>{clinicalName(row)}</option>)}{nerve.length > 0 && muscle.length === 2 && <option value={muscle.map(row => row.id).join(',')}>Limited + complete, on different limbs</option>}</select></label>}
      {emgRequested && muscle.filter(row => selected.includes(row.id!)).map(row => {
        const otherLimbs = muscle.filter(item => item.id !== row.id && selected.includes(item.id!)).reduce((sum, item) => sum + (quantities[item.id!] || 1), 0)
        return <label className="plan-field" key={row.id}>{clinicalName(row)} · extent<select disabled={busy} value={quantities[row.id!] || 1} onChange={e => { setQuantities(current => ({ ...current, [row.id!]: Number(e.target.value) })); setCashSelection(''); setChanged(true) }}>{Array.from({ length: Math.max(1, 4 - otherLimbs) }, (_, i) => <option key={i} value={i + 1}>{i + 1} {i === 0 ? 'limb' : 'limbs'}</option>)}</select></label>
      })}
      <details className="plan-additional"><summary><Plus size={16} /> Another service</summary><label className="plan-field">Find by name or CPT<input type="search" value={search} onChange={e => setSearch(e.target.value)} /></label>{other.filter(row => selected.includes(row.id!) || search && `${row.code} ${row.description}`.toLowerCase().includes(search.toLowerCase())).slice(0, 20).map(row => <label className="plan-field" key={row.id}><span><input type="checkbox" disabled={busy} checked={selected.includes(row.id!)} onChange={e => update(e.target.checked ? [...selected, row.id!] : selected.filter(id => id !== row.id))} /> {clinicalName(row)} · {row.code}</span>{selected.includes(row.id!) && <span>Planned quantity<input type="number" min="1" max="10000" value={quantities[row.id!] || ''} onChange={e => { setQuantities(current => ({ ...current, [row.id!]: Number(e.target.value) })); setChanged(true) }} /></span>}</label>)}</details>
    </div>}
    {editError && <p role="alert">{editError}</p>}
    <div className="visit-plan-footer"><span>Editable price scenario · final coding follows the care provided.</span><button type="button" className="visit-primary" onClick={openEstimate}>Continue to collection <ArrowRight size={17} /></button></div>
  </section>
}

export default function VisitServices({ result }: { result: PrevisitEstimate }) {
  const lines = result.price_range?.lines || []
  if (!lines.length) return null
  const cash = result.payment_mode === 'cash'
  return <section className="visit-services"><div className="visit-section-heading"><h2>Planned services & patient amounts</h2><span>Main service → supporting care</span></div><div className="clinical-table-wrap"><table className="clinical-service-table"><thead><tr><th>Procedure</th><th>{cash ? 'Cash price' : 'Patient obligation'}</th></tr></thead><tbody>{lines.map(row => <tr key={row.id}><th scope="row">{clinicalName(row)}<small>CPT {row.code}{Number(row.units) > 1 ? ` · ${row.units} ${groupFor(row.code) === 'muscle' ? 'limbs' : 'planned'}` : ''}</small>{!cash && <small>{`Copay: ${row.benefits.copay_cents != null ? money(row.benefits.copay_cents) : row.calculation?.cost_share === 'coinsurance' ? 'not applied' : 'not specified'} · Coinsurance: ${row.benefits.coinsurance_percent != null ? row.benefits.coinsurance_percent + '%' : row.calculation?.cost_share === 'copay' ? 'not applied' : 'not specified'}`}{row.calculation?.deductible_cents ? ` · ${money(row.calculation.deductible_cents)} to deductible` : ''}</small>}</th><td>{cash ? row.cash_included ? 'Included' : money(row.cash_cents) : row.calculation ? money(row.calculation.patient_cents) : 'Not established'}</td></tr>)}</tbody></table></div>
      {cash && result.cash_quote?.complete && <p className="clinical-origin">{result.cash_quote.message}</p>}
      <details className="visit-line-details"><summary>After-claim amounts & service evidence <ChevronDown size={16} /></summary>{lines.map(row => <article className="service-evidence" key={row.id}><h3>CPT {row.code} · {clinicalName(row)}</h3><dl><div><dt>Contracted allowance</dt><dd>{money(row.allowed_cents)}</dd></div><div><dt>Estimated total patient responsibility</dt><dd>{money(row.calculation?.patient_cents)}</dd></div><div><dt>Estimated insurer share</dt><dd>{money(row.calculation?.plan_cents)}</dd></div><div><dt>Deductible applied</dt><dd>{row.calculation ? money(row.calculation.deductible_cents) : 'Not calculated'}</dd></div><div><dt>Copay applied</dt><dd>{row.calculation ? money(row.calculation.copay_cents) : 'Not calculated'}</dd></div><div><dt>Coinsurance applied</dt><dd>{row.calculation ? money(row.calculation.coinsurance_cents) : 'Not calculated'}</dd></div><div><dt>Benefit source</dt><dd>{row.benefits.policy_evidence ? <a href={row.benefits.policy_evidence.url} target="_blank" rel="noreferrer">{row.benefits.policy_evidence.title}</a> : 'Stedi payer response'}<small>{result.checked_at ? new Date(result.checked_at).toLocaleString() : result.eligibility_failure ? 'Check rejected' : 'Not checked'}</small></dd></div><div><dt>Price source</dt><dd>{row.rate_version ? 'Practice fee schedule' : row.allowed_cents != null ? 'Practice rate' : 'No matching contracted rate'}{row.rate_effective_from && <small>Effective {row.rate_effective_from}</small>}</dd></div></dl>{!!row.issues.length && <ul>{row.issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}</article>)}</details>
  </section>
}
