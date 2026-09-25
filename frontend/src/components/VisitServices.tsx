import { useState } from 'react'
import type { PrevisitEstimate, VisitCode, VisitReview } from '../services/api/commercial-estimator'
const money = (value?: number | null) => value == null ? '—' : new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value / 100)
type Save = (review: VisitReview) => Promise<void>
const groupFor = (code: string) => /^992(0[2-5]|1[2-5])$/.test(code) ? 'visit' : /^959(0[7-9]|1[0-3])$/.test(code) ? 'nerve' : /^9588[5-7]$/.test(code) ? 'muscle' : 'other'
const clinicalName = (row: VisitCode) => {
  if (groupFor(row.code) === 'nerve') return ['1–2 studies', '3–4 studies', '5–6 studies', '7–8 studies', '9–10 studies', '11–12 studies', '13 or more studies'][Number(row.code) - 95907]
  if (row.code === '95885') return 'Limited muscle examination'
  if (row.code === '95886') return 'Complete muscle examination'
  if (row.code === '95887') return 'Non-limb muscle examination'
  if (groupFor(row.code) === 'visit') return `${Number(row.code) < 99210 ? 'New patient' : 'Follow-up'} · ${['Straightforward', 'Low complexity', 'Moderate complexity', 'High complexity'][Number(row.code) % 10 - 2]}`
  return row.description || `Procedure ${row.code}`
}

