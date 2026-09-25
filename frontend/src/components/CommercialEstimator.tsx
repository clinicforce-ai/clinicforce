import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronDown, CircleHelp, Clock3, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { deleteStaffInsuranceCheck, getStaffIntake, getStaffSession, type StaffPatient, type StaffSession } from '../services/api/staff'
import { commercialAppointments, commercialHistory, commercialPrevisit, commercialSavedPrevisit, commercialSelectCoverageCandidate, commercialVisitReview, type VisitReview, type CommercialAppointment, type CommercialPastVisit, type PrevisitEstimate } from '../services/api/commercial-estimator'
import VisitServices, { ServicePlanner } from './VisitServices'
import PracticeCashSchedule from './PracticeCashSchedule'
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
      if (current === requestId.current) { setResult(updated); if (review.selected_ids) setPage('estimate'); setNotice('') }
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
  const patientPrice = benefits?.active === true && benefits.network_status === 'in_network' && (price?.complete && !price?.conditional) && price?.patient_min_cents != null && price.patient_max_cents != null ? range(price.patient_min_cents, price.patient_max_cents) : null
  const serviceOptions = price?.mode === 'options'
  const insurancePrice = patientPrice && price?.plan_min_cents != null && price.plan_max_cents != null ? range(price.plan_min_cents, price.plan_max_cents) : null
  const benefitLines = [...new Map((price?.lines || []).filter(line => line.benefits.benefit_summary || line.benefits.copay_cents != null || line.benefits.coinsurance_percent != null).map(line => [line.benefits.benefit_summary || `${line.benefits.copay_cents}:${line.benefits.coinsurance_percent}`, line])).values()]
  const requirementValue = (kind: 'referral' | 'authorization') => {
    const values = [...new Set(price?.lines?.map(line => line.benefits.requirements[kind].required) || [])]
    return values.length === 1 && values[0] === 'not_required' ? 'Not required' : values.includes('required') ? 'Required' : null
  }
  const policySources = [...new Map((price?.lines || []).filter(line => line.benefits.policy_evidence).map(line => [line.benefits.policy_evidence!.url, line.benefits.policy_evidence!])).values()]
  const cashPrice = price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null ? range(price.cash_min_cents, price.cash_max_cents) : null
  const planName = result?.plan_details?.stedi.names?.join(' · ') || result?.plan_details?.ecw.plan?.filter(value => value !== 'CI').join(' · ') || (result?.plan !== 'CI' ? result?.plan : null)
  const tabs = [{ id: 'plan', label: 'Plan visit' }, { id: 'estimate', label: 'Patient estimate' }, { id: 'coverage', label: 'Coverage & approvals' }, { id: 'sources', label: 'Sources & history' }] as const
  const policyStatus = benefits?.active === true ? 'Coverage active' : benefits?.active === false ? 'Coverage inactive' : result?.ecw_coverage_active ? 'Policy in eCW · verify benefits' : 'Coverage check in progress'

  return <section className="visit-estimator" aria-labelledby="visit-estimate-title">
    <header className="visit-page-header"><div><button className="visit-back" onClick={onBack}><ArrowLeft size={17} /> Back to patient</button><p className="visit-label">Patient pricing</p><h1 id="visit-estimate-title">Visit estimate</h1></div><span className="visit-practice">{session?.practice_name}</span></header>
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
          <section className="visit-answer" aria-labelledby="payment-heading"><div className="visit-section-heading"><div><p className="visit-label">{price?.mode === 'alternatives' ? 'Visit-level comparison' : 'For the selected care'}</p><h2 id="payment-heading">What will the patient pay?</h2></div><button className="visit-text-button" onClick={() => setPage('plan')}>Change services <ArrowRight size={16} /></button></div>
            <div className="payment-choice" aria-label="Payment method">
              <button type="button" aria-pressed={result.payment_mode !== 'cash'} disabled={!!busy} onClick={() => void saveReview({ payment_mode: 'insurance' })}><span>Use insurance</span><strong>{serviceOptions ? 'Choose services' : patientPrice || 'See plan benefits'}</strong><small>{planName || result.payer || 'Your insurance plan'}</small></button>
              <button type="button" aria-pressed={result.payment_mode === 'cash'} disabled={!!busy} onClick={() => void saveReview({ payment_mode: 'cash' })}><span>Pay cash</span><strong>{serviceOptions ? 'Choose services' : cashPrice || 'Choose cash package'}</strong><small>Clinic self-pay price</small></button>
            </div>
            {result.payment_mode === 'cash' ? <div className="payment-explanation"><h3>{cashPrice ? `Cash total · ${cashPrice}` : 'Choose the cash scope'}</h3><p>{result.cash_quote?.message || 'This practice has not uploaded a cash price for these services.'}</p>{!cashPrice && <button className="visit-text-button" onClick={() => setPage('plan')}>Choose package <ArrowRight size={16} /></button>}</div> : <div className="payment-explanation">
              {patientPrice ? <><div className="payment-split"><div><span>Patient estimate</span><strong>{patientPrice}</strong></div><div><span>Insurance estimate</span><strong>{insurancePrice}</strong></div></div>{price?.collection?.amount_cents != null ? <p>Collect now: <strong>{money(price.collection.amount_cents)}</strong>, after recorded payments.</p> : <p>Estimated responsibility before any payments already made to the clinic.</p>}</> : <><h3>{benefits?.active ? 'Coverage is active' : 'Coverage details'}</h3><p>{price?.lines?.some(line => line.allowed_cents == null) ? 'The practice fee schedule needs a match to this exact plan before an insured dollar estimate can be shown.' : 'The payer response does not resolve a complete dollar split for these services.'} Your returned benefits are shown here.</p></>}
              <div className="benefit-digest">{benefitLines.map(line => <div key={line.id}><strong>{line.benefits.benefit_label || 'Selected service benefit'}</strong><p>{line.benefits.benefit_summary || (line.benefits.copay_cents != null ? `${money(line.benefits.copay_cents)} copay` : `${line.benefits.coinsurance_percent}% patient coinsurance`)}</p></div>)}</div>
            </div>}
          </section>
          <VisitServices result={result} />
        </>}
        {page === 'coverage' && <section className="visit-coverage" aria-labelledby="visit-coverage-title"><div className="visit-section-heading"><div><p className="visit-label">{result.payer || 'Insurance'}</p><h2 id="visit-coverage-title">Coverage & benefits</h2></div>{result.checked_at && <span><Clock3 size={16} />{visitDate(result.checked_at)} · {visitTime(result.checked_at)}</span>}</div>
          <dl className="visit-audit-grid">
            {benefits?.active != null && <CoverageCheck label="Coverage" value={benefits.active ? 'Active' : 'Inactive'} confirmed={benefits.active} />}
            {planName && <CoverageCheck label="Plan / product" value={planName} confirmed />}
            {benefits?.network_status === 'in_network' && <CoverageCheck label="Provider network" value="In network" confirmed />}
            {benefits?.deductible_remaining_cents != null && <CoverageCheck label="Deductible remaining" value={shown(benefits.deductible_remaining_cents)} confirmed detail="Individual balance returned by the payer" />}
            {benefits?.oop_remaining_cents != null && <CoverageCheck label="Out-of-pocket remaining" value={shown(benefits.oop_remaining_cents)} confirmed detail="Individual balance returned by the payer" />}
            {requirementValue('referral') && <CoverageCheck label="Referral" value={requirementValue('referral')!} confirmed={requirementValue('referral') === 'Not required'} />}
            {requirementValue('authorization') && <CoverageCheck label="Prior authorization" value={requirementValue('authorization')!} confirmed={requirementValue('authorization') === 'Not required'} />}
            {result.referral_evidence?.map(row => <CoverageCheck key={row.reference} label="Referral in eCW" value="On file for this appointment" confirmed detail={row.authored_on ? visitDate(row.authored_on) : undefined} />)}
            {price?.lines?.filter(line => line.benefits.requirements.authorization.status === 'valid').map(line => <CoverageCheck key={line.id} label={`Authorization · CPT ${line.code}`} value="Approval on file" confirmed detail={line.benefits.requirements.authorization.reference} />)}
          </dl>
          <div className="benefit-digest">{benefitLines.map(line => <div key={line.id}><h3>{line.benefits.benefit_label || 'Selected service'}</h3><p>{line.benefits.benefit_summary || (line.benefits.copay_cents != null ? `${money(line.benefits.copay_cents)} copay` : `${line.benefits.coinsurance_percent}% patient coinsurance`)}</p></div>)}</div>
          <p className="visit-muted">Only returned or source-supported facts are shown. A missing item is not an approval.</p>
        </section>}
        {page === 'sources' && <>
          <section className="visit-provenance"><div className="visit-section-heading"><h2>Where the estimate comes from</h2></div><dl>
            <div><dt>Patient and appointment</dt><dd>eClinicalWorks<small>{result.ecw_retrieved_at && `${visitDate(result.ecw_retrieved_at)} · ${visitTime(result.ecw_retrieved_at)}`}</small></dd></div>
            <div><dt>Coverage and remaining balances</dt><dd>Stedi payer response<small>{result.checked_at && `${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}`}</small></dd></div>
            {policySources.map(source => <div key={source.url}><dt>Service-specific plan rules</dt><dd><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a><small>{source.section}</small></dd></div>)}
            <div><dt>Practice pricing</dt><dd>{session?.practice_name}<small>{result.network_evidence?.network_name ? `${result.network_evidence.network_name} fee schedule` : 'No fee schedule matched to the exact insurance product'}</small></dd></div>
            {result.network_evidence?.product_source && <div><dt>Plan network mapping</dt><dd><a href={result.network_evidence.product_source} target="_blank" rel="noreferrer">BCBSTX provider manual · FEP</a><small>FEP uses the Blue Choice PPO network.</small></dd></div>}
            {result.practice_cash_source && <div><dt>Cash schedule</dt><dd>{result.practice_cash_source.filename}<small>Effective {result.practice_cash_source.effective_from}</small></dd></div>}
            {result.cash_schedule && <div><dt>Cash schedule</dt><dd>{result.cash_schedule.source}<small>Effective {result.cash_schedule.effective_from}</small></dd></div>}
            <div><dt>Calculated</dt><dd>{result.calculated_at ? `${visitDate(result.calculated_at)} · ${visitTime(result.calculated_at)}` : 'Choose services'}</dd></div>
          </dl></section>
          {session?.role === 'admin' && !result.cash_schedule && <PracticeCashSchedule refresh={async () => { await saveReview({}) }} />}
          <details className="visit-history" onToggle={event => { if (event.currentTarget.open && history === null && !historyBusy && !historyError) void loadHistory() }}><summary>Previous visits used for procedure suggestions <ChevronDown size={18} /></summary>{historyBusy && <p role="status">Loading history…</p>}{historyError && <p className="visit-error">{historyError}<button onClick={() => void loadHistory()}>Retry</button></p>}{history?.map(visit => <article key={visit.id}><strong>{visitDate(visit.start)} · {visit.reason || 'Previous visit'}</strong><p>{visit.coded_services.join(', ')}</p></article>)}{history?.length === 0 && <p>No completed visits returned in the last year.</p>}</details>
        </>}
        <footer className="visit-result-footer"><span>Previsit estimate · Final charges follow the care provided.</span><div className="visit-result-actions">{intakeId && result.check_id && <button className="visit-text-button visit-delete-button" disabled={!!busy || recovering} onClick={() => void deleteCheck()}><Trash2 size={15} />{cleanupPending ? 'Retry file cleanup' : 'Delete check'}</button>}<button className="visit-text-button" disabled={!!busy || cleanupPending || recovering} onClick={() => void estimate()}><RefreshCw size={15} />Check again</button></div></footer>
      </> : !error && !busy && <div className="visit-empty"><CalendarDays size={28} /><h2>{selected ? 'Preparing this visit' : 'Schedule a visit in eCW'}</h2><p>{selected ? 'Reading the appointment and benefits.' : 'Once the appointment is scheduled, refresh to build its estimate.'}</p></div>}
    </div>
  </section>
}
