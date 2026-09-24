import { useEffect, useState } from 'react'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { getStaffSession, type StaffSession } from '../services/api/staff'
import { commercialAppointments, commercialHistory, commercialPost, commercialReadiness, commercialStartFromEcw, type CommercialAppointment, type CommercialCase, type CommercialPastVisit, type CommercialReadiness } from '../services/api/commercial-estimator'

const money = (cents?: number | null) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format((cents || 0) / 100)
const appointmentTime = (value: string) => value ? new Date(value).toLocaleString() : 'Scheduled time unavailable'

export default function CommercialEstimator({ onBack }: { onBack: () => void }) {
  const [session, setSession] = useState<StaffSession | null>(null)
  const [appointments, setAppointments] = useState<CommercialAppointment[]>([])
  const [history, setHistory] = useState<CommercialPastVisit[]>([])
  const [historyError, setHistoryError] = useState('')
  const [caseData, setCaseData] = useState<CommercialCase | null>(null)
  const [message, setMessage] = useState('')
  const [loadError, setLoadError] = useState('')
  const [estimateError, setEstimateError] = useState('')
  const [readiness, setReadiness] = useState<CommercialReadiness | null>(null)
  const [selectedVisit, setSelectedVisit] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const load = async () => { setBusy(true); setLoadError(''); setHistoryError(''); try { const [staff, schedule, past] = await Promise.all([getStaffSession(), commercialAppointments(), commercialHistory().catch(caught => { setHistoryError(caught instanceof Error ? caught.message : 'Past visits could not be loaded.'); return { visits: [] } })]); setSession(staff.staff); setAppointments(schedule.appointments); setHistory(past.visits); setLoaded(true) } catch (caught) { setLoadError(caught instanceof Error ? caught.message : 'Scheduled appointments could not be loaded.') } finally { setBusy(false) } }
  useEffect(() => { const timer = window.setTimeout(() => { void load() }, 0); return () => window.clearTimeout(timer) }, [])
  const act = (work: () => Promise<void>) => { setBusy(true); setEstimateError(''); setMessage(''); setReadiness(null); setCaseData(null); work().catch(caught => setEstimateError(caught instanceof Error ? caught.message : 'This estimate could not be completed.')).finally(() => setBusy(false)) }
  const automaticEstimate = (id: string) => act(async () => {
    setSelectedVisit(id)
    const status = await commercialReadiness(id)
    setReadiness(status)
    if (!status.ready) return
    let next = await commercialStartFromEcw(id)
    setCaseData(next)
    if (next.estimate) {
      setMessage('Patient cost estimate ready.')
      return
    }
    if (!next.commercial.lines.length || next.commercial.lines.some(line => line.allowed_cents == null) || next.review_tasks.length) {
      setMessage('A clinic staff review is needed before a patient price can be shown. The details are below.')
      return
    }
    if (next.commercial.request_state === 'pending') {
      setMessage('ClinicForce is completing the insurance review. No action is needed from the clinician.')
      return
    }
    if (!next.commercial.evidence_id) next = await commercialPost(next.id, 'eligibility')
    if (next.commercial.evidence_id && !next.review_tasks.length) next = await commercialPost(next.id, 'calculate')
    setCaseData(next)
    setMessage(next.estimate ? 'Patient cost estimate ready.' : next.review_tasks.length || next.commercial.request_state === 'review' ? 'Clinic staff review is needed before a patient price can be shown.' : 'ClinicForce is completing the insurance review.')
  })
  return <section className="staff-shell"><button className="staff-text-button" onClick={onBack}><ArrowLeft size={18} /> Back to patient</button><header className="staff-header"><div><div className="staff-eyebrow">Commercial insurance</div><h1>{session?.practice_name || 'Clinic'} patient cost estimate</h1><p className="staff-muted">Select the scheduled visit. ClinicForce checks the eClinicalWorks record and clinic contract setup before requesting Stedi benefits or calculating an estimate.</p></div><button disabled={busy} onClick={() => void load()}><RefreshCw size={17} /> Refresh appointments</button></header>{estimateError && <p className="staff-alert staff-warning" role="status">{estimateError}</p>}{message && <p className="staff-alert" role="status">{message}</p>}
    <section className="staff-panel"><h2>Scheduled visits</h2><p className="staff-small staff-muted">Select a visit to check estimate readiness. No insurance fields or service codes need to be entered here.</p>{loadError && <p className="staff-alert staff-warning" role="status">{loadError}</p>}{!loaded && busy ? <p className="staff-muted" role="status">ClinicForce is loading visits…</p> : appointments.length ? <div className="staff-intake-list">{appointments.map(appointment => <button className="staff-intake-row" key={appointment.id} disabled={busy} onClick={() => automaticEstimate(appointment.id)}><span><strong>{appointmentTime(appointment.start)}</strong><small>{appointment.reason || 'Visit reason not supplied'}</small></span><span className="staff-status">{appointment.status}</span><span>{busy && selectedVisit === appointment.id ? 'Checking visit…' : 'Get estimate'}</span></button>)}</div> : loaded ? <p className="staff-muted">No upcoming eClinicalWorks appointment was found for this confirmed patient.</p> : null}</section>
    {readiness && !readiness.ready && <section className="staff-panel" aria-live="polite"><h2>Why this visit cannot be estimated yet</h2><p className="staff-small staff-muted">ClinicForce read the selected eClinicalWorks visit. The clinician does not need to enter insurance details here.</p><dl className="staff-facts"><div><dt>eCW payer</dt><dd>{readiness.ecw.payer || 'Not returned'}</dd></div><div><dt>eCW plan</dt><dd>{readiness.ecw.plan || 'Not returned'}</dd></div><div><dt>Scheduled CPT/HCPCS</dt><dd>{readiness.ecw.scheduled_codes.length ? readiness.ecw.scheduled_codes.map(line => `${line.code} × ${line.units ?? '?'}`).join(', ') : 'None linked to this visit'}</dd></div></dl>{readiness.blockers.map(blocker => <p className="staff-alert staff-warning" key={blocker}>{blocker}</p>)}</section>}
    <section className="staff-panel"><h2>Past visits</h2><p className="staff-small staff-muted">Read-only eClinicalWorks history. Opening this section never sends Stedi eligibility or recalculates a past visit.</p>{historyError && <p className="staff-alert staff-warning">{historyError}</p>}{history.length ? history.map(visit => <article className="staff-document" key={visit.id}><div><strong>{appointmentTime(visit.start)}</strong><small>{visit.reason || 'Visit reason not supplied'} · {visit.status}</small><dl className="staff-facts"><div><dt>Coded services</dt><dd>{visit.coded_services.length ? visit.coded_services.join(', ') : 'No CPT/HCPCS returned by eClinicalWorks'}</dd></div><div><dt>Saved ClinicForce estimate</dt><dd>{visit.saved_estimate ? 'Available' : 'No estimate saved'}</dd></div></dl>{visit.saved_estimate && <><dl className="staff-facts"><div><dt>Allowed amount</dt><dd>{money(visit.saved_estimate.allowed_cents)}</dd></div><div><dt>Patient estimate</dt><dd>{money(visit.saved_estimate.patient_cents)}</dd></div><div><dt>Insurer estimate</dt><dd>{money(visit.saved_estimate.plan_cents)}</dd></div><div><dt>Saved</dt><dd>{visit.saved_estimate.created_at ? appointmentTime(visit.saved_estimate.created_at) : 'Date unavailable'}</dd></div></dl><small>{visit.saved_estimate.disclaimer || 'This was a pre-service estimate, not final claim adjudication.'}</small></>}</div></article>) : !historyError && <p className="staff-muted">No completed eClinicalWorks visits were found in the last year.</p>}</section>
    {caseData && <section className="staff-panel" aria-live="polite"><h2>{caseData.estimate ? 'Patient cost estimate' : 'Insurance estimate status'}</h2>{caseData.estimate ? <><p className="staff-small staff-muted">ClinicForce used the scheduled eClinicalWorks services and current Stedi eligibility response.</p><dl className="staff-facts"><div><dt>Services</dt><dd>{caseData.commercial.lines.map(line => `${line.code} × ${line.units}`).join(', ')}</dd></div><div><dt>Contracted allowed amount</dt><dd>{money(caseData.estimate.allowed_cents)}</dd></div><div><dt>Estimated patient amount</dt><dd>{money(caseData.estimate.patient_cents)}</dd></div><div><dt>Estimated insurer share</dt><dd>{money(caseData.estimate.plan_cents)}</dd></div><div><dt>Deductible / copay / coinsurance</dt><dd>{money(caseData.estimate.deductible_cents)} / {money(caseData.estimate.copay_cents)} / {money(caseData.estimate.coinsurance_cents)}</dd></div></dl><p className="staff-small staff-muted">{caseData.estimate.disclaimer}</p></> : <p className="staff-muted">{caseData.commercial.request_state === 'pending' ? 'The insurance check is still in progress.' : 'ClinicForce cannot show a patient price until the clinic resolves the review items below.'}</p>}
      {caseData.commercial.benefits_summary && <dl className="staff-facts"><div><dt>Coverage</dt><dd>{caseData.commercial.benefits_summary.active === true ? 'Active' : 'Needs review'}</dd></div><div><dt>Network</dt><dd>{caseData.commercial.benefits_summary.network_status || 'Unknown'}</dd></div><div><dt>Remaining deductible</dt><dd>{caseData.commercial.benefits_summary.deductible_remaining_cents == null ? 'Not returned' : money(caseData.commercial.benefits_summary.deductible_remaining_cents)}</dd></div><div><dt>Remaining out of pocket</dt><dd>{caseData.commercial.benefits_summary.oop_remaining_cents == null ? 'Not returned' : money(caseData.commercial.benefits_summary.oop_remaining_cents)}</dd></div><div><dt>Copay / coinsurance</dt><dd>{caseData.commercial.benefits_summary.copay_cents == null ? 'Not returned' : money(caseData.commercial.benefits_summary.copay_cents)} / {caseData.commercial.benefits_summary.coinsurance_percent == null ? 'Not returned' : `${caseData.commercial.benefits_summary.coinsurance_percent}%`}</dd></div><div><dt>Authorization / referral</dt><dd>{caseData.commercial.benefits_summary.authorization_signal || 'Unknown'} / {caseData.commercial.benefits_summary.referral_signal || 'Unknown'}</dd></div></dl>}
      {caseData.review_tasks.map(task => <p className="staff-alert staff-warning" key={task}>{task}</p>)}
    </section>}
  </section>
}