export function ServicePlanner({ result, busy, save }: { result: PrevisitEstimate; busy: boolean; save: Save }) {
  const available = result.available_codes || result.codes
  const defaults = result.selected_ids?.length ? result.selected_ids : result.service_plan?.recommended_ids || []
  const [selected, setSelected] = useState<string[]>(defaults)
  const [quantities, setQuantities] = useState<Record<string, number>>(() => Object.fromEntries(available.map(row => [row.id!, Number(row.units) || (row.code === 'J0585' ? 0 : 1)])))
  const [cashSelection, setCashSelection] = useState(result.cash_quote?.selection || '')
  const [search, setSearch] = useState('')
  const emgVisit = result.service_plan?.kind === 'emg_ncs'
  const careOrder = emgVisit ? ['nerve', 'muscle', 'visit', 'other'] : ['visit', 'nerve', 'muscle', 'other']
  const chosen = available.filter(row => selected.includes(row.id!)).sort((a, b) => careOrder.indexOf(groupFor(a.code)) - careOrder.indexOf(groupFor(b.code)))
  const nerve = available.filter(row => groupFor(row.code) === 'nerve').sort((a, b) => a.code.localeCompare(b.code))
  const muscle = available.filter(row => ['95885', '95886'].includes(row.code))
  const office = available.filter(row => groupFor(row.code) === 'visit' && (result.visit_type === 'new' ? Number(row.code) < 99210 : result.visit_type === 'established' ? Number(row.code) > 99210 : true)).sort((a, b) => a.code.localeCompare(b.code))
  const other = available.filter(row => groupFor(row.code) === 'other' || row.code === '95887')
  const pickOne = (rows: VisitCode[], id?: string) => setSelected(current => [...current.filter(key => !rows.some(row => row.id === key)), ...(id ? [id] : [])])
  const packageChoice = result.cash_schedule?.packages.find(row => row.id === cashSelection)
  const limbs = chosen.filter(row => groupFor(row.code) === 'muscle').reduce((sum, row) => sum + quantities[row.id!], 0)
  const missingNerve = chosen.some(row => groupFor(row.code) === 'muscle') && !chosen.some(row => groupFor(row.code) === 'nerve')
  const tooManyLimbs = chosen.filter(row => ['95885', '95886'].includes(row.code)).reduce((sum, row) => sum + quantities[row.id!], 0) > 4
  const exceedsPackage = !!packageChoice && limbs > packageChoice.max_limbs
  const choosePackage = (id: string) => {
    setCashSelection(id)
    const pack = result.cash_schedule?.packages.find(row => row.id === id)
    if (!pack) return
    const ncs = nerve.find(row => row.code === pack.ncs_example)
    const emg = muscle.find(row => row.code === pack.emg_example)
    if (ncs && emg) {
      setSelected([ncs.id!, emg.id!])
      setQuantities(current => ({ ...current, [ncs.id!]: 1, [emg.id!]: pack.limbs_example }))
    }
  }
  const rangeControl = (rows: VisitCode[], title: string, detail: string) => {
    if (!rows.length) return null
    const active = rows.find(row => selected.includes(row.id!))
    const index = Math.max(0, rows.findIndex(row => row.id === active?.id))
    return <fieldset className="clinical-range" disabled={busy}><legend>{title}</legend><div className="clinical-range-heading"><label className="clinical-include"><input type="checkbox" checked={!!active} onChange={e => pickOne(rows, e.target.checked ? rows[index].id : undefined)} />Include this service</label><output>{active ? clinicalName(active) : 'Choose a range'}<small>{active ? `CPT ${active.code}` : `${rows[0].code}–${rows.at(-1)!.code}`}</small></output></div>
      <input className="clinical-slider" aria-label={title} type="range" min="0" max={rows.length - 1} step="1" value={index} aria-valuetext={clinicalName(rows[index])} onChange={e => pickOne(rows, rows[Number(e.target.value)].id)} onPointerUp={e => pickOne(rows, rows[Number(e.currentTarget.value)].id)} />
      <div className="clinical-range-stops">{rows.map(row => <button type="button" key={row.id} aria-label={clinicalName(row)} aria-pressed={active?.id === row.id} onClick={() => pickOne(rows, row.id)}>{title === 'Nerve conduction studies' ? ['1–2', '3–4', '5–6', '7–8', '9–10', '11–12', '13+'][Number(row.code) - 95907] : row.code}</button>)}</div><p>{detail}</p>
    </fieldset>
  }
  return <section className="visit-planner" aria-labelledby="service-plan-title"><div className="visit-section-heading"><div><p className="visit-label">Anticipated care</p><h2 id="service-plan-title">Plan this visit</h2></div><span className="visit-badge">{emgVisit ? 'EMG / nerve study' : result.visit_type === 'new' ? 'New patient' : 'Office visit'}</span></div>
    <p className="visit-muted">Choose the clinical scope, then adjust the expected level. Final coding follows the care provided.</p>
    {result.service_plan?.recommendation_basis && <p className="clinical-origin">{result.service_plan.recommendation_basis}</p>}
    <form onSubmit={event => { event.preventDefault(); void save({ selected_ids: selected, confirmed_units: Object.fromEntries(selected.map(id => [id, quantities[id]])), ...(result.cash_schedule ? { cash_selection: cashSelection } : {}) }) }}>
      {result.cash_schedule && (emgVisit || chosen.some(row => groupFor(row.code) === 'nerve')) && <fieldset className="clinical-scenario" disabled={busy}><legend>Procedure scenario</legend><label>Clinical package<select aria-label="Clinical package" value={packageChoice ? cashSelection : ''} onChange={e => choosePackage(e.target.value)}><option value="">Custom scope · choose a package for a cash quote</option>{result.cash_schedule.packages.map(row => <option key={row.id} value={row.id}>{row.label} · {money(row.amount_cents)}</option>)}</select></label><p>Package selection loads the clinic’s example study range. Adjust it for this patient.</p></fieldset>}
      {!emgVisit && rangeControl(office, 'Office consultation', result.cash_schedule ? 'One anticipated level for the visit. The NMCT cash fee is the same across levels.' : 'Choose the anticipated consultation level.')}
      {(emgVisit || chosen.some(row => ['nerve', 'muscle'].includes(groupFor(row.code)))) && <div className="clinical-study-grid">
        {rangeControl(nerve, 'Nerve conduction studies', 'One range for the total nerve studies across all examined limbs.')}
        {!!muscle.length && <fieldset className="clinical-range" disabled={busy}><legend>Supporting muscle examination</legend><div className="clinical-segments"><button type="button" aria-pressed={!muscle.some(row => selected.includes(row.id!))} onClick={() => pickOne(muscle)}>No needle EMG</button>{muscle.map(row => <button type="button" key={row.id} aria-pressed={selected.includes(row.id!)} onClick={() => pickOne(muscle, row.id)}>{row.code === '95885' ? 'Limited' : 'Complete'}<small>CPT {row.code}</small></button>)}{muscle.length === 2 && <button type="button" aria-pressed={muscle.every(row => selected.includes(row.id!))} onClick={() => setSelected(current => [...current.filter(key => !muscle.some(row => row.id === key)), ...muscle.map(row => row.id!)])}>Mixed<small>Different limbs</small></button>}</div>
          {muscle.filter(row => selected.includes(row.id!)).map(row => <label className="clinical-limbs" key={row.id}>{clinicalName(row)} · <strong>{quantities[row.id!]} {quantities[row.id!] === 1 ? 'limb' : 'limbs'}</strong><input className="clinical-slider" aria-label={`${clinicalName(row)} limbs`} type="range" min="1" max="4" step="1" value={quantities[row.id!] || 1} onChange={e => setQuantities(current => ({ ...current, [row.id!]: Number(e.target.value) }))} /><span>1 limb <span>4 limbs</span></span></label>)}
        </fieldset>}
      </div>}
      <details className="visit-line-details"><summary>{emgVisit ? 'Add an office consultation or another service' : 'Add nerve testing or another service'}</summary>
        {emgVisit ? rangeControl(office, 'Office consultation', result.cash_schedule ? 'A focused evaluation is already included in the EMG cash package.' : 'Include a separate consultation only when it is part of the planned care.') : rangeControl(nerve, 'Nerve conduction studies', 'Choose one range for the expected studies.')}
        <label className="visit-search">Find another service<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Name or CPT" /></label>
        <div className="clinical-extra-list">{other.filter(row => selected.includes(row.id!) || search && `${row.code} ${row.description}`.toLowerCase().includes(search.toLowerCase())).slice(0, 20).map(row => <div key={row.id}><label><input type="checkbox" checked={selected.includes(row.id!)} onChange={e => setSelected(current => e.target.checked ? [...current, row.id!] : current.filter(id => id !== row.id))} />{clinicalName(row)} <small>CPT {row.code}</small></label>{selected.includes(row.id!) && <label>{row.code === 'J0585' ? 'Botox drug units' : 'Number planned'}<input type="number" required min="1" max="10000" value={quantities[row.id!] || ''} onChange={e => setQuantities(current => ({ ...current, [row.id!]: Number(e.target.value) }))} /></label>}</div>)}</div>
      </details>
      {result.cash_schedule && chosen.length > 0 && chosen.every(row => /^9920[2-5]$/.test(row.code)) && <label className="clinical-discount"><input type="checkbox" checked={cashSelection === 'friends_family'} onChange={e => setCashSelection(e.target.checked ? 'friends_family' : '')} />Apply NMCT Friends & Family new-patient cash price · $100</label>}
      {missingNerve && <p role="alert">Include the nerve study that accompanies the muscle examination.</p>}
      {tooManyLimbs && <p role="alert">Choose up to four distinct limbs across the limited and complete examinations.</p>}
      {exceedsPackage && <p role="alert">This plan covers {limbs} limbs; the selected cash package covers {packageChoice?.max_limbs}. Choose the package matching the intended scope.</p>}
      <div className="visit-plan-footer"><div><strong>{chosen.length ? chosen.map(row => row.code).join(' + ') : 'Choose the anticipated care'}</strong><span>{packageChoice ? `${packageChoice.label} · ${money(packageChoice.amount_cents)} cash, including evaluation and report` : 'Your estimate will show the relevant plan benefits and clinic price.'}</span></div><button className="visit-primary" disabled={busy || !selected.length || missingNerve || exceedsPackage || tooManyLimbs || !result.check_id || !result.context_token}>{busy ? 'Calculating…' : 'See patient estimate'}</button></div>
    </form>
  </section>
}

