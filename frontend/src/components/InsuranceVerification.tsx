import { ArrowLeft, Camera, Check, FileCheck2, LoaderCircle, ShieldCheck, Upload } from 'lucide-react'
import { useState } from 'react'
import {
  confirmAnitaInsuranceEvidence, confirmInsuranceIntake, confirmInsuranceService, createInsuranceCase,
  runInsuranceStage, suggestInsuranceServices, uploadInsuranceEvidence,
  type InsuranceCase, type InsuranceStage,
} from '../services/api'

interface Props { onBack: () => void }
const steps: Array<{ id: InsuranceStage; label: string }> = [
  { id: 'intake', label: 'Intake' }, { id: 'eligibility', label: 'Eligibility' },
  { id: 'network', label: 'Network' }, { id: 'service', label: 'Anticipated service' },
  { id: 'authorization', label: 'Referral & authorization' },
  { id: 'benefits', label: 'Rate & benefits' }, { id: 'estimate', label: 'Estimate' },
]
const stageCopy: Record<Exclude<InsuranceStage, 'intake' | 'service'>, { title: string; detail: string; action: string }> = {
  eligibility: { title: 'Active medical coverage', detail: 'Review the dated medical benefit response and the individual—not family—accumulators.', action: 'Confirm eligibility' },
  network: { title: 'Provider and location match', detail: 'Network status comes from the provider query, agreement, and payer matrix—not from the member card.', action: 'Confirm network' },
  authorization: { title: 'Separate referral and authorization checks', detail: 'The PPO referral rule and CPT-specific authorization response are independently sourced.', action: 'Confirm requirements' },
  benefits: { title: 'Contracted rate and benefit arithmetic', detail: 'Apply the selected fee-schedule rate, nominal coinsurance, and individual out-of-pocket cap.', action: 'Calculate estimate' },
  estimate: { title: 'Pre-visit estimate', detail: 'Show the collection amount first, then the complete evidence-backed explanation.', action: 'Complete estimate' },
}

const money = (cents: number | null | undefined) => cents == null ? 'Not available' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
const cardDetailLabels: Array<[keyof InsuranceCase['ocr'], string]> = [
  ['plan_type', 'Plan type'], ['coverage_date', 'Coverage date'], ['coinsurance', 'Coinsurance'],
  ['in_network_deductible', 'In-network deductible'], ['out_of_network_deductible', 'Out-of-network deductible'],
  ['in_network_oop_maximum', 'In-network out-of-pocket maximum'], ['out_of_network_oop_maximum', 'Out-of-network out-of-pocket maximum'],
  ['rx_bin', 'RxBIN'], ['rx_pcn', 'RxPCN'], ['rx_group', 'RxGRP'],
  ['preauth_medical_phone', 'Preauth—medical'], ['mdlive_phone', 'MDLive'], ['nurseline_phone', '24/7 Nurseline'],
  ['customer_service_phone', 'Customer service'], ['provider_service_phone', 'Provider service'],
  ['benefit_advocate_phone', 'Benefit advocate'], ['eap_phone', 'EAP'], ['pharmacy_phone', 'Pharmacy'],
]

