import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronDown, CircleHelp, Clock3, RefreshCw, ShieldCheck, Trash2, BadgeDollarSign, Network, Wallet } from 'lucide-react'
import { deleteStaffInsuranceCheck, getStaffIntake, getStaffSession, type StaffPatient, type StaffSession } from '../services/api/staff'
import { commercialFinancials, type VisitFinancials, commercialAppointments, commercialHistory, commercialPrevisit, commercialSavedPrevisit, commercialSelectCoverageCandidate, commercialVisitReview, type VisitReview, type CommercialAppointment, type CommercialPastVisit, type PrevisitEstimate } from '../services/api/commercial-estimator'
import VisitServices, { ServicePlanner } from './VisitServices'
import InsuranceCards from './InsuranceCards'
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


function BenefitTable({ rows }: { rows: NonNullable<NonNullable<PrevisitEstimate['benefits']>['reported_options']> }) {
  return <div className="clinical-table-wrap"><table className="clinical-service-table"><thead><tr><th>Benefit / service</th><th>Network</th><th>Amount / limit</th><th>Applies to</th></tr></thead><tbody>{rows.map((row, index) => <tr key={row.id || index}><th scope="row">{row.kind.replaceAll('_', ' ')}<small>{row.payer_note || `Service category ${row.service_type_code}`}</small></th><td>{row.network.replaceAll('_', ' ').toLowerCase()}</td><td>{row.amount_cents != null ? money(row.amount_cents) : row.percent != null ? `${row.percent}%` : row.quantity?.value || 'Not specified'}</td><td>{[row.coverage_level, row.time_period].filter(Boolean).join(' · ').replaceAll('_', ' ').toLowerCase()}</td></tr>)}</tbody></table></div>
}

