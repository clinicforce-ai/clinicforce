import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowLeft, ArrowRight, CalendarDays, Check, CircleHelp, Clock3, RefreshCw, ShieldCheck, Trash2, BadgeDollarSign, Network, X, CreditCard, FileText, Stethoscope, Wallet, PieChart, ArrowUpRight, SlidersHorizontal } from 'lucide-react'
import { deleteStaffInsuranceCheck, getStaffIntake, getStaffSession, type StaffPatient, type StaffSession } from '../services/api/staff'
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

function CoverageCheck({ label, value, confirmed, detail }: { label: string; value: string; confirmed: boolean; detail?: string }) {
  return <div className="visit-check"><span className={confirmed ? 'visit-check-icon is-confirmed' : 'visit-check-icon'} aria-hidden="true">{confirmed ? <Check size={16} /> : <CircleHelp size={16} />}</span>
    <div><dt>{label}</dt>{detail && <small>{detail}</small>}</div><dd>{value}</dd></div>
}


function BenefitTable({ rows }: { rows: NonNullable<NonNullable<PrevisitEstimate['benefits']>['reported_options']> }) {
  return <div className="clinical-table-wrap"><table className="clinical-service-table"><thead><tr><th>Benefit / service</th><th>Network</th><th>Amount / limit</th><th>Applies to</th></tr></thead><tbody>{rows.map((row, index) => <tr key={row.id || index}><th scope="row">{row.kind.replaceAll('_', ' ')}<small>{row.procedure_code ? `CPT ${row.procedure_code}` : `Service category ${row.service_type_code}`}</small>{row.payer_note && <small className="benefit-note">{row.payer_note}</small>}</th><td>{row.network.replaceAll('_', ' ').toLowerCase()}</td><td>{row.amount_cents != null ? money(row.amount_cents) : row.percent != null ? `${row.percent}%` : row.quantity?.value || 'Not specified'}</td><td>{[row.coverage_level, row.time_period].filter(Boolean).join(' · ').replaceAll('_', ' ').toLowerCase()}</td></tr>)}</tbody></table></div>
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
  return <section className="front-desk-coverage" aria-labelledby="front-desk-heading"><div className="visit-section-heading"><div><h2 id="front-desk-heading">Coverage & approvals</h2></div><ShieldCheck size={24} aria-hidden="true" /></div>
    <div className="coverage-plan"><Network size={22} aria-hidden="true" /><div><small>Exact plan / product</small><strong>{plan || 'Plan name not returned'}</strong><span>{result.payer}</span></div></div>
    <dl className="visit-audit-grid">
      <CoverageCheck label={`Eligibility for ${visitDate(result.service_date)}`} value={benefits?.service_date_matches === false ? 'Appointment date not verified' : benefits?.active === true ? 'Eligible' : benefits?.active === false ? 'Ineligible' : result.eligibility_failure ? 'Could not verify' : 'Not verified'} confirmed={benefits?.active === true && benefits?.service_date_matches !== false} detail={benefits?.service_date_matches === false ? `Payer returned ${visitDate(benefits.returned_service_dates?.start || '')} · requested ${visitDate(result.service_date)}` : `Stedi · ${time}`} />
      <CoverageCheck label="Network status" value={benefits?.network_status === 'in_network' ? 'In network' : benefits?.network_status === 'out_of_network' ? 'Out of network' : 'Not established'} confirmed={benefits?.network_status === 'in_network'} detail={result.network_evidence?.network_name || 'For this provider and plan'} />
      <CoverageCheck label="Saved eCW insurance status" value={saved?.status === 'verified_active' ? 'Verified · active' : saved?.status === 'verified_inactive' ? 'Verified · inactive' : record?.status || 'Not returned'} confirmed={saved?.status === 'verified_active'} detail={saved?.checked_at ? `${visitDate(saved.checked_at)} · ${visitTime(saved.checked_at)}` : record?.message || 'A saved policy does not verify this appointment'} />
      <CoverageCheck label="Deductible remaining" value={benefits?.deductible_remaining_cents != null ? money(benefits.deductible_remaining_cents) : 'Balance not returned'} confirmed={benefits?.deductible_remaining_cents != null}  />
      <CoverageCheck label="Out-of-pocket remaining" value={benefits?.oop_remaining_cents != null ? money(benefits.oop_remaining_cents) : 'Balance not returned'} confirmed={benefits?.oop_remaining_cents != null}  />
      <CoverageCheck label="Referral" value={`${referral.required === 'Yes' ? 'Required' : referral.required === 'No' ? 'Not required' : 'Requirement not returned'} · ${referral.status}`} confirmed={referral.valid} />
      <CoverageCheck label="Prior authorization" value={`${authorization.required === 'Yes' ? 'Required' : authorization.required === 'No' ? 'Not required' : 'Requirement not returned'} · ${authorization.status}`} confirmed={authorization.valid} />
    </dl>
    {!!benefits?.reported_options?.some(row => row.kind === 'copay') && <section className="payer-copays"><h3>Payer-reported copays</h3><p className="visit-muted">Plan benefits only · check network and service conditions before collecting.</p><BenefitTable rows={benefits.reported_options.filter(row => row.kind === 'copay')} /></section>}
    {!!benefits?.reported_options?.some(row => row.kind !== 'copay') && <section><h3>Deductibles, coinsurance & other benefits</h3><BenefitTable rows={benefits.reported_options.filter(row => row.kind !== 'copay')} /></section>}
    <div className="coverage-footnote"><Clock3 size={15} aria-hidden="true" /><span>Benefits: Stedi · {time}. Appointment and saved checks: eCW.</span></div>
  </section>
}

