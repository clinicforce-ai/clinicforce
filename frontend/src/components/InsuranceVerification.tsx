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

export default function InsuranceVerification({ onBack }: Props) {
  const [insuranceCase, setInsuranceCase] = useState<InsuranceCase | null>(null)
  const [draft, setDraft] = useState({ member_name: '', member_id: '', group_number: '' })
  const [selectedCodes, setSelectedCodes] = useState<string[]>(['99204'])
  const [clinicalContext, setClinicalContext] = useState('')
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
    try { replaceCase(await suggestInsuranceServices(insuranceCase.id, clinicalContext), 'CPT candidates refreshed for clinician review.') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to suggest CPT candidates.') }
    finally { setBusy(false) }
  }
  const uploadEvidence = async (files: File[]) => {
    if (!insuranceCase || files.length === 0) return
    setBusy(true); setError(''); setMessage('Reading the insurance PDFs locally…')
    try {
      replaceCase(await uploadInsuranceEvidence(insuranceCase.id, files), 'Insurance documents read and hashed.')
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to verify the insurance documents.') }
    finally { setBusy(false) }
  }
  const confirmEvidence = async () => {
    if (!insuranceCase) return
    setBusy(true); setError('')
    try {
      replaceCase(await confirmAnitaInsuranceEvidence(insuranceCase.id), 'Anita’s benefits and NMCT contract were read and calculated.')
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to confirm the evidence.') }
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
      <div><p className="eyebrow"><ShieldCheck aria-hidden="true" className="size-4" /> Evidence-backed beta · NMCT and BCBSTX</p><h1 id="insurance-heading">Verify insurance before the visit</h1><p>ClinicForce requires the card, patient eligibility response, and provider agreement for every demo patient. No payer API or AI coding service is called.</p></div>
      <div className="beta-session"><span>Secure share session</span><strong>15 minutes</strong></div>
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
            <label className="button button-primary"><Upload aria-hidden="true" className="size-4" /> Upload card<input type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={(event) => void uploadCard(event.target.files?.[0])} /></label>
            <label className="button button-secondary"><Camera aria-hidden="true" className="size-4" /> Capture live<input type="file" accept="image/*" capture="environment" onChange={(event) => void uploadCard(event.target.files?.[0])} /></label>
          </div>
        </>}
        {viewStage === 'intake' && insuranceCase && <>
          <p className="section-kicker">1 · Intake</p><h2>Confirm OCR fields</h2><p>Compare these values with the card before unlocking the reviewed evidence.</p>
          <div className="ocr-grid">
            <label>Member name<input value={draft.member_name} onChange={(event) => setDraft({ ...draft, member_name: event.target.value })} /></label>
            <label>Member ID<input value={draft.member_id} onChange={(event) => setDraft({ ...draft, member_id: event.target.value })} /></label>
            <label>Group number<input value={draft.group_number} onChange={(event) => setDraft({ ...draft, group_number: event.target.value })} /></label>
          </div>
          {insuranceCase.next_stage === 'intake' && <button className="button button-primary" type="button" disabled={busy} onClick={() => void confirmIntake()}>{busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <FileCheck2 className="size-4" aria-hidden="true" />} Confirm card and appointment</button>}
        </>}
        {viewStage === 'eligibility' && insuranceCase?.source_mode === 'documents' && insuranceCase.next_stage === 'eligibility' && <>
          <p className="section-kicker">2 · Eligibility</p><h2>Match this patient to the provider contract</h2>
          <p>Upload Anita’s dated BCBS eligibility response and NMCT–BCBS provider contract together. ClinicForce reads both files, identifies the plan, network, deductible, out-of-pocket amount, and applicable benefit—there is nothing to type.</p>
          <label className="button button-primary"><Upload aria-hidden="true" className="size-4" /> Upload insurance documents<input type="file" accept=".pdf,application/pdf" multiple disabled={busy} onChange={(event) => void uploadEvidence(Array.from(event.target.files ?? []))} /></label>
          {(insuranceCase.documents?.eligibility || insuranceCase.documents?.contract) && <div className="insurance-facts">
            <div><dt>Benefits response</dt><dd>{insuranceCase.documents?.eligibility?.filename ?? 'Still needed'}</dd></div>
            <div><dt>Provider contract</dt><dd>{insuranceCase.documents?.contract?.filename ?? 'Still needed'}</dd></div>
            {insuranceCase.documents?.eligibility?.findings?.suggested_coverage && <><div><dt>Plan</dt><dd>{insuranceCase.documents.eligibility.findings.suggested_coverage.plan_name ?? 'Not found'}</dd></div><div><dt>Network</dt><dd>{insuranceCase.documents.eligibility.findings.suggested_coverage.network_status === 'in_network' ? 'In network' : 'Needs review'}</dd></div><div><dt>Patient responsibility</dt><dd>$0.00 after Anita’s $0.00 OOP remaining</dd></div></>}
          </div>}
          <p className="estimate-disclaimer">The contract establishes NMCT’s rate and participation. Anita’s dated eligibility response establishes her benefits; this demo calculates $0.00 because it documents no remaining individual out-of-pocket amount.</p>
          {insuranceCase.next_stage === 'eligibility' && <button className="button button-primary" type="button" disabled={busy || !insuranceCase.documents?.eligibility || !insuranceCase.documents?.contract} onClick={() => void confirmEvidence()}>Read documents and continue</button>}
        </>}
        {viewStage === 'service' && insuranceCase && <>
          <p className="section-kicker">4 · Anticipated service</p><h2>Clinician confirmation required</h2><p>Enter why the patient is being seen today. ClinicForce returns deterministic, reviewable candidates from the NMCT fee list; they are never final post-encounter coding. Neuropathy suggests the reviewed EMG/NCS codes. Sonography suggestions require the body area and a reviewed NMCT rate before pricing.</p>
          {insuranceCase.next_stage === 'service' && <div className="clinical-context-form"><label htmlFor="clinical-context">Conditions or symptoms for this visit<textarea id="clinical-context" value={clinicalContext} onChange={(event) => setClinicalContext(event.target.value)} placeholder="e.g. burning, numbness and tingling in both feet" /></label><button className="button button-secondary" type="button" disabled={busy || clinicalContext.trim().length < 3} onClick={() => void suggestServices()}>{insuranceCase.service_lines.length ? 'Update CPT suggestions' : 'Suggest CPT codes'}</button></div>}
          <fieldset className="service-options"><legend>Choose anticipated CPT candidates</legend>{insuranceCase.service_lines.map((line) => <label key={line.code} className={selectedCodes.includes(line.code) ? 'selected' : ''}><input type={line.code.startsWith('992') ? 'radio' : 'checkbox'} name={line.code.startsWith('992') ? 'office-service' : `service-${line.code}`} value={line.code} disabled={insuranceCase.next_stage !== 'service'} checked={selectedCodes.includes(line.code)} onChange={() => toggleCode(line.code)} /><span><strong>{line.code}</strong>{line.description}<small>{line.allowed_cents == null ? line.evidence_note : `Reviewed Blue Choice PPO amount ${money(line.allowed_cents)}`}{line.suggested_by ? ` · Suggested by ${line.suggested_by.toLowerCase()}` : ''}</small></span></label>)}</fieldset>
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