export default function VisitServices({ result }: { result: PrevisitEstimate }) {
  const lines = result.price_range?.lines || []
  if (!lines.length) return null
  const complete = result.price_range?.complete && !result.price_range?.conditional
  const cash = result.payment_mode === 'cash'
  return <section className="visit-services"><div className="visit-section-heading"><h2>Services in this visit</h2><span>Main service → supporting care</span></div><div className="clinical-table-wrap"><table className="clinical-service-table"><thead><tr><th>Procedure</th><th>{cash ? 'Cash price' : 'Plan allowance'}</th>{!cash && <><th>Patient</th><th>Insurance</th></>}</tr></thead><tbody>{lines.map(row => <tr key={row.id}><th scope="row">{clinicalName(row)}<small>CPT {row.code}{Number(row.units) > 1 ? ` · ${row.units} ${groupFor(row.code) === 'muscle' ? 'limbs' : 'planned'}` : ''}</small>{!cash && <small>{row.benefits.copay_cents != null ? `${money(row.benefits.copay_cents)} copay` : row.benefits.coinsurance_percent != null ? `${row.benefits.coinsurance_percent}% coinsurance` : ''}{row.calculation?.deductible_cents ? ` · ${money(row.calculation.deductible_cents)} to deductible` : ''}</small>}</th><td>{cash ? row.cash_included ? 'Included' : money(row.cash_cents) : money(row.allowed_cents)}</td>{!cash && <><td>{complete ? money(row.calculation?.patient_cents) : '—'}</td><td>{complete ? money(row.calculation?.plan_cents) : '—'}</td></>}</tr>)}</tbody></table></div>
      {cash && result.cash_quote?.complete && <p className="clinical-origin">{result.cash_quote.message}</p>}
      {!cash && complete && <div className="visit-math"><p>Your estimated share is <strong>{money(lines.reduce((sum, row) => sum + (row.calculation?.deductible_cents || 0), 0))}</strong> toward the deductible + <strong>{money(lines.reduce((sum, row) => sum + (row.calculation?.copay_cents || 0), 0))}</strong> in copays + <strong>{money(lines.reduce((sum, row) => sum + (row.calculation?.coinsurance_cents || 0), 0))}</strong> in coinsurance{lines.some(row => row.calculation?.oop_adjustment_cents) ? ', reduced by the remaining out-of-pocket limit' : ''}.</p></div>}
  </section>
}
