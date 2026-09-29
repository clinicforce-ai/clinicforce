import { useState } from 'react'
import { ArrowRight, Check, Minus, Plus } from 'lucide-react'
import type { PrevisitEstimate, VisitCode, VisitReview } from '../services/api/commercial-estimator'
const money = (value?: number | null) => value == null ? '—' : new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value / 100)
type Save = (review: VisitReview) => Promise<boolean>
const groupFor = (code: string) => /^992(0[2-5]|1[2-5])$/.test(code) ? 'visit' : /^959(0[7-9]|1[0-3])$/.test(code) ? 'nerve' : /^9588[5-7]$/.test(code) ? 'muscle' : 'other'
const clinicalName = (row: VisitCode) => {
  if (groupFor(row.code) === 'nerve') return 'Nerve conduction · ' + ['1–2 studies', '3–4 studies', '5–6 studies', '7–8 studies', '9–10 studies', '11–12 studies', '13 or more studies'][Number(row.code) - 95907]
  if (row.code === '95885') return 'Limited muscle examination'
  if (row.code === '95886') return 'Complete muscle examination'
  if (row.code === '95887') return 'Non-limb muscle examination'
  if (groupFor(row.code) === 'visit') return `${Number(row.code) < 99210 ? 'New patient' : 'Follow-up'} · ${['Straightforward', 'Low complexity', 'Moderate complexity', 'High complexity'][Number(row.code) % 10 - 2]}`
  const injectionNames: Record<string, string> = {
    '64613': 'Retired neck injection code · remove from plan', '64614': 'Retired limb injection code · remove from plan',
    '64611': 'Salivary gland injections', '64612': 'Facial muscle injections',
    '64615': 'Chronic migraine injections', '64616': 'Neck muscle injections',
    '64642': 'First limb · 1–4 muscles', '64643': 'Additional limb · 1–4 muscles',
    '64644': 'First limb · 5 or more muscles', '64645': 'Additional limb · 5 or more muscles',
    J0585: 'Botox medication · per unit',
  }
  return injectionNames[row.code] || row.description || `Procedure ${row.code}`
}

