import { ApiError } from '../services/api/client'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowLeft, ArrowRight, CalendarDays, Check, Clock3, RefreshCw, ShieldCheck, Trash2, BadgeDollarSign, Network, FileText, Wallet, PieChart } from 'lucide-react'
import { getStaffCheckFinancials, deleteStaffInsuranceCheck, getStaffIntake, getStaffSession, type InsuranceCheckSnapshot, type StaffPatient, type StaffSession } from '../services/api/staff'
import { commercialFinancials, type VisitFinancials, commercialAppointments, commercialHistory, commercialPrevisit, commercialSavedPrevisit, commercialSelectCoverageCandidate, commercialSelectPayerRoute, commercialVisitReview, type VisitReview, type CommercialAppointment, type CommercialPastVisit, type PrevisitEstimate, type RequirementEvidence } from '../services/api/commercial-estimator'
import VisitServices, { ServicePlanner } from './VisitServices'
import RequirementSourceLink from './RequirementSourceLink'
import InsuranceCards from './InsuranceCards'
import './CommercialEstimator.css'

const money = (cents: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(cents / 100)
const range = (min: number, max: number) => min === max ? money(min) : `${money(min)} – ${money(max)}`
const memberSource = (value?: string | null) => value === 'card_ocr' ? 'Insurance card OCR · confirm against the image' : value?.startsWith('Coverage.') ? `eCW ${value}` : value || 'Source not retained'
const dateLabel = (value: string, options: Intl.DateTimeFormatOptions) => {
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value)
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString(undefined, options)
}
const visitDate = (value: string) => dateLabel(value, { month: 'short', day: 'numeric', year: 'numeric' })
const visitTime = (value: string, timeZone?: string) => dateLabel(value, { hour: 'numeric', minute: '2-digit', ...(timeZone ? { timeZone, timeZoneName: 'short' } : {}) })

function ApplicableBenefits({ result }: { result: PrevisitEstimate }) {
  const services = result.collection_guidance?.services || []
  const otherNetworkTerms = (result.benefits?.reported_options || []).filter(row => ['N', 'NO', 'OUT_OF_NETWORK'].includes(row.network))
  const responseRow = (id?: string | null) => id && <small>Response row: {id}</small>
  return <section><h3>{result.payment_mode === 'cash' ? 'Insurance benefits returned for review' : 'Benefits used for this visit'}</h3><p>{result.payment_mode === 'cash' ? 'These benefits do not set the clinic cash price.' : 'Matched to the selected services, visit date and location.'}</p>
    {services.map(row => { const terms = result.price_range?.lines?.find(line => line.id === row.id)?.benefits; return <article className="service-evidence" key={row.id}><h4>CPT {row.code} · {row.benefit}</h4><dl>
      <div><dt>Benefit source</dt><dd>{row.benefit_source || result.benefit_source || 'Not confirmed'}</dd></div>
      {row.copay_cents != null && <div><dt>Applicable copay</dt><dd>{money(row.copay_cents)}{responseRow(terms?.copay_id)}</dd></div>}
      {row.coinsurance_percent != null && <div><dt>Coinsurance</dt><dd>{row.coinsurance_percent}%{responseRow(terms?.coinsurance_id)}</dd></div>}
      {row.deductible_remaining_cents != null && <div><dt>Deductible remaining</dt><dd>{money(row.deductible_remaining_cents)}{responseRow(terms?.deductible_id)}</dd></div>}
      {row.deductible_annual_cents != null && <div><dt>Annual deductible</dt><dd>{money(row.deductible_annual_cents)}{responseRow(terms?.deductible_annual_id)}</dd></div>}
      <div><dt>Deductible for this service</dt><dd>{({ DOES_NOT_APPLY: 'Does not apply', APPLIES: 'Applies first', ALREADY_MET: 'Already met', UNKNOWN: 'Not confirmed' })[row.deductible_applicability || 'UNKNOWN']}{responseRow(terms?.deductible_applicability_ids?.join(' · '))}</dd></div>
      {row.oop_remaining_cents != null && <div><dt>Out-of-pocket remaining</dt><dd>{money(row.oop_remaining_cents)}{responseRow(terms?.oop_id)}</dd></div>}
    </dl>{!!row.selected_benefit_ids?.length && <details><summary>Response rows used</summary><small>{row.selected_benefit_ids.join(' · ')}</small></details>}{row.oop_remaining_cents === 0 && <p>The remaining out-of-pocket limit reduces the estimated cost for covered in-network care to $0.</p>}</article> })}
    {!services.length && <p>Applicable benefits are still needed to calculate the collection amount.</p>}
    {result.benefits?.network_status !== 'out_of_network' && !!otherNetworkTerms.length && <div className="service-evidence"><h4>Other network terms reported by the plan</h4><p>The plan also returned out-of-network terms. They do not show whether this doctor is out of network.</p><ul>{otherNetworkTerms.map(row => <li key={row.id}>{row.procedure_code ? `CPT ${row.procedure_code}` : `Service category ${row.service_type_code || 'not stated'}`} · {row.kind.replaceAll('_', ' ')} {row.percent != null ? `${row.percent}%` : row.amount_cents != null ? money(row.amount_cents) : 'amount not returned'}</li>)}</ul></div>}
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
  const split = result.benefits?.network_status === 'in_network' && price?.complete && price.mode === 'services' && patient != null && insurer != null && patient === price.patient_max_cents && insurer === price.plan_max_cents && patient + insurer > 0
  if (result.payment_mode === 'cash') return <div className="cost-picture"><Wallet size={36} aria-hidden="true" /><h3>Clinic cash price</h3><p>{result.cash_quote?.message || 'The clinic’s saved price for the selected services.'}</p></div>
  if (result.planning_estimate?.basis === 'out_of_pocket_limit') return <div className="cost-picture cost-limit"><div className="cost-emblem"><ShieldCheck size={42} aria-hidden="true" /></div><h3>Out-of-pocket limit reached</h3><p>Payer reports <strong>$0 remaining</strong> for in-network care.</p><span>Covered services · assumptions apply</span></div>
  if (result.benefits?.network_status === 'out_of_network') return <div className="cost-picture"><Network size={36} aria-hidden="true" /><h3>Out-of-network benefits</h3><p>{price?.complete ? 'The known share uses the payer’s allowed amount. Extra doctor charges are still unknown.' : 'The plan returned benefit terms, but the payer’s allowed price is still unknown.'}</p></div>
  if (split) return <div className="cost-picture"><span className="visit-label">Estimated cost sharing</span><div className="cost-share-bar" role="img" aria-label={`Patient ${money(patient)}, insurer ${money(insurer)}`}><span style={{ flex: patient }} /><span style={{ flex: insurer }} /></div><dl className="cost-share-legend"><div><dt><i />Patient</dt><dd>{money(patient)}</dd></div><div><dt><i />Insurance</dt><dd>{money(insurer)}</dd></div></dl><p>Contracted visit total <strong>{money(patient + insurer)}</strong></p></div>
  return <div className="cost-picture"><Network size={36} aria-hidden="true" /><h3>{result.benefits?.active ? 'Policy benefits received' : 'Coverage check'}</h3><p>{result.benefits?.active ? 'A final insured amount also depends on the service, provider network and clinic contract.' : 'The saved clinic cash price is available separately when provided.'}</p></div>
}

