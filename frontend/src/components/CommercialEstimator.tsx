import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronDown, CircleHelp, Clock3, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { deleteStaffInsuranceCheck, getStaffIntake, getStaffSession, type StaffPatient, type StaffSession } from '../services/api/staff'
import { commercialAppointments, commercialHistory, commercialPrevisit, commercialVisitReview, type VisitReview, type CommercialAppointment, type CommercialPastVisit, type PrevisitEstimate } from '../services/api/commercial-estimator'
import VisitServices from './VisitServices'
import './CommercialEstimator.css'

const money = (cents: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(cents / 100)
const range = (min: number, max: number) => min === max ? money(min) : `${money(min)} – ${money(max)}`
const shown = (cents?: number | null) => cents == null ? 'Not confirmed' : money(cents)
const dateLabel = (value: string, options: Intl.DateTimeFormatOptions) => {
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value)
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString(undefined, options)
}
const visitDate = (value: string) => dateLabel(value, { month: 'short', day: 'numeric', year: 'numeric' })
const visitTime = (value: string) => dateLabel(value, { hour: 'numeric', minute: '2-digit' })
const humanize = (value: string) => value.replaceAll('_', ' ').toLowerCase()
const networkLabel = (value: string) => ({ Y: 'In network', YES: 'In network', IN_NETWORK: 'In network', IN_AND_OUT_OF_NETWORK: 'In and out of network', W: 'In and out of network' })[value] || humanize(value)
const periodLabel = (value: string) => ({ '29': 'Remaining', '23': 'Calendar year', '27': 'Per visit', '7': 'Per episode' })[value] || humanize(value)
const requirement = (signal?: string) => signal === 'not_required' ? 'Not required' : signal === 'required' ? 'Required' : 'Needs confirmation'

function CoverageCheck({ label, value, confirmed, detail }: { label: string; value: string; confirmed: boolean; detail?: string }) {
  return <div className="visit-check"><span className={confirmed ? 'visit-check-icon is-confirmed' : 'visit-check-icon'} aria-hidden="true">{confirmed ? <Check size={16} /> : <CircleHelp size={16} />}</span>
    <div><dt>{label}</dt>{detail && <small>{detail}</small>}</div><dd>{value}</dd></div>
}