export default function InsuranceVerification({ onBack }: Props) {
  const [insuranceCase, setInsuranceCase] = useState<InsuranceCase | null>(null)
  const [draft, setDraft] = useState({ member_name: '', member_id: '', group_number: '' })
  const [selectedCodes, setSelectedCodes] = useState<string[]>([])
  const [clinicalContext, setClinicalContext] = useState('')
  const [visitType, setVisitType] = useState<'new' | 'established'>('new')
  const [viewStage, setViewStage] = useState<InsuranceStage>('intake')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('Upload or capture the patient’s insurance card to begin.')
  const [error, setError] = useState('')

  const replaceCase = (next: InsuranceCase, success: string) => {
    const chosen = next.service_lines.filter((line) => line.selected).map((line) => line.code)
    if (chosen.length) setSelectedCodes(chosen)
    if (next.clinical_context) setClinicalContext(next.clinical_context)
    setInsuranceCase(next); setViewStage(next.next_stage ?? 'estimate'); setMessage(success); setError('')
  }
  const uploadCard = async (file?: File) => {
    if (!file) return
    setBusy(true); setError(''); setMessage('Reading the insurance card locally…')
    try {
      const next = await createInsuranceCase(file)
      setDraft(next.ocr); replaceCase(next, 'Card fields are ready for clinician review.')
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to read the card.') }
    finally { setBusy(false) }
  }
  const confirmIntake = async () => {
    if (!insuranceCase) return
    setBusy(true); setError('')
    try { replaceCase(await confirmInsuranceIntake(insuranceCase.id, { ...draft, confirmed: false }), 'QR patient and card member name matched.') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The card could not be matched.') }
    finally { setBusy(false) }
  }
  const confirmService = async () => {
    if (!insuranceCase) return
    setBusy(true); setError('')
    try { replaceCase(await confirmInsuranceService(insuranceCase.id, selectedCodes), `CPT ${selectedCodes.join(', ')} confirmed as anticipated.`) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to confirm the service.') }
    finally { setBusy(false) }
  }
  const suggestServices = async () => {
    if (!insuranceCase) return
    setBusy(true); setError('')
    try { replaceCase(await suggestInsuranceServices(insuranceCase.id, clinicalContext, visitType), 'CPT candidates refreshed for clinician review.') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to suggest CPT candidates.') }
    finally { setBusy(false) }
  }
  const uploadEvidence = async (file?: File) => {
    if (!insuranceCase || !file) return
    setBusy(true); setError(''); setMessage('Reading the NMCT–BCBS provider contract locally…')
    try {
      await uploadInsuranceEvidence(insuranceCase.id, file)
      replaceCase(await confirmAnitaInsuranceEvidence(insuranceCase.id), 'Provider contract matched; Anita’s demo benefits are ready.')
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to verify the insurance documents.') }
    finally { setBusy(false) }
  }
  const toggleCode = (code: string) => {
    if (code.startsWith('992')) setSelectedCodes((current) => [...current.filter((item) => !item.startsWith('992')), code])
    else setSelectedCodes((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code])
  }
  const advance = async (stage: Exclude<InsuranceStage, 'intake' | 'service'>) => {
    if (!insuranceCase) return
    setBusy(true); setError('')
    try { replaceCase(await runInsuranceStage(insuranceCase.id, stage), `${stageCopy[stage].title} confirmed.`) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to complete this stage.') }
    finally { setBusy(false) }
  }

  const completed = new Set<InsuranceStage>(insuranceCase?.stage_decisions.map((item) => item.stage) ?? [])
  if (insuranceCase?.ocr.confirmed) completed.add('intake')
  const currentIndex = steps.findIndex((step) => step.id === (insuranceCase?.next_stage ?? 'estimate'))
  const stageFacts = insuranceCase?.facts.filter((fact) => fact.stage === viewStage) ?? []
  const ledgerFacts = viewStage === 'estimate' ? insuranceCase?.facts ?? [] : stageFacts

  return <section className="insurance-beta" aria-labelledby="insurance-heading">
    <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to clinical summary</button>
    <header className="insurance-beta-hero">
      <div><p className="eyebrow"><ShieldCheck aria-hidden="true" className="size-4" /> Evidence-backed beta · NMCT and BCBSTX</p><h1 id="insurance-heading">Verify insurance before the visit</h1><p>Upload the card and NMCT–BCBS provider contract. Anita’s reviewed demo eligibility snapshot supplies benefits until the payer API is connected.</p></div>
      <div className="beta-session"><span>Secure share session</span><strong>3 hours</strong></div>
    </header>
    <div className="insurance-stepper" aria-label="Insurance verification progress">
      {steps.map((step, index) => {
        const available = completed.has(step.id) || step.id === insuranceCase?.next_stage || (!insuranceCase && step.id === 'intake')
        return <button key={step.id} type="button" disabled={!available} aria-current={viewStage === step.id ? 'step' : undefined} onClick={() => available && setViewStage(step.id)}>
          <span>{completed.has(step.id) ? <Check aria-hidden="true" /> : index + 1}</span>{step.label}
        </button>
      })}
    </div>
    <p className="sr-status" aria-live="polite">{busy ? 'Working.' : error || message}</p>
    <div className="insurance-layout">
      <main className="insurance-stage-card">
        {viewStage === 'intake' && !insuranceCase && <>
          <p className="section-kicker">1 · Intake</p><h2>Read the member card locally</h2>
          <p>PDF, JPG, and PNG cards are OCR’d inside ClinicForce. Images and extracted text are not sent to an external AI service.</p>
          <div className="card-actions">
            <label className="button button-primary"><Upload aria-hidden="true" className="size-4" /> Upload card<input type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={(event) => { void uploadCard(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
            <label className="button button-secondary"><Camera aria-hidden="true" className="size-4" /> Capture live<input type="file" accept="image/*" capture="environment" onChange={(event) => { void uploadCard(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
          </div>
        </>}
        {viewStage === 'intake' && insuranceCase && <>
          <p className="section-kicker">1 · Intake</p><h2>Confirm OCR fields</h2><p>Compare these values with the card before unlocking the reviewed evidence.</p>
          <div className="ocr-grid">
            <label>Member name<input value={draft.member_name} readOnly /></label>
            <label>Member ID<input value={draft.member_id} readOnly /></label>
            <label>Group number<input value={draft.group_number} readOnly /></label>
          </div>
          {cardDetailLabels.some(([key]) => insuranceCase.ocr[key]) && <dl className="insurance-facts card-details">{cardDetailLabels.map(([key, label]) => insuranceCase.ocr[key] && <div key={key}><dt>{label}</dt><dd>{insuranceCase.ocr[key]}</dd></div>)}</dl>}
          {insuranceCase.next_stage === 'intake' && <button className="button button-primary" type="button" disabled={busy} onClick={() => void confirmIntake()}>{busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <FileCheck2 className="size-4" aria-hidden="true" />} Confirm card and appointment</button>}
        </>}
        {viewStage === 'eligibility' && insuranceCase?.source_mode === 'documents' && insuranceCase.next_stage === 'eligibility' && <>
          <p className="section-kicker">2 · Eligibility</p><h2>Match this patient to the provider contract</h2>
          <p>Upload the NMCT–BCBS provider contract. Anita’s reviewed eligibility snapshot is already part of this no-API demo, so the doctor does not need an Availity PDF or any benefit values.</p>
          <label className={insuranceCase.documents?.contract ? 'evidence-upload complete' : 'button button-primary'}><Upload aria-hidden="true" className="size-4" /> Upload NMCT–BCBS provider contract<input type="file" accept=".pdf,application/pdf" disabled={busy || Boolean(insuranceCase.documents?.contract)} onChange={(event) => void uploadEvidence(event.target.files?.[0])} /></label>
          {insuranceCase.documents?.contract && <div className="insurance-facts"><div><dt>Provider contract</dt><dd>{insuranceCase.documents.contract.filename}</dd></div><div><dt>Anita’s demo eligibility</dt><dd>Active · in network · deductible and OOP met</dd></div><div><dt>Patient responsibility</dt><dd>$0.00 after $0.00 OOP remaining</dd></div></div>}
          <p className="estimate-disclaimer">For Anita only, ClinicForce uses the reviewed demo eligibility snapshot. For any other patient, live eligibility data requires the payer API.</p>
        </>}
        {viewStage === 'service' && insuranceCase && <>
          <p className="section-kicker">4 · Anticipated service</p><h2>Clinician confirmation required</h2><p>Enter the condition, symptom, body area, planned test, or treatment. ClinicForce returns reviewable CPT candidates from the local demo catalog and NMCT fee list; they are never final post-encounter coding.</p>
          {insuranceCase.next_stage === 'service' && <div className="clinical-context-form"><label>Visit type<select value={visitType} onChange={(event) => setVisitType(event.target.value as 'new' | 'established')}><option value="new">New patient</option><option value="established">Established patient</option></select></label><label htmlFor="clinical-context">Condition, symptom, body area, test, or treatment<textarea id="clinical-context" value={clinicalContext} onChange={(event) => setClinicalContext(event.target.value)} placeholder="Describe why the patient is being seen and any planned test or treatment" /></label><button className="button button-secondary" type="button" disabled={busy || clinicalContext.trim().length < 3} onClick={() => void suggestServices()}>{insuranceCase.service_lines.length ? 'Update CPT suggestions' : 'Suggest CPT codes'}</button></div>}
          {insuranceCase.next_stage === 'service' && insuranceCase.service_lines.length > 0 && !insuranceCase.service_lines.some((line) => !line.code.startsWith('992')) && <p className="estimate-disclaimer">No procedure candidate matched the local demo catalog. ClinicForce has not guessed a code; select the visit level only or add the service to the reviewed catalog.</p>}
          <fieldset className="service-options"><legend>Choose anticipated CPT candidates</legend>{insuranceCase.service_lines.map((line) => <label key={line.code} className={selectedCodes.includes(line.code) ? 'selected' : ''}><input type={line.code.startsWith('992') ? 'radio' : 'checkbox'} name={line.code.startsWith('992') ? 'office-service' : `service-${line.code}`} value={line.code} disabled={insuranceCase.next_stage !== 'service'} checked={selectedCodes.includes(line.code)} onChange={() => toggleCode(line.code)} /><span><strong>{line.code}</strong>{line.plain_description && <span>What this code is for: {line.plain_description}</span>}<small>NMCT fee schedule: {line.description} · {line.allowed_cents == null ? line.evidence_note : `Reviewed Blue Choice PPO amount ${money(line.allowed_cents)}`}{line.suggested_by ? ` · Suggested by ${line.suggested_by.toLowerCase()}` : ''}</small></span></label>)}</fieldset>
          {insuranceCase.next_stage === 'service' && insuranceCase.service_lines.length > 0 && <button className="button button-primary" disabled={busy || selectedCodes.length === 0} type="button" onClick={() => void confirmService()}>Confirm {selectedCodes.join(', ')}</button>}
        </>}
        {viewStage !== 'intake' && viewStage !== 'service' && !(viewStage === 'eligibility' && insuranceCase?.source_mode === 'documents' && insuranceCase.next_stage === 'eligibility') && insuranceCase && <StagePanel stage={viewStage} insuranceCase={insuranceCase} facts={stageFacts} active={insuranceCase.next_stage === viewStage} busy={busy} onAdvance={advance} />}
        {error && <p className="form-error" role="alert">{error}</p>}
        {!error && <p className="insurance-status" role="status">{busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <ShieldCheck className="size-4" aria-hidden="true" />}{message}</p>}
      </main>
      <aside className="evidence-ledger" aria-label="Evidence ledger"><p className="section-kicker">Evidence ledger</p><h2>Every fact has a source</h2>
        {!insuranceCase?.ocr.confirmed && <p>Reviewed evidence stays locked until the patient and card identities match.</p>}
        {ledgerFacts.map((fact) => <article key={`${fact.stage}-${fact.label}-${fact.value}`}><span>{fact.label}</span><strong>{fact.value}</strong><small>{fact.citation.source_filename} · {fact.citation.reference} · {fact.citation.observed_at}</small><code>{fact.citation.sha256.slice(0, 12)}…</code></article>)}
        {insuranceCase?.ocr.confirmed && ledgerFacts.length === 0 && insuranceCase.sources.map((source) => <article key={source.id}><span>Uploaded evidence</span><strong>{source.filename}</strong><small>Received {source.observed_at}</small><code>{source.sha256.slice(0, 12)}…</code></article>)}
        {insuranceCase?.ocr.confirmed && ledgerFacts.length === 0 && insuranceCase.sources.length === 0 && <p>This completed stage contains clinician confirmation only; select another completed step to review its cited facts.</p>}
      </aside>
    </div>
    {insuranceCase?.appointment && <footer className="insurance-appointment"><strong>{insuranceCase.appointment.appointment_type} · {insuranceCase.appointment.reason}</strong><span>{insuranceCase.appointment.date} · {insuranceCase.appointment.provider} · {insuranceCase.appointment.location}</span></footer>}
    {currentIndex > -1 && <span className="visually-hidden">Current step {currentIndex + 1} of {steps.length}</span>}
  </section>
}

function StagePanel({ stage, insuranceCase, facts, active, busy, onAdvance }: {
  stage: Exclude<InsuranceStage, 'intake' | 'service'>; insuranceCase: InsuranceCase
  facts: InsuranceCase['facts']; active: boolean; busy: boolean
  onAdvance: (stage: Exclude<InsuranceStage, 'intake' | 'service'>) => Promise<void>
}) {
  const copy = stageCopy[stage]
  const estimate = insuranceCase.estimate
  return <><p className="section-kicker">{steps.findIndex((step) => step.id === stage) + 1} · {steps.find((step) => step.id === stage)?.label}</p><h2>{copy.title}</h2><p>{copy.detail}</p>
    {stage === 'estimate' && estimate && <div className="estimate-lead"><span>Collect now</span><strong>{estimate.estimate_available === false ? 'Not estimated' : money(estimate.patient_cents)}</strong><p>{estimate.estimate_available === false ? estimate.unavailable_reason : `Estimated plan responsibility ${money(estimate.plan_cents)}`}</p><small>Calculated {new Date(estimate.calculated_at).toLocaleString()}</small></div>}
    <dl className="insurance-facts">{facts.map((fact) => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}<small>{fact.citation.reference}</small></dd></div>)}</dl>
    {stage === 'benefits' && estimate && estimate.estimate_available !== false && <p className="calculation-line">{insuranceCase.source_mode === 'golden'
      ? `${money(estimate.allowed_cents)} × 20% = ${money(estimate.nominal_coinsurance_cents)}; OOP cap adjustment −${money(Math.abs(estimate.oop_cap_adjustment_cents ?? 0))} = ${money(estimate.patient_cents)}.`
      : `${money(estimate.allowed_cents)} allowed; ${money(estimate.deductible_applied_cents ?? 0)} deductible + ${money(estimate.nominal_coinsurance_cents)} coinsurance + ${money(estimate.copay_cents ?? 0)} copay; OOP adjustment ${money(estimate.oop_cap_adjustment_cents)} = ${money(estimate.patient_cents)} patient.`}</p>}
    {stage === 'estimate' && estimate && <p className="estimate-disclaimer">{estimate.disclaimer}</p>}
    {active && <button className="button button-primary" type="button" disabled={busy} onClick={() => void onAdvance(stage)}>{copy.action}</button>}
  </>
}
