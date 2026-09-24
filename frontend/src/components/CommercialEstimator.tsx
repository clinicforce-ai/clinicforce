import { useEffect, useState } from 'react'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { getStaffSession, type StaffSession } from '../services/api/staff'
import { commercialAppointments, commercialHistory, commercialPrevisit, type CommercialAppointment, type CommercialPastVisit, type PrevisitEstimate } from '../services/api/commercial-estimator'

const money = (cents: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(cents / 100)
const shown = (cents?: number | null) => cents == null ? 'Not returned' : money(cents)
const appointmentTime = (value: string) => value ? new Date(value).toLocaleString() : 'Scheduled time unavailable'

export default function CommercialEstimator({ onBack }: { onBack: () => void }) {
  const [session, setSession] = useState<StaffSession | null>(null)
  const [appointments, setAppointments] = useState<CommercialAppointment[]>([])
  const [history, setHistory] = useState<CommercialPastVisit[]>([])
  const [result, setResult] = useState<PrevisitEstimate | null>(null)
  const [error, setError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [selectedVisit, setSelectedVisit] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setBusy(true); setError(''); setHistoryError('')
    try {
      const [staff, schedule, past] = await Promise.all([
        getStaffSession(), commercialAppointments(),
        commercialHistory().catch(caught => { setHistoryError(caught instanceof Error ? caught.message : 'Past visits could not be loaded.'); return { visits: [] } }),
      ])
      setSession(staff.staff); setAppointments(schedule.appointments); setHistory(past.visits); setLoaded(true)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Scheduled appointments could not be loaded.')
    } finally { setBusy(false) }
  }
  useEffect(() => { const timer = window.setTimeout(() => { void load() }, 0); return () => window.clearTimeout(timer) }, [])

  const estimate = async (id: string) => {
    setSelectedVisit(id); setResult(null); setError(''); setBusy(true)
    try { setResult(await commercialPrevisit(id)) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'This visit could not be checked.') }
    finally { setBusy(false) }
  }

  return <section className="staff-shell">
    <button className="staff-text-button" onClick={onBack}><ArrowLeft size={18} /> Back to patient</button>
    <header className="staff-header"><div><div className="staff-eyebrow">Commercial insurance</div>
      <h1>{session?.practice_name || 'Clinic'} patient cost estimate</h1>
      <p className="staff-muted">Select the scheduled visit. ClinicForce reads eClinicalWorks, checks Stedi office-visit benefits, and prices only verified contracted CPT alternatives.</p>
    </div><button disabled={busy} onClick={() => void load()}><RefreshCw size={17} /> Refresh appointments</button></header>
    {error && <p className="staff-alert staff-warning" role="status">{error}</p>}
    <section className="staff-panel"><h2>Scheduled visits</h2><p className="staff-small staff-muted">No insurance ID or completed CPT code is needed from the clinician.</p>
      {!loaded && busy ? <p className="staff-muted" role="status">Loading visits…</p> : appointments.length ?
        <div className="staff-intake-list">{appointments.map(appointment => <button className="staff-intake-row" key={appointment.id} disabled={busy} onClick={() => void estimate(appointment.id)}>
          <span><strong>{appointmentTime(appointment.start)}</strong><small>{appointment.reason || 'Visit reason not supplied'}</small></span>
          <span className="staff-status">{appointment.status}</span><span>{busy && selectedVisit === appointment.id ? 'Checking eCW and Stedi…' : 'Get estimate'}</span>
        </button>)}</div> : loaded ? <p className="staff-muted">No upcoming eClinicalWorks appointment was found for this confirmed patient.</p> : null}
    </section>
    {result && <section className="staff-panel" aria-live="polite"><h2>Previsit estimate</h2>
      <p className="staff-small staff-muted">{result.checked_at ? 'Stedi checked ' + appointmentTime(result.checked_at) : 'A payer benefit check was not completed.'} This is a pre-service scenario, not a final bill.</p>
      <dl className="staff-facts">
        <div><dt>eCW insurer</dt><dd>{result.payer || 'Not returned'}</dd></div>
        <div><dt>eCW plan</dt><dd>{result.plan || 'Not returned'}</dd></div>
        <div><dt>Visit date</dt><dd>{result.service_date}</dd></div>
        <div><dt>Reason</dt><dd>{result.reason || 'Not returned'}</dd></div>
        <div><dt>Possible CPT codes</dt><dd>{result.codes.length ? result.codes.map(row => row.code).join(', ') : 'Undetermined'}</dd></div>
      </dl>
      {result.codes.length > 1 && <p className="staff-small staff-muted">Office-visit levels are alternatives. Only one applies to the final visit, based on documented medical decision making or time.</p>}
      {result.benefits && <><h3>Stedi office-visit benefits</h3><dl className="staff-facts">
        <div><dt>Coverage</dt><dd>{result.benefits.active === true ? 'Active' : result.benefits.active === false ? 'Inactive' : 'Unconfirmed'}</dd></div>
        <div><dt>Provider network</dt><dd>{result.benefits.network_status === 'in_network' ? 'Verified by clinic record' : 'Unverified'}</dd></div>
        <div><dt>Remaining deductible</dt><dd>{result.benefits.deductible_remaining_cents == null ? 'Not established for this visit' : shown(result.benefits.deductible_remaining_cents)}</dd></div>
        <div><dt>Remaining out of pocket</dt><dd>{shown(result.benefits.oop_remaining_cents)}</dd></div>
        <div><dt>Office-visit copay</dt><dd>{result.benefits.copay_cents == null && result.benefits.reported_options?.some(row => row.kind === 'copay') ? 'Several payer terms below' : shown(result.benefits.copay_cents)}</dd></div>
        <div><dt>Coinsurance</dt><dd>{result.benefits.coinsurance_percent == null && result.benefits.reported_options?.some(row => row.kind === 'coinsurance') ? 'Several payer terms below' : result.benefits.coinsurance_percent == null ? 'Not returned' : result.benefits.coinsurance_percent + '%'}</dd></div>
        <div><dt>Prior authorization</dt><dd>{result.benefits.authorization_signal === 'required' ? 'Required on at least one payer benefit; verify applicability' : result.benefits.authorization_signal || 'Unknown'}</dd></div>
        <div><dt>Referral</dt><dd>{result.benefits.referral_signal === 'unknown' ? 'Not confirmed by payer' : result.benefits.referral_signal || 'Unknown'}</dd></div>
      </dl>{result.benefits.reported_options?.length ? <><h3>Payer-reported benefit terms</h3>
        <p className="staff-small staff-muted">These are separate terms from Stedi. The payer did not identify one term as the patient’s final cost share for this visit.</p>
        <ul>{result.benefits.reported_options.map((row, index) => <li key={index} className="staff-small">
          <strong>{row.kind.replaceAll('_', ' ')}</strong> · {row.service_type_code === '98' ? 'Office visit' : 'General medical'} · {row.network.replaceAll('_', ' ').toLowerCase()}{row.time_period ? ' · ' + row.time_period.replaceAll('_', ' ').toLowerCase() : ''}: {row.amount_cents == null ? row.percent == null ? 'Amount not stated' : row.percent + '%' : money(row.amount_cents)}{row.payer_note ? ' — ' + row.payer_note : ''}
        </li>)}</ul></> : null}</>}
      {result.price_range && <><h3>Office-visit pricing scenarios</h3><dl className="staff-facts">
        <div><dt>Contracted allowed range</dt><dd>{money(result.price_range.allowed_min_cents)} – {money(result.price_range.allowed_max_cents)}</dd></div>
        <div><dt>Estimated patient range</dt><dd>{result.price_range.patient_min_cents == null || result.price_range.patient_max_cents == null ? 'Awaiting complete benefits and referral / authorization review' : money(result.price_range.patient_min_cents) + ' – ' + money(result.price_range.patient_max_cents)}</dd></div>
      </dl><ul>{result.price_range.scenarios.map(row => <li key={row.code}>{row.code}: {money(row.allowed_cents)} contracted allowed amount</li>)}</ul>
        <p className="staff-small staff-muted">Each CPT alternative is priced separately; these amounts are not added together.</p></>}
      {result.suggestions?.groups.length ? <><h3>Other coding possibilities for review</h3>{result.suggestions.groups.map(group => <p key={group.label} className="staff-small">{group.label}: {group.codes.map(row => row.code).join(', ')}</p>)}
        <p className="staff-small staff-muted">Bedrock suggestions are unpriced and require clinical confirmation.</p></> : null}
      {result.issues.map(issue => <p className="staff-alert staff-warning" key={issue}>{issue}</p>)}
    </section>}
    <section className="staff-panel"><h2>Past visits</h2><p className="staff-small staff-muted">Read-only eClinicalWorks history.</p>
      {historyError && <p className="staff-alert staff-warning">{historyError}</p>}
      {history.length ? history.map(visit => <article className="staff-document" key={visit.id}><div>
        <strong>{appointmentTime(visit.start)}</strong><small>{visit.reason || 'Visit reason not supplied'} · {visit.status}</small>
        <dl className="staff-facts"><div><dt>Coded services</dt><dd>{visit.coded_services.length ? visit.coded_services.join(', ') : 'None returned'}</dd></div>
          <div><dt>Saved ClinicForce estimate</dt><dd>{visit.saved_estimate ? shown(visit.saved_estimate.patient_cents) : 'No estimate saved'}</dd></div></dl>
      </div></article>) : !historyError && <p className="staff-muted">No completed eClinicalWorks visits were found in the last year.</p>}
    </section>
  </section>
}