export default function CommercialEstimator({ onBack, intakeId }: { onBack: () => void; intakeId?: string }) {
  const [session, setSession] = useState<StaffSession | null>(null)
  const [patient, setPatient] = useState<StaffPatient | null>(null)
  const [appointments, setAppointments] = useState<CommercialAppointment[]>([])
  const [history, setHistory] = useState<CommercialPastVisit[] | null>(null)
  const [result, setResult] = useState<PrevisitEstimate | null>(null)
  const [error, setError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [historyBusy, setHistoryBusy] = useState(false)
  const [selectedVisit, setSelectedVisit] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState<'visits' | 'estimate' | 'delete' | 'review' | null>('visits')
  const [deleteError, setDeleteError] = useState('')
  const [cleanupPending, setCleanupPending] = useState(false)
  const [notice, setNotice] = useState('')
  const requestId = useRef(0)

  const load = useCallback(async () => {
    const current = ++requestId.current
    setBusy('visits'); setError(''); setDeleteError(''); setCleanupPending(false); setNotice(''); setResult(null); setPatient(null)
    const [staff, schedule, record] = await Promise.allSettled([
      getStaffSession(), commercialAppointments(), intakeId ? getStaffIntake(intakeId) : Promise.resolve(null),
    ])
    if (current !== requestId.current) return
    if (staff.status === 'fulfilled') setSession(staff.value.staff)
    if (record.status === 'fulfilled') setPatient(record.value?.patient || null)
    else { setError('Patient details could not be loaded. Return to the patient record and try again.'); setAppointments([]); setSelectedVisit(''); setLoaded(false); setBusy(null); return }
    if (schedule.status === 'fulfilled') {
      setAppointments(schedule.value.appointments)
      setSelectedVisit(previous => schedule.value.appointments.some(visit => visit.id === previous) ? previous : schedule.value.appointments[0]?.id || '')
      setLoaded(true)
    } else {
      setError(schedule.reason instanceof Error ? schedule.reason.message : 'Scheduled visits could not be loaded.')
      setAppointments([]); setSelectedVisit(''); setLoaded(false)
    }
    setBusy(null)
  }, [intakeId])
  useEffect(() => {
    const requests = requestId
    const timer = window.setTimeout(() => { void load() }, 0)
    return () => { window.clearTimeout(timer); requests.current++ }
  }, [load])

  const estimate = async () => {
    if (!selectedVisit || busy) return
    const current = ++requestId.current
    setResult(null); setError(''); setNotice(''); setDeleteError(''); setBusy('estimate')
    try {
      const response = await commercialPrevisit(selectedVisit)
      if (response.appointment_id !== selectedVisit) throw new Error('The returned estimate does not match this visit. Refresh visits and try again.')
      if (current === requestId.current) setResult(response)
    } catch (caught) {
      if (current === requestId.current) setError(caught instanceof Error ? caught.message : 'This visit could not be checked.')
    } finally { if (current === requestId.current) setBusy(null) }
  }
  const saveReview = async (review: VisitReview) => {
    if (!result || busy) return
    const current = ++requestId.current
    setBusy('review'); setError(''); setNotice('')
    try {
      const updated = await commercialVisitReview(result, review)
      if (current === requestId.current) { setResult(updated); setNotice('Verification saved. Estimate recalculated from the saved payer response.') }
    } catch (caught) {
      if (current === requestId.current) setError(caught instanceof Error ? caught.message : 'Verification could not be saved.')
    } finally { if (current === requestId.current) setBusy(null) }
  }
  const deleteCheck = async () => {
    if (!intakeId || !result?.check_id || busy) return
    const patientName = patient ? `${patient.given_name} ${patient.family_name}` : 'this patient'
    if (!cleanupPending && !window.confirm(`Delete the saved insurance check for ${patientName} on ${visitDate(result.service_date)}? The patient and eCW appointment stay unchanged. This cannot be undone.`)) return
    setBusy('delete'); setDeleteError('')
    try {
      const deleted = await deleteStaffInsuranceCheck(intakeId, result.check_id)
      if (deleted.cleanup_pending) {
        setCleanupPending(true)
        setDeleteError('The saved check was removed. Some stored files still need cleanup. Select Retry file cleanup to finish.')
      } else {
        setResult(null); setHistory(null); setCleanupPending(false); setNotice('Insurance check deleted. The patient and scheduled visit are still available.')
      }
    } catch (caught) { setDeleteError(caught instanceof Error ? caught.message : 'The insurance check could not be deleted.') }
    finally { setBusy(null) }
  }
  const loadHistory = async () => {
    if (historyBusy) return
    setHistoryBusy(true); setHistoryError('')
    try { setHistory((await commercialHistory()).visits) }
    catch (caught) { setHistoryError(caught instanceof Error ? caught.message : 'Past visits could not be loaded.') }
    finally { setHistoryBusy(false) }
  }

  const chooseVisit = (id: string) => { if (selectedVisit !== id) { setSelectedVisit(id); setResult(null); setError(''); setDeleteError(''); setCleanupPending(false); setNotice('') } }
  const selected = appointments.find(visit => visit.id === selectedVisit)
  const benefits = result?.benefits
  const price = result?.price_range
  const patientPrice = benefits?.active === true && benefits.network_status === 'in_network' && (price?.complete ?? !benefits.requires_review) && price?.patient_min_cents != null && price.patient_max_cents != null ? range(price.patient_min_cents, price.patient_max_cents) : null
  const options = benefits?.reported_options || []
  const reviewItems = [...new Set([...(price?.lines ? price.lines.flatMap(line => line.issues) : benefits?.review_reasons || []), ...(result?.issues || [])])]
  const requirementValue = (kind: 'referral' | 'authorization', field: 'required' | 'status') => {
    const values = [...new Set(price?.lines?.map(line => line.benefits.requirements[kind][field]) || [])]
    if (!values.length) return field === 'required' ? requirement(benefits?.[`${kind}_signal`]) : 'Not verified'
    if (values.length > 1) return 'Varies by service'
    return field === 'required' ? requirement(values[0]) : values[0] === 'valid' ? 'Received and valid' : humanize(values[0])
  }
  const conditionalCodes = !!result?.codes.length && result.codes.every(row => row.source === 'conditional office E/M alternative')

  return <section className="visit-estimator" aria-labelledby="visit-estimate-title">
    <header className="visit-page-header">
      <div><button className="visit-back" onClick={onBack}><ArrowLeft size={17} /> Back to patient</button><h1 id="visit-estimate-title">Visit estimate</h1></div>
      <span className="visit-practice">{session?.practice_name}</span>
    </header>
    <div className="visit-patient-bar">
      <div><strong>{patient ? `${patient.given_name} ${patient.family_name}` : 'Patient record'}</strong>{patient?.birth_date && <span>DOB {visitDate(patient.birth_date)}</span>}</div>
      <span><ShieldCheck size={16} aria-hidden="true" /> Clinic workspace</span>
    </div>
    <div className="visit-layout">
      <aside className="visit-rail" aria-label="Patient visits">
        <div className="visit-rail-heading"><h2 id="visit-choice-heading">Upcoming visits</h2><button className="visit-icon-button" aria-label="Refresh visits" disabled={!!busy} onClick={() => void load()}><RefreshCw size={17} className={busy === 'visits' ? 'visit-spin' : ''} /></button></div>
        {!!appointments.length && <select className="visit-mobile-selector" aria-labelledby="visit-choice-heading" value={selectedVisit} disabled={!!busy} onChange={event => chooseVisit(event.target.value)}>{appointments.map(visit => <option key={visit.id} value={visit.id}>{visitDate(visit.start)} · {visitTime(visit.start)} · {visit.reason || 'Scheduled visit'}</option>)}</select>}
        {busy === 'visits' ? <p className="visit-muted" role="status">Loading visits…</p> : appointments.length ? <div className="visit-list">{appointments.map(appointment => <button key={appointment.id} className={`visit-choice${selectedVisit === appointment.id ? ' is-selected' : ''}`} aria-pressed={selectedVisit === appointment.id} disabled={!!busy} onClick={() => chooseVisit(appointment.id)}>
          <span className="visit-date-marker"><small>{dateLabel(appointment.start, { month: 'short' })}</small><strong>{dateLabel(appointment.start, { day: 'numeric' })}</strong></span>
          <span><strong>{visitTime(appointment.start)}</strong><small>{appointment.reason || 'Scheduled visit'}</small>{appointment.status && appointment.status !== 'unknown' && <small className="visit-status-label">{humanize(appointment.status)}</small>}</span>
        </button>)}</div> : <p className="visit-muted">{loaded ? 'No upcoming visits found.' : 'Visit list unavailable.'}</p>}
        <details className="visit-history" onToggle={event => { if (event.currentTarget.open && history === null && !historyBusy && !historyError) void loadHistory() }}>
          <summary>Past visits <ChevronDown size={16} aria-hidden="true" /></summary>
          {historyBusy && <p role="status">Loading history…</p>}
          {historyError && <div><p>History is unavailable.</p><button className="visit-text-button" onClick={() => void loadHistory()}>Try again</button></div>}
          {history?.length ? history.map(visit => <article key={visit.id}><strong>{visitDate(visit.start)}</strong><p>{visit.reason || 'Previous visit'}</p><small>{visit.coded_services.length ? `CPT ${visit.coded_services.join(', ')}` : 'No coded services returned'}</small><p>{visit.saved_estimate?.patient_cents != null ? `Saved patient estimate: ${money(visit.saved_estimate.patient_cents)}` : 'No saved patient estimate'}</p></article>) : history && <p>No completed visits returned in the last year.</p>}
        </details>
      </aside>
      <div className="visit-content" aria-busy={!!busy}>
        {notice && <p className="visit-notice" role="status">{notice}</p>}
        {deleteError && <p className="visit-error" role="alert">{deleteError}</p>}
        {error && <div className="visit-error" role="alert"><CircleHelp size={20} aria-hidden="true" /><div><strong>{selected ? 'The estimate could not be checked' : 'Visits could not be loaded'}</strong><p>{error}</p><button className="visit-text-button" disabled={!!busy} onClick={() => void (selected ? estimate() : load())}>Try again <ArrowRight size={15} /></button></div></div>}
        {selected && <div className="visit-context"><CalendarDays size={18} aria-hidden="true" /><div><strong>{visitDate(selected.start)} <span>at {visitTime(selected.start)}</span></strong><p>{selected.reason || 'Scheduled visit'}</p></div></div>}
        {busy === 'estimate' ? <div className="visit-empty" role="status"><RefreshCw className="visit-spin" size={26} aria-hidden="true" /><h2>Checking this visit</h2><p>Reading the appointment, insurance benefits, and available clinic rates.</p></div> : result ? <>
          <section className="visit-answer" aria-live="polite">
            <p className="visit-label">Estimated patient cost</p>
            <h2 className={patientPrice ? 'visit-amount' : 'visit-pending'}>{patientPrice || (benefits ? 'Needs insurance review' : 'Coverage check incomplete')}</h2>
            <p className="visit-answer-description">{patientPrice ? 'Based on the returned benefits and the clinic’s contracted rates. The final cost depends on the care provided.' : benefits?.active === false ? 'The payer reported inactive coverage. The clinic needs to confirm coverage or self-pay pricing.' : benefits ? 'The clinic’s billing team needs to confirm the applicable benefits before a patient amount can be shown.' : 'A complete benefit response is needed before a patient amount can be shown.'}</p>
            {!patientPrice && <div className="visit-next-step"><strong>Next step · Billing team</strong><p>{benefits ? 'Review the benefit terms and confirm the items below with the payer.' : 'Resolve the coverage check before discussing a patient amount.'}</p>{!!reviewItems.length && <details open={!benefits}><summary>{reviewItems.length} {reviewItems.length === 1 ? 'item' : 'items'} to confirm <ChevronDown size={16} aria-hidden="true" /></summary><ul>{reviewItems.map(item => <li key={item}>{item}</li>)}</ul></details>}</div>}
            {price?.allowed_min_cents != null && price.allowed_max_cents != null && <div className="visit-contracted"><span>Contracted visit price <small>Before the patient’s insurance cost sharing</small></span><strong>{range(price.allowed_min_cents, price.allowed_max_cents)}</strong></div>}
            {price?.collection && <div className="visit-collection"><strong>Collection recommendation{price.collection.amount_cents != null ? ` · ${money(price.collection.amount_cents)}` : ''}</strong><p>{price.collection.reason}</p>{price.collection.payment_reference && <small>Payment verification: {price.collection.payment_reference}</small>}</div>}
          </section>
          <section className="visit-coverage" aria-labelledby="visit-coverage-title"><div className="visit-section-heading"><h3 id="visit-coverage-title">Coverage at a glance</h3>{result.checked_at && <span><Clock3 size={14} aria-hidden="true" /> Checked {visitDate(result.checked_at)}, {visitTime(result.checked_at)}</span>}</div>
            <p className="visit-payer">{result.payer || 'Insurer not confirmed'}{result.plan && <span> · Plan {result.plan}</span>}</p>
            <dl>
              <CoverageCheck label="Coverage" value={benefits?.active === true ? 'Active' : benefits?.active === false ? 'Inactive' : 'Not confirmed'} confirmed={benefits?.active === true} />
              <CoverageCheck label="Provider network" value={benefits?.network_status === 'in_network' ? 'In network' : benefits?.network_status === 'out_of_network' ? 'Out of network' : 'Not verified'} confirmed={benefits?.network_status === 'in_network'} />
              <CoverageCheck label="Exact plan / product" value={result.plan_details?.stedi.names?.join(' · ') || result.plan_details?.ecw.plan?.join(' · ') || result.plan || 'Not returned'} confirmed={!!(result.plan_details?.stedi.names?.length || result.plan_details?.ecw.plan?.length)} detail={result.plan_details?.stedi.names?.length ? 'Payer response' : 'eCW coverage record'} />
              <CoverageCheck label="Deductible remaining" value={shown(benefits?.deductible_remaining_cents)} confirmed={benefits?.deductible_remaining_cents != null} />
              <CoverageCheck label="Out of pocket remaining" value={shown(benefits?.oop_remaining_cents)} confirmed={benefits?.oop_remaining_cents != null} />
              <CoverageCheck label="Referral required?" value={requirementValue('referral', 'required')} confirmed={!!price?.lines?.length && price.lines.every(line => line.benefits.requirements.referral.cleared)} />
              <CoverageCheck label="PA required?" value={requirementValue('authorization', 'required')} confirmed={!!price?.lines?.length && price.lines.every(line => line.benefits.requirements.authorization.cleared)} detail={benefits?.authorization_basis} />
              <CoverageCheck label="Referral received / valid?" value={requirementValue('referral', 'status')} confirmed={!!price?.lines?.length && price.lines.every(line => line.benefits.requirements.referral.cleared)} />
              <CoverageCheck label="PA obtained?" value={requirementValue('authorization', 'status')} confirmed={!!price?.lines?.length && price.lines.every(line => line.benefits.requirements.authorization.cleared)} />
            </dl>
          </section>
          {price?.lines && <VisitServices key={`${result.check_id}:${result.revision}`} result={result} busy={!!busy} save={saveReview} />}
          <div className="visit-details">
            {!price?.lines && <details><summary><span>Visit pricing & CPT codes <small>{result.codes.length ? `${result.codes.length} ${conditionalCodes ? 'possible visit levels' : 'scheduled services'}` : 'Coding not yet available'}</small></span><ChevronDown size={18} aria-hidden="true" /></summary><div className="visit-detail-body">
              <p>{conditionalCodes ? 'These are alternative office visit levels. One level applies, based on the documented medical decision making or time. Amounts are not added together.' : 'These services were returned by the appointment record. Final coding depends on the care provided.'}</p>
              {result.codes.length ? <table><caption className="visit-sr-only">CPT codes and contracted visit prices</caption><thead><tr><th scope="col">CPT</th><th scope="col">Source</th><th scope="col">Contracted price</th></tr></thead><tbody>{result.codes.map((row, index) => <tr key={`${row.code}-${index}`}><td>{row.code}</td><td>{row.source === 'conditional office E/M alternative' ? 'Possible office visit level' : row.source}</td><td>{shown(price?.scenarios.find(scenario => scenario.code === row.code)?.allowed_cents)}</td></tr>)}</tbody></table> : <p>No service codes are available yet.</p>}
              {result.suggestions?.groups.length ? <><h4>Other coding suggestions</h4><p>AI suggestions require clinical confirmation and are excluded from the price above.</p>{result.suggestions.groups.map((group, index) => <p key={`${group.label}-${index}`}><strong>{group.label}</strong>: {group.codes.map(row => row.code).join(', ')}</p>)}</> : null}
            </div></details>}
            <details><summary><span>Insurance benefits <small>Copay, deductible & payer terms</small></span><ChevronDown size={18} aria-hidden="true" /></summary><div className="visit-detail-body">
              <dl className="visit-benefit-values"><div><dt>Office visit copay</dt><dd>{benefits?.copay_cents == null && options.some(row => row.kind === 'copay') ? 'Multiple terms returned' : shown(benefits?.copay_cents)}</dd></div><div><dt>Coinsurance</dt><dd>{benefits?.coinsurance_percent != null ? `${benefits.coinsurance_percent}%` : options.some(row => row.kind === 'coinsurance') ? 'Multiple terms returned' : 'Not confirmed'}</dd></div><div><dt>Remaining deductible</dt><dd>{shown(benefits?.deductible_remaining_cents)}</dd></div><div><dt>Remaining out of pocket</dt><dd>{shown(benefits?.oop_remaining_cents)}</dd></div></dl>
              {!!options.length && <><h4>Terms returned by the payer</h4><p>Each term retains its service and network scope. A returned term may not apply to this visit.</p><ul className="visit-payer-terms">{options.map((row, index) => <li key={index}><div><strong>{humanize(row.kind)}</strong><span>{row.amount_cents == null ? row.percent == null ? 'Amount not stated' : `${row.percent}%` : money(row.amount_cents)}</span></div><small>{row.procedure_code ? `CPT ${row.procedure_code}` : row.service_type_code === '98' ? 'Office visit' : row.service_type_code === '30' ? 'General medical' : row.service_type_code ? `Service ${row.service_type_code}` : 'Service scope not stated'} · {networkLabel(row.network)}{row.coverage_level ? ` · ${humanize(row.coverage_level)}` : ''}{row.time_period ? ` · ${periodLabel(row.time_period)}` : ''}</small>{row.payer_note && <p>{row.payer_note}</p>}</li>)}</ul></>}
            </div></details>
            <details><summary><span>Check details <small>Sources & items needing review</small></span><ChevronDown size={18} aria-hidden="true" /></summary><div className="visit-detail-body"><p>Appointment and coverage: eClinicalWorks. Benefit response: Stedi. Contracted rates and network participation: clinic configuration.</p>{result.calculated_at && <p>Calculated {visitDate(result.calculated_at)} at {visitTime(result.calculated_at)}.</p>}{result.ecw_retrieved_at && <p>eCW retrieved {visitDate(result.ecw_retrieved_at)} at {visitTime(result.ecw_retrieved_at)}.</p>}{result.response_reference && <p>Payer response reference: {result.response_reference}</p>}{result.network_evidence?.evidence_reference && <p>Network participation: {result.network_evidence.network_name} · {result.network_evidence.evidence_reference} · Effective {result.network_evidence.effective_from}</p>}{result.plan_details && <dl className="visit-benefit-values">{Object.entries(result.plan_details.ecw).map(([key, values]) => <div key={key}><dt>eCW {humanize(key)}</dt><dd>{values.join(' · ') || 'Not returned'}</dd></div>)}{result.plan_details.stedi.group_number && <div><dt>Payer group</dt><dd>{result.plan_details.stedi.group_number}</dd></div>}{result.plan_details.stedi.plan_number && <div><dt>Payer plan number</dt><dd>{result.plan_details.stedi.plan_number}</dd></div>}</dl>}{result.checked_at && <p>Benefits checked {visitDate(result.checked_at)} at {visitTime(result.checked_at)}.</p>}{benefits?.normalization_notes?.length ? <ul>{benefits.normalization_notes.map(note => <li key={note}>{note}</li>)}</ul> : null}{!!reviewItems.length && <p>Review items are listed with the estimate above.</p>}</div></details>
          </div>
          <div className="visit-result-footer"><span>Estimate only · Final charges depend on services provided.</span><div className="visit-result-actions">{intakeId && result.check_id && <button className="visit-text-button visit-delete-button" disabled={!!busy} onClick={() => void deleteCheck()}><Trash2 size={15} aria-hidden="true" />{busy === 'delete' ? 'Deleting…' : cleanupPending ? 'Retry file cleanup' : 'Delete check'}</button>}<button className="visit-text-button" disabled={!!busy || cleanupPending} onClick={() => void estimate()}><RefreshCw size={15} /> Check again</button></div></div>
        </> : !error && !busy && <div className="visit-empty"><CalendarDays size={28} aria-hidden="true" /><h2>{selected ? 'Ready to check this visit' : 'No upcoming visit to estimate'}</h2><p>{selected ? 'ClinicForce brings together the appointment, coverage, and clinic rates. No insurance details need to be entered here.' : 'Refresh visits after the appointment is scheduled in eClinicalWorks.'}</p>{selected && <button className="visit-primary" onClick={() => void estimate()}>Check estimate <ArrowRight size={17} /></button>}</div>}
        {busy === 'visits' && <div className="visit-empty" role="status"><RefreshCw className="visit-spin" size={26} aria-hidden="true" /><p>Loading the patient’s upcoming visits…</p></div>}
      </div>
    </div>
  </section>
}
