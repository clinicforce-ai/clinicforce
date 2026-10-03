import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CalendarDays, Check, Clock3, RefreshCw, ShieldCheck, Trash2, BadgeDollarSign, Network, FileText, Wallet, PieChart } from 'lucide-react'
import { getStaffCheckFinancials, deleteStaffInsuranceCheck, getStaffIntake, getStaffSession, type InsuranceCheckSnapshot, type StaffPatient, type StaffSession } from '../services/api/staff'
import { commercialFinancials, type VisitFinancials, commercialAppointments, commercialHistory, commercialPrevisit, commercialSavedPrevisit, commercialSelectCoverageCandidate, commercialVisitReview, type VisitReview, type CommercialAppointment, type CommercialPastVisit, type PrevisitEstimate } from '../services/api/commercial-estimator'
import VisitServices, { ServicePlanner } from './VisitServices'
import InsuranceCards from './InsuranceCards'
import './CommercialEstimator.css'

const money = (cents: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(cents / 100)
const range = (min: number, max: number) => min === max ? money(min) : `${money(min)} – ${money(max)}`
const dateLabel = (value: string, options: Intl.DateTimeFormatOptions) => {
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value)
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString(undefined, options)
}
const visitDate = (value: string) => dateLabel(value, { month: 'short', day: 'numeric', year: 'numeric' })
const visitTime = (value: string, timeZone?: string) => dateLabel(value, { hour: 'numeric', minute: '2-digit', ...(timeZone ? { timeZone, timeZoneName: 'short' } : {}) })

function ApplicableBenefits({ result }: { result: PrevisitEstimate }) {
  const services = result.collection_guidance?.services || []
  return <section><h3>Benefits used for this visit</h3><p>Matched to the selected services, visit date and location.</p>
    {services.map(row => <article className="service-evidence" key={row.id}><h4>CPT {row.code} · {row.benefit}</h4><dl>
      {row.copay_cents != null && <div><dt>Applicable copay</dt><dd>{money(row.copay_cents)}</dd></div>}
      {row.coinsurance_percent != null && <div><dt>Coinsurance</dt><dd>{row.coinsurance_percent}%</dd></div>}
      {row.deductible_remaining_cents != null && <div><dt>Deductible remaining</dt><dd>{money(row.deductible_remaining_cents)}</dd></div>}
      {row.oop_remaining_cents != null && <div><dt>Out-of-pocket remaining</dt><dd>{money(row.oop_remaining_cents)}</dd></div>}
    </dl>{row.oop_remaining_cents === 0 && <p>The remaining out-of-pocket limit reduces the estimated cost for covered in-network care to $0.</p>}</article>)}
    {!services.length && <p>Applicable benefits are still needed to calculate the collection amount.</p>}
  </section>
}