export function ServicePlanner({ result, busy, save, viewEstimate }: { result: PrevisitEstimate; busy: boolean; save: Save; viewEstimate: (message?: string) => void }) {
  const available = result.available_codes || result.codes
  const defaults = result.selected_ids?.length ? result.selected_ids : result.service_plan?.recommended_ids || []
  const [selected, setSelected] = useState<string[]>(defaults)
  const [quantities, setQuantities] = useState<Record<string, number>>(() => Object.fromEntries(available.map(row => [row.id!, Number(row.units) || (row.code.startsWith('J') ? 0 : 1)])))
  const [cashSelection, setCashSelection] = useState(result.cash_quote?.selection || '')
  const [changed, setChanged] = useState(false)
  const [editError, setEditError] = useState('')
  const emgVisit = result.service_plan?.kind === 'emg_ncs'
  const [category, setCategory] = useState(emgVisit ? 'nerve' : 'visit')
  const chosen = available.filter(row => selected.includes(row.id!))
  const nerve = available.filter(row => groupFor(row.code) === 'nerve').sort((a, b) => a.code.localeCompare(b.code))
  const muscle = available.filter(row => ['95885', '95886'].includes(row.code))
  const office = available.filter(row => groupFor(row.code) === 'visit' && (result.visit_type === 'new' ? Number(row.code) < 99210 : result.visit_type === 'established' ? Number(row.code) > 99210 : true)).sort((a, b) => a.code.localeCompare(b.code))
  // CMS retired these two codes in 2014; retain existing selections only so staff can remove them.
  const retired = (row: VisitCode) => result.service_date >= '2014-01-01' && ['64613', '64614'].includes(row.code)
  const other = available.filter(row => (groupFor(row.code) === 'other' || row.code === '95887') && (!retired(row) || selected.includes(row.id!)))
  const update = (ids: string[]) => { setSelected(ids); setChanged(true); setCashSelection(''); setEditError('') }
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
  const apply = async (continueToCollection: boolean) => {
    if (changed) {
      if (chosen.some(retired)) { setEditError('Remove retired procedure codes before updating this plan.'); return }
      if (!chosen.length) { setEditError('Select at least one service.'); return }
      if (chosen.some(row => !Number.isInteger(quantities[row.id!]) || quantities[row.id!] < 1 || quantities[row.id!] > 10000)) { setEditError('Choose the planned quantity using the + and − buttons.'); return }
      if (chosen.filter(row => ['95885', '95886'].includes(row.code)).reduce((sum, row) => sum + quantities[row.id!], 0) > 4) { setEditError('EMG examinations can include at most four limbs in this scenario.'); return }
      if (busy || !result.check_id || !result.context_token) { setEditError('Wait for the current check before updating services.'); return }
      const saved = await save({ selected_ids: selected, confirmed_units: Object.fromEntries(selected.map(id => [id, quantities[id]])), ...(result.cash_schedule ? { cash_selection: cashSelection } : {}) })
      if (!saved) return
    }
    if (continueToCollection) viewEstimate()
  }
  const groups = [
    { id: 'visit', label: 'Office visit', rows: office, single: true },
    { id: 'nerve', label: 'Nerve study', rows: nerve, single: true },
    { id: 'muscle', label: 'Muscle EMG', rows: muscle, single: false },
    { id: 'other', label: 'Other care', rows: other, single: false },
  ].filter(group => group.rows.length)
  const activeGroup = groups.find(group => group.id === category) || groups[0]
  const quantity = (row: VisitCode, change: number) => {
    setQuantities(current => ({ ...current, [row.id!]: Math.min(groupFor(row.code) === 'muscle' ? 4 : 10000, Math.max(1, (current[row.id!] || 0) + change)) }))
    setChanged(true); setCashSelection(''); setEditError('')
  }
  return <section className="visit-planner" aria-labelledby="service-plan-title">
    <h3 id="service-plan-title">Choose care for this estimate</h3><p className="visit-muted">Selections update the estimate. Final coding follows the care provided.</p>
    <div className="selected-service-chips" aria-label="Selected services">{chosen.map(row => <span key={row.id}><Check size={14} aria-hidden="true" />{row.code} × {quantities[row.id!] || '—'}</span>)}{!chosen.length && <span>{result.price_range?.mode === 'alternatives' ? 'Office consultation · final E/M level pending' : 'No services selected'}</span>}</div>
    <div className="service-category-buttons" aria-label="Service categories">{groups.map(group => <button key={group.id} aria-pressed={activeGroup?.id === group.id} onClick={() => setCategory(group.id)}>{group.label}</button>)}</div>
    {activeGroup && <><div className="procedure-group-tools"><h3>{activeGroup.label}</h3>{activeGroup.single && <button disabled={busy || !activeGroup.rows.some(row => selected.includes(row.id!))} onClick={() => pickOne(activeGroup.rows, '')}>Remove from plan</button>}</div>
      <div className="procedure-options">{activeGroup.rows.map(row => {
        const included = selected.includes(row.id!), single = activeGroup.single
        const disable = busy || groupFor(row.code) === 'muscle' && !nerve.some(item => selected.includes(item.id!))
        return <article key={row.id} className={`procedure-choice${included ? ' is-selected' : ''}`}>
          <button className="procedure-pick" aria-pressed={included} disabled={disable} onClick={() => single ? pickOne(activeGroup.rows, row.id!) : update(included ? selected.filter(id => id !== row.id) : [...selected, row.id!])}><span className="procedure-check" aria-hidden="true">{included && <Check size={16} />}</span><span><strong>{clinicalName(row)}</strong><small>{row.code.startsWith('J') ? 'HCPCS' : 'CPT'} {row.code}</small></span></button>
          <div className="procedure-price-note">{row.unit_allowed_cents != null ? `Contract / unit ${money(row.unit_allowed_cents)}` : row.unit_cash_cents != null ? `Cash / unit ${money(row.unit_cash_cents)}` : 'Price follows plan and clinic schedule'}</div>
          {included && !single && <div className="procedure-quantity"><span>{groupFor(row.code) === 'muscle' ? 'Limbs' : row.code.startsWith('J') ? 'Medication units' : 'Quantity'}</span><div className="quantity-stepper">{row.code.startsWith('J') && <button aria-label={`Decrease ${row.code} by 10 units`} disabled={busy || quantities[row.id!] <= 1} onClick={() => quantity(row, -10)}>−10</button>}<button aria-label={`Decrease ${row.code} quantity`} disabled={busy || quantities[row.id!] <= 1} onClick={() => quantity(row, -1)}><Minus size={16} /></button><output aria-label={`${row.code} quantity`} aria-live="polite">{quantities[row.id!] || '—'}</output><button aria-label={`Increase ${row.code} quantity`} disabled={busy || quantities[row.id!] >= (groupFor(row.code) === 'muscle' ? 4 : 10000)} onClick={() => quantity(row, 1)}><Plus size={16} /></button>{row.code.startsWith('J') && <button aria-label={`Increase ${row.code} by 10 units`} disabled={busy || quantities[row.id!] >= 10000} onClick={() => quantity(row, 10)}>+10</button>}</div></div>}
        </article>
      })}</div>
      {activeGroup.id === 'muscle' && !nerve.some(item => selected.includes(item.id!)) && <p className="visit-muted">Choose a nerve study first to add needle EMG.</p>}
    </>}
    {result.payment_mode === 'cash' && result.cash_schedule && emgVisit && <section className="cash-package-options"><h3>Cash package</h3>{result.cash_schedule.packages.map(row => <button key={row.id} disabled={busy} aria-pressed={cashSelection === row.id} onClick={() => choosePackage(row.id)}>{row.label} · {money(row.amount_cents)}</button>)}</section>}
    {editError && <p className="visit-error" role="alert">{editError}</p>}
    <div className="visit-plan-footer"><span>{chosen.length} {chosen.length === 1 ? 'service' : 'services'}{changed ? ' · Changes not applied' : ' · Current estimate'}</span><button className="visit-primary" disabled={busy} onClick={() => void apply(true)}>{busy ? 'Updating estimate…' : 'Recalculate estimate'} <ArrowRight size={17} /></button></div>
  </section>
}