function FrontDeskCoverage({ result }: { result: PrevisitEstimate }) {
  const benefits = result.benefits
  const lines = result.price_range?.lines || []
  const plan = result.plan_details?.stedi.names?.join(' · ') || result.plan_details?.ecw.plan?.filter(value => value !== 'CI').join(' · ') || (result.plan !== 'CI' ? result.plan : '')
  const requirement = (kind: 'referral' | 'authorization') => {
    const values = lines.map(line => line.benefits.requirements[kind])
    const required = values.some(value => value.required === 'required') ? 'Yes' : values.length && values.every(value => value.required === 'not_required') ? 'No' : 'Not stated by payer'
    const status = required === 'No' ? 'Not needed' : values.length && values.every(value => value.cleared) ? 'Valid for planned care' : values.some(value => value.status === 'denied') ? 'Denied' : values.some(value => value.status === 'expired') ? 'Expired' : values.some(value => value.status === 'pending') ? 'Pending' : kind === 'referral' && result.referral_evidence?.length ? 'On file · validity not established' : 'No matching record returned'
    return { required, status, valid: status === 'Not needed' || status === 'Valid for planned care' }
  }
  const referral = requirement('referral'), authorization = requirement('authorization')
  const saved = result.ecw_saved_eligibility
  const checked = result.checked_at || result.eligibility_failure?.attempted_at
  const time = checked ? `${visitDate(checked)} · ${visitTime(checked)}` : 'Not checked'
  const record = result.insurance_details?.record_status
  return <section className="front-desk-coverage" aria-labelledby="front-desk-heading"><div className="visit-section-heading"><div><p className="visit-label">At a glance</p><h2 id="front-desk-heading">Coverage & approvals</h2></div><ShieldCheck size={24} aria-hidden="true" /></div>
    <div className="coverage-plan"><Network size={22} aria-hidden="true" /><div><small>Exact plan / product</small><strong>{plan || 'Plan name not returned'}</strong><span>{result.payer}</span></div></div>
    <dl className="visit-audit-grid">
      <CoverageCheck label={`Eligibility for ${visitDate(result.service_date)}`} value={benefits?.service_date_matches === false ? 'Appointment date not verified' : benefits?.active === true ? 'Eligible' : benefits?.active === false ? 'Ineligible' : result.eligibility_failure ? 'Could not verify' : 'Not verified'} confirmed={benefits?.active === true && benefits?.service_date_matches !== false} detail={benefits?.service_date_matches === false ? `Payer returned ${visitDate(benefits.returned_service_dates?.start || '')} · requested ${visitDate(result.service_date)}` : `Stedi · ${time}`} />
      <CoverageCheck label="Network status" value={benefits?.network_status === 'in_network' ? 'In network' : benefits?.network_status === 'out_of_network' ? 'Out of network' : 'Not established'} confirmed={benefits?.network_status === 'in_network'} detail={result.network_evidence?.network_name || 'For this provider and plan'} />
      <CoverageCheck label="Saved eCW insurance status" value={saved?.status === 'verified_active' ? 'Verified · active' : saved?.status === 'verified_inactive' ? 'Verified · inactive' : record?.status || 'Not returned'} confirmed={saved?.status === 'verified_active'} detail={saved?.checked_at ? `${visitDate(saved.checked_at)} · ${visitTime(saved.checked_at)}` : record?.message || 'A saved policy does not verify this appointment'} />
      <CoverageCheck label="Deductible remaining" value={benefits?.deductible_remaining_cents != null ? money(benefits.deductible_remaining_cents) : 'Balance not returned'} confirmed={benefits?.deductible_remaining_cents != null} detail="Amount left before deductible-based sharing" />
      <CoverageCheck label="Out-of-pocket remaining" value={benefits?.oop_remaining_cents != null ? money(benefits.oop_remaining_cents) : 'Balance not returned'} confirmed={benefits?.oop_remaining_cents != null} detail="Remaining limit for applicable covered care" />
      <CoverageCheck label="Referral" value={`${referral.required === 'Yes' ? 'Required' : referral.required === 'No' ? 'Not required' : 'Requirement not returned'} · ${referral.status}`} confirmed={referral.valid} />
      <CoverageCheck label="Prior authorization" value={`${authorization.required === 'Yes' ? 'Required' : authorization.required === 'No' ? 'Not required' : 'Requirement not returned'} · ${authorization.status}`} confirmed={authorization.valid} />
    </dl>
    {!!benefits?.reported_options?.some(row => row.kind === 'copay') && <section className="payer-copays"><h3>Payer-reported copays</h3><p className="visit-muted">These are returned benefit amounts. The provider’s network, service and any tier or visit conditions must match before collection.</p><BenefitTable rows={benefits.reported_options.filter(row => row.kind === 'copay')} /></section>}
    {!!benefits?.reported_options?.length && <details className="visit-line-details"><summary>All in-network and out-of-network benefits</summary><BenefitTable rows={benefits.reported_options} /></details>}
    <div className="coverage-footnote"><Clock3 size={15} aria-hidden="true" /><span>Benefits: Stedi · {time}. Appointment and saved checks: eCW.</span></div>
  </section>
}