function RequirementCard({ title, evidence, icon }: { title: string; evidence?: RequirementEvidence; icon: ReactNode }) {
  const status = evidence?.status || 'Verification required'
  const reason = evidence?.reason || 'The saved plan information does not state this rule for the selected services.'
  const sources = evidence?.sources || []
  return <div className={status === 'Not required' ? 'is-confirmed' : status === 'Required' && evidence?.approval_status !== 'Verified' ? 'is-missing' : ''}>{icon}<div className="requirement-content">{title}<strong>{status === 'Verification required' ? 'Plan rule not stated' : status === 'Required' ? 'Yes' : 'No'}</strong>
    {status === 'Required' && evidence?.approval_status && <small>Approval: {evidence.approval_status === 'Verified' ? 'Recorded' : 'Not recorded'}</small>}
    <details className="requirement-evidence"><summary>Why and sources</summary><p>{reason}</p>
      {sources.map((source, index) => <p key={`${source.document_id || source.label}:${source.page || index}`}><RequirementSourceLink source={source} /></p>)}
      {!sources.length && <p>No confirmed source</p>}
      {evidence?.approval_reference && <p>Approval reference: {evidence.approval_reference}{evidence.approval_verified_at ? ` · verified ${visitDate(evidence.approval_verified_at)}` : ''}</p>}
      {evidence?.verification && <p>{evidence.verification}</p>}{evidence?.contact && <p>{evidence.contact}</p>}
      {evidence?.verify_url && <p><a href={evidence.verify_url} target="_blank" rel="noopener noreferrer">Open payer portal</a></p>}
    </details>
  </div></div>
}

