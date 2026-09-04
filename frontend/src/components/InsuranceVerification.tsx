import { ArrowLeft, Camera, Check, ExternalLink, FileCheck2, LoaderCircle, ShieldCheck, Upload } from 'lucide-react'
import { useState } from 'react'
import {
  confirmInsuranceIntake, confirmInsuranceService, createInsuranceCase, runInsuranceStage,
  suggestInsuranceServices, uploadInsuranceEvidence, type InsuranceCase, type InsuranceStage,
} from '../services/api'

interface Props { onBack: () => void }
const steps: Array<{ id: InsuranceStage; label: string }> = [
  { id: 'intake', label: 'Intake' }, { id: 'eligibility', label: 'Eligibility' },
  { id: 'network', label: 'Network & coverage' }, { id: 'service', label: 'Anticipated service' },
  { id: 'authorization', label: 'Referral & authorization' },
  { id: 'benefits', label: 'Rate & benefits' }, { id: 'estimate', label: 'Estimate' },
]
const stageCopy = {
  eligibility: { title: 'The insurance card is valid', action: 'Save details and check network' },
  network: { title: 'Match the patient’s plan to the clinic', action: 'Continue to anticipated service' },
  authorization: { title: 'Check every selected service', action: 'Confirm requirements' },
  benefits: { title: 'Contracted rate and benefit arithmetic', detail: 'Apply the uploaded fee-schedule rates and the patient’s individual benefit balances.', action: 'Calculate estimate' },
  estimate: { title: 'Pre-visit estimate', detail: 'Show the collection amount first, then the complete evidence-backed explanation.', action: 'Complete estimate' },
} as const

