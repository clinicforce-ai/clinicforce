import { useState } from 'react'
import { ArrowRight, ChevronDown, Sparkles } from 'lucide-react'
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
  const [emgRequested, setEmgRequested] = useState(emgVisit || available.some(row => selected.includes(row.id!) && ['nerve', 'muscle'].includes(groupFor(row.code))))
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
      if (chosen.some(row => !Number.isInteger(quantities[row.id!]) || quantities[row.id!] < 1 || quantities[row.id!] > 10000)) { setEditError('Enter the planned quantity for each selected service.'); return }
      if (chosen.filter(row => ['95885', '95886'].includes(row.code)).reduce((sum, row) => sum + quantities[row.id!], 0) > 4) { setEditError('EMG examinations can include at most four limbs in this scenario.'); return }
      if (busy || !result.check_id || !result.context_token) { setEditError('Wait for the current check before updating services.'); return }
      const saved = await save({ selected_ids: selected, confirmed_units: Object.fromEntries(selected.map(id => [id, quantities[id]])), ...(result.cash_schedule ? { cash_selection: cashSelection } : {}) })
      if (!saved) return
    }
    if (continueToCollection) viewEstimate()
  }
  const option = (row: VisitCode, rows?: VisitCode[]) => {
    const included = selected.includes(row.id!)
    // Unsaved quantities or combinations must not reuse a prior scenario's approvals or patient amount.
    const priced = !changed && included ? result.price_range?.lines?.find(line => line.id === row.id) : undefined
    const approval = (kind: 'referral' | 'authorization') => {
      const item = priced?.benefits.requirements[kind]
      return !item ? 'Check scenario' : item.required === 'not_required' && item.cleared ? 'Not required' : item.cleared ? 'Verified' : item.required === 'required' ? `Required · ${item.status === 'unknown' ? 'verify' : item.status.replaceAll('_', ' ')}` : 'Not returned'
    }
    return <article key={row.id} className={`procedure-choice${included ? ' is-selected' : ''}`}>
      <label className="procedure-choice-label"><input type={rows ? 'radio' : 'checkbox'} name={rows ? `procedure-${groupFor(row.code)}` : undefined} checked={included} disabled={busy || groupFor(row.code) === 'muscle' && !nerve.some(item => selected.includes(item.id!))} onChange={event => rows ? pickOne(rows, row.id!) : update(event.target.checked ? [...selected, row.id!] : selected.filter(id => id !== row.id))} /><span><strong>{clinicalName(row)}</strong><small>{row.code.startsWith('J') ? 'HCPCS' : 'CPT'} {row.code}{result.service_plan?.recommended_ids.includes(row.id!) ? ' · Suggested' : ''}</small></span></label>
      <dl className="procedure-facts"><div><dt>Cash / unit</dt><dd>{row.unit_cash_cents != null ? money(row.unit_cash_cents) : 'Package / verify'}</dd></div><div><dt>Contract / unit</dt><dd>{row.unit_allowed_cents != null ? money(row.unit_allowed_cents) : 'Not available'}</dd></div><div><dt>Referral</dt><dd>{approval('referral')}</dd></div><div><dt>Authorization</dt><dd>{approval('authorization')}</dd></div></dl>
      {included && <div className="procedure-patient-amount"><span>Patient estimate</span><strong>{priced?.calculation ? money(priced.calculation.patient_cents) : changed ? 'Update scenario' : 'Needs verification'}</strong></div>}
      {included && !rows && <label className="procedure-quantity">{groupFor(row.code) === 'muscle' ? 'Limbs' : row.code.startsWith('J') ? 'Medication units' : 'Quantity'}<input type="number" min="1" max={groupFor(row.code) === 'muscle' ? 4 : 10000} value={quantities[row.id!] || ''} disabled={busy} onChange={event => { setQuantities(current => ({ ...current, [row.id!]: Number(event.target.value) })); setChanged(true); setCashSelection('') }} /></label>}
    </article>
  }
  const choices = (rows: VisitCode[], title: string) => rows.length > 0 && <fieldset className="procedure-group"><legend>{title}</legend><div className="procedure-group-tools"><span>{rows[0].code}–{rows[rows.length - 1].code} · Choose one</span><button type="button" className="visit-text-button" disabled={busy || !rows.some(row => selected.includes(row.id!))} onClick={() => pickOne(rows, '')}>Not included</button></div><div className="procedure-options">{rows.map(row => option(row, rows))}</div></fieldset>
  const extraGroups = [
    { title: 'Botox / muscle injections', rows: other.filter(row => /^646/.test(row.code)) },
    { title: 'Medication', rows: other.filter(row => row.code.startsWith('J')) },
    { title: 'Other procedures', rows: other.filter(row => !/^646|^J/.test(row.code)) },
  ].filter(group => group.rows.length)
  return <section className="visit-planner" aria-labelledby="service-plan-title">
    <div className="visit-section-heading"><h2 id="service-plan-title">Plan this visit</h2><span className="visit-badge"><Sparkles size={16} aria-hidden="true" /> {result.service_plan?.selection_source === 'clinician' ? 'Your plan' : 'Suggested plan'}</span></div>
    <p className="visit-muted">Choose the planned care. Update the scenario to check its price and approvals.</p>
    {choices(office, result.visit_type === 'new' ? 'New patient consultation' : 'Office consultation')}
    <label className="emg-request"><input type="checkbox" checked={emgRequested} disabled={busy} onChange={event => {
      setEmgRequested(event.target.checked)
      if (!event.target.checked) update(selected.filter(id => !available.some(row => row.id === id && ['nerve', 'muscle'].includes(groupFor(row.code)))))
      else { const initial = nerve[0]; if (initial) update([...selected, initial.id!]) }
    }} /> Doctor requested EMG / nerve testing</label>
    {emgRequested && <div className="emg-options">
      {result.cash_schedule && emgVisit && <label className="plan-field">Cash package<select disabled={busy} value={cashSelection} onChange={event => choosePackage(event.target.value)}><option value="">Custom plan</option>{result.cash_schedule.packages.map(row => <option key={row.id} value={row.id}>{row.label} · {money(row.amount_cents)}</option>)}</select></label>}
      {choices(nerve, 'Nerve conduction · study range')}
      {!!muscle.length && <fieldset className="procedure-group"><legend>Needle EMG · muscle testing</legend><p className="visit-muted">Add to a nerve study. Choose the examination and limb count.</p><div className="procedure-options">{muscle.map(row => option(row))}</div></fieldset>}
    </div>}
    <div className="procedure-catalog"><h3>Additional procedures</h3>{extraGroups.map(group => <details className="visit-line-details" key={group.title}><summary>{group.title}<small>{group.rows.length} options</small></summary><div className="procedure-options">{group.rows.map(row => option(row))}</div></details>)}</div>
    {!!result.service_plan?.assumptions?.length && <details className="plan-assumptions"><summary>Why these suggestions?</summary><p>{result.service_plan.recommendation_basis}</p><ul>{result.service_plan.assumptions.map(item => <li key={item}>{item}</li>)}</ul></details>}
    {editError && <p className="visit-error" role="alert">{editError}</p>}
    <div className="visit-plan-footer"><span>{chosen.length} {chosen.length === 1 ? 'service' : 'services'} selected{changed ? ' · Unsaved changes' : ' · Current scenario'}</span><div className="plan-scenario-actions"><button type="button" className="visit-text-button" disabled={busy || !changed} onClick={() => void apply(false)}>Update scenario</button><button type="button" className="visit-primary" disabled={busy} onClick={() => void apply(true)}>Continue to collection <ArrowRight size={17} /></button></div></div>
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