export function FinancialHistory({ checkId, intakeId }: { checkId: string; intakeId?: string }) {
  const [data, setData] = useState<VisitFinancials | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const read = useCallback(() => intakeId ? getStaffCheckFinancials(intakeId, checkId) : commercialFinancials(checkId), [checkId, intakeId])
  const load = useCallback(async () => { setBusy(true); setError(''); try { setData(await read()) } catch { setError('The financial connection could not be read. Retry the check.') } finally { setBusy(false) } }, [read])
  useEffect(() => { let active = true; read().then(value => { if (active) setData(value) }).catch(() => { if (active) setError('The financial connection could not be read. Retry the check.') }).finally(() => { if (active) setBusy(false) }); return () => { active = false } }, [read])
  return <section className="financial-history"><div className="visit-section-heading"><div><h3><BadgeDollarSign size={20} aria-hidden="true" /> Processed claims</h3></div><button className="visit-text-button" disabled={busy} onClick={() => void load()}><RefreshCw size={16} /> Refresh</button></div>
    <p className="visit-muted">Actual amounts from the insurer’s processed claims. They appear when the connected payer feed receives the receipt; timing varies.</p>
    {busy ? <p role="status">Reading insurance receipts…</p> : <p role={error ? 'alert' : undefined}>{error || data?.message}</p>}
    {!!data?.lines.length && <div className="clinical-table-wrap"><table className="clinical-service-table"><thead><tr><th>Service / payer</th><th>Allowed amount</th><th>Insurer paid</th><th>Patient share</th></tr></thead><tbody>{data.lines.map(row => <tr key={`${row.transaction_id}:${row.claim_reference}:${row.line_index}`}><th scope="row">CPT {row.code}{row.modifiers.length ? ` · ${row.modifiers.join(', ')}` : ''}<small>{row.service_date ? visitDate(row.service_date.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')) : 'Service date not returned'} · {row.payer}</small><small>{({ '1': 'Primary insurance processed', '2': 'Secondary insurance processed', '3': 'Additional insurance processed', '4': 'Denied', '22': 'Payment reversed' } as Record<string, string>)[row.claim_status || ''] || 'Payer response'} · Received {row.received_at ? visitDate(row.received_at) : 'date not supplied'}</small></th><td>{row.allowed_cents == null ? 'Not returned' : money(row.allowed_cents)}</td><td>{row.insurer_paid_cents == null ? 'Not returned' : money(row.insurer_paid_cents)}</td><td>{row.patient_responsibility_cents == null ? 'Not returned' : money(row.patient_responsibility_cents)}</td></tr>)}</tbody></table></div>}
    {data && <small>{data.source} · Checked {visitDate(data.checked_at)} {visitTime(data.checked_at)}{data.partial ? ' · Partial history' : ''}. These records do not show payments collected from the patient.</small>}
  </section>
}

type DetailPanel = 'coverage' | 'notes' | 'calculation' | 'sources' | 'services'
function AppointmentNotes({ result }: { result: PrevisitEstimate }) {
  const fields = result.appointment_context?.imported_fields || []
  const isStatus = (label: string) => /^(?:(?:appointment|encounter|visit)[ _-]+)?status$/i.test(label.trim())
  const statuses = fields.filter(field => isStatus(field.label))
  const details = fields.filter(field => !isStatus(field.label))
  return <section className="visit-imported">
    <h3>{result.reason || 'Scheduled visit'}</h3>
    <p className="visit-muted">{[result.appointment_context?.rendering_provider?.name, result.appointment_context?.location].filter(Boolean).join(' · ')}</p>
    {statuses.map((field, index) => <p className="visit-muted" key={index}>Appointment status: {field.text}</p>)}
    {details.map((field, index) => <article key={`${field.source}:${index}`}><h3>{field.label}</h3><p>{field.text}</p><small>From eCW{field.updated_at ? ` · ${visitDate(field.updated_at)}` : ''}</small></article>)}
  </section>
}

function CostPicture({ result }: { result: PrevisitEstimate }) {
  const price = result.price_range
  const patient = price?.patient_min_cents, insurer = price?.plan_min_cents
  const split = price?.complete && price.mode === 'services' && patient != null && insurer != null && patient === price.patient_max_cents && insurer === price.plan_max_cents && patient + insurer > 0
  if (result.payment_mode === 'cash') return <div className="cost-picture"><Wallet size={36} aria-hidden="true" /><h3>Clinic cash price</h3><p>{result.cash_quote?.message || 'The clinic’s saved price for the selected services.'}</p></div>
  if (result.planning_estimate?.basis === 'out_of_pocket_limit') return <div className="cost-picture cost-limit"><div className="cost-emblem"><ShieldCheck size={42} aria-hidden="true" /></div><h3>Out-of-pocket limit reached</h3><p>Payer reports <strong>$0 remaining</strong> for in-network care.</p><span>Covered services · assumptions apply</span></div>
  if (split) return <div className="cost-picture"><span className="visit-label">Estimated cost sharing</span><div className="cost-share-bar" role="img" aria-label={`Patient ${money(patient)}, insurer ${money(insurer)}`}><span style={{ flex: patient }} /><span style={{ flex: insurer }} /></div><dl className="cost-share-legend"><div><dt><i />Patient</dt><dd>{money(patient)}</dd></div><div><dt><i />Insurance</dt><dd>{money(insurer)}</dd></div></dl><p>Contracted visit total <strong>{money(patient + insurer)}</strong></p></div>
  return <div className="cost-picture"><Network size={36} aria-hidden="true" /><h3>{result.benefits?.active ? 'Policy benefits received' : 'Coverage check'}</h3><p>{result.benefits?.active ? 'A final insured amount also depends on the service, provider network and clinic contract.' : 'The saved clinic cash price is available separately when provided.'}</p></div>
}

export default function CommercialEstimator({ onBack, intakeId, refreshBenefits = false, saved, currentAccountNumber }: { onBack?: () => void; intakeId?: string; refreshBenefits?: boolean; saved?: InsuranceCheckSnapshot; currentAccountNumber?: string | null }) {
  const readOnly = !!saved
  const [session, setSession] = useState<StaffSession | null>(null)
  const [patient, setPatient] = useState<StaffPatient | null>(saved?.patient || null)
  const [appointments, setAppointments] = useState<CommercialAppointment[]>([])
  const [history, setHistory] = useState<CommercialPastVisit[] | null>(null)
  const [result, setResult] = useState<PrevisitEstimate | null>(saved?.result || null)
  const [error, setError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [historyBusy, setHistoryBusy] = useState(false)
  const [selectedVisit, setSelectedVisit] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState<'visits' | 'estimate' | 'delete' | 'review' | 'coverage' | null>(readOnly ? null : 'visits')
  const [deleteError, setDeleteError] = useState('')
  const [cleanupPending, setCleanupPending] = useState(false)
  const [notice, setNotice] = useState('')
  const [panel, setPanel] = useState<DetailPanel | null>(null)
  const requestId = useRef(0)
  const automaticEstimate = useRef('')

  const load = useCallback(async () => {
    const current = ++requestId.current
    automaticEstimate.current = ''
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
    if (readOnly) return
    const requests = requestId
    const timer = window.setTimeout(() => { void load() }, 0)
    return () => { window.clearTimeout(timer); requests.current++ }
  }, [load, readOnly])

  const recovering = !readOnly && result?.recovery?.state === 'pending'
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

  const estimate = useCallback(async (refresh = refreshBenefits) => {
    if (!selectedVisit || busy) return
    const current = ++requestId.current
    setResult(null); setError(''); setNotice(''); setDeleteError(''); setBusy('estimate')
    try {
      const response = await commercialPrevisit(selectedVisit, refresh)
      if (response.appointment_id !== selectedVisit) throw new Error('The returned estimate does not match this visit. Refresh visits and try again.')
      if (current === requestId.current) setResult(response)
    } catch (caught) {
      if (current === requestId.current) setError(caught instanceof Error ? caught.message : 'This visit could not be checked.')
    } finally { if (current === requestId.current) setBusy(null) }
  }, [selectedVisit, busy, refreshBenefits])
  useEffect(() => {
    if (!loaded || !selectedVisit || result || error || busy) return
    if (automaticEstimate.current === selectedVisit) return
    automaticEstimate.current = selectedVisit
    void estimate()
  }, [loaded, selectedVisit, result, error, busy, estimate])
  const saveReview = async (review: VisitReview) => {
    if (!result || busy) return false
    const current = ++requestId.current
    setBusy('review'); setError(''); setNotice('')
    try {
      const updated = await commercialVisitReview(result, review)
      if (current === requestId.current) { setResult(updated); setNotice(''); return true }
      return false
    } catch (caught) {
      if (current === requestId.current) setError(caught instanceof Error ? caught.message : 'Verification could not be saved.')
      return false
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

  const chooseVisit = (id: string) => { if (selectedVisit !== id) { setPanel(null); setSelectedVisit(id); setResult(null); setError(''); setDeleteError(''); setCleanupPending(false); setNotice('') } }
  const selected = appointments.find(visit => visit.id === selectedVisit)
  const benefits = result?.benefits
  const price = result?.price_range
  const failed = !!result?.eligibility_error
  const failure = result?.eligibility_failure
  const patientPrice = benefits?.active === true && benefits.network_status === 'in_network' && price?.complete && price?.patient_min_cents != null && price.patient_max_cents != null ? range(price.patient_min_cents, price.patient_max_cents) : null
  const calculationReasons = [...new Set((price?.lines || []).map(line => line.benefits.benefit_summary).filter(Boolean))]
  const policySources = [...new Map((price?.lines || []).filter(line => line.benefits.policy_evidence).map(line => [line.benefits.policy_evidence!.url, line.benefits.policy_evidence!])).values()]
  const cashPrice = price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null ? range(price.cash_min_cents, price.cash_max_cents) : null
  const planName = result?.plan_details?.stedi.names?.join(' · ') || result?.plan_details?.ecw.plan?.filter(value => value !== 'CI').join(' · ') || (result?.plan !== 'CI' ? result?.plan : null)
  const payerSubscriber = result?.insurance_details?.payer_subscriber
  const payerPatient = result?.insurance_details?.payer_patient
  const policyDates = result?.insurance_details?.payer_coverage_period?.start ? result.insurance_details.payer_coverage_period : result?.insurance_details?.coverage_period

  const tooEarly = result?.eligibility_timing?.status === 'too_early'
  const policyStatus = tooEarly ? 'Check closer to visit' : benefits?.service_date_matches === false ? 'Coverage returned · verify appointment date' : benefits?.active === true ? 'Coverage active' : benefits?.active === false ? 'Coverage inactive' : result?.eligibility_error === 'payer_routing_unresolved' ? 'Eligibility unverified' : result?.ecw_coverage_active ? 'Policy in eCW · verify benefits' : 'Coverage check in progress'

  const cash = result?.payment_mode === 'cash'
  const calculating = (!!busy && busy !== 'delete') || recovering || (!!selected && !result && !error)
  const guidance = !error && result?.collection_guidance?.status !== 'deferred' ? result?.collection_guidance : undefined
  const unavailableReason = error ? 'Current benefits could not be loaded. A collection amount has not been calculated.' : 'The available information does not yet establish a collection amount.'
  const supportedAmount = guidance?.status !== 'deferred_billing' && guidance?.amount_cents != null && Number.isFinite(guidance.amount_cents) && guidance.amount_cents >= 0
  const emg = result?.emg_option
  const emgScenario = !cash && emg?.requested && !emg.included
  const amount = calculating || error ? null : cash ? cashPrice : emgScenario ? emg.total_min_cents != null ? money(emg.total_min_cents) : null : supportedAmount ? money(guidance!.amount_cents!) : null
  const deferred = !calculating && !tooEarly && (!cash && guidance?.status === 'deferred_billing' || !amount)
  const cashFallback = !readOnly && !cash && deferred && !error && !emgScenario && !!cashPrice
  const amountText = readOnly && !result?.collection_guidance && !cash ? 'Not retained' : calculating ? 'Calculating…' : amount || (cashFallback ? cashPrice : tooEarly ? 'Too early to check' : cash ? 'Price pending' : '$0.00')
  const amountLabel = cashFallback ? 'Self-pay option · select Cash to use' : deferred && !cash ? 'Insurance advance today' : readOnly ? 'Suggested collection at this visit' : 'Collect at arrival'
  const cashExplanation = `${result?.eligibility_error === 'payer_routing_unresolved' && failure?.detail ? `${failure.detail} ` : ''}${result?.cash_quote?.message || 'The cash price is not established.'}`
  const explanation = readOnly && !result?.collection_guidance && !cash ? 'The original collection recommendation was not retained. The saved benefit and calculation details are shown below.' : cashFallback ? `No insurance advance is set: ${guidance?.reason || unavailableReason} This is the clinic’s self-pay price for the planned services; select Cash before quoting or collecting it.` : deferred ? cash ? cashExplanation : error ? 'Current benefits could not be loaded; collect no insurance advance today.' : emgScenario ? emg.message : `${guidance?.reason || unavailableReason} Collect no insurance advance today; bill any patient responsibility after the claim.` : calculating ? 'Reading the visit, insurance and applicable benefits to calculate the amount.' : cash ? cashPrice && !error ? cashExplanation : 'The clinic cash price could not be calculated for this service plan.' : emgScenario ? emg.total_min_cents != null ? 'Lowest estimated total for the office consultation plus EMG / nerve testing. The testing scope below shows the possible range.' : emg.message : guidance?.reason || unavailableReason
  const requirementLabel = (kind: 'referral' | 'authorization') => guidance?.requirements[kind] || 'Determined by plan'
  const provider = result?.appointment_context?.rendering_provider?.name
  const anticipatedService = result?.price_range?.mode === 'alternatives' ? `${result.visit_type === 'new' ? 'New patient' : 'Established patient'} office consultation · E/M alternatives ${result.codes.map(row => row.code).join(', ')}` : result?.service_plan?.recommendation_basis

  return <section className="visit-estimator visit-visual" aria-labelledby="visit-estimate-title">
    {!readOnly && <header className="visit-page-header"><div><button className="visit-back" onClick={onBack}><ArrowLeft size={17} /> Back to patient</button><h1 id="visit-estimate-title">{cash ? 'Self-pay collection' : 'Insurance estimator'}</h1></div><span className="visit-practice">{session?.practice_name}</span></header>}
    {readOnly && <header className="visit-page-header"><div><h2 id="visit-estimate-title">{cash ? 'Saved self-pay estimate' : 'Saved insurance estimate'}</h2><p>Recorded recommendation for {visitDate(result!.service_date)}.{!cash && ' Final responsibility follows the processed claim.'}</p>{saved?.saved_at && <small>Saved {visitDate(saved.saved_at)} · {visitTime(saved.saved_at)} · Revision {result?.revision}</small>}{saved?.legacy && <p className="visit-muted">This older check contains the last retained result. Earlier versions and the exact save time were not recorded.</p>}</div></header>}
    <div className="visit-patient-bar"><div><span className="patient-monogram" aria-hidden="true">{patient?.given_name?.[0]}{patient?.family_name?.[0]}</span><div><strong>{patient ? `${patient.given_name} ${patient.family_name}` : 'Patient record'}</strong>{patient?.birth_date && <small>DOB {visitDate(patient.birth_date)}</small>}{(result?.patient_details?.ecw_account_number || (!readOnly && patient?.ecw_account_number) || currentAccountNumber) && <small>eCW ACCT{readOnly && !result?.patient_details?.ecw_account_number ? ' (current record)' : ''}: {result?.patient_details?.ecw_account_number || (!readOnly && patient?.ecw_account_number) || currentAccountNumber}</small>}</div></div><span className={benefits?.active === true && benefits.service_date_matches !== false ? 'coverage-chip' : 'coverage-chip is-neutral'}><ShieldCheck size={18} aria-hidden="true" />{policyStatus}</span></div>
    {!readOnly && <div className="visit-appointment-strip"><CalendarDays size={22} aria-hidden="true" /><label>Scheduled visit<select value={selectedVisit} disabled={!!busy || !appointments.length} onChange={event => chooseVisit(event.target.value)}>{appointments.length ? appointments.map(visit => <option key={visit.id} value={visit.id}>{visitDate(visit.start)} · {visitTime(visit.start, result?.appointment_context?.timezone)} · {visit.reason || 'Scheduled care'}</option>) : <option value="">{loaded ? 'No scheduled visit returned by eCW' : 'Loading appointments…'}</option>}</select></label><button className="visit-icon-button" aria-label="Refresh visits" disabled={!!busy} onClick={() => void load()}><RefreshCw size={18} className={busy === 'visits' ? 'visit-spin' : ''} /></button></div>}
    {result && <dl className="visit-context-facts">
      <div><dt>Visit type</dt><dd>{result.visit_type === 'new' ? 'New patient' : result.visit_type === 'established' ? 'Established patient' : result.appointment_context?.appointment_type || 'Not returned'}</dd></div>
      <div><dt>Reason</dt><dd>{result.reason || selected?.reason || 'Not returned'}</dd></div>
      <div><dt>Provider</dt><dd>{provider || 'Name not returned by eCW'}</dd></div>
      <div><dt>Insurance</dt><dd>{result.payer || 'Not returned'}<small>{planName}</small></dd></div>
    </dl>}
    <div className="visit-content" aria-busy={!!busy || recovering}>
      {notice && <p className="visit-notice" role="status">{notice}</p>}{deleteError && <p className="visit-error" role="alert">{deleteError}</p>}
      {error && <p className="visit-notice" role="status">The estimate could not be updated. Collect no insurance advance from this result.<button disabled={!!busy} onClick={() => void (selected ? estimate() : load())}>Try again <ArrowRight size={15} /></button></p>}
      {(!result || busy === 'estimate' || busy === 'visits') && <section className="visit-overview" aria-label="Arrival collection"><div className="arrival-total"><span>Collect at arrival</span><strong className="amount-pending" role="status">{amountText}</strong><p>{explanation}</p></div></section>}
      {busy === 'estimate' || busy === 'visits' ? <div className="visit-empty" role="status"><RefreshCw className="visit-spin" size={26} aria-hidden="true" /><h2>{busy === 'visits' ? 'Finding your visit' : 'Putting the estimate together'}</h2><p>Reading the appointment, patient and insurance from eCW. Checking saved eligibility and any current benefits needed.</p></div> : result ? <>
        <section id="visit-estimate" className="visit-overview" aria-labelledby="payment-heading">
          <div className="overview-heading"><h2 id="payment-heading">{cash ? 'Self-pay estimate' : cashFallback ? 'Self-pay option' : 'Arrival collection'}</h2>{!readOnly && <div className="payment-toggle" aria-label="Payment method"><button aria-pressed={!cash} disabled={!!busy || recovering} onClick={() => { if (cash) void saveReview({ payment_mode: 'insurance' }) }}><ShieldCheck size={16} />Insurance</button><button aria-pressed={cash} disabled={!!busy || recovering} onClick={() => { if (!cash) void saveReview({ payment_mode: 'cash' }) }}><Wallet size={16} />Cash</button></div>}</div>
          <div className="overview-amounts"><div className="arrival-total"><span>{amountLabel}</span><strong className={!amount && !cashFallback ? 'amount-pending' : undefined} role="status">{amountText}</strong><p><b>Why:</b> {explanation}</p>
            {cashFallback && <button disabled={!!busy || recovering} onClick={() => void saveReview({ payment_mode: 'cash' })}>Use this cash price</button>}
            {!cash && <><span className="estimate-condition">{cashFallback ? 'Self-pay price applies only if the patient chooses Cash.' : deferred ? 'No insurance advance today · bill after the claim.' : amount ? 'Advance estimate · assumptions apply.' : calculating ? 'Checking applicable benefits…' : tooEarly ? 'Appointment found · check within 7 days.' : 'No amount calculated.'}</span><p>{!calculating && (cashFallback ? 'Choosing self-pay changes the payment method for this estimate.' : guidance?.patient_message || 'The final patient share follows the insurer’s processed claim.')}</p>
              {!!guidance?.patient_actions.length && <div><b>Tell the patient:</b><ul>{guidance.patient_actions.map(action => <li key={action}>{action}</li>)}</ul></div>}
              {!!guidance?.assumptions.length && <details open={readOnly || undefined}><summary>Assumptions used</summary><ul>{guidance.assumptions.map(item => <li key={item}>{item}</li>)}</ul></details>}</>}
          </div><div className="arrival-context"><span className="visit-label">Anticipated service</span><h3>{anticipatedService || result.reason || 'Service not established'}</h3>{result.reason && result.reason !== anticipatedService && <p>eCW visit reason: {result.reason}</p>}<p>{readOnly ? 'Service plan recorded for this appointment.' : 'Based on the scheduled appointment. Modify the services below if the planned care changes.'}</p></div></div>
          <div className="visit-snapshots" aria-label="Coverage summary">
            <div><ShieldCheck size={20} aria-hidden="true" /><span>Coverage<strong>{tooEarly ? 'Check closer to visit' : benefits?.service_date_matches === false ? 'Different date returned' : benefits?.active === true ? 'Active' : benefits?.active === false ? 'Inactive' : failed && !recovering ? 'Unverified' : 'Check pending'}</strong></span></div>
            <div><Network size={20} aria-hidden="true" /><span>Network<strong>{guidance?.network || 'Not reported'}</strong>{guidance?.network_source && <small>{guidance.network_source}</small>}</span></div>
            <div><FileText size={20} aria-hidden="true" /><span>Referral<strong>{requirementLabel('referral')}</strong>{guidance?.requirement_sources?.referral && <small>{guidance.requirement_sources.referral}</small>}</span></div>
            <div><ShieldCheck size={20} aria-hidden="true" /><span>Prior authorization<strong>{requirementLabel('authorization')}</strong>{guidance?.requirement_sources?.authorization && <small>{guidance.requirement_sources.authorization}</small>}</span></div>
          </div>
          <p className="arrival-sources">Sources: eCW{tooEarly ? ' · Payer check deferred until closer to the visit' : result.checked_at ? ` + ${result.benefit_source || 'Stedi'} · benefits checked ${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}` : ' · Current payer benefits unavailable'}</p>
          <div className="overview-plan-name"><ShieldCheck size={16} aria-hidden="true" /><span>{cash ? 'Clinic self-pay schedule' : planName || result.payer || 'Policy details not returned'}</span>{!cash && cashPrice && <span className="cash-comparison">Cash option {cashPrice}</span>}</div>
        </section>
        {emg && !cash && <section className="visit-overview emg-option" aria-label="Additional testing">
          <label>{!readOnly && <input type="checkbox" checked={emg.requested || emg.included} disabled={!!busy || recovering || emg.included} onChange={event => void saveReview({ emg_required: event.target.checked })} />} EMG / nerve testing {readOnly ? emg.requested || emg.included ? 'included in estimate' : 'not selected' : 'required'}</label>
          <p>{calculating ? 'Calculating additional testing cost…' : emg.message}</p>
          {(emg.requested || emg.included) && !calculating && !error && <dl className="collection-components">
            <div><dt>Additional estimated patient cost</dt><dd>{emg.additional_min_cents != null && emg.additional_max_cents != null ? range(emg.additional_min_cents, emg.additional_max_cents) : 'Not yet calculated'}</dd></div>
            <div><dt>Visit total with testing</dt><dd>{emg.total_min_cents != null && emg.total_max_cents != null ? range(emg.total_min_cents, emg.total_max_cents) : 'Not yet calculated'}</dd></div>
          </dl>}
          {!readOnly && (emg.requested || emg.included) && <button onClick={() => { const detail = document.getElementById('visit-detail-services') as HTMLDetailsElement; detail.open = true; detail.scrollIntoView({ block: 'start' }) }}>Specify testing scope</button>}
        </section>}
        {result.identity?.status === 'warning' && <p className="visit-identity-notice"><Check size={16} aria-hidden="true" />{result.identity.message}</p>}
        {recovering && <p role="status" className="visit-muted">Checking additional coverage in the background…</p>}
        {readOnly && result.recovery?.state === 'pending' && <p className="visit-muted">An additional coverage search was pending when this version was saved.</p>}
        <div className="visit-detail-sections">
          <details open={readOnly || undefined} id="visit-detail-services" className="visit-detail-section"><summary>{readOnly ? 'Saved service plan' : 'Possible additional services / modify inputs'}</summary><div className="visit-detail-body">
            <p>{anticipatedService}</p>
            {readOnly ? <ul>{result.codes.map((row, index) => <li key={row.id || index}><strong>CPT {row.code} × {row.units ?? 'Units not retained'}</strong>{row.description && <p>{row.description}</p>}<small>{row.source}{row.modifiers?.length ? ` · Modifiers ${row.modifiers.join(', ')}` : ''}</small></li>)}</ul> : <ServicePlanner key={`${result.check_id}:${result.revision}`} result={result} busy={!!busy || recovering} save={saveReview} viewEstimate={message => { setNotice(message || 'Estimate updated.'); document.getElementById('visit-estimate')?.scrollIntoView({ block: 'start' }) }} />}
          </div></details>
          <details open={readOnly || undefined} id="visit-detail-coverage" className="visit-detail-section"><summary>Detailed benefits, insurance & card</summary><div className="visit-detail-body">
            {failed && <section className="visit-recovery"><h3>{recovering ? 'Finding coverage…' : failure?.title || 'Coverage check incomplete'}</h3><p>{guidance?.reason || unavailableReason}</p>{result.recovery?.message && <p>{result.recovery.message}</p>}{result.recovery && (result.recovery.reason_code || result.recovery.reference) && <small className="visit-muted">{result.recovery.reason_code?.replaceAll('_', ' ')}{result.recovery.reason_code && result.recovery.reference ? ' · ' : ''}{result.recovery.reference ? `Stedi discovery ${result.recovery.reference}` : ''}</small>}{result.recovery?.candidates?.map(candidate => <article key={candidate.id}><strong>{candidate.payer}</strong><p>{candidate.plans.join(' · ')}</p>{!readOnly && (result.eligibility_error !== 'payer_routing_unresolved' || candidate.matches_ecw_policy) && <button disabled={!!busy || recovering} onClick={() => void selectCoverage(candidate.id)}>Check this policy</button>}</article>)}</section>}
            <section className="patient-policy"><div className="policy-relationship"><div><span>Patient</span><strong>{payerPatient?.lastName ? [payerPatient.firstName, payerPatient.lastName].filter(Boolean).join(' ') : patient ? `${patient.given_name} ${patient.family_name}` : 'Not returned'}</strong><small>eCW: {patient?.given_name} {patient?.family_name}</small></div><ArrowRight size={22} aria-hidden="true" /><div><span>Policyholder · {result.insurance_details?.payer_relationship?.toLowerCase() || ({ '18': 'self', '01': 'spouse', '19': 'child', G8: 'other' } as Record<string, string>)[result.insurance_details?.relationship || ''] || 'relationship not returned'}</span><strong>{payerSubscriber?.lastName ? [payerSubscriber.firstName, payerSubscriber.lastName].filter(Boolean).join(' ') : [result.insurance_details?.subscriber_first, result.insurance_details?.subscriber_last].filter(Boolean).join(' ') || 'Not returned'}</strong></div></div>
              <dl><div><dt>Member ID</dt><dd>{result.insurance_details?.member_id || 'Not returned'}</dd></div>{(result.insurance_details?.group_number || result.plan_details?.stedi.group_number) && <div><dt>Group</dt><dd>{result.insurance_details?.group_number || result.plan_details?.stedi.group_number}</dd></div>}{policyDates?.start && <div><dt>Coverage dates</dt><dd>{policyDates.start}{policyDates.end ? ` – ${policyDates.end}` : ' onward'}</dd></div>}<div><dt>Contact</dt><dd>{result.patient_details?.phone || 'Phone not returned'}<small>{result.patient_details?.email}</small></dd></div><div><dt>Address</dt><dd>{Object.values(result.patient_details?.address || {}).filter(Boolean).join(', ') || 'Not returned'}</dd></div><div><dt>Payer patient DOB</dt><dd>{payerPatient?.dateOfBirth ? visitDate(payerPatient.dateOfBirth) : 'Not returned'}</dd></div></dl>
              {!!result.identity?.differences.length && <p className="visit-muted">Both names are retained with their source. {result.identity.status === 'reconciled' ? 'A verified relationship is on file.' : result.identity.status === 'warning' ? result.identity.message : 'The patient details need reconciliation before collection.'}</p>}
              {intakeId && <InsuranceCards key={intakeId} intakeId={intakeId} background inline savedOnly={readOnly} />}
            </section><ApplicableBenefits result={result} />
            <AppointmentNotes result={result} />
          </div></details>
          <details open={readOnly || undefined} id="visit-detail-calculation" className="visit-detail-section"><summary>Patient estimate</summary><div className="visit-detail-body"><section>
            <div className="planning-summary"><h3>{cash ? 'Cash price' : 'Estimated patient responsibility'} · {cash ? cashPrice || 'Billed once priced' : deferred || !amount ? 'Not yet calculated' : emgScenario && emg.total_max_cents != null ? range(emg.total_min_cents!, emg.total_max_cents) : result.planning_estimate ? money(result.planning_estimate.amount_cents) : patientPrice || 'Applicable advance amount shown above'}</h3>
            {amount && !deferred && !emgScenario && result.planning_estimate && <p>{result.planning_estimate.explanation}</p>}
            {amount && !deferred && !emgScenario && <CostPicture result={result} />}</div><div className="calculation-summary"><PieChart size={24} aria-hidden="true" /><div><h3>{amountLabel} · {amountText}</h3><p>{explanation}</p></div></div>
            {!!guidance?.assumptions.length && <section><h3>Assumptions used</h3><ul>{guidance.assumptions.map(item => <li key={item}>{item}</li>)}</ul></section>}
            {!!result.service_plan?.assumptions?.length && <section><h3>Service-plan assumptions</h3><ul>{result.service_plan.assumptions.map(item => <li key={item}>{item}</li>)}</ul></section>}
            {!cash && !deferred && !emgScenario && price?.complete && <dl className="collection-components">{(['copay_cents', 'deductible_cents', 'coinsurance_cents', 'oop_adjustment_cents'] as const).map(key => <div key={key}><dt>{({ copay_cents: 'Copay applied', deductible_cents: 'Deductible applied', coinsurance_cents: 'Coinsurance applied', oop_adjustment_cents: 'Out-of-pocket adjustment' })[key]}</dt><dd>{price.mode === 'services' ? money((price.lines || []).reduce((sum, line) => sum + (line.calculation?.[key] || 0), 0)) : 'Depends on final visit level'}</dd></div>)}</dl>}
            {calculationReasons.map(reason => <p key={reason}>{reason}</p>)}{emgScenario && <p>The service details below cover the consultation. The additional EMG scenario and combined total are shown above.</p>}<VisitServices result={result} />
            <p className="visit-muted">The arrival amount is an advance collection. Final patient responsibility follows the insurer’s processed claim.</p>
          </section></div></details>
          <details open={readOnly || undefined} id="visit-detail-sources" className="visit-detail-section" onToggle={event => { if (!readOnly && event.currentTarget.open) { setPanel('sources'); if (history === null && !historyBusy) void loadHistory() } }}><summary>Audit trail & history</summary><div className="visit-detail-body"><section className="visit-provenance"><dl><div><dt>Patient & appointment</dt><dd>eClinicalWorks<small>{result.ecw_retrieved_at && `${visitDate(result.ecw_retrieved_at)} · ${visitTime(result.ecw_retrieved_at)}`}</small></dd></div><div><dt>Payer benefits</dt><dd>{result.benefit_source || 'Stedi'}<small>{result.checked_at ? `${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}` : 'Not returned'}</small></dd></div><div><dt>Clinic contract</dt><dd>{result.network_evidence?.network_name || 'No exact product mapping returned'}</dd></div><div><dt>Cash schedule</dt><dd>{result.cash_schedule?.source || result.practice_cash_source?.filename || 'Not returned'}</dd></div></dl>
            {(result.insurance_fallback?.payer || result.insurance_fallback?.member_id) && <p>Missing {result.insurance_fallback.payer && result.insurance_fallback.member_id ? 'payer and member ID' : result.insurance_fallback.payer ? 'payer' : 'member ID'} came from the labeled payment preference in eCW General Notes. Payer verification is checked separately.</p>}
            {policySources.map(source => <p key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> · {source.section}</p>)}
            {patientPrice && !emgScenario && <p>Estimated patient share after the claim: {patientPrice}.</p>}
            {result.eligibility_reconciliation && <p>{result.eligibility_reconciliation.message}</p>}
            {!!result.identity?.differences.length && <p>{result.identity.message || 'Patient identity requires review.'}</p>}
            <p>Check ID: {result.check_id || 'Not saved'} · Response reference: {result.response_reference || 'Not returned'} · Estimate revision: {result.revision ?? 'Not saved'}</p>
            {!!result.audit_trail?.length && <section><h3>Changes to this estimate</h3><ol>{result.audit_trail.map((entry, index) => <li key={index}>{entry.changes.join(', ')} · {entry.by}{entry.checked_at && <small>{visitDate(entry.checked_at)} · {visitTime(entry.checked_at)}</small>}</li>)}</ol></section>}
            {!readOnly && <><h3>Previous eCW visits</h3>{historyBusy && <p role="status">Reading visit history…</p>}{historyError && <p>{historyError}<button onClick={() => void loadHistory()}>Retry</button></p>}{history?.map(visit => <article key={visit.id}><strong>{visitDate(visit.start)} · {visit.reason || 'Previous visit'}</strong><p>{visit.coded_services.join(', ')}</p></article>)}{history?.length === 0 && <p>No completed visits returned in the last year.</p>}</>}
            {!readOnly && panel === 'sources' && result.check_id && <FinancialHistory key={result.check_id} checkId={result.check_id} />}
            {!readOnly && intakeId && result.check_id && <button className="visit-delete-button" disabled={!!busy || recovering} onClick={() => void deleteCheck()}><Trash2 size={15} />{cleanupPending ? 'Retry file cleanup' : 'Delete saved check'}</button>}
          </section></div></details>
        </div>
        <footer className="visit-result-footer"><span><Clock3 size={14} aria-hidden="true" />{tooEarly ? `Benefits check opens ${visitDate(result.eligibility_timing!.check_from!)}` : result.checked_at ? `Benefits checked ${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}` : 'Benefits not yet returned'}</span>{!readOnly && <button disabled={!!busy || cleanupPending || recovering} onClick={() => void estimate(true)}><RefreshCw size={16} />Check again</button>}</footer>
      </> : !error && !busy && <div className="visit-empty"><CalendarDays size={28} /><h2>{selected ? 'Preparing this visit' : 'No upcoming visit returned'}</h2><p>{selected ? 'Reading the appointment and benefits.' : 'Clinicforce needs a scheduled eCW visit to match the date, provider and insurance.'}</p><button onClick={() => void load()}><RefreshCw size={16} />Refresh visits</button></div>}
    </div>
  </section>
}