export default function VisitServices({ result }: { result: PrevisitEstimate }) {
  const lines = result.price_range?.lines || []
  if (!lines.length) return null
  const cash = result.payment_mode === 'cash'
  return <section className="visit-services"><div className="visit-section-heading"><h2>Planned services & patient amounts</h2></div>
    <div className="clinical-table-wrap"><table className="clinical-service-table"><thead><tr><th>Procedure</th><th>{cash ? 'Cash price' : 'Estimated patient share'}</th></tr></thead><tbody>{lines.map(row => {
      const estimate = result.collection_guidance?.services.find(item => item.id === row.id)
      return <tr key={row.id}><th scope="row">{clinicalName(row)}<small>CPT {row.code}{Number(row.units) > 1 ? ` × ${row.units}` : ''}</small><small>{estimate?.benefit}</small></th><td>{cash ? row.cash_included ? 'Included' : row.cash_cents != null ? money(row.cash_cents) : 'Billed once priced' : estimate?.patient_cents != null ? money(estimate.patient_cents) : 'Billed after the claim'}</td></tr>
    })}</tbody></table></div>
    {result.price_range?.mode === 'alternatives' && <p>One office consultation. These visit levels are alternatives, not separate charges.</p>}
    {cash && result.cash_quote?.complete && <p>{result.cash_quote.message}</p>}
  </section>
}