function FinancialHistory({ checkId }: { checkId: string }) {
  const [data, setData] = useState<VisitFinancials | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const load = useCallback(async () => { setBusy(true); setError(''); try { setData(await commercialFinancials(checkId)) } catch { setError('The financial connection could not be read. Retry the check.') } finally { setBusy(false) } }, [checkId])
  useEffect(() => { let active = true; commercialFinancials(checkId).then(value => { if (active) setData(value) }).catch(() => { if (active) setError('The financial connection could not be read. Retry the check.') }).finally(() => { if (active) setBusy(false) }); return () => { active = false } }, [checkId])
  return <section className="financial-history"><div className="visit-section-heading"><div><p className="visit-label">Actual payer records</p><h2><BadgeDollarSign size={23} aria-hidden="true" /> Previous insurance payments</h2></div><button className="visit-text-button" disabled={busy} onClick={() => void load()}><RefreshCw size={16} /> Refresh</button></div>
    {busy ? <p role="status">Reading Stedi remittances…</p> : <p role={error ? 'alert' : undefined}>{error || data?.message}</p>}
    {!!data?.lines.length && <div className="financial-records">{data.lines.map(row => <article key={`${row.transaction_id}:${row.claim_reference}:${row.line_index}`}><div><strong>CPT {row.code}{row.modifiers.length ? ` · ${row.modifiers.join(', ')}` : ''}</strong><small>{row.service_date ? visitDate(row.service_date.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')) : 'Service date not returned'} · {row.payer} · {({ '1': 'Primary insurance processed', '2': 'Secondary insurance processed', '3': 'Additional insurance processed', '4': 'Denied', '22': 'Payment reversed' } as Record<string, string>)[row.claim_status || ''] || 'Payer response'}</small></div><dl><div><dt>Previous allowance</dt><dd>{shown(row.allowed_cents)}</dd></div><div><dt>Insurer paid</dt><dd>{shown(row.insurer_paid_cents)}</dd></div><div><dt>Patient share assigned</dt><dd>{shown(row.patient_responsibility_cents)}</dd></div></dl><small>{row.source} · received {row.received_at ? visitDate(row.received_at) : 'date not supplied'}</small></article>)}</div>}
    {data && <small>{data.source} · Checked {visitDate(data.checked_at)} {visitTime(data.checked_at)}{data.partial ? ' · Partial history' : ''}. These records do not show payments collected from the patient.</small>}
  </section>
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
  const [paymentsReceived, setPaymentsReceived] = useState('')
  const [paymentReference, setPaymentReference] = useState('')
  const [page, setPage] = useState<'plan' | 'estimate' | 'coverage' | 'sources'>('coverage')
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

  const chooseVisit = (id: string) => { if (selectedVisit !== id) { setPaymentsReceived(''); setPaymentReference(''); setSelectedVisit(id); setPage('coverage'); setResult(null); setError(''); setDeleteError(''); setCleanupPending(false); setNotice('') } }
  const selected = appointments.find(visit => visit.id === selectedVisit)
  const benefits = result?.benefits
  const price = result?.price_range
  const failed = !!result?.eligibility_error
  const failure = result?.eligibility_failure
  const patientPrice = benefits?.active === true && benefits.network_status === 'in_network' && price?.complete && price?.patient_min_cents != null && price.patient_max_cents != null ? range(price.patient_min_cents, price.patient_max_cents) : null
  const serviceOptions = price?.mode === 'options'
  const insurancePrice = patientPrice && price?.plan_min_cents != null && price.plan_max_cents != null ? range(price.plan_min_cents, price.plan_max_cents) : null
  const benefitLines = [...new Map((price?.lines || []).filter(line => line.benefits.benefit_summary || line.benefits.copay_cents != null || line.benefits.coinsurance_percent != null).map(line => [line.benefits.benefit_summary || `${line.benefits.copay_cents}:${line.benefits.coinsurance_percent}`, line])).values()]
  const policySources = [...new Map((price?.lines || []).filter(line => line.benefits.policy_evidence).map(line => [line.benefits.policy_evidence!.url, line.benefits.policy_evidence!])).values()]
  const cashPrice = price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null ? range(price.cash_min_cents, price.cash_max_cents) : null
  const planName = result?.plan_details?.stedi.names?.join(' · ') || result?.plan_details?.ecw.plan?.filter(value => value !== 'CI').join(' · ') || (result?.plan !== 'CI' ? result?.plan : null)
  const payerSubscriber = result?.insurance_details?.payer_subscriber
  const payerPatient = result?.insurance_details?.payer_patient
  const policyDates = result?.insurance_details?.payer_coverage_period?.start ? result.insurance_details.payer_coverage_period : result?.insurance_details?.coverage_period
  const tabs = [{ id: 'coverage', label: 'Patient & coverage' }, { id: 'plan', label: 'Plan visit' }, { id: 'estimate', label: 'Collect at arrival' }, { id: 'sources', label: 'Sources & history' }] as const
  const policyStatus = benefits?.service_date_matches === false ? 'Coverage returned · verify appointment date' : benefits?.active === true ? 'Coverage active' : benefits?.active === false ? 'Coverage inactive' : result?.ecw_coverage_active ? 'Policy in eCW · verify benefits' : 'Coverage check in progress'

  return <section className="visit-estimator" aria-labelledby="visit-estimate-title">
    <header className="visit-page-header"><div><button className="visit-back" onClick={onBack}><ArrowLeft size={17} /> Back to patient</button><p className="visit-label">Front desk collection</p><h1 id="visit-estimate-title">Visit estimate</h1></div><span className="visit-practice">{session?.practice_name}</span></header>
    <div className="visit-patient-bar"><div><strong>{patient ? `${patient.given_name} ${patient.family_name}` : 'Patient record'}</strong>{patient?.birth_date && <span>DOB {visitDate(patient.birth_date)}</span>}</div><span className={benefits?.active === false ? 'is-negative' : ''}><ShieldCheck size={18} aria-hidden="true" />{policyStatus}</span></div>
    <div className="visit-appointment-strip"><CalendarDays size={22} aria-hidden="true" /><label>Scheduled visit<select value={selectedVisit} disabled={!!busy || !appointments.length} onChange={event => chooseVisit(event.target.value)}>{appointments.length ? appointments.map(visit => <option key={visit.id} value={visit.id}>{visitDate(visit.start)} · {visitTime(visit.start)} · {visit.reason || 'Scheduled care'}</option>) : <option value="">{loaded ? 'Schedule an appointment in eCW, then refresh' : 'Loading appointments…'}</option>}</select></label><button className="visit-icon-button" aria-label="Refresh visits" disabled={!!busy} onClick={() => void load()}><RefreshCw size={18} className={busy === 'visits' ? 'visit-spin' : ''} /></button></div>
    {selected && <div className="visit-appointment-context"><p>{result?.reason || selected.reason || 'Review the scheduled visit in eCW.'}</p>{result?.appointment_context && <span>{[result.appointment_context.rendering_provider?.name, result.appointment_context.location].filter(Boolean).join(' · ')}</span>}</div>}
    <nav className="visit-tabs" aria-label="Estimate sections">{tabs.map(tab => <button key={tab.id} aria-current={page === tab.id ? 'page' : undefined} onClick={() => setPage(tab.id)}>{tab.label}</button>)}</nav>
    <div className="visit-content" aria-busy={!!busy || recovering}>
      {notice && <p className="visit-notice" role="status">{notice}</p>}{deleteError && <p className="visit-error" role="alert">{deleteError}</p>}
      {error && <div className="visit-error" role="alert"><CircleHelp size={20} aria-hidden="true" /><div><strong>Let’s complete this check</strong><p>{error}</p><button className="visit-text-button" disabled={!!busy} onClick={() => void (selected ? estimate() : load())}>Try again <ArrowRight size={15} /></button></div></div>}
      {busy === 'estimate' || busy === 'visits' ? <div className="visit-empty" role="status"><RefreshCw className="visit-spin" size={26} aria-hidden="true" /><h2>{busy === 'visits' ? 'Finding the scheduled visits' : 'Building the visit picture'}</h2><p>Reading eCW appointment details, saved verification, payer benefits and clinic prices.</p></div> : result ? <>
        {(!result.checked_at && !failed) && !!result.issues.length && <div className="visit-notice"><strong>Next step for this visit</strong><ul>{result.issues.map(item => <li key={item}>{item}</li>)}</ul></div>}
        {failed && <section className="visit-recovery"><h2>{recovering ? 'Finding coverage with Stedi…' : failure?.title || 'Complete the coverage check'}</h2><p>{failure?.action || result.issues.join(' ')}</p>{failure?.codes?.length ? <small>Payer rejection code: {failure.codes.join(', ')} · {failure.attempted_at ? visitDate(failure.attempted_at) + ' ' + visitTime(failure.attempted_at) : 'Saved check'}</small> : null}{result.recovery?.message && <p>{result.recovery.message}</p>}{result.recovery?.candidates?.map(candidate => <article key={candidate.id}><div><strong>{candidate.payer}</strong><p>{candidate.plans.join(' · ') || 'Compare this policy with the eCW record'}</p><small>{candidate.address_matches ? 'Name, date of birth and address match eCW' : 'Name and date of birth match; compare the current card'}</small></div><button className="visit-primary" disabled={!!busy || recovering} onClick={() => void selectCoverage(candidate.id)}>Use this policy</button></article>)}<p className="visit-muted">The visit plan and available clinic prices remain usable while coverage is resolved.</p></section>}
        {(page === 'coverage' || page === 'estimate') && <section className="visit-provenance"><h2>Patient & insurance details</h2><dl>
          <div><dt>Member / subscriber ID</dt><dd>{result.insurance_details?.member_id || 'Not returned'}</dd></div>
          <div><dt>Policyholder</dt><dd>{payerSubscriber?.lastName ? [payerSubscriber.firstName, payerSubscriber.lastName].filter(Boolean).join(' ') : [result.insurance_details?.subscriber_first, result.insurance_details?.subscriber_last].filter(Boolean).join(' ') || 'Not returned'}<small>{payerSubscriber?.lastName ? 'Stedi payer response' : 'eCW record'}</small><small>eCW: {[result.insurance_details?.subscriber_first, result.insurance_details?.subscriber_last].filter(Boolean).join(' ') || 'Not returned'}</small></dd></div>
          <div><dt>Relationship to policyholder</dt><dd>{result.insurance_details?.payer_relationship?.toLowerCase() || ({ '18': 'Self', '01': 'Spouse', '19': 'Child', G8: 'Other' } as Record<string, string>)[result.insurance_details?.relationship || ''] || 'Not returned'}</dd></div>
          {payerPatient?.lastName && <div><dt>Patient returned by payer</dt><dd>{[payerPatient.firstName, payerPatient.lastName].filter(Boolean).join(' ')}<small>{payerPatient.dateOfBirth ? visitDate(payerPatient.dateOfBirth) : ''}</small></dd></div>}
          <div><dt>Group number</dt><dd>{result.insurance_details?.group_number || result.plan_details?.stedi.group_number || 'Not returned'}</dd></div>
          <div><dt>Contact</dt><dd>{result.patient_details?.phone || 'Phone not returned'}<small>{result.patient_details?.email}</small></dd></div>
          <div><dt>Address</dt><dd>{Object.values(result.patient_details?.address || {}).filter(Boolean).join(', ') || 'Not returned'}</dd></div>
          <div><dt>Provider</dt><dd>{result.appointment_context?.rendering_provider?.name || 'Not returned'}<small>{result.appointment_context?.location}</small></dd></div>
          <div><dt>Coverage period</dt><dd>{policyDates?.start || 'Start not returned'} – {policyDates?.end || 'End not returned'}</dd></div>
        </dl>{benefits?.review_reasons?.filter(reason => reason.includes('identity') || reason.includes('Payer evidence covers service date')).map(reason => <p className="visit-notice" key={reason}>{reason}</p>)}{intakeId && <InsuranceCards key={intakeId} intakeId={intakeId} />}</section>}
        {(page === 'coverage' || page === 'estimate' && result.payment_mode !== 'cash') && <FrontDeskCoverage result={result} />}
        {page === 'coverage' && <button className="visit-primary" onClick={() => setPage('plan')}>Continue to visit services <ArrowRight size={16} /></button>}
        <div hidden={page !== 'plan'}><ServicePlanner key={`${result.check_id}:${result.revision}`} result={result} busy={!!busy || recovering} save={saveReview} viewEstimate={message => { setPage('estimate'); setNotice(message || '') }} /></div>
        {page === 'estimate' && <>
          <VisitServices result={result} />
          <section className="visit-answer" aria-labelledby="payment-heading"><div className="visit-section-heading"><div><p className="visit-label">{price?.mode === 'alternatives' ? 'Visit-level comparison' : 'For the selected care'}</p><h2 id="payment-heading">{result.payment_mode === 'cash' ? 'What will the patient pay?' : 'Collect when the patient arrives'}</h2></div><button className="visit-text-button" onClick={() => setPage('plan')}>Change services <ArrowRight size={16} /></button></div>
            <div className="payment-choice" aria-label="Payment method">
              <button type="button" aria-pressed={result.payment_mode !== 'cash'} disabled={!!busy} onClick={() => void saveReview({ payment_mode: 'insurance' })}><span>Use insurance</span><strong>{serviceOptions ? 'Choose services' : result.arrival_collection?.amount_cents != null ? money(result.arrival_collection.amount_cents) : 'Verify collection'}</strong><small>{planName || result.payer || 'Your insurance plan'}</small></button>
              <button type="button" aria-pressed={result.payment_mode === 'cash'} disabled={!!busy} onClick={() => void saveReview({ payment_mode: 'cash' })}><span>Pay cash</span><strong>{serviceOptions ? 'Choose services' : cashPrice || (result.cash_schedule ? 'Choose cash package' : 'Cash schedule needed')}</strong><small>Clinic self-pay price</small></button>
            </div>
            {result.payment_mode === 'cash' ? <div className="payment-explanation"><h3>{cashPrice ? `Cash total · ${cashPrice}` : result.cash_schedule ? 'Choose the cash scope' : 'Add the practice cash price'}</h3><p>{result.cash_quote?.message || 'This practice has not uploaded a cash price for these services.'}</p>{!cashPrice && <button className="visit-text-button" onClick={() => setPage(result.cash_schedule ? 'plan' : 'sources')}>{result.cash_schedule ? 'Choose package' : 'Practice cash prices'} <ArrowRight size={16} /></button>}</div> : <div className="payment-explanation">
              <dl className="collection-components"><div><dt>Verified office copay</dt><dd>{result.arrival_collection?.verified_copay_cents != null ? money(result.arrival_collection.verified_copay_cents) : 'Not established for this visit'}</dd></div><div><dt>Estimated total responsibility today</dt><dd>{result.arrival_collection?.estimated_cents != null ? money(result.arrival_collection.estimated_cents) : 'Not established'}</dd></div></dl>
              <h3>{result.arrival_collection?.amount_cents != null ? `${money(result.arrival_collection.amount_cents)} ${result.arrival_collection.status === 'copay_only' ? 'verified copay only' : 'estimated for this visit'}` : 'Verify before collecting'}</h3>{result.arrival_collection?.amount_cents == null && result.arrival_collection?.estimated_cents != null && <p>Planning amount: <strong>{money(result.arrival_collection.estimated_cents)}</strong></p>}<p>{result.arrival_collection?.reason || 'Coverage and applicable service benefits must be verified before a check-in amount can be established.'}</p>
              <dl className="collection-components">{(['copay_cents', 'deductible_cents', 'coinsurance_cents', 'oop_adjustment_cents'] as const).map(key => <div key={key}><dt>{({ copay_cents: 'Copay', deductible_cents: 'Deductible applied to this visit', coinsurance_cents: 'Coinsurance', oop_adjustment_cents: 'Reduction for patient payment limits' })[key]}</dt><dd>{price?.complete ? money((price.lines || []).reduce((sum, line) => sum + (line.calculation?.[key] || 0), 0)) : 'Not established'}</dd></div>)}<div><dt>Payments already received</dt><dd>{result.arrival_collection?.payments_received_cents != null ? money(result.arrival_collection.payments_received_cents) : 'Ledger not returned'}</dd></div><div><dt>Outstanding prior balance</dt><dd>Ledger not returned</dd></div><div><dt>Additional non-covered charges</dt><dd>No verified charge supplied</dd></div></dl>
              {result.check_id && <details className="visit-line-details"><summary>Confirm payments already received</summary><form onSubmit={event => { event.preventDefault(); void saveReview({ payments_received_cents: Math.round(Number(paymentsReceived) * 100), payment_reference: paymentReference }) }}><p>Use the clinic ledger. Enter 0 only after checking that no payment has been received for this visit.</p><div className="staff-two-columns"><label className="plan-field">Payments received for this visit ($)<input type="number" min="0" step="0.01" required value={paymentsReceived} onChange={event => setPaymentsReceived(event.target.value)} disabled={!!busy} /></label><label className="plan-field">Payment ledger reference<input required minLength={3} maxLength={200} value={paymentReference} onChange={event => setPaymentReference(event.target.value)} disabled={!!busy} /></label></div><button className="visit-primary" disabled={!!busy}>Update collection amount</button></form></details>}
              <div className="benefit-digest">{benefitLines.map(line => <div key={line.id}><strong>{line.benefits.benefit_label || 'Selected service benefit'}</strong><p>{line.benefits.benefit_summary || (line.benefits.copay_cents != null ? `${money(line.benefits.copay_cents)} copay` : `${line.benefits.coinsurance_percent}% patient coinsurance`)}</p></div>)}</div>
            </div>}
          </section>
          {result.payment_mode !== 'cash' && <section className="collection-summary"><Wallet size={24} aria-hidden="true" /><div><h3>Estimated collection for this visit</h3><strong>{result.arrival_collection?.amount_cents != null ? money(result.arrival_collection.amount_cents) : 'Not yet established'}</strong><p>{result.arrival_collection?.reason}</p></div></section>}
        </>}
        {page === 'sources' && <>
          {patientPrice && <section className="visit-provenance"><h2>After the insurance claim</h2><p>Estimated total patient responsibility: {patientPrice}. Estimated insurer share: {insurancePrice}. These are claim estimates, separate from check-in collection.</p></section>}
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
          {result.check_id && <FinancialHistory key={result.check_id} checkId={result.check_id} />}
          <details className="visit-history" onToggle={event => { if (event.currentTarget.open && history === null && !historyBusy && !historyError) void loadHistory() }}><summary>Previous visits used for procedure suggestions <ChevronDown size={18} /></summary>{historyBusy && <p role="status">Loading history…</p>}{historyError && <p className="visit-error">{historyError}<button onClick={() => void loadHistory()}>Retry</button></p>}{history?.map(visit => <article key={visit.id}><strong>{visitDate(visit.start)} · {visit.reason || 'Previous visit'}</strong><p>{visit.coded_services.join(', ')}</p></article>)}{history?.length === 0 && <p>No completed visits returned in the last year.</p>}</details>
        </>}
        <footer className="visit-result-footer"><span>Previsit estimate · Final charges follow the care provided.</span><div className="visit-result-actions">{intakeId && result.check_id && <button className="visit-text-button visit-delete-button" disabled={!!busy || recovering} onClick={() => void deleteCheck()}><Trash2 size={15} />{cleanupPending ? 'Retry file cleanup' : 'Delete check'}</button>}<button className="visit-text-button" disabled={!!busy || cleanupPending || recovering} onClick={() => void estimate()}><RefreshCw size={15} />Check again</button></div></footer>
      </> : !error && !busy && <div className="visit-empty"><CalendarDays size={28} /><h2>{selected ? 'Preparing this visit' : 'Schedule a visit in eCW'}</h2><p>{selected ? 'Reading the appointment and benefits.' : 'Once the appointment is scheduled, refresh to build its estimate.'}</p></div>}
    </div>
  </section>
}
