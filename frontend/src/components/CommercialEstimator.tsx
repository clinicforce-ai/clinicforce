import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronDown, CircleHelp, Clock3, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { deleteStaffInsuranceCheck, getStaffIntake, getStaffSession, type StaffPatient, type StaffSession } from '../services/api/staff'
import { commercialAppointments, commercialHistory, commercialPrevisit, commercialSavedPrevisit, commercialSelectCoverageCandidate, commercialVisitReview, type VisitReview, type CommercialAppointment, type CommercialPastVisit, type PrevisitEstimate } from '../services/api/commercial-estimator'
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
const requirement = (signal?: string) => signal === 'not_required' ? 'Not required' : signal === 'required' ? 'Required' : 'Not specified by payer'

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
  const [busy, setBusy] = useState<'visits' | 'estimate' | 'delete' | 'review' | 'coverage' | null>('visits')
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

  const recovering = result?.recovery?.state === 'pending'
  useEffect(() => {
    if (!recovering || !result?.check_id) return
    const id = result.check_id
    const current = requestId.current
    let stopped = false
    let attempts = 0
    let timer: number
    const poll = async () => {
      try {
        const updated = await commercialSavedPrevisit(id)
        if (stopped || current !== requestId.current) return
        setResult(updated)
        if (updated.recovery?.state === 'pending' && ++attempts < 180) timer = window.setTimeout(() => void poll(), 3000)
        else if (updated.recovery?.state === 'pending') setNotice('Recovery is taking longer than expected. The request is saved; reopen this visit to check its status.')
      } catch (caught) {
        if (!stopped && current === requestId.current) setError(caught instanceof Error ? caught.message : 'The recovery result could not be loaded. Reopen this visit to check its status.')
      }
    }
    timer = window.setTimeout(() => void poll(), 2000)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [recovering, result?.check_id])

  const estimate = useCallback(async () => {
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
  }, [selectedVisit, busy])
  const automaticEstimate = useRef('')
  useEffect(() => {
    if (!loaded || !selectedVisit || result || error || busy) return
    if (automaticEstimate.current === selectedVisit) return
    automaticEstimate.current = selectedVisit
    void estimate()
  }, [loaded, selectedVisit, result, error, busy, estimate])
  const saveReview = async (review: VisitReview) => {
    if (!result || busy) return
    const current = ++requestId.current
    setBusy('review'); setError(''); setNotice('')
    try {
      const updated = await commercialVisitReview(result, review)
      if (current === requestId.current) { setResult(updated); setNotice(updated.eligibility_error ? 'Planned services saved. Clinic rates updated; the insurance split still needs usable benefits.' : `${review.verification ? 'Verification saved.' : 'Estimate updated.'} Recalculated from the saved payer response.`) }
    } catch (caught) {
      if (current === requestId.current) setError(caught instanceof Error ? caught.message : 'Verification could not be saved.')
    } finally { if (current === requestId.current) setBusy(null) }
  }
  const selectCoverage = async (candidateId: string) => {
    if (!result || busy) return
    const current = ++requestId.current
    setBusy('coverage'); setError(''); setNotice('')
    try {
      const updated = await commercialSelectCoverageCandidate(result, candidateId)
      if (current === requestId.current) { setResult(updated); setNotice('Checking the selected policy with Stedi…') }
    } catch (caught) {
      if (current === requestId.current) setError(caught instanceof Error ? caught.message : 'The selected policy could not be checked.')
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
  const failed = !!result?.eligibility_error
  const failure = result?.eligibility_failure
  const patientPrice = benefits?.active === true && benefits.network_status === 'in_network' && (price?.complete ?? !benefits.requires_review) && price?.patient_min_cents != null && price.patient_max_cents != null ? range(price.patient_min_cents, price.patient_max_cents) : null
  const serviceOptions = price?.mode === 'options'
  const insurancePrice = patientPrice && price?.plan_min_cents != null && price.plan_max_cents != null ? range(price.plan_min_cents, price.plan_max_cents) : null
  const conditions = [...new Set(price?.lines?.flatMap(line => line.conditions || []) || [])]
  const options = benefits?.reported_options || []
  const reviewItems = [...new Set([...(price?.service_issues || []), ...(price?.lines ? price.lines.flatMap(line => line.issues) : benefits?.review_reasons || []), ...(result?.issues || [])])]
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
      <div className="visit-content" aria-busy={!!busy || recovering}>
        {notice && <p className="visit-notice" role="status">{notice}</p>}
        {deleteError && <p className="visit-error" role="alert">{deleteError}</p>}
        {error && <div className="visit-error" role="alert"><CircleHelp size={20} aria-hidden="true" /><div><strong>{selected ? 'The estimate could not be checked' : 'Visits could not be loaded'}</strong><p>{error}</p><button className="visit-text-button" disabled={!!busy} onClick={() => void (selected ? estimate() : load())}>Try again <ArrowRight size={15} /></button></div></div>}
        {selected && <div className="visit-context"><CalendarDays size={18} aria-hidden="true" /><div><strong>{visitDate(selected.start)} <span>at {visitTime(selected.start)}</span></strong><p>{selected.reason || 'Scheduled visit'}</p></div></div>}
        {busy === 'estimate' ? <div className="visit-empty" role="status"><RefreshCw className="visit-spin" size={26} aria-hidden="true" /><h2>Checking this visit</h2><p>Reading the appointment, insurance benefits, and available clinic rates.</p></div> : result ? <>
          <section className="visit-answer" aria-live="polite">
            <p className="visit-label">{failed ? 'Insurance check needs attention' : serviceOptions ? 'Plan the visit' : price?.conditional ? 'Conditional previsit estimate' : 'Previsit estimate'}</p>
            {failed ? <><h2 className="visit-pending">{recovering ? 'Finding coverage with Stedi…' : result.recovery?.outcome === 'no_match' ? 'No matching policy returned' : result.recovery?.candidates?.length ? 'Choose the policy for this estimate' : failure?.title || 'The payer could not complete the check'}</h2><p className="visit-answer-description">{failure?.detail || 'No usable benefits were returned. This does not establish inactive coverage.'}</p><p className="visit-assumption" role={recovering ? 'status' : undefined}>{result.recovery ? result.recovery.message : <><strong>Next step:</strong> {failure?.action || result.issues.join(' ')}</>}</p>{result.recovery?.candidates?.length ? <div className="visit-coverage-candidates" aria-label="Coverage options returned by Stedi">{result.recovery.candidates.map(candidate => <article key={candidate.id}><div><strong>{candidate.payer}</strong><span>{candidate.plans.join(' · ') || 'Plan name not returned'} · {candidate.relationship === 'self' ? 'Patient is subscriber' : 'Patient is dependent'}</span><small>{candidate.address_matches ? 'Name, date of birth, and address match eCW' : 'Name and date of birth match eCW; compare the current card before use'}</small></div><button className="visit-primary" disabled={!!busy || recovering} onClick={() => void selectCoverage(candidate.id)}>{busy === 'coverage' ? 'Checking…' : 'Use this policy'}</button></article>)}</div> : null}{failure && <p className="visit-muted">Stedi{failure.codes.length ? ` · Payer error ${failure.codes.join(', ')}` : ''} · Attempted {visitDate(failure.attempted_at)}, {visitTime(failure.attempted_at)}</p>}<p className="visit-answer-description">Build the anticipated visit below to see its contracted total. The patient / insurance split will be calculated when usable benefits are returned.</p>{price?.mode === 'services' && price.allowed_min_cents != null && <p className="visit-assumption"><strong>Planned contracted total: {money(price.allowed_min_cents)}</strong>{!!price.service_issues?.length && <span> · {price.service_issues[0]}</span>}</p>}</> : serviceOptions ? <><h2 className="visit-pending">Choose the anticipated services</h2><p className="visit-answer-description">Your clinic’s contracted prices are ready below. Build a visit estimate with the expected services and units; final coding can happen after care.</p></> : <>
              <div className="visit-payment-split"><div><span>Estimated patient share</span><h2 className={patientPrice ? 'visit-amount' : 'visit-pending'}>{patientPrice || (price?.service_issues?.length ? 'Complete the planned services' : benefits?.active === true ? 'Benefit details needed' : 'Check incomplete')}</h2></div>{insurancePrice && <div><span>Estimated insurance share</span><strong className="visit-insurance-amount">{insurancePrice}</strong></div>}</div>
              <p className="visit-answer-description">{patientPrice ? price?.conditional ? 'Planning amount, subject to the conditions below. Final payment depends on covered services and the payer’s claim decision.' : 'Calculated from the payer’s benefits and your clinic’s contracted rates. Final cost depends on the care provided.' : benefits?.active === false ? 'The payer reported inactive coverage. Confirm coverage or review self-pay pricing.' : 'Available coverage and contract information remains below. The check details explain what is still needed for the split.'}</p>
              {conditions.some(item => item.startsWith('Assumes this covered')) && <p className="visit-assumption"><strong>Why this split:</strong> The estimate applies the remaining deductible ({shown(benefits?.deductible_remaining_cents)}) to these covered services first. In this scenario, insurance starts paying after the deductible. A deductible waiver or special copay would change the split.</p>}
              {price?.allowed_min_cents != null && price.allowed_max_cents != null && <div className="visit-contracted"><span>Contracted {price.mode === 'alternatives' ? 'visit alternatives' : price.service_issues?.length ? 'selected-service subtotal' : 'visit total'}<small>{price.service_issues?.length ? 'Companion service still needed' : 'Patient share + insurance share'}</small></span><strong>{range(price.allowed_min_cents, price.allowed_max_cents)}</strong></div>}
            </>}
            {!failed && (!!reviewItems.length || !!conditions.length) && <details className="visit-next-step" open={!!price?.service_issues?.length}><summary>Before collecting · follow-up & assumptions <ChevronDown size={16} aria-hidden="true" /></summary><ul>{[...new Set([...reviewItems, ...conditions])].map(item => <li key={item}>{item}</li>)}</ul></details>}
            {!failed && price?.collection && <div className="visit-collection"><strong>Collection recommendation{price.collection.amount_cents != null ? ` · ${money(price.collection.amount_cents)}` : ''}</strong><p>{price.collection.reason}</p>{price.collection.payment_reference && <small>Payment verification: {price.collection.payment_reference}</small>}</div>}
          </section>
          <section className="visit-coverage" aria-labelledby="visit-coverage-title"><div className="visit-section-heading"><h3 id="visit-coverage-title">Coverage at a glance</h3>{result.checked_at && <span><Clock3 size={14} aria-hidden="true" /> Checked {visitDate(result.checked_at)}, {visitTime(result.checked_at)}</span>}</div>
            <p className="visit-payer">{result.payer || 'Insurer not confirmed'}{result.plan_details?.stedi.names?.length ? <span> · {result.plan_details.stedi.names.join(' · ')}</span> : null}</p>
            <dl>
              <CoverageCheck label="Coverage" value={failed ? result.ecw_coverage_active ? 'Active policy in eCW · Payer check unresolved' : 'Payer check unsuccessful' : benefits?.active === true ? 'Active' : benefits?.active === false ? 'Inactive' : 'Not confirmed'} confirmed={benefits?.active === true} />
              <CoverageCheck label="Provider network" detail="Clinic contract record" value={benefits?.network_status === 'in_network' ? 'In network' : benefits?.network_status === 'out_of_network' ? 'Out of network' : 'Not verified'} confirmed={benefits?.network_status === 'in_network'} />
              <CoverageCheck label="Exact plan / product" value={result.plan_details?.stedi.names?.join(' · ') || (result.plan !== 'CI' ? result.plan_details?.ecw.plan?.join(' · ') || result.plan : null) || 'Not returned'} confirmed={!!result.plan_details?.stedi.names?.length} detail={result.plan_details?.stedi.names?.length ? 'Payer response' : 'eCW coverage record'} />
              {!failed && <><CoverageCheck label="Deductible remaining" value={shown(benefits?.deductible_remaining_cents)} confirmed={benefits?.deductible_remaining_cents != null} />
              <CoverageCheck label="Out of pocket remaining" value={shown(benefits?.oop_remaining_cents)} confirmed={benefits?.oop_remaining_cents != null} />
              <CoverageCheck label="Referral required?" value={requirementValue('referral', 'required')} confirmed={!!price?.lines?.length && price.lines.every(line => ['required', 'not_required'].includes(line.benefits.requirements.referral.required))} />
              <CoverageCheck label="PA required?" value={requirementValue('authorization', 'required')} confirmed={!!price?.lines?.length && price.lines.every(line => ['required', 'not_required'].includes(line.benefits.requirements.authorization.required))} detail={benefits?.authorization_basis} />
              <CoverageCheck label="Referral received / valid?" value={requirementValue('referral', 'status')} confirmed={!!price?.lines?.length && price.lines.every(line => line.benefits.requirements.referral.cleared)} />
              <CoverageCheck label="PA obtained?" value={requirementValue('authorization', 'status')} confirmed={!!price?.lines?.length && price.lines.every(line => line.benefits.requirements.authorization.cleared)} /></>}
            </dl>
          </section>
          {price?.lines && <VisitServices key={`${result.check_id}:${result.revision}`} result={result} busy={!!busy || recovering} save={saveReview} />}
          <div className="visit-details">
            {!price?.lines && <details><summary><span>Visit pricing & CPT codes <small>{result.codes.length ? `${result.codes.length} ${conditionalCodes ? 'possible visit levels' : 'scheduled services'}` : 'Coding not yet available'}</small></span><ChevronDown size={18} aria-hidden="true" /></summary><div className="visit-detail-body">
              <p>{conditionalCodes ? 'These are alternative office visit levels. One level applies, based on the documented medical decision making or time. Amounts are not added together.' : 'These services were returned by the appointment record. Final coding depends on the care provided.'}</p>
              {result.codes.length ? <table><caption className="visit-sr-only">CPT codes and contracted visit prices</caption><thead><tr><th scope="col">CPT</th><th scope="col">Source</th><th scope="col">Contracted price</th></tr></thead><tbody>{result.codes.map((row, index) => <tr key={`${row.code}-${index}`}><td>{row.code}</td><td>{row.source === 'conditional office E/M alternative' ? 'Possible office visit level' : row.source}</td><td>{shown(price?.scenarios.find(scenario => scenario.code === row.code)?.allowed_cents)}</td></tr>)}</tbody></table> : <p>No service codes are available yet.</p>}
              {result.suggestions?.groups.length ? <><h4>Other coding suggestions</h4><p>AI suggestions require clinical confirmation and are excluded from the price above.</p>{result.suggestions.groups.map((group, index) => <p key={`${group.label}-${index}`}><strong>{group.label}</strong>: {group.codes.map(row => row.code).join(', ')}</p>)}</> : null}
            </div></details>}
            {!failed && <details><summary><span>Insurance benefits <small>Copay, deductible & payer terms</small></span><ChevronDown size={18} aria-hidden="true" /></summary><div className="visit-detail-body">
              <dl className="visit-benefit-values"><div><dt>Applicable service copay</dt><dd>{benefits?.copay_cents == null && options.some(row => row.kind === 'copay') ? 'Multiple terms returned' : shown(benefits?.copay_cents)}</dd></div><div><dt>Coinsurance</dt><dd>{benefits?.coinsurance_percent != null ? `${benefits.coinsurance_percent}%` : options.some(row => row.kind === 'coinsurance') ? 'Multiple terms returned' : 'Not confirmed'}</dd></div><div><dt>Remaining deductible</dt><dd>{shown(benefits?.deductible_remaining_cents)}</dd></div><div><dt>Remaining out of pocket</dt><dd>{shown(benefits?.oop_remaining_cents)}</dd></div></dl>
              {!!options.length && <><h4>Terms returned by the payer</h4><p>Each term retains its service and network scope. A returned term may not apply to this visit.</p><ul className="visit-payer-terms">{options.map((row, index) => <li key={index}><div><strong>{humanize(row.kind)}</strong><span>{row.quantity?.value != null ? `${row.quantity.value} ${humanize(row.quantity.qualifier || 'units')}` : row.amount_cents == null ? row.percent == null ? 'Amount not stated' : `${row.percent}%` : money(row.amount_cents)}</span></div><small>{row.procedure_code ? `CPT ${row.procedure_code}` : row.service_type_code === '98' ? 'Professional physician / neurology' : row.service_type_code === '30' ? 'General medical' : row.service_type_code ? `Service ${row.service_type_code}` : 'Service scope not stated'} · {networkLabel(row.network)}{row.coverage_level ? ` · ${humanize(row.coverage_level)}` : ''}{row.time_period ? ` · ${periodLabel(row.time_period)}` : ''}</small>{row.payer_note && <p>{row.payer_note}</p>}</li>)}</ul></>}
            </div></details>}
            <details><summary><span>Check details <small>Sources & items needing review</small></span><ChevronDown size={18} aria-hidden="true" /></summary><div className="visit-detail-body">{result.recovery && <p>Coverage recovery: Stedi · {result.recovery.message}{result.recovery.reference && ` · Reference ${result.recovery.reference}`}{result.recovery.checked_at && ` · Checked ${visitDate(result.recovery.checked_at)} at ${visitTime(result.recovery.checked_at)}`}</p>}<p>Appointment and coverage: eClinicalWorks. Benefit response: Stedi. Contracted rates and network participation: clinic configuration.</p>{result.calculated_at && <p>Calculated {visitDate(result.calculated_at)} at {visitTime(result.calculated_at)}.</p>}{result.ecw_retrieved_at && <p>eCW retrieved {visitDate(result.ecw_retrieved_at)} at {visitTime(result.ecw_retrieved_at)}.</p>}{result.response_reference && <p>Payer response reference: {result.response_reference}</p>}{result.network_evidence?.evidence_reference && <p>Network participation: {result.network_evidence.network_name} · {result.network_evidence.evidence_reference} · Effective {result.network_evidence.effective_from}</p>}{result.plan_details && <dl className="visit-benefit-values">{Object.entries(result.plan_details.ecw).map(([key, values]) => <div key={key}><dt>eCW {humanize(key)}</dt><dd>{values.join(' · ') || 'Not returned'}</dd></div>)}{result.plan_details.stedi.group_number && <div><dt>Payer group</dt><dd>{result.plan_details.stedi.group_number}</dd></div>}{result.plan_details.stedi.plan_number && <div><dt>Payer plan number</dt><dd>{result.plan_details.stedi.plan_number}</dd></div>}</dl>}{result.checked_at && <p>Benefits checked {visitDate(result.checked_at)} at {visitTime(result.checked_at)}.</p>}{benefits?.normalization_notes?.length ? <ul>{benefits.normalization_notes.map(note => <li key={note}>{note}</li>)}</ul> : null}{!!reviewItems.length && <p>Review items are listed with the estimate above.</p>}</div></details>
          </div>
          <div className="visit-result-footer"><span>Estimate only · Final charges depend on services provided.</span><div className="visit-result-actions">{intakeId && result.check_id && <button className="visit-text-button visit-delete-button" disabled={!!busy || recovering} onClick={() => void deleteCheck()}><Trash2 size={15} aria-hidden="true" />{busy === 'delete' ? 'Deleting…' : cleanupPending ? 'Retry file cleanup' : 'Delete check'}</button>}<button className="visit-text-button" disabled={!!busy || cleanupPending || recovering} onClick={() => void estimate()}><RefreshCw size={15} /> Check again</button></div></div>
        </> : !error && !busy && <div className="visit-empty"><CalendarDays size={28} aria-hidden="true" /><h2>{selected ? 'Preparing this visit' : 'No upcoming visit to estimate'}</h2><p>{selected ? 'ClinicForce is reading the appointment, payer benefits, and clinic rates.' : 'Refresh visits after the appointment is scheduled in eClinicalWorks.'}</p></div>}
        {busy === 'visits' && <div className="visit-empty" role="status"><RefreshCw className="visit-spin" size={26} aria-hidden="true" /><p>Loading the patient’s upcoming visits…</p></div>}
      </div>
    </div>
  </section>
}