const money = (cents: number | null | undefined) => cents == null ? 'Not stated' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
const cardDetailLabels: Array<[keyof InsuranceCase['ocr'], string]> = [
  ['plan_type', 'Plan type'], ['coverage_date', 'Coverage date'], ['coinsurance', 'Coinsurance shown on card'],
  ['in_network_deductible', 'In-network deductible'], ['out_of_network_deductible', 'Out-of-network deductible'],
  ['in_network_oop_maximum', 'In-network out-of-pocket maximum'], ['out_of_network_oop_maximum', 'Out-of-network out-of-pocket maximum'],
  ['rx_bin', 'RxBIN'], ['rx_pcn', 'RxPCN'], ['rx_group', 'RxGRP'],
  ['preauth_medical_phone', 'Preauthorization—medical'], ['customer_service_phone', 'Customer service'],
  ['provider_service_phone', 'Provider service'], ['pharmacy_phone', 'Pharmacy'],
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
    setSelectedCodes(next.service_lines.filter((line) => line.selected).map((line) => line.code))
    if (next.clinical_context) setClinicalContext(next.clinical_context)
    if (next.visit_type) setVisitType(next.visit_type)
    setInsuranceCase(next); setViewStage(next.next_stage ?? 'estimate'); setMessage(success); setError('')
  }
  const uploadCard = async (file?: File) => {
    if (!file) return
    setBusy(true); setError(''); setMessage('Reading the insurance card locally…')
    try {
      const next = await createInsuranceCase(file)
      setDraft(next.ocr); replaceCase(next, 'Card fields are ready for review.')
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to read the card.') }
    finally { setBusy(false) }
  }
  const uploadEvidence = async (kind: string, label: string, file?: File) => {
    if (!insuranceCase || !file) return
    setBusy(true); setError(''); setMessage(`Reading ${label} locally…`)
    try { replaceCase(await uploadInsuranceEvidence(insuranceCase.id, kind, file), `${label} read and saved to this review.`) }
    catch (caught) { setError(caught instanceof Error ? caught.message : `Unable to read ${label}.`) }
    finally { setBusy(false) }
  }
  const confirmIntake = async () => {
    if (!insuranceCase) return
    setBusy(true); setError('')
    try { replaceCase(await confirmInsuranceIntake(insuranceCase.id, { ...draft, confirmed: false }), 'The card member matches the patient opened from the QR.') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The card could not be matched.') }
    finally { setBusy(false) }
  }
  const suggestServices = async () => {
    if (!insuranceCase) return
    setBusy(true); setError('')
    try { replaceCase(await suggestInsuranceServices(insuranceCase.id, clinicalContext, visitType), 'Related CPT candidates are ready for clinician review.') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to suggest CPT candidates.') }
    finally { setBusy(false) }
  }
  const confirmService = async () => {
    if (!insuranceCase) return
    setBusy(true); setError('')
    try { replaceCase(await confirmInsuranceService(insuranceCase.id, selectedCodes), `CPT ${selectedCodes.join(', ')} saved as anticipated services.`) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to confirm the service.') }
    finally { setBusy(false) }
  }
  const advance = async (stage: Exclude<InsuranceStage, 'intake' | 'service'>) => {
    if (!insuranceCase) return
    setBusy(true); setError('')
    try { replaceCase(await runInsuranceStage(insuranceCase.id, stage), `${stageCopy[stage].title} confirmed.`) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to complete this step.') }
    finally { setBusy(false) }
  }
  const toggleCode = (code: string) => {
    if (code.startsWith('992')) setSelectedCodes((current) => [...current.filter((item) => !item.startsWith('992')), code])
    else setSelectedCodes((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code])
  }

  const completed = new Set<InsuranceStage>(insuranceCase?.stage_decisions.map((item) => item.stage) ?? [])
  if (insuranceCase?.ocr.confirmed) completed.add('intake')
  const currentIndex = steps.findIndex((step) => step.id === (insuranceCase?.next_stage ?? 'estimate'))
  const stageFacts = insuranceCase?.facts.filter((fact) => fact.stage === viewStage) ?? []
  const documents = insuranceCase?.documents ?? {}
  const selectedLines = insuranceCase?.service_lines.filter((line) => line.selected) ?? []

  return <section className="insurance-beta" aria-labelledby="insurance-heading">
    <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to clinical summary</button>
    <header className="insurance-beta-hero">
      <div><p className="eyebrow"><ShieldCheck aria-hidden="true" className="size-4" /> Evidence-backed beta · NMCT and BCBSTX</p><h1 id="insurance-heading">Verify insurance before the visit</h1><p>Today, every patient-specific answer comes from a file uploaded in this review. If a required file is missing, ClinicForce stops instead of guessing. Each upload below also names the API or connected system that can replace it later.</p></div>
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
          <p>Use either button for the same document: a clear image of the patient’s current insurance card. Include both sides because contact and authorization information may be on the back.</p>
          <div className="source-plan"><strong>What ClinicForce reads</strong><p>Member name, member ID, group number, plan details, benefit labels, and payer contact numbers. The file stays local and is not sent to an external AI service.</p><small><b>Future replacement:</b> EHR registration data plus the payer member-eligibility lookup.</small></div>
          <div className="card-actions">
            <label className="button button-primary"><Upload aria-hidden="true" className="size-4" /> Choose card PDF or image<input type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={(event) => { void uploadCard(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
            <label className="button button-secondary"><Camera aria-hidden="true" className="size-4" /> Photograph the card<input type="file" accept="image/*" capture="environment" onChange={(event) => { void uploadCard(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
          </div>
        </>}
        {viewStage === 'intake' && insuranceCase && <>
          <p className="section-kicker">1 · Intake</p><h2>Confirm the details read from the card</h2><p>Compare these values with the card. The member name must match the patient opened from the QR before these details can be used.</p>
          <div className="ocr-grid"><label>Member name<input value={draft.member_name} readOnly /></label><label>Member ID<input value={draft.member_id} readOnly /></label><label>Group number<input value={draft.group_number} readOnly /></label></div>
          {cardDetailLabels.some(([key]) => insuranceCase.ocr[key]) && <dl className="insurance-facts card-details">{cardDetailLabels.map(([key, label]) => insuranceCase.ocr[key] && <div key={key}><dt>{label}</dt><dd>{insuranceCase.ocr[key]}</dd></div>)}</dl>}
          {insuranceCase.next_stage === 'intake' && <button className="button button-primary" type="button" disabled={busy} onClick={() => void confirmIntake()}>{busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <FileCheck2 className="size-4" aria-hidden="true" />} Confirm card details</button>}
        </>}
        {viewStage === 'eligibility' && insuranceCase && <>
          <p className="section-kicker">2 · Eligibility</p><h2>{stageCopy.eligibility.title}</h2>
          <div className="evidence-summary success"><ShieldCheck aria-hidden="true" /><div><strong>The card belongs to this patient.</strong><p>ClinicForce matched the member name to the patient opened from the QR. The member ID, group number, and plan details are saved inside this short-lived review and will be used to match the next documents.</p></div></div>
          <p className="estimate-disclaimer">A valid card identifies the plan; it does not prove active coverage, network status, or the amount the patient will owe. Those answers come from the next documents.</p>
          {insuranceCase.next_stage === 'eligibility' && <button className="button button-primary" type="button" disabled={busy} onClick={() => void advance('eligibility')}>{stageCopy.eligibility.action}</button>}
          <FactList facts={stageFacts} />
        </>}
        {viewStage === 'network' && insuranceCase && <>
          <p className="section-kicker">3 · Network & coverage</p><h2>{stageCopy.network.title}</h2>
          <p>First upload the clinic’s signed BCBSTX contract, then the patient’s dated Availity eligibility and benefits response. ClinicForce needs both: the contract proves the clinic participates, while Availity connects this patient and plan to that network and supplies current benefit balances.</p>
          <div className="evidence-upload-grid">
            <UploadTile title="Provider’s BCBSTX contract" hint="Choose the signed NMCT–BCBSTX Professional Provider Agreement PDF." reads="Proves NMCT’s Blue Choice PPO participation and identifies the contract section that controls the fee schedule." future="BCBSTX provider-directory and contract-management integration." accept=".pdf,application/pdf" source={documents.contract} disabled={busy || insuranceCase.next_stage !== 'network'} onFile={(file) => uploadEvidence('contract', 'provider contract', file)} />
            {documents.contract && <UploadTile title="Patient’s Availity coverage details" hint="Choose the Availity Eligibility and Benefits Result PDF for this patient and date of service." reads="Confirms active coverage, plan, network, deductible and out-of-pocket balances, plus authorization results for queried CPT codes." future="Availity eligibility API using the HIPAA X12 270/271 transaction." accept=".pdf,application/pdf" source={documents.coverage} disabled={busy || insuranceCase.next_stage !== 'network'} onFile={(file) => uploadEvidence('coverage', 'Availity coverage details', file)} />}
          </div>
          {documents.contract && documents.coverage && <>
            <div className="evidence-summary"><FileCheck2 aria-hidden="true" /><div><strong>Network and benefits evidence matched</strong><p>The findings below keep the contract and patient benefit claims separate. In particular, “100%” on page 2 means the individual limits are fully met—not that the contract promises 100% coverage for every service.</p></div></div>
            <FactList facts={stageFacts} />
            <h3 className="upload-next-heading">Add the clinic’s contracted prices</h3><p>Upload the Availity fee schedule CSV. It provides the Blue Choice PPO allowed amount for each CPT code; the signed contract says the provider accepts the lesser of billed charges or the current fee schedule, less patient cost share.</p>
            <UploadTile title="Blue Choice PPO fee schedule" hint="Choose the Availity Fee Schedule Listing export as a CSV—not a screenshot or PDF." reads="Looks up the contracted allowed amount for each suggested CPT code." future="BCBSTX or Availity fee-schedule API." accept=".csv,text/csv" source={documents.fee_schedule} disabled={busy || insuranceCase.next_stage !== 'network'} onFile={(file) => uploadEvidence('fee_schedule', 'fee schedule', file)} />
          </>}
          {insuranceCase.next_stage === 'network' && <button className="button button-primary" type="button" disabled={busy || !documents.contract || !documents.coverage || !documents.fee_schedule} onClick={() => void advance('network')}>{stageCopy.network.action}</button>}
          <p className="estimate-disclaimer">Until those integrations are enabled, all three files are required and remain the source of the result.</p>
        </>}
        {viewStage === 'service' && insuranceCase && <>
          <p className="section-kicker">4 · Anticipated service</p><h2>Tell us why the patient is coming</h2><p>Upload the appointment email to fill in the visit type and reason, or enter them manually. Upload the clinic’s NCS/EMG guide so staff can compare the suggested study scope with the clinic’s patient-facing guidance.</p>
          {insuranceCase.next_stage === 'service' && <div className="evidence-upload-grid">
            <UploadTile title="Appointment email thread" hint="Choose a PDF export of the email thread that states new or established patient and the reason for the visit." reads="Fills the visit type, symptoms, condition, and planned test so staff do not need to retype them." future="EHR scheduling API or connected clinic mailbox." accept=".pdf,application/pdf" source={documents.appointment_email} disabled={busy} onFile={(file) => uploadEvidence('appointment_email', 'appointment email', file)} />
            <UploadTile title="NCS/EMG pricing and scope guide" hint="Choose the current NMCT NCS/EMG cash-pay pricing guide PDF." reads="Provides clinic-reviewed study-scope guidance and a cash-price comparison; insured rates still come from the uploaded fee schedule." future="Clinic policy or document-management service—not a payer API." accept=".pdf,application/pdf" source={documents.pricing_guide} disabled={busy} onFile={(file) => uploadEvidence('pricing_guide', 'NCS/EMG guide', file)} />
          </div>}
          {insuranceCase.next_stage === 'service' && <div className="clinical-context-form"><label>Visit type<select value={visitType} onChange={(event) => setVisitType(event.target.value as 'new' | 'established')}><option value="new">New patient</option><option value="established">Established patient</option></select></label><label htmlFor="clinical-context">Condition, symptoms, body area, planned test, or treatment<textarea id="clinical-context" value={clinicalContext} onChange={(event) => setClinicalContext(event.target.value)} placeholder="For example: numbness and tingling in both feet; neuropathy evaluation" /></label><button className="button button-secondary" type="button" disabled={busy || clinicalContext.trim().length < 3 || !documents.pricing_guide} onClick={() => void suggestServices()}>{insuranceCase.service_lines.length ? 'Update related CPT codes' : 'Show related CPT codes'}</button>{!documents.pricing_guide && <small>Upload the NCS/EMG guide before generating suggestions.</small>}</div>}
          {insuranceCase.next_stage === 'service' && <div className="source-plan"><strong>How code suggestions are built</strong><p>The visit reason comes from the uploaded email or what staff enter. Related CPT candidates and plain-language explanations come from the clinic’s internal conversational-research.md. Prices come only from the uploaded fee-schedule CSV, and prior-authorization results come only from the uploaded Availity coverage PDF.</p><small>These are pre-visit candidates for clinician review, not final coding.</small></div>}
          {insuranceCase.next_stage === 'service' && insuranceCase.service_lines.length > 0 && !insuranceCase.service_lines.some((line) => !line.code.startsWith('992')) && <p className="estimate-disclaimer">No procedure matched the reviewed local rules. ClinicForce has not guessed a procedure; only office-visit candidates are shown.</p>}
          <fieldset className="service-options"><legend>Choose anticipated CPT candidates</legend>{insuranceCase.service_lines.map((line) => <label key={line.code} className={selectedCodes.includes(line.code) ? 'selected' : ''}><input type={line.code.startsWith('992') ? 'radio' : 'checkbox'} name={line.code.startsWith('992') ? 'office-service' : `service-${line.code}`} value={line.code} disabled={insuranceCase.next_stage !== 'service'} checked={selectedCodes.includes(line.code)} onChange={() => toggleCode(line.code)} /><span><strong>{line.code}</strong>{line.plain_description && <span>{line.plain_description}</span>}{line.recommendation_reason && <small>Why it appears: {line.recommendation_reason}</small>}<small>Uploaded Blue Choice PPO fee schedule: {line.allowed_cents == null ? line.evidence_note : `${money(line.allowed_cents)} · ${line.description}`}</small></span></label>)}</fieldset>
          {insuranceCase.next_stage === 'service' && insuranceCase.service_lines.length > 0 && <button className="button button-primary" disabled={busy || selectedCodes.length === 0} type="button" onClick={() => void confirmService()}>Confirm {selectedCodes.join(', ')}</button>}
        </>}
        {viewStage === 'authorization' && insuranceCase && <>
          <p className="section-kicker">5 · Referral & authorization</p><h2>{stageCopy.authorization.title}</h2><p>Referral and prior authorization are different checks. ClinicForce matches the plan with its internal payer-plan rule, while the patient’s uploaded Availity response answers prior authorization only for CPT codes included in that query. The referral source shown here is the official BCBSTX website—not the clinic workbook.</p>
          <a className="evidence-link" href="https://www.bcbstx.com/provider/education/education/tools/availity-authorizations" target="_blank" rel="noreferrer">BCBSTX referral and authorization information <ExternalLink aria-hidden="true" /></a>
          <p className="estimate-disclaimer">Future replacement: the Availity Authorizations integration can check requirements electronically. Until then, the uploaded coverage response and official BCBSTX guidance are used.</p>
          <div className="authorization-list">{selectedLines.map((line) => <article key={line.code}><strong>CPT {line.code}</strong><div><span>Referral</span><b>Not required for this BCBSTX PPO plan</b></div><div><span>Prior authorization</span><b>{line.authorization_status === 'not_required' ? 'No authorization required' : line.authorization_status === 'required' ? 'Authorization required' : 'Not included in the uploaded query—verify before scheduling'}</b></div><small>{line.authorization_reference}</small></article>)}</div>
          <FactList facts={stageFacts} />
          {insuranceCase.next_stage === 'authorization' && <button className="button button-primary" type="button" disabled={busy} onClick={() => void advance('authorization')}>{stageCopy.authorization.action}</button>}
        </>}
        {(viewStage === 'benefits' || viewStage === 'estimate') && insuranceCase && <StagePanel stage={viewStage} insuranceCase={insuranceCase} facts={stageFacts} active={insuranceCase.next_stage === viewStage} busy={busy} onAdvance={advance} />}
        {error && <p className="form-error" role="alert">{error}</p>}
        {!error && <p className="insurance-status" role="status">{busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <ShieldCheck className="size-4" aria-hidden="true" />}{message}</p>}
      </main>
    </div>
    {insuranceCase?.appointment && <footer className="insurance-appointment"><strong>{insuranceCase.appointment.appointment_type} · {insuranceCase.appointment.reason}</strong><span>{insuranceCase.appointment.date} · {insuranceCase.appointment.provider} · {insuranceCase.appointment.location}</span></footer>}
    {currentIndex > -1 && <span className="visually-hidden">Current step {currentIndex + 1} of {steps.length}</span>}
  </section>
}

function UploadTile({ title, hint, reads, future, accept, source, disabled, onFile }: { title: string; hint: string; reads: string; future: string; accept: string; source?: NonNullable<InsuranceCase['documents']>[string]; disabled: boolean; onFile: (file?: File) => Promise<void> }) {
  return <label className={`evidence-upload${source ? ' complete' : ''}`}><span className="upload-icon">{source ? <Check aria-hidden="true" /> : <Upload aria-hidden="true" />}</span><span><strong>{source ? `${title} added` : title}</strong><small className="upload-file">{source?.filename ?? hint}</small><small><b>ClinicForce reads:</b> {reads}</small><small><b>Future replacement:</b> {future}</small></span><input type="file" accept={accept} disabled={disabled || Boolean(source)} onChange={(event) => { void onFile(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
}

function FactList({ facts }: { facts: InsuranceCase['facts'] }) {
  if (!facts.length) return null
  return <section className="evidence-reasoning" aria-label="Explanation and sources"><header><FileCheck2 aria-hidden="true" /><div><h3>How ClinicForce reached this</h3><p>Each answer names the uploaded file and exact page or section that supports it.</p></div></header><dl className="insurance-facts">{facts.map((fact) => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}<Citation fact={fact} /></dd></div>)}</dl></section>
}

function Citation({ fact }: { fact: InsuranceCase['facts'][number] }) {
  const isWebsite = fact.citation.reference.startsWith('https://')
  return <small><b>Source:</b> {isWebsite ? <a href={fact.citation.reference} target="_blank" rel="noreferrer">{fact.citation.source_filename} <ExternalLink aria-hidden="true" /></a> : <>{fact.citation.source_filename} · {fact.citation.reference}</>}</small>
}

function StagePanel({ stage, insuranceCase, facts, active, busy, onAdvance }: {
  stage: 'benefits' | 'estimate'; insuranceCase: InsuranceCase; facts: InsuranceCase['facts']; active: boolean; busy: boolean
  onAdvance: (stage: Exclude<InsuranceStage, 'intake' | 'service'>) => Promise<void>
}) {
  const copy = stageCopy[stage]
  const estimate = insuranceCase.estimate
  return <><p className="section-kicker">{steps.findIndex((step) => step.id === stage) + 1} · {steps.find((step) => step.id === stage)?.label}</p><h2>{copy.title}</h2><p>{copy.detail}</p>
    {stage === 'estimate' && estimate && <div className="estimate-lead"><span>Collect now</span><strong>{estimate.estimate_available === false ? 'Not estimated' : money(estimate.patient_cents)}</strong><p>{estimate.estimate_available === false ? estimate.unavailable_reason : `Estimated plan responsibility ${money(estimate.plan_cents)}`}</p><small>Calculated {new Date(estimate.calculated_at).toLocaleString()}</small></div>}
    <FactList facts={facts} />
    {stage === 'benefits' && estimate && estimate.estimate_available !== false && <p className="calculation-line">{money(estimate.allowed_cents)} allowed − {money(estimate.patient_cents)} estimated patient responsibility = {money(estimate.plan_cents)} estimated plan responsibility. Individual deductible remaining: {money(estimate.individual_deductible_remaining_cents)}. Individual out-of-pocket remaining: {money(estimate.individual_oop_remaining_cents)}.</p>}
    {stage === 'estimate' && estimate && <p className="estimate-disclaimer">{estimate.disclaimer}</p>}
    {active && <button className="button button-primary" type="button" disabled={busy} onClick={() => void onAdvance(stage)}>{copy.action}</button>}
  </>
}