export default function CommercialEstimator({ onBack, onResolvePatient, intakeId, refreshBenefits = false, saved, currentAccountNumber }: { onBack?: () => void; onResolvePatient?: () => void; intakeId?: string; refreshBenefits?: boolean; saved?: InsuranceCheckSnapshot; currentAccountNumber?: string | null }) {
  const readOnly = !!saved
  const [session, setSession] = useState<StaffSession | null>(null)
  const [patient, setPatient] = useState<StaffPatient | null>(saved?.patient || null)
  const [appointments, setAppointments] = useState<CommercialAppointment[]>([])
  const [history, setHistory] = useState<CommercialPastVisit[] | null>(null)
  const [result, setResult] = useState<PrevisitEstimate | null>(saved?.result || null)
  const [error, setError] = useState('')
  const [patientResolutionNeeded, setPatientResolutionNeeded] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [historyBusy, setHistoryBusy] = useState(false)
  const [selectedVisit, setSelectedVisit] = useState('')
  const [candidateSuggestion, setCandidateSuggestion] = useState('')
  const [payerSuggestion, setPayerSuggestion] = useState('')
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
    setBusy('visits'); setError(''); setPatientResolutionNeeded(false); setDeleteError(''); setCleanupPending(false); setNotice(''); setResult(null); setPatient(null)
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
      setPatientResolutionNeeded(schedule.reason instanceof ApiError && schedule.reason.code === 'ecw_patient_selection_required')
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
    setPatientResolutionNeeded(false)
    setError(''); setNotice(''); setDeleteError(''); setBusy('estimate')
    try {
      const response = await commercialPrevisit(selectedVisit, refresh)
      if (response.appointment_id !== selectedVisit) throw new Error('The returned estimate does not match this visit. Refresh visits and try again.')
      if (current === requestId.current) setResult(response)
    } catch (caught) {
      if (current === requestId.current) {
        setPatientResolutionNeeded(caught instanceof ApiError && caught.code === 'ecw_patient_selection_required')
        setError(caught instanceof Error ? caught.message : 'This visit could not be checked.')
      }
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
  const selectCoverage = async (candidateId: string, payerRoute = false) => {
    if (!result || busy) return
    const current = ++requestId.current
    setBusy('coverage'); setError(''); setNotice('')
    try {
      const updated = await (payerRoute ? commercialSelectPayerRoute(result, candidateId) : commercialSelectCoverageCandidate(result, candidateId))
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
  const officeCopayLine = price?.lines?.find(line => /^992(?:0[2-5]|1[2-5])$/.test(line.code) && line.benefits.copay_id)
  const copayEvidenceId = officeCopayLine?.benefits.copay_id || benefits?.copay_id
  const copaySource = officeCopayLine?.benefit_source || result?.benefit_source || 'payer eligibility'
  const failed = !!result?.eligibility_error
  const failure = result?.eligibility_failure
  const patientPrice = benefits?.active === true && benefits.network_status === 'in_network' && price?.complete && price?.patient_min_cents != null && price.patient_max_cents != null ? range(price.patient_min_cents, price.patient_max_cents) : null
  const calculationReasons = [...new Set((price?.lines || []).map(line => line.benefits.benefit_summary).filter(Boolean))]
  const policySources = [...new Map((price?.lines || []).filter(line => line.benefits.policy_evidence).map(line => [line.benefits.policy_evidence!.url, line.benefits.policy_evidence!])).values()]
  const cashPrice = price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null ? range(price.cash_min_cents, price.cash_max_cents) : null
  const planName = result?.plan_details?.stedi.names?.join(' · ') || result?.plan_details?.ecw.plan?.filter(value => value !== 'CI').join(' · ') || (result?.plan !== 'CI' ? result?.plan : null)
  const planSource = result?.plan_details?.stedi.names?.length ? `${result.benefit_source || result.plan_details.stedi.source || 'Payer'} eligibility response` : planName ? 'eCW policy record' : null
  const planTypes = [
    /\bHMO\b|health maintenance organization/i.test(planName || '') ? 'HMO' : null,
    /\bPPO\b|preferred provider (?:option|organization)/i.test(planName || '') ? 'PPO' : null,
  ].filter(Boolean)
  const planType = planTypes.length === 1 ? planTypes[0] : null
  const blueCross = /blue cross|bcbs/i.test(result?.payer || '')
  const blueCard = /bluecard/i.test([planName, ...Object.values(result?.plan_details?.ecw || {}).flat()].filter(Boolean).join(' '))
  const payerSource = result?.payer_source || (result?.payer && result.coverage_records?.some(row =>
    row.payer?.some(name => name.trim().toLowerCase() === result.payer?.trim().toLowerCase())) ? 'Coverage.payor' : null)
  const payerSubscriber = result?.insurance_details?.payer_subscriber
  const payerPatient = result?.insurance_details?.payer_patient
  const policyDates = result?.insurance_details?.payer_coverage_period?.start ? result.insurance_details.payer_coverage_period : result?.insurance_details?.coverage_period

  const tooEarly = result?.eligibility_timing?.status === 'too_early'
  const candidateStates = [...new Set(result?.recovery?.candidates?.map(row => row.coverage_evidence?.status).filter(Boolean))]
  const candidateStatus = candidateStates.length === 1 ? candidateStates[0] : undefined
  const policyStatus = tooEarly ? 'Check closer to visit'
    : result?.recovery?.state === 'pending' ? readOnly ? 'Coverage check was pending' : 'Coverage check in progress'
    : result?.recovery?.state === 'indeterminate' ? 'Coverage check outcome unknown'
    : benefits?.service_date_matches === false ? 'Coverage returned · verify appointment date'
    : benefits?.active === true ? 'Coverage active'
    : benefits?.coverage_evidence?.status === 'EXPIRED' ? 'Returned policy expired'
    : benefits?.coverage_evidence?.status === 'FUTURE' ? 'Coverage starts after this visit'
    : benefits?.active === false ? 'Coverage inactive'
    : candidateStatus === 'EXPIRED' ? 'Returned policy expired'
    : candidateStatus === 'INACTIVE' ? 'Returned policy inactive'
    : candidateStatus === 'FUTURE' ? 'Returned coverage starts after this visit'
    : result?.coverage_status === 'NOT_FOUND' ? 'Coverage not found electronically'
    : result?.recovery?.state === 'rejected' ? 'Coverage check could not complete'
    : result?.ecw_coverage_active ? 'Policy in eCW · verify benefits'
    : result?.recovery?.reason_code === 'multiple_active_policies' ? 'Two active policies found'
    : failure?.kind === 'provider_enrollment' ? 'Clinic payer access missing'
    : result?.identity?.status === 'review' ? 'Policy details differ'
    : result?.cash_fallback ? 'Insurance amount unavailable'
    : result ? 'Payer check incomplete'
    : busy === 'estimate' ? 'Coverage check in progress' : 'Coverage not checked'
  const coverageSource = benefits?.active != null || benefits?.coverage_evidence
    ? result?.benefit_source ? `Payer eligibility via ${result.benefit_source}` : 'Payer eligibility source not retained'
    : result?.recovery?.state ? result.insurance_diagnosis?.coverageSource === 'stedi_discovery'
      ? 'Stedi coverage discovery · eligibility not confirmed' : 'Coverage discovery · eligibility not confirmed'
    : result?.ecw_coverage_active ? 'eCW policy record · payer benefits not confirmed'
    : 'No verified coverage source'
  const contractEvidence = result?.network_evidence || result?.mapping_candidate?.contract
  const contractPage = contractEvidence?.locator?.match(/^Page (\d+)(?:,|$)/)?.[1]
  const contractDocumentId = contractEvidence?.document_id || result?.network_evidence?.related_document_id
  const contractUrl = result?.payment_mode !== 'cash' && contractDocumentId
    ? `/api/staff/library/${encodeURIComponent(contractDocumentId)}/document?preview=true${contractPage ? `#page=${contractPage}` : ''}`
    : null

  const cash = result?.payment_mode === 'cash'
  const coverageTone = policyStatus === 'Coverage active' ? 'is-confirmed'
    : !result || tooEarly || result.recovery?.state === 'pending' || failure?.kind === 'provider_enrollment' || (cash && !result.cash_fallback) ? '' : 'is-missing'
  const calculating = (!!busy && busy !== 'delete') || recovering || (!!selected && !result && !error)
  const previousEstimate = !!error && !!result
  const guidance = result?.collection_guidance?.status !== 'deferred' ? result?.collection_guidance : undefined
  const contractCandidate = !cash && benefits?.network_status === 'unknown' ? result?.mapping_candidate?.network : null
  const networkLabel = contractCandidate ? 'Clinic contract on file' : guidance?.network || 'Not reported'
  const networkSource = guidance?.network_source
  const networkTone = cash || tooEarly || result?.recovery?.state === 'pending' ? ''
    : benefits?.network_status === 'in_network' && !/not confirmed|unverified/i.test(networkSource || '') ? 'is-confirmed'
    : benefits?.network_status === 'out_of_network' ? 'is-missing' : ''
  const unavailableReason = error ? 'Current benefits could not be loaded.' : failure?.detail || 'The payer did not return the patient share for the selected services.'
  const supportedAmount = guidance?.status !== 'deferred_billing' && guidance?.amount_cents != null && Number.isFinite(guidance.amount_cents) && guidance.amount_cents >= 0
  const emg = result?.emg_option
  const billingInstruction = guidance?.billing_note?.kind === 'no_collection'
  const emgScenario = !billingInstruction && !cash && emg?.requested && !emg.included
  const cashAmount = cash && supportedAmount && guidance?.basis === 'practice_cash_price'
    ? guidance.range_min_cents != null && guidance.range_max_cents != null
      ? range(guidance.range_min_cents, guidance.range_max_cents) : money(guidance.amount_cents!) : cashPrice
  const amount = calculating ? null : cash ? cashAmount : supportedAmount ? money(guidance!.amount_cents!) : null
  const deferred = !calculating && !tooEarly && (!cash && guidance?.status === 'deferred_billing' || !amount)
  const amountText = readOnly && !result?.collection_guidance && !cash ? 'Not retained' : calculating ? 'Calculating…' : amount ?? (error ? 'Check visit details' : tooEarly ? 'Too early to check' : cash ? 'Confirm clinic price' : 'Review billing details')
  const amountLabel = previousEstimate ? 'Previous estimate · needs recheck' : error ? 'Visit needs review' : result?.cash_fallback ? 'Self-pay option for this visit' : deferred && !cash ? 'Insurance advance today' : readOnly ? 'Suggested collection at this visit' : 'Collect at arrival'
  const amountParts = result?.encounter_summary?.components
  const officeDeductible = guidance?.services.find(service => service.code === officeCopayLine?.code)?.deductible_applicability
  const cashExplanation = `${result?.cash_fallback?.reason ? `${result.cash_fallback.reason} ` : ''}${result?.eligibility_error === 'payer_routing_unresolved' && failure?.detail ? `${failure.detail} ` : ''}${result?.cash_quote?.message || 'The cash price is not established.'}`
  const baseExplanation = previousEstimate ? `The last saved result${result?.calculated_at ? ` from ${visitDate(result.calculated_at)} · ${visitTime(result.calculated_at)}` : ''} is shown. The update failed; reconfirm this visit before collecting.` : calculating ? 'Reading the visit, insurance and applicable benefits to calculate the amount.' : result?.payment_explanation?.sentence || (readOnly && !result?.collection_guidance && !cash ? 'The original collection recommendation was not retained. The saved benefit and calculation details are shown below.' : deferred ? cash ? cashExplanation : error ? 'The appointment or current benefits could not be loaded. No insured collection amount is available.' : `${guidance?.reason || unavailableReason} Collect no insurance advance today; bill any patient responsibility after the claim.` : cash ? cashPrice && !error ? cashExplanation : 'The clinic cash price could not be calculated for this service plan.' : guidance?.reason || unavailableReason)
  const explanation = emgScenario && !calculating && !/nerve testing/i.test(baseExplanation)
    ? `${baseExplanation.replace(/\.$/, '')}; ${emg.total_min_cents != null ? 'the nerve-testing total below is for planning only' : 'nerve testing is excluded because its patient cost is unknown'}.`
    : baseExplanation
  const summaryExplanation = explanation
  const provider = result?.appointment_context?.rendering_provider?.name
  const anticipatedService = result?.price_range?.mode === 'alternatives' ? `${result.visit_type === 'new' ? 'New patient' : 'Established patient'} office consultation · E/M alternatives ${result.codes.map(row => row.code).join(', ')}` : result?.service_plan?.recommendation_basis

  return <section className="visit-estimator visit-visual" aria-labelledby="visit-estimate-title">
    {!readOnly && <header className="visit-page-header"><div><button className="visit-back" onClick={onBack}><ArrowLeft size={17} /> Back to patient</button><h1 id="visit-estimate-title">{cash ? 'Self-pay collection' : 'Insurance estimator'}</h1></div><span className="visit-practice">{session?.practice_name}</span></header>}
    {readOnly && <header className="visit-page-header"><div><h2 id="visit-estimate-title">{cash ? 'Saved self-pay estimate' : 'Saved insurance estimate'}</h2><p>Recorded recommendation for {visitDate(result!.service_date)}.{!cash && ' Final responsibility follows the processed claim.'}</p>{saved?.saved_at && <small>Saved {visitDate(saved.saved_at)} · {visitTime(saved.saved_at)} · Revision {result?.revision}</small>}{saved?.legacy && <p className="visit-muted">This older check contains the last retained result. Earlier versions and the exact save time were not recorded.</p>}</div></header>}
    <div className="visit-patient-bar"><div><span className="patient-monogram" aria-hidden="true">{patient?.given_name?.[0]}{patient?.family_name?.[0]}</span><div><strong>{patient ? `${patient.given_name} ${patient.family_name}` : 'Patient record'}</strong>{patient?.birth_date && <small>DOB {visitDate(patient.birth_date)}</small>}{(result?.patient_details?.ecw_account_number || (!readOnly && patient?.ecw_account_number) || currentAccountNumber) && <small>eCW ACCT{readOnly && !result?.patient_details?.ecw_account_number ? ' (current record)' : ''}: {result?.patient_details?.ecw_account_number || (!readOnly && patient?.ecw_account_number) || currentAccountNumber}</small>}</div></div><span className={`coverage-chip ${coverageTone}`}><ShieldCheck size={18} aria-hidden="true" />{policyStatus}</span></div>
    {!readOnly && <div className="visit-appointment-strip"><CalendarDays size={22} aria-hidden="true" /><label>Scheduled visit<select value={selectedVisit} disabled={!!busy || !appointments.length} onChange={event => chooseVisit(event.target.value)}>{appointments.length ? appointments.map(visit => <option key={visit.id} value={visit.id}>{visitDate(visit.start)} · {visitTime(visit.start, result?.appointment_context?.timezone)} · {visit.reason || 'Scheduled care'}</option>) : <option value="">{loaded ? 'No scheduled visit returned by eCW' : 'Loading appointments…'}</option>}</select></label><button className="visit-icon-button" aria-label="Refresh visits" disabled={!!busy} onClick={() => void load()}><RefreshCw size={18} className={busy === 'visits' ? 'visit-spin' : ''} /></button></div>}
    {result && <dl className="visit-context-facts">
      <div><dt>Visit type</dt><dd>{result.visit_type === 'new' ? 'New patient' : result.visit_type === 'established' ? 'Established patient' : result.appointment_context?.appointment_type || 'Not returned'}</dd></div>
      <div><dt>Reason</dt><dd>{result.reason || selected?.reason || 'Not returned'}</dd></div>
      <div><dt>Provider</dt><dd>{provider || 'Name not returned by eCW'}</dd></div>
      <div><dt>Insurance</dt><dd>{result.payer || (result.recovery?.candidates?.length === 1 ? `${result.recovery.candidates[0].payer || 'Unknown payer'} · returned policy` : result.recovery?.candidates?.length ? `${result.recovery.candidates.length} policy suggestions` : 'Not returned')}{result.payer && <small>Insurer source: {memberSource(payerSource)}</small>}<small>{planName}</small>{planSource && <small>Plan source: {planSource}</small>}{planType && <small>Plan type: {planType} · inferred from the reported plan description</small>}{blueCross && <small>Member home plan: {blueCard ? 'BlueCard indicated; confirm the issuing state' : 'In-state or out-of-state not established by eCW or Stedi'}</small>}</dd></div>
    </dl>}
    <div className="visit-content" aria-busy={!!busy || recovering}>
      {notice && <p className="visit-notice" role="status">{notice}</p>}{deleteError && <p className="visit-error" role="alert">{deleteError}</p>}
      {patientResolutionNeeded && onResolvePatient && <section className="visit-notice" aria-label="Confirm eCW patient"><h2>Confirm the eCW patient</h2><p>The saved details need review. Search for a matching patient and confirm the name, date of birth and account before checking benefits.</p><button onClick={onResolvePatient}>Find matching eCW patients</button></section>}
      {error && !(patientResolutionNeeded && onResolvePatient) && <p className="visit-notice" role="alert">The estimate could not be updated: {error} {previousEstimate ? 'The saved visit details and estimate are retained below. Reconfirm before collecting.' : 'Check the eCW visit and billing details, then retry the benefits check.'}<button disabled={!!busy} onClick={() => void (selected ? estimate() : load())}>Try again <ArrowRight size={15} /></button></p>}
      {(!result || busy === 'estimate' || busy === 'visits') && <section className="visit-overview" aria-label="Arrival collection"><div className="arrival-total"><span>{amountLabel}</span><strong className="amount-pending" role="status">{amountText}</strong><p>{explanation}</p></div></section>}
      {busy === 'estimate' || busy === 'visits' ? <div className="visit-empty" role="status"><RefreshCw className="visit-spin" size={26} aria-hidden="true" /><h2>{busy === 'visits' ? 'Finding your visit' : 'Putting the estimate together'}</h2><p>Reading the appointment, patient and insurance from eCW. Checking saved eligibility and any current benefits needed.</p></div> : result ? <>
        <section id="visit-estimate" className="visit-overview" aria-labelledby="payment-heading">
          <div className="overview-heading"><h2 id="payment-heading">{cash ? 'Self-pay estimate' : 'Arrival collection'}</h2>{!readOnly && <div className="payment-toggle" aria-label="Payment method"><button aria-pressed={!cash} disabled={!!busy || recovering} onClick={() => { if (cash) void saveReview({ payment_mode: 'insurance' }) }}><ShieldCheck size={16} />Insurance</button><button aria-pressed={cash} disabled={!!busy || recovering} onClick={() => { if (!cash) void saveReview({ payment_mode: 'cash' }) }}><Wallet size={16} />Cash</button></div>}</div>
          <div className="overview-amounts"><div className={`arrival-total${!amount && !calculating && !tooEarly ? ' is-blocked' : ''}`}><span>{amountLabel}</span><strong className={!amount ? 'amount-pending' : undefined} role="status">{amountText}</strong><p>{summaryExplanation}</p>{!cash && amount && !previousEstimate && (amountParts?.copay_cents != null || guidance?.basis === 'office_copay') && <dl className="arrival-money-picture" aria-label="What the arrival amount includes">
              <div><dt>Copay</dt><dd>{amountParts?.copay_cents != null ? money(amountParts.copay_cents) : guidance?.basis === 'office_copay' && guidance.amount_cents != null ? money(guidance.amount_cents) : '—'}</dd></div>
              <div><dt>Deductible</dt><dd>{amountParts?.deductible_cents != null ? money(amountParts.deductible_cents) : officeDeductible === 'DOES_NOT_APPLY' ? 'Does not apply' : officeDeductible === 'ALREADY_MET' ? 'Already met' : 'See assumptions'}</dd></div>
              <div><dt>Coinsurance</dt><dd>{amountParts?.coinsurance_cents != null ? money(amountParts.coinsurance_cents) : 'See assumptions'}</dd></div>
            </dl>}{cash && !amount && !!result.cash_schedule?.packages.length && !readOnly && <button className="cash-package-shortcut" onClick={() => { const detail = document.getElementById('visit-detail-services') as HTMLDetailsElement | null; if (detail) detail.open = true; detail?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}>View {result.cash_schedule.packages.length} clinic cash packages</button>}{result.payment_explanation?.notice && !previousEstimate && <p className="visit-identity-notice">{result.payment_explanation.notice}</p>}
            {guidance?.billing_note && <details><summary>eCW billing note · {visitDate(guidance.billing_note.updated_at)}</summary><blockquote>{guidance.billing_note.text}</blockquote></details>}
            {!cash && <details className="summary-assumptions"><summary>Assumptions used</summary>
              <p>{previousEstimate ? 'Saved result · reconfirm before collection.' : billingInstruction ? 'Follow the recorded billing instruction for this visit.' : deferred ? 'No insurance advance today · bill after the claim.' : amount ? 'Advance estimate for the selected services.' : calculating ? 'Checking applicable benefits…' : tooEarly ? 'Appointment found · check within 7 days.' : 'No amount calculated.'}</p>
              <p>{guidance?.patient_message || 'The final patient share follows the insurer’s processed claim.'}</p>
              {result.encounter_summary && <dl className="arrival-breakdown" aria-label="Patient cost breakdown">
                <div><dt>Copay included</dt><dd>{result.encounter_summary.components.copay_cents == null ? 'Unknown' : money(result.encounter_summary.components.copay_cents)}</dd></div>
                <div><dt>Deductible included</dt><dd>{result.encounter_summary.components.deductible_cents == null ? 'Unknown' : money(result.encounter_summary.components.deductible_cents)}</dd></div>
                <div><dt>Coinsurance included</dt><dd>{result.encounter_summary.components.coinsurance_cents == null ? 'Unknown' : money(result.encounter_summary.components.coinsurance_cents)}</dd></div>
                <div><dt>Prior payments</dt><dd>{result.encounter_summary.payments_verified_cents == null ? 'Not available' : `−${money(result.encounter_summary.payments_verified_cents)}`}</dd></div>
                <div><dt>{result.encounter_summary.conditional ? 'Conditional patient estimate' : 'Full patient estimate'}</dt><dd>{result.encounter_summary.estimated_total_patient_min_cents != null && result.encounter_summary.estimated_total_patient_max_cents != null ? range(result.encounter_summary.estimated_total_patient_min_cents, result.encounter_summary.estimated_total_patient_max_cents) : 'Not yet calculated'}</dd></div>
                <div><dt>Estimated later balance</dt><dd>{result.encounter_summary.estimated_later_balance_cents == null ? 'Unknown' : money(result.encounter_summary.estimated_later_balance_cents)}</dd></div>
              </dl>}
              {!!guidance?.patient_actions.length && <div><b>Tell the patient:</b><ul>{guidance.patient_actions.map(action => <li key={action}>{action}</li>)}</ul></div>}
              {!!guidance?.assumptions.length && <ul>{guidance.assumptions.map(item => <li key={item}>{item}</li>)}</ul>}
            </details>}
          </div><div className="arrival-context"><span className="visit-label">Anticipated service</span><h3>{anticipatedService || result.reason || 'Service not established'}</h3>{result.reason && result.reason !== anticipatedService && <p>eCW visit reason: {result.reason}</p>}<p>{readOnly ? 'Service plan recorded for this appointment.' : 'Based on the scheduled appointment. Modify the services below if the planned care changes.'}</p></div></div>
          <div className="visit-snapshots" aria-label="Coverage summary">
            <div className={coverageTone}><ShieldCheck size={20} aria-hidden="true" /><span>Coverage<strong>{policyStatus}</strong><small>{result?.benefit_source ? `Checked via ${result.benefit_source}` : 'Payer result unavailable'}</small></span></div>
            <div className={networkTone}><Network size={20} aria-hidden="true" /><span>Network<strong>{networkLabel}</strong>{contractCandidate && <small>{contractCandidate} clinic contract</small>}{contractUrl && <small><a href={contractUrl} target="_blank" rel="noopener noreferrer">{result?.network_evidence?.related_document_id ? 'Related payer contract document' : `${contractEvidence?.network_name || 'Payer plan'} contract`}</a></small>}</span></div>
            <RequirementCard title="Referral" evidence={result.requirement_evidence?.referral} icon={<FileText size={20} aria-hidden="true" />} />
            <RequirementCard title="Prior authorization" evidence={result.requirement_evidence?.authorization} icon={<ShieldCheck size={20} aria-hidden="true" />} />
          </div>
          <p className="arrival-sources">Sources: eCW{tooEarly ? ' · Payer check deferred until closer to the visit' : result.checked_at ? ` + ${result.benefit_source || 'payer source not retained'} · benefits checked ${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}` : ' · Current payer benefits unavailable'}</p>
          <div className="overview-plan-name"><ShieldCheck size={16} aria-hidden="true" /><span>{cash ? 'Clinic self-pay schedule' : planName || result.payer || 'Policy details not returned'}</span>{!cash && cashPrice && <span className="cash-comparison">Cash option {cashPrice}</span>}</div>
        </section>
        {emg && !cash && <section className={`visit-overview emg-option${emg.total_min_cents == null ? ' is-unpriced' : ''}`} aria-label="Additional testing">
          <label>{!readOnly && <input type="checkbox" checked={emg.requested || emg.included} disabled={!!busy || recovering || emg.included} onChange={event => void saveReview({ emg_required: event.target.checked })} />} EMG / nerve testing {readOnly ? emg.requested || emg.included ? 'included in estimate' : 'not selected' : 'required'}</label>
          <p>{calculating ? 'Calculating additional testing cost…' : emg.message}</p>
          {(emg.requested || emg.included) && !calculating && !error && <dl className="collection-components">
            <div><dt>Additional estimated patient cost</dt><dd>{emg.additional_min_cents != null && emg.additional_max_cents != null ? range(emg.additional_min_cents, emg.additional_max_cents) : 'Not yet calculated'}</dd></div>
            <div><dt>Planning total with testing</dt><dd>{emg.total_min_cents != null && emg.total_max_cents != null ? range(emg.total_min_cents, emg.total_max_cents) : 'Not yet calculated'}</dd></div>
          </dl>}
          {!readOnly && (emg.requested || emg.included) && <button onClick={() => { const detail = document.getElementById('visit-detail-services') as HTMLDetailsElement; detail.open = true; detail.scrollIntoView({ block: 'start' }) }}>Specify testing scope</button>}
        </section>}
        {result.identity?.status === 'warning' && <p className="visit-identity-notice"><Check size={16} aria-hidden="true" />{result.identity.message}</p>}
        {recovering && <p role="status" className="visit-muted">Checking additional coverage in the background…</p>}
        {readOnly && result.recovery?.state === 'pending' && <p className="visit-muted">An additional coverage search was pending when this version was saved.</p>}
        <VisitServices result={result} />
        <div className="visit-detail-sections">
          <details open={readOnly || undefined} id="visit-detail-services" className="visit-detail-section"><summary>{readOnly ? 'Saved service plan' : 'Possible additional services / modify inputs'}</summary><div className="visit-detail-body">
            <p>{anticipatedService}</p>
            {readOnly ? <ul>{result.codes.map((row, index) => <li key={row.id || index}><strong>CPT {row.code} × {row.units ?? 'Units not retained'}</strong>{row.description && <p>{row.description}</p>}<small>{row.source}{row.modifiers?.length ? ` · Modifiers ${row.modifiers.join(', ')}` : ''}</small></li>)}</ul> : <ServicePlanner key={`${result.check_id}:${result.revision}`} result={result} busy={!!busy || recovering} save={saveReview} viewEstimate={message => { setNotice(message || 'Estimate updated.'); document.getElementById('visit-estimate')?.scrollIntoView({ block: 'start' }) }} />}
          </div></details>
          <details open={readOnly || undefined} id="visit-detail-coverage" className="visit-detail-section"><summary>Detailed benefits, insurance & card</summary><div className="visit-detail-body">
            {!!result.recovery?.payer_routes?.length && <section className="visit-recovery"><h3>Payer suggestions</h3><p>Optional suggestions from the payer directory. A selected route still needs a successful eligibility response.</p>{result.recovery.payer_routes.length >= 2 && <label>Payer suggestions<select value={result.recovery.payer_routes.some(row => row.id === payerSuggestion) ? payerSuggestion : ""} onChange={event => setPayerSuggestion(event.target.value)}><option value="">View a payer suggestion</option>{result.recovery.payer_routes.map(route => <option key={route.id} value={route.id}>{route.name} · {route.payer_id || route.id}</option>)}</select></label>}{result.recovery.payer_routes.filter(route => result.recovery!.payer_routes!.length < 2 || route.id === payerSuggestion).map(route => <article key={route.id}><strong>{route.name}</strong><p>Payer ID: {route.payer_id || route.id} · Stedi route: {route.id}</p>{!!route.names.length && <p>Also known as: {route.names.join(', ')}</p>}{!!route.aliases.length && <p>Payer ID aliases: {route.aliases.join(', ')}</p>}<p>{route.match_basis === 'directory_suggestion' ? 'Suggested name match — review against the card' : 'Matches the recorded payer name or ID'}{route.eligibility === 'ENROLLMENT_REQUIRED' ? ' · Provider enrollment required' : ''}</p>{!readOnly && <button disabled={!!busy || recovering || result.recovery?.state === 'indeterminate'} onClick={() => void selectCoverage(route.id, true)}>Check eligibility with this payer</button>}</article>)}</section>}
            {failed && <section className="visit-recovery"><h3>{recovering ? 'Finding coverage…' : result.cash_fallback ? 'Automatic insurance checks finished' : failure?.title || 'Coverage check incomplete'}</h3><p>{guidance?.reason || unavailableReason}</p>{result.recovery?.message && <p>{result.recovery.message}</p>}{result.recovery && (result.recovery.reason_code || result.recovery.reference) && <small className="visit-muted">{result.recovery.reason_code?.replaceAll('_', ' ')}{result.recovery.reason_code && result.recovery.reference ? ' · ' : ''}{result.recovery.reference ? `Stedi discovery ${result.recovery.reference}` : ''}</small>}</section>}
            {!!result.recovery?.candidates?.length && <section className="visit-recovery" aria-label="Returned insurance candidates"><h3>Returned insurance candidates</h3><p>Returned policies are retained, including inactive policies. Suggestions are optional; automatic estimation does not wait for selection.</p>{result.recovery.candidates.length >= 2 && <label>Insurance policy suggestions<select value={result.recovery.candidates.some(row => row.id === candidateSuggestion) ? candidateSuggestion : ""} onChange={event => setCandidateSuggestion(event.target.value)}><option value="">View a policy suggestion</option>{result.recovery.candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.payer || "Unknown payer"} · {candidate.member_id || "Member ID not returned"} · {candidate.coverage_evidence?.status.toLowerCase() || "Status unknown"}</option>)}</select></label>}{result.recovery.candidates.filter(candidate => result.recovery!.candidates!.length < 2 || candidate.id === candidateSuggestion).map(candidate => <article key={candidate.id}>
              <strong>{candidate.payer || 'Payer not returned'} · {candidate.coverage_evidence && ['INACTIVE', 'EXPIRED', 'FUTURE', 'CONFLICT'].includes(candidate.coverage_evidence.status) ? candidate.coverage_evidence.status.toLowerCase() : candidate.verification_status === 'VERIFIED' ? 'Identity and coverage evidence matched' : 'Coverage not verified'}</strong>
              <p>{candidate.plans.join(' · ')}</p><dl><div><dt>Candidate member ID</dt><dd>{candidate.member_id || 'Not returned'}</dd></div>
              <div><dt>Group</dt><dd>{candidate.group_number || 'Not returned'}</dd></div>{candidate.coverage_evidence && <div><dt>Coverage dates</dt><dd>{candidate.coverage_evidence.start || 'Start not returned'} – {candidate.coverage_evidence.end || 'End not returned'}</dd></div>}<div><dt>Policyholder</dt><dd>{[candidate.subscriber?.firstName, candidate.subscriber?.lastName].filter(Boolean).join(' ') || 'Not returned'}</dd></div>
              <div><dt>Policyholder DOB</dt><dd>{candidate.subscriber?.dateOfBirth || 'Unknown'}</dd></div>
              {candidate.dependent && <div><dt>Dependent</dt><dd>{[candidate.dependent.firstName, candidate.dependent.lastName].filter(Boolean).join(' ')} · DOB {candidate.dependent.dateOfBirth || 'Unknown'}</dd></div>}
              <div><dt>Source</dt><dd>{candidate.source?.replaceAll('_', ' ') || 'Saved insurance discovery'}</dd></div></dl>
              {!!candidate.reason_codes?.length && <ul>{candidate.reason_codes.map(reason => <li key={reason}>{reason.replaceAll('_', ' ').toLowerCase()}</li>)}</ul>}
              {!readOnly && <button disabled={!!busy || recovering || result.recovery?.state === 'indeterminate' || candidate.reason_codes?.some(reason => ['PATIENT_NAME_MISMATCH', 'PATIENT_DOB_MISMATCH', 'PATIENT_DOB_MISSING', 'RELATIONSHIP_CONFLICT'].includes(reason))} onClick={() => void selectCoverage(candidate.id)}>Check this policy</button>}
            </article>)}</section>}
            {!!result.insurance_hints?.length && <section className="visit-recovery"><h3>Recorded insurance clues</h3><p>These details can support an inquiry; they do not verify active coverage.</p>{result.insurance_hints.map((hint, index) => <p key={index}>{hint.payer || 'Payer unknown'} · Member ID {hint.member_id || 'unknown'} · {hint.sources.join(', ')}</p>)}</section>}
            {result.insurance_diagnosis && <section className="visit-recovery" aria-label="Insurance check diagnosis"><h3>Insurance check status</h3>
              <p>Identity: {result.insurance_diagnosis.identityStatus.replaceAll('_', ' ').toLowerCase()} · Eligibility: {result.insurance_diagnosis.eligibilityStatus.replaceAll('_', ' ').toLowerCase()} · Insurance estimate: {result.insurance_diagnosis.pricingStatus.replaceAll('_', ' ').toLowerCase()}</p>
              {result.insurance_diagnosis.rawCandidateCount != null && <p>{result.insurance_diagnosis.rawCandidateCount} returned · {result.insurance_diagnosis.retainedCandidateCount} retained · {result.insurance_diagnosis.verifiedCandidateCount} matched candidates</p>}
              {result.insurance_diagnosis.enrollment && <p>Provider enrollment: {result.insurance_diagnosis.enrollment.status.replaceAll('_', ' ').toLowerCase()}</p>}
              {!!result.insurance_diagnosis.pricingReasons.length && <ul>{result.insurance_diagnosis.pricingReasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
              {result.cash_fallback && <p>{result.cash_fallback.reason}</p>}
            </section>}
            <section className="patient-policy"><div className="policy-relationship"><div><span>Patient</span><strong>{payerPatient?.lastName ? [payerPatient.firstName, payerPatient.lastName].filter(Boolean).join(' ') : patient ? `${patient.given_name} ${patient.family_name}` : 'Not returned'}</strong><small>eCW: {patient?.given_name} {patient?.family_name}</small></div><ArrowRight size={22} aria-hidden="true" /><div><span>Policyholder · {result.insurance_details?.payer_relationship?.toLowerCase() || ({ '18': 'self', '01': 'spouse', '19': 'child', G8: 'other' } as Record<string, string>)[result.insurance_details?.relationship || ''] || 'relationship not returned'}</span><strong>{payerSubscriber?.lastName ? [payerSubscriber.firstName, payerSubscriber.lastName].filter(Boolean).join(' ') : [result.insurance_details?.subscriber_first, result.insurance_details?.subscriber_last].filter(Boolean).join(' ') || 'Not returned'}</strong></div></div>
              <dl><div><dt>Member ID</dt><dd>{result.insurance_details?.member_id || 'Not returned'}{result.insurance_details?.member_id && <small>Source: {memberSource(result.insurance_details.member_source)}</small>}</dd></div>{(result.insurance_details?.group_number || result.plan_details?.stedi.group_number) && <div><dt>Group</dt><dd>{result.insurance_details?.group_number || result.plan_details?.stedi.group_number}<small>Source: {result.insurance_details?.group_number ? memberSource(result.insurance_details.group_source) : result.benefit_source || 'Payer response'}</small></dd></div>}{policyDates?.start && <div><dt>Coverage dates</dt><dd>{policyDates.start}{policyDates.end ? ` – ${policyDates.end}` : ' onward'}</dd></div>}<div><dt>Contact</dt><dd>{result.patient_details?.phone || 'Phone not returned'}<small>{result.patient_details?.email}</small></dd></div><div><dt>Address</dt><dd>{Object.values(result.patient_details?.address || {}).filter(Boolean).join(', ') || 'Not returned'}</dd></div><div><dt>Payer patient DOB</dt><dd>{payerPatient?.dateOfBirth ? visitDate(payerPatient.dateOfBirth) : 'Not returned'}</dd></div></dl>
              {!!result.identity?.differences.length && <p className="visit-muted">Both names are retained with their source. {result.identity.status === 'reconciled' ? 'A verified relationship is on file.' : result.identity.status === 'warning' ? result.identity.message : 'The patient details need reconciliation before collection.'}</p>}
              {intakeId && <InsuranceCards key={intakeId} intakeId={intakeId} background inline savedOnly={readOnly} />}
              {result.card_check && <p className="visit-muted" role="status">{({
                filled_missing: `Card OCR filled missing ${result.card_check.fields_added.map(field => field.replaceAll('_', ' ')).join(', ')} before the payer check.`,
                matched: 'Card OCR matched the available eCW policy details before the payer check.',
                conflict: result.recovery?.outcome === 'recovered' ? 'The payer confirmed the card-based check; eCW was not changed.' : result.recovery?.state === 'pending' ? 'The card and eCW differ. A single card-based payer check is in progress.' : result.card_check.alternative_supported ? 'The card and eCW differ. The card-based check did not confirm this policy; review both sources.' : 'The card and eCW differ. There was not enough matching evidence to send a card-based check.',
                unverified: 'The card text could not be matched to the eCW patient or policy. No card fields were added.',
                not_found: 'No insurance card was available to compare with eCW before the payer check.',
                multiple_cards: 'Multiple card sets need review. No new Stedi check was sent.',
                unavailable: 'The insurance card could not be read before the payer check.',
                read: 'The insurance card was read before the payer check.',
              } as Record<string, string>)[result.card_check.status]}</p>}
              {result.card_check?.payer_comparison && <p className="visit-muted">Insurer comparison: eCW says {result.card_check.payer_comparison.ecw}; card says {result.card_check.payer_comparison.card}. Confirm which route covers this visit.</p>}
            </section><ApplicableBenefits result={result} />
            <AppointmentNotes result={result} />
          </div></details>
          <details open={readOnly || undefined} id="visit-detail-calculation" className="visit-detail-section"><summary>Patient estimate</summary><div className="visit-detail-body"><section>
            <div className="planning-summary"><h3>{cash ? 'Cash price' : emgScenario ? 'Planning total with testing' : price?.conditional ? 'Conditional patient estimate' : 'Estimated patient responsibility'} · {cash ? cashPrice || 'Price unavailable' : emgScenario && emg.total_min_cents != null && emg.total_max_cents != null ? range(emg.total_min_cents, emg.total_max_cents) : deferred || !amount ? 'Not yet calculated' : result.planning_estimate ? money(result.planning_estimate.amount_cents) : patientPrice || 'Applicable advance amount shown above'}</h3>
            {amount && !deferred && !emgScenario && result.planning_estimate && <p>{result.planning_estimate.explanation}</p>}
            {amount && !deferred && !emgScenario && <CostPicture result={result} />}</div><div className="calculation-summary"><PieChart size={24} aria-hidden="true" /><div><h3>{amountLabel} · {amountText}</h3><p>{explanation}</p></div></div>
            {!!guidance?.assumptions.length && <section><h3>Assumptions used</h3><ul>{guidance.assumptions.map(item => <li key={item}>{item}</li>)}</ul></section>}
            {!!result.service_plan?.assumptions?.length && <section><h3>Service-plan assumptions</h3><ul>{result.service_plan.assumptions.map(item => <li key={item}>{item}</li>)}</ul></section>}
            {!cash && !deferred && !emgScenario && price?.complete && <dl className="collection-components">{(['copay_cents', 'deductible_cents', 'coinsurance_cents', 'oop_adjustment_cents'] as const).map(key => <div key={key}><dt>{({ copay_cents: 'Copay applied', deductible_cents: 'Deductible applied', coinsurance_cents: 'Coinsurance applied', oop_adjustment_cents: 'Out-of-pocket adjustment' })[key]}</dt><dd>{price.mode === 'services' ? money((price.lines || []).reduce((sum, line) => sum + (line.calculation?.[key] || 0), 0)) : 'Depends on final visit level'}</dd></div>)}</dl>}
            {calculationReasons.map(reason => <p key={reason}>{reason}</p>)}{emgScenario && <p>The service details above cover the consultation. The additional EMG scenario and combined total are shown above.</p>}
            <p className="visit-muted">The arrival amount is an advance collection. Final patient responsibility follows the insurer’s processed claim.</p>
          </section></div></details>
          <details open={readOnly || undefined} id="visit-detail-sources" className="visit-detail-section" onToggle={event => { if (!readOnly && event.currentTarget.open) { setPanel('sources'); if (history === null && !historyBusy) void loadHistory() } }}><summary>Audit trail & history</summary><div className="visit-detail-body"><section className="visit-provenance"><dl><div><dt>Patient & appointment</dt><dd>eClinicalWorks<small>{result.ecw_retrieved_at && `${visitDate(result.ecw_retrieved_at)} · ${visitTime(result.ecw_retrieved_at)}`}</small></dd></div><div><dt>Payer benefits</dt><dd>{result.benefit_source || 'Not returned'}<small>{result.checked_at ? `${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}` : 'Not returned'}</small></dd></div><div><dt>Clinic contract</dt><dd>{result.network_evidence?.network_name || 'No exact product mapping returned'}</dd></div><div><dt>Cash schedule</dt><dd>{result.cash_schedule?.source || result.practice_cash_source?.filename || 'Not returned'}</dd></div></dl>
            <p>{coverageSource}. {networkSource || 'Provider network source unavailable'}.</p>
            {guidance?.basis === 'office_copay' && copayEvidenceId && <p>Office copay: {copaySource} response, row {copayEvidenceId}. This is a payer benefit, not a Library price.</p>}
            {!!result.library_pricing?.length && <details className="library-diagnostics"><summary>Library pricing sources and extraction diagnostics</summary>{result.library_pricing.map(document => <div key={document.document_id}><a href={`/api/staff/library/${encodeURIComponent(document.document_id)}/text`} target="_blank" rel="noreferrer">{document.label}</a> · {document.summary?.status.replaceAll("_", " ") || "Processing"}{document.summary?.issues?.map((issue, index) => <p key={index}>{issue.locator ? `${issue.locator}: ` : ""}{issue.reason}</p>)}</div>)}</details>}
            {(result.insurance_fallback?.payer || result.insurance_fallback?.member_id) && <p>Missing {result.insurance_fallback.payer && result.insurance_fallback.member_id ? 'payer and member ID' : result.insurance_fallback.payer ? 'payer' : 'member ID'} came from the labeled payment preference in eCW General Notes. Payer verification is checked separately.</p>}
            {policySources.map(source => <p key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> · {source.section}</p>)}
            {patientPrice && !emgScenario && <p>{price?.conditional ? 'Conditional patient share if unresolved requirements are cleared' : 'Estimated patient share after the claim'}: {patientPrice}.</p>}
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