function FinancialHistory({ checkId }: { checkId: string }) {
  const [data, setData] = useState<VisitFinancials | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const load = useCallback(async () => { setBusy(true); setError(''); try { setData(await commercialFinancials(checkId)) } catch { setError('The financial connection could not be read. Retry the check.') } finally { setBusy(false) } }, [checkId])
  useEffect(() => { let active = true; commercialFinancials(checkId).then(value => { if (active) setData(value) }).catch(() => { if (active) setError('The financial connection could not be read. Retry the check.') }).finally(() => { if (active) setBusy(false) }); return () => { active = false } }, [checkId])
  return <section className="financial-history"><div className="visit-section-heading"><div><h3><BadgeDollarSign size={20} aria-hidden="true" /> Processed claims</h3></div><button className="visit-text-button" disabled={busy} onClick={() => void load()}><RefreshCw size={16} /> Refresh</button></div>
    <p className="visit-muted">Actual amounts from the insurer’s processed claims. They appear when the connected payer feed receives the receipt; timing varies.</p>
    {busy ? <p role="status">Reading insurance receipts…</p> : <p role={error ? 'alert' : undefined}>{error || data?.message}</p>}
    {!!data?.lines.length && <div className="clinical-table-wrap"><table className="clinical-service-table"><thead><tr><th>Service / payer</th><th>Allowed amount</th><th>Insurer paid</th><th>Patient share</th></tr></thead><tbody>{data.lines.map(row => <tr key={`${row.transaction_id}:${row.claim_reference}:${row.line_index}`}><th scope="row">CPT {row.code}{row.modifiers.length ? ` · ${row.modifiers.join(', ')}` : ''}<small>{row.service_date ? visitDate(row.service_date.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')) : 'Service date not returned'} · {row.payer}</small><small>{({ '1': 'Primary insurance processed', '2': 'Secondary insurance processed', '3': 'Additional insurance processed', '4': 'Denied', '22': 'Payment reversed' } as Record<string, string>)[row.claim_status || ''] || 'Payer response'} · Received {row.received_at ? visitDate(row.received_at) : 'date not supplied'}</small></th><td>{row.allowed_cents == null ? 'Not returned' : money(row.allowed_cents)}</td><td>{row.insurer_paid_cents == null ? 'Not returned' : money(row.insurer_paid_cents)}</td><td>{row.patient_responsibility_cents == null ? 'Not returned' : money(row.patient_responsibility_cents)}</td></tr>)}</tbody></table></div>}
    {data && <small>{data.source} · Checked {visitDate(data.checked_at)} {visitTime(data.checked_at)}{data.partial ? ' · Partial history' : ''}. These records do not show payments collected from the patient.</small>}
  </section>
}

type DetailPanel = 'coverage' | 'notes' | 'calculation' | 'sources' | 'services'
const panelTitles: Record<DetailPanel, string> = { coverage: 'Coverage & insurance card', notes: 'Appointment notes', calculation: 'How this estimate works', sources: 'Sources & history', services: 'Adjust planned services' }

function VisitDialog({ open, title, close, children }: { open: boolean; title: string; close: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal()
    if (!open && dialog.current?.open) dialog.current.close()
  }, [open])
  return <dialog ref={dialog} className="visit-detail-panel" aria-labelledby="visit-detail-title" onCancel={event => { event.preventDefault(); close() }}>
    <header className="visit-detail-header"><h2 id="visit-detail-title">{title}</h2><button aria-label="Close details" onClick={close}><X size={22} /></button></header>
    <div className="visit-detail-body">{children}</div>
  </dialog>
}

function AppointmentNotes({ result }: { result: PrevisitEstimate }) {
  const fields = result.appointment_context?.imported_fields || []
  const isStatus = (label: string) => /^(?:(?:appointment|encounter|visit)[ _-]+)?status$/i.test(label.trim())
  const statuses = fields.filter(field => isStatus(field.label))
  const details = fields.filter(field => !isStatus(field.label))
  const hasNotes = details.some(field => /note|comment|instruction|narrative|description/i.test(field.label))
  return <section className="visit-imported">
    <h3>{result.reason || 'Scheduled visit'}</h3>
    <p className="visit-muted">{[result.appointment_context?.rendering_provider?.name, result.appointment_context?.location].filter(Boolean).join(' · ')}</p>
    {statuses.map((field, index) => <p className="visit-muted" key={index}>Appointment status: {field.text}</p>)}
    {!hasNotes && <article><h3>General Notes not returned</h3><p>The eCW connection did not return General Notes or patient comments for this appointment.</p></article>}
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

export default function CommercialEstimator({ onBack, intakeId, refreshBenefits = false }: { onBack: () => void; intakeId?: string; refreshBenefits?: boolean }) {
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
  const [panel, setPanel] = useState<DetailPanel | null>(null)
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
  const automaticEstimate = useRef('')
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
  const collectionBlockers = [...new Set([
    benefits?.service_date_matches === false ? `Appointment date unverified · payer returned ${visitDate(benefits.returned_service_dates?.start || '')}.` : '',
    failed ? failure?.title || 'Eligibility check incomplete.' : '',
    benefits?.active === false ? 'Coverage is inactive for this visit.' : '',
    ...(benefits?.review_reasons || []).filter(reason => reason.includes('identity')),
    benefits?.network_status !== 'in_network' ? 'Provider network: verify participation.' : '',
    price?.lines?.some(line => line.allowed_cents == null) ? 'Contract rate: missing for selected services.' : '',
    benefits?.requires_review ? 'Service benefits / approvals: review needed.' : '',
  ].filter(Boolean))]
  const reportedOfficeCopays = benefits?.reported_options?.filter(row => row.kind === 'copay' && row.amount_cents != null && row.network === 'IN_NETWORK' && /\b(BQ|BY|98)\b/.test(row.service_type_code) && !/injection|lab|x-ray|primary care|first|after|only|limit/i.test(row.payer_note || '')) || []
  const reportedCopayAmounts = reportedOfficeCopays.map(row => row.amount_cents!)
  const reportedOfficeCopay = price?.lines?.some(line => /^992(0[2-5]|1[2-5])$/.test(line.code)) && reportedCopayAmounts.length ? range(Math.min(...reportedCopayAmounts), Math.max(...reportedCopayAmounts)) : null
  const calculationReasons = [...new Set((price?.lines || []).map(line => line.benefits.benefit_summary).filter(Boolean))]
  const collectionDetails = [...new Set([...(result?.issues || []), ...(price?.lines || []).flatMap(line => line.issues || [])])]
  const policySources = [...new Map((price?.lines || []).filter(line => line.benefits.policy_evidence).map(line => [line.benefits.policy_evidence!.url, line.benefits.policy_evidence!])).values()]
  const cashPrice = price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null ? range(price.cash_min_cents, price.cash_max_cents) : null
  const planName = result?.plan_details?.stedi.names?.join(' · ') || result?.plan_details?.ecw.plan?.filter(value => value !== 'CI').join(' · ') || (result?.plan !== 'CI' ? result?.plan : null)
  const payerSubscriber = result?.insurance_details?.payer_subscriber
  const payerPatient = result?.insurance_details?.payer_patient
  const policyDates = result?.insurance_details?.payer_coverage_period?.start ? result.insurance_details.payer_coverage_period : result?.insurance_details?.coverage_period

  const policyStatus = benefits?.service_date_matches === false ? 'Coverage returned · verify appointment date' : benefits?.active === true ? 'Coverage active' : benefits?.active === false ? 'Coverage inactive' : result?.ecw_coverage_active ? 'Policy in eCW · verify benefits' : 'Coverage check in progress'

  const cash = result?.payment_mode === 'cash'
  const amount = cash ? cashPrice : result?.planning_estimate ? money(result.planning_estimate.amount_cents) : result?.arrival_collection?.amount_cents != null ? money(result.arrival_collection.amount_cents) : reportedOfficeCopay
  const amountLabel = cash ? 'Cash price' : result?.planning_estimate ? 'Planning estimate' : result?.arrival_collection?.amount_cents == null && reportedOfficeCopay ? 'Payer-reported office copay' : result?.arrival_collection?.status === 'copay_only' ? 'Verified copay only' : 'Estimated amount to collect'
  const explanation = cash ? 'Clinic self-pay price for this service plan.' : result?.planning_estimate ? result.planning_estimate.explanation : result?.arrival_collection?.amount_cents == null ? reportedOfficeCopay ? 'Reference benefit. The total insured amount is not established.' : collectionDetails[0] || failure?.title || 'The available evidence does not establish an insured amount for this visit.' : result.arrival_collection.status === 'copay_only' ? 'The full visit amount is not established.' : result.arrival_collection.payments_received_cents == null ? 'For this visit, before payments or credits.' : 'After recorded payments for this visit.'
  const openPanel = (next: DetailPanel) => { setPanel(next); if (next === 'sources' && history === null && !historyBusy) void loadHistory() }

  return <section className="visit-estimator visit-visual" aria-labelledby="visit-estimate-title">
    <header className="visit-page-header"><div><button className="visit-back" onClick={onBack}><ArrowLeft size={17} /> Back to patient</button><h1 id="visit-estimate-title">Your visit, at a glance</h1></div><span className="visit-practice">{session?.practice_name}</span></header>
    <div className="visit-patient-bar"><div><span className="patient-monogram" aria-hidden="true">{patient?.given_name?.[0]}{patient?.family_name?.[0]}</span><div><strong>{patient ? `${patient.given_name} ${patient.family_name}` : 'Patient record'}</strong>{patient?.birth_date && <small>DOB {visitDate(patient.birth_date)}</small>}</div></div><span className={benefits?.active === true && benefits.service_date_matches !== false ? 'coverage-chip' : 'coverage-chip is-neutral'}><ShieldCheck size={18} aria-hidden="true" />{policyStatus}</span></div>
    <div className="visit-appointment-strip"><CalendarDays size={22} aria-hidden="true" /><label>Scheduled visit<select value={selectedVisit} disabled={!!busy || !appointments.length} onChange={event => chooseVisit(event.target.value)}>{appointments.length ? appointments.map(visit => <option key={visit.id} value={visit.id}>{visitDate(visit.start)} · {visitTime(visit.start, result?.appointment_context?.timezone)} · {visit.reason || 'Scheduled care'}</option>) : <option value="">{loaded ? 'No scheduled visit returned by eCW' : 'Loading appointments…'}</option>}</select></label><button className="visit-icon-button" aria-label="Refresh visits" disabled={!!busy} onClick={() => void load()}><RefreshCw size={18} className={busy === 'visits' ? 'visit-spin' : ''} /></button></div>
    <div className="visit-content" aria-busy={!!busy || recovering}>
      {notice && <p className="visit-notice" role="status">{notice}</p>}{deleteError && <p className="visit-error" role="alert">{deleteError}</p>}
      {error && <div className="visit-error" role="alert"><CircleHelp size={20} aria-hidden="true" /><div><strong>This check could not be completed</strong><p>{error}</p><button disabled={!!busy} onClick={() => void (selected ? estimate() : load())}>Try again <ArrowRight size={15} /></button></div></div>}
      {busy === 'estimate' || busy === 'visits' ? <div className="visit-empty" role="status"><RefreshCw className="visit-spin" size={26} aria-hidden="true" /><h2>{busy === 'visits' ? 'Finding your visit' : 'Putting the estimate together'}</h2><p>eCW appointment · Payer benefits · Clinic prices</p></div> : result ? <>
        <section id="visit-estimate" className="visit-overview" aria-labelledby="payment-heading">
          <div className="overview-heading"><h2 id="payment-heading">{cash ? 'Self-pay estimate' : 'Visit estimate'}</h2><div className="payment-toggle" aria-label="Payment method"><button aria-pressed={!cash} disabled={!!busy || recovering} onClick={() => { if (cash) void saveReview({ payment_mode: 'insurance' }) }}><ShieldCheck size={16} />Insurance</button><button aria-pressed={cash} disabled={!!busy || recovering} onClick={() => { if (!cash) void saveReview({ payment_mode: 'cash' }) }}><Wallet size={16} />Cash</button></div></div>
          <div className="overview-amounts"><div className="arrival-total"><span>{amountLabel}</span><strong className={!amount ? 'amount-pending' : ''}>{amount || 'Not available yet'}</strong><p>{explanation}</p>
            {!cash && result.planning_estimate && <span className="estimate-condition">Assumes the returned policy applies, in-network covered care and required approvals.</span>}
            {!cash && !result.planning_estimate && result.arrival_collection?.amount_cents != null && result.arrival_collection.payments_received_cents == null && <span className="estimate-condition">Payments and credits have not been returned by the clinic ledger.</span>}
            <button className="visit-text-button" onClick={() => openPanel('calculation')}>How it’s calculated <ArrowUpRight size={16} /></button></div><CostPicture result={result} /></div>
          <div className="overview-plan-name"><ShieldCheck size={16} aria-hidden="true" /><span>{cash ? 'Clinic self-pay schedule' : planName || result.payer || 'Policy details not returned'}</span>{!cash && cashPrice && <span className="cash-comparison">Cash option {cashPrice}</span>}</div>
        </section>
        <div className="visit-snapshots" aria-label="Coverage summary">
          <div><Network size={22} aria-hidden="true" /><span>Provider network<strong>{benefits?.network_status === 'in_network' ? 'In network' : benefits?.network_status === 'out_of_network' ? 'Out of network' : 'Not established'}</strong></span></div>
          <div><Wallet size={22} aria-hidden="true" /><span>Deductible remaining<strong>{benefits?.deductible_remaining_cents != null ? money(benefits.deductible_remaining_cents) : 'Not returned'}</strong></span></div>
          <div><ShieldCheck size={22} aria-hidden="true" /><span>Out-of-pocket remaining<strong>{benefits?.oop_remaining_cents != null ? money(benefits.oop_remaining_cents) : 'Not returned'}</strong></span></div>
        </div>
        <section id="visit-plan" className="visit-care-summary"><div className="visit-section-heading"><div><h2>{result.service_plan?.selection_source === 'clinician' ? 'Planned services' : 'Suggested services'}</h2><small>{result.service_plan?.selection_source === 'clinician' ? 'Estimate follows this service plan' : 'Planning assumptions · final services follow the care provided'}</small></div><button onClick={() => openPanel('services')} disabled={!!busy || recovering}><SlidersHorizontal size={17} />Adjust services</button></div>
          <div className="visit-care-cards">{result.codes.map((row, index) => <article key={row.id || index}><span className="care-icon"><Stethoscope size={24} aria-hidden="true" /></span><div><strong>{row.description || `Procedure ${row.code}`}</strong><small>{row.code.startsWith('J') ? 'HCPCS' : 'CPT'} {row.code} · {row.units || 'Unspecified'} {Number(row.units) === 1 ? 'unit' : 'units'}</small></div></article>)}{!result.codes.length && <p>No planned services returned. Available service options are under Adjust services.</p>}</div>
        </section>
        <nav className="visit-detail-links" aria-label="Visit details"><button onClick={() => openPanel('coverage')}><CreditCard size={22} /><span>Coverage & card<small>Patient, policy and benefits</small></span><ArrowUpRight size={18} /></button><button onClick={() => openPanel('notes')}><FileText size={22} /><span>Appointment notes<small>Context from eCW</small></span><ArrowUpRight size={18} /></button><button onClick={() => openPanel('sources')}><Clock3 size={22} /><span>Sources & history<small>Where the numbers come from</small></span><ArrowUpRight size={18} /></button></nav>
        {recovering && <p role="status" className="visit-muted">Checking additional coverage in the background…</p>}
        <VisitDialog open={panel !== null} title={panel ? panelTitles[panel] : 'Visit details'} close={() => setPanel(null)}>
          <div hidden={panel !== 'coverage'}>
            {failed && <section className="visit-recovery"><h3>{recovering ? 'Finding coverage…' : failure?.title || 'Coverage check incomplete'}</h3><p>{failure?.action || result.issues.join(' ')}</p>{result.recovery?.message && <p>{result.recovery.message}</p>}{result.recovery?.candidates?.map(candidate => <article key={candidate.id}><strong>{candidate.payer}</strong><p>{candidate.plans.join(' · ')}</p><button disabled={!!busy || recovering} onClick={() => void selectCoverage(candidate.id)}>Use this policy</button></article>)}</section>}
            <section className="patient-policy"><div className="policy-relationship"><div><span>Patient</span><strong>{payerPatient?.lastName ? [payerPatient.firstName, payerPatient.lastName].filter(Boolean).join(' ') : patient ? `${patient.given_name} ${patient.family_name}` : 'Not returned'}</strong><small>eCW: {patient?.given_name} {patient?.family_name}</small></div><ArrowRight size={22} aria-hidden="true" /><div><span>Policyholder · {result.insurance_details?.payer_relationship?.toLowerCase() || ({ '18': 'self', '01': 'spouse', '19': 'child', G8: 'other' } as Record<string, string>)[result.insurance_details?.relationship || ''] || 'relationship not returned'}</span><strong>{payerSubscriber?.lastName ? [payerSubscriber.firstName, payerSubscriber.lastName].filter(Boolean).join(' ') : [result.insurance_details?.subscriber_first, result.insurance_details?.subscriber_last].filter(Boolean).join(' ') || 'Not returned'}</strong></div></div>
              <dl><div><dt>Member ID</dt><dd>{result.insurance_details?.member_id || 'Not returned'}</dd></div><div><dt>Group</dt><dd>{result.insurance_details?.group_number || result.plan_details?.stedi.group_number || 'Not returned'}</dd></div><div><dt>Coverage dates</dt><dd>{policyDates?.start || 'Start not returned'} – {policyDates?.end || 'End not returned'}</dd></div><div><dt>Contact</dt><dd>{result.patient_details?.phone || 'Phone not returned'}<small>{result.patient_details?.email}</small></dd></div><div><dt>Address</dt><dd>{Object.values(result.patient_details?.address || {}).filter(Boolean).join(', ') || 'Not returned'}</dd></div><div><dt>Payer patient DOB</dt><dd>{payerPatient?.dateOfBirth ? visitDate(payerPatient.dateOfBirth) : 'Not returned'}</dd></div></dl>
              {!!result.identity?.differences.length && <p className="visit-muted">Both names are retained with their source. {result.identity.status === 'reconciled' ? 'A verified relationship is on file.' : 'Any policy-match assumption remains part of the estimate.'}</p>}
              {intakeId && <InsuranceCards key={intakeId} intakeId={intakeId} background inline />}
            </section><FrontDeskCoverage result={result} />
            {!!result.coverage_records?.length && <section className="visit-records"><h3>Policies returned by eCW</h3>{result.coverage_records.map(row => <article key={row.id}><strong>{row.payer?.join(' · ') || 'Insurance record'}</strong><p>{row.status} · Order {row.order ?? 'not returned'} · {row.relationship || 'Relationship not returned'}</p><small>Member {row.member_id || 'not returned'} · Group {row.group_number || 'not returned'}</small><small>Subscriber: {row.subscriber || 'Not returned'} · Coverage: {row.period?.start || 'Start not returned'} – {row.period?.end || 'End not returned'}</small>{row.cost_to_beneficiary?.map((cost, index) => <p key={index}>{cost.type?.text || 'Patient cost share'}: {cost.valueMoney ? `${cost.valueMoney.value} ${cost.valueMoney.currency || 'USD'}` : cost.valueQuantity ? `${cost.valueQuantity.value}${cost.valueQuantity.unit || ''}` : 'See policy'}</p>)}</article>)}</section>}
          </div>
          {panel === 'services' && <ServicePlanner key={`${result.check_id}:${result.revision}`} result={result} busy={!!busy || recovering} save={saveReview} viewEstimate={message => { setPanel(null); setNotice(message || '') }} />}
          {panel === 'notes' && <AppointmentNotes result={result} />}
          {panel === 'calculation' && <section><div className="calculation-summary"><PieChart size={24} aria-hidden="true" /><div><h3>{amountLabel} · {amount || 'Not established'}</h3><p>{explanation}</p></div></div>
            {!!result.planning_estimate?.assumptions.length && <section><h3>Assumptions used</h3><ul>{result.planning_estimate.assumptions.map(item => <li key={item}>{item}</li>)}</ul></section>}
            {!!result.service_plan?.assumptions?.length && <section><h3>Service-plan assumptions</h3><ul>{result.service_plan.assumptions.map(item => <li key={item}>{item}</li>)}</ul></section>}
            {!cash && <dl className="collection-components">{(['copay_cents', 'deductible_cents', 'coinsurance_cents', 'oop_adjustment_cents'] as const).map(key => <div key={key}><dt>{({ copay_cents: 'Copay applied', deductible_cents: 'Deductible applied', coinsurance_cents: 'Coinsurance applied', oop_adjustment_cents: 'Out-of-pocket adjustment' })[key]}</dt><dd>{price?.complete && price.mode === 'services' ? money((price.lines || []).reduce((sum, line) => sum + (line.calculation?.[key] || 0), 0)) : 'Not established'}</dd></div>)}<div><dt>Payments received</dt><dd>{result.arrival_collection?.payments_received_cents != null ? money(result.arrival_collection.payments_received_cents) : 'Not returned by the clinic ledger'}</dd></div></dl>}
            {calculationReasons.map(reason => <p key={reason}>{reason}</p>)}<VisitServices result={result} />
            {!!collectionDetails.length && <section><h3>Evidence still needed</h3><ul>{collectionDetails.map(item => <li key={item}>{item}</li>)}</ul></section>}
            {!cash && !amount && !!collectionBlockers.length && <ul>{collectionBlockers.map(item => <li key={item}>{item}</li>)}</ul>}
            <p className="visit-muted">Daily copays apply once to the same benefit in this visit. Earlier same-day collections, credits and prior balances require clinic ledger data.</p>
          </section>}
          {panel === 'sources' && <section className="visit-provenance"><dl><div><dt>Patient & appointment</dt><dd>eClinicalWorks<small>{result.ecw_retrieved_at && `${visitDate(result.ecw_retrieved_at)} · ${visitTime(result.ecw_retrieved_at)}`}</small></dd></div><div><dt>Payer benefits</dt><dd>Stedi<small>{result.checked_at ? `${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}` : 'Not returned'}</small></dd></div><div><dt>Clinic contract</dt><dd>{result.network_evidence?.network_name || 'No exact product mapping returned'}</dd></div><div><dt>Cash schedule</dt><dd>{result.cash_schedule?.source || result.practice_cash_source?.filename || 'Not returned'}</dd></div></dl>
            {policySources.map(source => <p key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> · {source.section}</p>)}
            {patientPrice && <p>Estimated patient share after the claim: {patientPrice}.</p>}
            <h3>Previous eCW visits</h3>{historyBusy && <p role="status">Reading visit history…</p>}{historyError && <p>{historyError}<button onClick={() => void loadHistory()}>Retry</button></p>}{history?.map(visit => <article key={visit.id}><strong>{visitDate(visit.start)} · {visit.reason || 'Previous visit'}</strong><p>{visit.coded_services.join(', ')}</p></article>)}{history?.length === 0 && <p>No completed visits returned in the last year.</p>}
            {result.check_id && <FinancialHistory key={result.check_id} checkId={result.check_id} />}
            {intakeId && result.check_id && <button className="visit-delete-button" disabled={!!busy || recovering} onClick={() => void deleteCheck()}><Trash2 size={15} />{cleanupPending ? 'Retry file cleanup' : 'Delete saved check'}</button>}
          </section>}
        </VisitDialog>
        <footer className="visit-result-footer"><span><Clock3 size={14} aria-hidden="true" />{result.checked_at ? `Benefits checked ${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}` : 'Benefits not yet returned'}</span><button disabled={!!busy || cleanupPending || recovering} onClick={() => void estimate(true)}><RefreshCw size={16} />Check again</button></footer>
      </> : !error && !busy && <div className="visit-empty"><CalendarDays size={28} /><h2>{selected ? 'Preparing this visit' : 'No upcoming visit returned'}</h2><p>{selected ? 'Reading the appointment and benefits.' : 'Clinicforce needs a scheduled eCW visit to match the date, provider and insurance.'}</p><button onClick={() => void load()}><RefreshCw size={16} />Refresh visits</button></div>}
    </div>
  </section>
}
