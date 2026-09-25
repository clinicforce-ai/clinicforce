import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronDown, CircleHelp, Clock3, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { deleteStaffInsuranceCheck, getStaffIntake, getStaffSession, type StaffPatient, type StaffSession } from '../services/api/staff'
import { commercialAppointments, commercialHistory, commercialPrevisit, commercialSavedPrevisit, commercialSelectCoverageCandidate, commercialVisitReview, type VisitReview, type CommercialAppointment, type CommercialPastVisit, type PrevisitEstimate } from '../services/api/commercial-estimator'
import VisitServices, { ServicePlanner } from './VisitServices'
import './CommercialEstimator.css'

const money = (cents: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(cents / 100)
const range = (min: number, max: number) => min === max ? money(min) : `${money(min)} – ${money(max)}`
const shown = (cents?: number | null) => cents == null ? 'Check payer benefit detail' : money(cents)
const dateLabel = (value: string, options: Intl.DateTimeFormatOptions) => {
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value)
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString(undefined, options)
}
const visitDate = (value: string) => dateLabel(value, { month: 'short', day: 'numeric', year: 'numeric' })
const visitTime = (value: string) => dateLabel(value, { hour: 'numeric', minute: '2-digit' })
const humanize = (value: string) => value.replaceAll('_', ' ').toLowerCase()
const networkLabel = (value: string) => ({ Y: 'In network', YES: 'In network', IN_NETWORK: 'In network', IN_AND_OUT_OF_NETWORK: 'In and out of network', W: 'In and out of network' })[value] || humanize(value)
const periodLabel = (value: string) => ({ '29': 'Remaining', '23': 'Calendar year', '27': 'Per visit', '7': 'Per episode' })[value] || humanize(value)
const requirement = (signal?: string) => signal === 'not_required' ? 'Not required' : signal === 'required' ? 'Required' : 'Check plan requirement below'

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
  const [page, setPage] = useState<'plan' | 'estimate' | 'coverage' | 'sources'>('plan')
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
      if (current === requestId.current) { setResult(updated); if (review.selected_ids) setPage('estimate'); setNotice(updated.eligibility_error ? 'Planned services saved. Clinic rates updated; the insurance split still needs usable benefits.' : `${review.verification ? 'Verification saved.' : 'Estimate updated.'} Recalculated from the saved payer response.`) }
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

  const chooseVisit = (id: string) => { if (selectedVisit !== id) { setSelectedVisit(id); setPage('plan'); setResult(null); setError(''); setDeleteError(''); setCleanupPending(false); setNotice('') } }
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
    if (!values.length) return field === 'required' ? requirement(benefits?.[`${kind}_signal`]) : 'Verify eCW evidence below'
    if (values.length > 1) return 'Varies by service'
    return field === 'required' ? requirement(values[0]) : values[0] === 'valid' ? 'Received and valid' : values[0] === 'not_verified' ? 'Verify eCW evidence below' : humanize(values[0])
  }
  const cashPrice = price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null ? range(price.cash_min_cents, price.cash_max_cents) : null
  const planName = result?.plan_details?.stedi.names?.join(' · ') || result?.plan_details?.ecw.plan?.filter(value => value !== 'CI').join(' · ') || (result?.plan !== 'CI' ? result?.plan : null)
  const tabs = [{ id: 'plan', label: 'Plan visit' }, { id: 'estimate', label: 'Patient estimate' }, { id: 'coverage', label: 'Coverage & approvals' }, { id: 'sources', label: 'Sources & history' }] as const
  const policyStatus = benefits?.active === true ? 'Coverage active' : benefits?.active === false ? 'Coverage inactive' : result?.ecw_coverage_active ? 'Policy in eCW · verify benefits' : 'Coverage check in progress'

  return <section className="visit-estimator" aria-labelledby="visit-estimate-title">
    <header className="visit-page-header"><div><button className="visit-back" onClick={onBack}><ArrowLeft size={17} /> Back to patient</button><p className="visit-label">Insurance estimator V2</p><h1 id="visit-estimate-title">Visit estimate</h1></div><span className="visit-practice">{session?.practice_name}<small>eClinicalWorks + Stedi</small></span></header>
    <div className="visit-patient-bar"><div><strong>{patient ? `${patient.given_name} ${patient.family_name}` : 'Patient record'}</strong>{patient?.birth_date && <span>DOB {visitDate(patient.birth_date)}</span>}</div><span className={benefits?.active === false ? 'is-negative' : ''}><ShieldCheck size={18} aria-hidden="true" />{policyStatus}</span></div>
    <div className="visit-appointment-strip"><CalendarDays size={22} aria-hidden="true" /><label>Scheduled visit<select value={selectedVisit} disabled={!!busy || !appointments.length} onChange={event => chooseVisit(event.target.value)}>{appointments.length ? appointments.map(visit => <option key={visit.id} value={visit.id}>{visitDate(visit.start)} · {visitTime(visit.start)} · {visit.reason || 'Scheduled care'}</option>) : <option value="">{loaded ? 'Schedule an appointment in eCW, then refresh' : 'Loading appointments…'}</option>}</select></label><button className="visit-icon-button" aria-label="Refresh visits" disabled={!!busy} onClick={() => void load()}><RefreshCw size={18} className={busy === 'visits' ? 'visit-spin' : ''} /></button></div>
    {selected && <div className="visit-appointment-context"><p>{result?.reason || selected.reason || 'Review the scheduled visit in eCW.'}</p>{result?.appointment_context && <span>{[result.appointment_context.rendering_provider?.name, result.appointment_context.location].filter(Boolean).join(' · ')}</span>}</div>}
    <nav className="visit-tabs" aria-label="Estimate sections">{tabs.map(tab => <button key={tab.id} aria-current={page === tab.id ? 'page' : undefined} onClick={() => setPage(tab.id)}>{tab.label}</button>)}</nav>
    <div className="visit-content" aria-busy={!!busy || recovering}>
      {notice && <p className="visit-notice" role="status">{notice}</p>}{deleteError && <p className="visit-error" role="alert">{deleteError}</p>}
      {error && <div className="visit-error" role="alert"><CircleHelp size={20} aria-hidden="true" /><div><strong>Let’s complete this check</strong><p>{error}</p><button className="visit-text-button" disabled={!!busy} onClick={() => void (selected ? estimate() : load())}>Try again <ArrowRight size={15} /></button></div></div>}
      {busy === 'estimate' || busy === 'visits' ? <div className="visit-empty" role="status"><RefreshCw className="visit-spin" size={26} aria-hidden="true" /><h2>{busy === 'visits' ? 'Finding the scheduled visits' : 'Building the visit picture'}</h2><p>Reading eCW appointment details, saved verification, payer benefits and clinic prices.</p></div> : result ? <>
        {!result.check_id && !!result.issues.length && <div className="visit-notice"><strong>Next step for this visit</strong><ul>{result.issues.map(item => <li key={item}>{item}</li>)}</ul></div>}
        {failed && <section className="visit-recovery"><h2>{recovering ? 'Finding coverage with Stedi…' : failure?.title || 'Complete the coverage check'}</h2><p>{result.recovery?.message || failure?.action || result.issues.join(' ')}</p>{result.recovery?.candidates?.map(candidate => <article key={candidate.id}><div><strong>{candidate.payer}</strong><p>{candidate.plans.join(' · ') || 'Compare this policy with the eCW record'}</p><small>{candidate.address_matches ? 'Name, date of birth and address match eCW' : 'Name and date of birth match; compare the current card'}</small></div><button className="visit-primary" disabled={!!busy || recovering} onClick={() => void selectCoverage(candidate.id)}>Use this policy</button></article>)}<p className="visit-muted">The visit plan and available clinic prices remain usable while coverage is resolved.</p></section>}
        <div hidden={page !== 'plan'}><ServicePlanner key={`${result.check_id}:${result.revision}`} result={result} busy={!!busy || recovering} save={saveReview} /></div>
        {page === 'estimate' && <>
          <section className="visit-answer" aria-labelledby="payment-heading"><div className="visit-section-heading"><div><p className="visit-label">{serviceOptions ? 'Choose services to build a total' : price?.mode === 'alternatives' ? 'Compare possible visit levels' : price?.conditional ? 'Conditional planning estimate' : 'For the anticipated visit'}</p><h2 id="payment-heading">What will the patient pay?</h2></div><button className="visit-text-button" onClick={() => setPage('plan')}>Change services <ArrowRight size={16} /></button></div>
            <div className="visit-price-comparison"><article><span>Without insurance</span><h3>Cash price</h3><strong className="visit-price-number">{serviceOptions ? 'Select services' : cashPrice || 'Add cash schedule'}</strong><p>{cashPrice ? 'Clinic cash price for the planned care.' : 'The clinic’s self-pay price is separate from its insurance contract. Add the cash rate to the clinic schedule.'}</p></article><article className="visit-insured"><span>Using insurance</span><h3>Patient estimate</h3><strong className="visit-price-number">{serviceOptions ? 'Select services' : patientPrice || 'Complete benefit review'}</strong><p>{price?.conditional ? 'For planning — resolve the conditions before collecting.' : 'Deductible, copay and coinsurance for the planned care.'}</p><div className="visit-insurance-share">Insurance pays <strong>{serviceOptions ? 'After selection' : insurancePrice || 'Complete benefit review'}</strong></div></article></div>
            <div className="visit-collection"><div><span>Collect now</span><strong>{price?.collection?.amount_cents != null ? money(price.collection.amount_cents) : 'Hold collection'}</strong></div><p>{price?.collection?.reason || 'Select planned services, verify benefits and confirm payments already received.'}</p>{price?.collection?.payment_reference && <small>Payment ledger: {price.collection.payment_reference}</small>}</div>
            {!!conditions.length && <details className="visit-line-details"><summary>What could change this estimate?</summary><ul>{conditions.map(item => <li key={item}>{item}</li>)}</ul></details>}
          </section>
          <VisitServices result={result} busy={!!busy || recovering} save={saveReview} />
        </>}
        {page === 'coverage' && <>
          <section className="visit-coverage" aria-labelledby="visit-coverage-title"><div className="visit-section-heading"><div><p className="visit-label">{result.payer || 'Match the insurer in eCW'}</p><h2 id="visit-coverage-title">Coverage & readiness</h2></div>{result.checked_at && <span><Clock3 size={16} aria-hidden="true" />{visitDate(result.checked_at)} · {visitTime(result.checked_at)}</span>}</div>
            <dl className="visit-audit-grid">
              <CoverageCheck label="Coverage active" value={benefits?.active === true ? 'Active' : benefits?.active === false ? 'Inactive — review cash option' : 'Complete Stedi policy check'} confirmed={benefits?.active === true} detail="Stedi payer response" />
              <CoverageCheck label="Exact plan / product" value={planName || 'Verify product on the eCW policy'} confirmed={!!result.plan_details?.stedi.names?.length} detail={result.plan_details?.stedi.names?.length ? 'Stedi payer response' : 'eCW policy'} />
              <CoverageCheck label="Provider network" value={benefits?.network_status === 'in_network' ? 'In network' : benefits?.network_status === 'out_of_network' ? 'Out of network — review benefits' : 'Verify clinic participation'} confirmed={benefits?.network_status === 'in_network'} detail="Clinic contract for this plan / location" />
              <CoverageCheck label="Already verified in eCW?" value={result.ecw_saved_eligibility?.status === 'verified_active' ? 'Yes · active coverage' : result.ecw_saved_eligibility?.status === 'verified_inactive' ? 'Saved check reports inactive' : 'Use current Stedi check'} confirmed={result.ecw_saved_eligibility?.status === 'verified_active'} detail={result.ecw_saved_eligibility?.message || 'An insurance policy on file is different from verified eligibility.'} />
              <CoverageCheck label="Deductible remaining" value={shown(benefits?.deductible_remaining_cents)} confirmed={benefits?.deductible_remaining_cents != null} detail="Patient’s remaining individual deductible" />
              <CoverageCheck label="Out-of-pocket remaining" value={shown(benefits?.oop_remaining_cents)} confirmed={benefits?.oop_remaining_cents != null} detail="Remaining individual limit for covered care" />
              <CoverageCheck label="Referral required?" value={requirementValue('referral', 'required')} confirmed={!!price?.lines?.length && price.lines.every(line => ['required', 'not_required'].includes(line.benefits.requirements.referral.required))} />
              <CoverageCheck label="Referral received / valid?" value={requirementValue('referral', 'status')} confirmed={!!price?.lines?.length && price.lines.every(line => line.benefits.requirements.referral.cleared)} />
              <CoverageCheck label="Prior authorization required?" value={requirementValue('authorization', 'required')} confirmed={!!price?.lines?.length && price.lines.every(line => ['required', 'not_required'].includes(line.benefits.requirements.authorization.required))} />
              <CoverageCheck label="Prior authorization obtained?" value={requirementValue('authorization', 'status')} confirmed={!!price?.lines?.length && price.lines.every(line => line.benefits.requirements.authorization.cleared)} />
            </dl>
          </section>
          <VisitServices result={result} busy={!!busy || recovering} save={saveReview} approvals />
        </>}
        {page === 'sources' && <>
          <section className="visit-provenance"><div className="visit-section-heading"><div><p className="visit-label">The evidence behind this estimate</p><h2>Source & timestamp</h2></div></div><dl>
            <div><dt>Patient, appointment, reason and policy</dt><dd>eClinicalWorks · {result.ecw_retrieved_at ? `${visitDate(result.ecw_retrieved_at)}, ${visitTime(result.ecw_retrieved_at)}` : 'Reload the appointment to refresh the source'}</dd></div>
            <div><dt>Saved eCW eligibility</dt><dd>{result.ecw_saved_eligibility?.message || 'Continue with the current Stedi verification.'}{result.ecw_saved_eligibility?.checked_at && <small>{result.ecw_saved_eligibility.reference} · {visitDate(result.ecw_saved_eligibility.checked_at)}, {visitTime(result.ecw_saved_eligibility.checked_at)}</small>}</dd></div>
            <div><dt>Coverage and benefits</dt><dd>Stedi · {result.checked_at ? `${visitDate(result.checked_at)}, ${visitTime(result.checked_at)}` : failure?.attempted_at ? `Attempted ${visitDate(failure.attempted_at)}, ${visitTime(failure.attempted_at)}` : 'Complete the payer check'}{result.response_reference && <small>Reference {result.response_reference}</small>}</dd></div>
            <div><dt>Network participation</dt><dd>{result.network_evidence?.network_name || 'Verify clinic participation'}<small>{result.network_evidence?.evidence_reference}{result.network_evidence?.effective_from && ` · Effective ${result.network_evidence.effective_from}`}</small></dd></div>
            <div><dt>Calculation and collection recommendation</dt><dd>ClinicForce · {result.calculated_at ? `${visitDate(result.calculated_at)}, ${visitTime(result.calculated_at)}` : 'Calculate selected services'}<small>Uses clinic rates and applicable payer terms. No payment is charged.</small></dd></div>
            {[...new Map(price?.lines?.map(line => [line.rate_version || line.rate_source, line]) || []).values()].map(line => <div key={line.rate_version || line.rate_source}><dt>Contracted and cash prices</dt><dd>{line.rate_source}<small>{line.rate_version && `Version ${line.rate_version}`}{line.rate_effective_from && ` · Effective ${line.rate_effective_from}`}</small></dd></div>)}
          </dl></section>
          {!!reviewItems.length && <details className="visit-line-details"><summary>Items to resolve ({reviewItems.length})</summary><ul>{reviewItems.map(item => <li key={item}>{item}</li>)}</ul></details>}
          <details className="visit-line-details"><summary>Benefits returned by the payer <ChevronDown size={18} /></summary><p className="visit-muted">Terms retain their service scope; a returned term does not necessarily apply to every procedure.</p><ul className="visit-payer-terms">{options.map((row, index) => <li key={index}><div><strong>{humanize(row.kind)}</strong><span>{row.quantity?.value != null ? `${row.quantity.value} ${humanize(row.quantity.qualifier || 'visits')}` : row.amount_cents == null ? row.percent == null ? 'Review payer note' : `${row.percent}%` : money(row.amount_cents)}</span></div><small>{row.procedure_code ? `CPT ${row.procedure_code}` : `Service category ${row.service_type_code || 'not specified'}`} · {networkLabel(row.network)}{row.coverage_level ? ` · ${humanize(row.coverage_level)}` : ''}{row.time_period ? ` · ${periodLabel(row.time_period)}` : ''}</small>{row.payer_note && <p>{row.payer_note}</p>}</li>)}</ul></details>
          <details className="visit-history" onToggle={event => { if (event.currentTarget.open && history === null && !historyBusy && !historyError) void loadHistory() }}><summary>Previous visits in eCW <ChevronDown size={18} /></summary>{historyBusy && <p role="status">Loading history…</p>}{historyError && <p className="visit-error">{historyError}<button onClick={() => void loadHistory()}>Retry history</button></p>}{history?.map(visit => <article key={visit.id}><strong>{visitDate(visit.start)} · {visit.reason || 'Previous visit'}</strong><p>{visit.coded_services.length ? `CPT ${visit.coded_services.join(', ')}` : 'Open the eCW visit to review performed procedures'}</p><p>{visit.saved_estimate?.patient_cents != null ? `Saved patient estimate: ${money(visit.saved_estimate.patient_cents)}` : 'No saved price for this visit'}</p></article>)}{history?.length === 0 && <p>No completed visits were returned for the last year.</p>}</details>
        </>}
        <footer className="visit-result-footer"><span>Previsit estimate · Final charges follow the care provided.</span><div className="visit-result-actions">{intakeId && result.check_id && <button className="visit-text-button visit-delete-button" disabled={!!busy || recovering} onClick={() => void deleteCheck()}><Trash2 size={15} />{cleanupPending ? 'Retry file cleanup' : 'Delete check'}</button>}<button className="visit-text-button" disabled={!!busy || cleanupPending || recovering} onClick={() => void estimate()}><RefreshCw size={15} />Check again</button></div></footer>
      </> : !error && !busy && <div className="visit-empty"><CalendarDays size={28} /><h2>{selected ? 'Preparing this visit' : 'Schedule a visit in eCW'}</h2><p>{selected ? 'Reading the appointment and benefits.' : 'Once the appointment is scheduled, refresh to build its estimate.'}</p></div>}
    </div>
  </section>
}
