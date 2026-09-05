import { AlertTriangle, ArrowLeft, Camera, Check, ExternalLink, FileCheck2, LoaderCircle, ShieldCheck, Upload, WalletCards } from 'lucide-react'
import { useState } from 'react'
import {
  confirmInsuranceIntake, confirmInsuranceService, createInsuranceCase, createSelfPayCase, runInsuranceStage,
  suggestInsuranceServices, uploadInsuranceEvidence, type InsuranceCase, type InsuranceStage,
  type ServiceLine,
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
  estimate: { title: 'Pre-visit estimate', detail: 'The result appears automatically after the rate-and-benefits calculation.' },
} as const

const money = (cents: number | null | undefined) => cents == null ? 'Not stated' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
const ncsCodes = new Set(['95907', '95908', '95909', '95910', '95911', '95912', '95913'])
const emgAddOnCodes = new Set(['95885', '95886', '95887'])
const botoxAdminCodes = new Set(['64612', '64615', '64642', '64643'])

const selectionError = (codes: string[]) => {
  const selected = new Set(codes)
  if (codes.filter((code) => code.startsWith('992')).length !== 1) return 'Choose one anticipated office-visit code.'
  const ncs = codes.filter((code) => ncsCodes.has(code))
  if (ncs.length > 1) return 'Choose one NCS range. Codes 95907–95913 are alternatives based on the total number of studies, not separate procedures.'
  if (codes.some((code) => emgAddOnCodes.has(code)) && ncs.length === 0) return 'Add one NCS base code (95907–95913) for the selected same-day EMG add-on.'
  const primaryBotox = codes.filter((code) => botoxAdminCodes.has(code) && code !== '64643')
  if (primaryBotox.length > 1) return 'Choose one primary Botox administration code for the condition being treated.'
  if (selected.has('64643') && !selected.has('64642')) return 'CPT 64643 is an additional-extremity service and requires CPT 64642.'
  if (codes.some((code) => botoxAdminCodes.has(code)) !== selected.has('J0585')) return 'Select both the Botox administration procedure and J0585 for the medicine.'
  return ''
}

const groupServices = (lines: ServiceLine[]) => Object.values(lines.reduce<Record<string, { id: string; name: string; status: 'scheduled' | 'if_elected'; lines: ServiceLine[] }>>((groups, line) => {
  const id = line.service_item_id ?? (line.code.startsWith('992') ? 'office-visit' : `procedure-${line.code.toLowerCase()}`)
  groups[id] ??= { id, name: line.service_item_name ?? (line.code.startsWith('992') ? 'Office visit' : line.description || `Procedure ${line.code}`), status: line.service_status ?? (line.code.startsWith('992') ? 'scheduled' : 'if_elected'), lines: [] }
  groups[id].lines.push(line)
  return groups
}, {})).sort((left, right) => Number(left.status === 'if_elected') - Number(right.status === 'if_elected'))
const dateTime = (value: string) => {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(undefined, /^\d{4}-\d{2}-\d{2}$/.test(value) ? { dateStyle: 'medium' } : { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
}
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
  const chooseSelfPay = async () => {
    setBusy(true); setError(''); setMessage('Saving the patient’s self-pay choice…')
    try { replaceCase(await createSelfPayCase(), 'Self-pay selected. Insurance will not be used for this visit.') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to save the self-pay choice.') }
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
    else if (ncsCodes.has(code)) setSelectedCodes((current) => [...current.filter((item) => !ncsCodes.has(item)), code])
    else if (botoxAdminCodes.has(code) && code !== '64643') setSelectedCodes((current) => [...current.filter((item) => !botoxAdminCodes.has(item) || item === '64643'), code])
    else setSelectedCodes((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code])
  }

  const completed = new Set<InsuranceStage>(insuranceCase?.stage_decisions.map((item) => item.stage) ?? [])
  if (insuranceCase?.ocr.confirmed) completed.add('intake')
  const currentIndex = steps.findIndex((step) => step.id === (insuranceCase?.next_stage ?? 'estimate'))
  const stageFacts = insuranceCase?.facts.filter((fact) => fact.stage === viewStage || (
    viewStage === 'estimate' && ['network', 'benefits'].includes(fact.stage)
  )) ?? []
  const documents = insuranceCase?.documents ?? {}
  const selectedLines = insuranceCase?.service_lines.filter((line) => line.selected) ?? []
  const serviceGroups = groupServices(insuranceCase?.service_lines ?? [])
  const selectedServiceGroups = groupServices(selectedLines)
  const currentSelectionError = selectionError(selectedCodes)

  return <section className="insurance-beta" aria-labelledby="insurance-heading">
    <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to clinical summary</button>
    <header className="insurance-beta-hero">
      {insuranceCase?.payment_path === 'self_pay' ? <div><p className="eyebrow"><WalletCards aria-hidden="true" className="size-4" /> Direct payment · NMCT</p><h1 id="insurance-heading">Patient payment choice</h1><p>The patient’s decision to pay directly is saved in this secure review. No insurance information is required or used.</p></div> : <div><p className="eyebrow"><ShieldCheck aria-hidden="true" className="size-4" /> Evidence-backed beta · NMCT and BCBSTX</p><h1 id="insurance-heading">Verify insurance before the visit</h1><p>Today, every patient-specific answer comes from a file uploaded in this review. If a required file is missing, ClinicForce stops instead of guessing. Each upload below also names the API or connected system that can replace it later.</p></div>}
      <div className="beta-session">{insuranceCase ? <><span>{insuranceCase.payment_path === 'self_pay' ? 'Self-pay selected' : 'Card uploaded'}</span><strong><time dateTime={insuranceCase.created_at}>{dateTime(insuranceCase.created_at)}</time></strong></> : <><span>Secure share session</span><strong>3 hours</strong></>}</div>
    </header>
    {insuranceCase?.payment_path !== 'self_pay' && <div className="insurance-stepper" aria-label="Insurance verification progress">
      {steps.map((step, index) => {
        const available = completed.has(step.id) || step.id === insuranceCase?.next_stage || (!insuranceCase && step.id === 'intake')
        return <button key={step.id} type="button" disabled={!available} aria-current={viewStage === step.id ? 'step' : undefined} onClick={() => available && setViewStage(step.id)}>
          <span>{completed.has(step.id) ? <Check aria-hidden="true" /> : index + 1}</span>{step.label}
        </button>
      })}
    </div>}
    <p className="sr-status" aria-live="polite">{busy ? 'Working.' : error || message}</p>
    <div className="insurance-layout">
      <main className="insurance-stage-card">
        {insuranceCase?.payment_path === 'self_pay' && <>
          <p className="section-kicker">Direct payment</p><h2>Self-pay selected</h2>
          <div className="evidence-summary success"><WalletCards aria-hidden="true" /><div><strong>The patient chose not to use insurance.</strong><p>ClinicForce will not run eligibility, network, authorization, or insurance-benefit calculations for this visit.</p></div></div>
          {!documents.pricing_guide && <><p>Upload the clinic’s current NCS/EMG cash-pay guide to show the available package prices. This is a clinic price document, not insurance evidence.</p><UploadTile title="Current NCS/EMG cash-pay guide" hint="Choose the current NMCT NCS/EMG cash-pay pricing guide PDF." reads="Lists each direct-pay testing package, what it includes, and its current price." future="Clinic policy or document-management API." accept=".pdf,application/pdf" disabled={busy} onFile={(file) => uploadEvidence('pricing_guide', 'NCS/EMG cash-pay guide', file)} /></>}
          {documents.pricing_guide && <CashPrices source={documents.pricing_guide} />}
          <p className="estimate-disclaimer">These are clinic package prices, not insurance allowed amounts. The physician decides the final study from the patient’s symptoms and examination. No insurance claim or insurance estimate has been created.</p>
          <button className="button button-secondary" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to clinical summary</button>
        </>}
        {viewStage === 'intake' && !insuranceCase && <>
          <p className="section-kicker">1 · Intake</p><h2>Read the member card locally</h2>
          <p>Use either button for the same document: a clear image of the patient’s current insurance card. Include both sides because contact and authorization information may be on the back.</p>
          <div className="source-plan"><strong>What ClinicForce reads</strong><p>Member name, member ID, group number, plan details, benefit labels, and payer contact numbers. The file stays local and is not sent to an external AI service.</p><small><b>Future replacement:</b> EHR registration data plus the payer member-eligibility lookup.</small></div>
          <div className="card-actions">
            <label className="button button-primary" aria-disabled={busy}><Upload aria-hidden="true" className="size-4" /> Choose card PDF or image<input type="file" accept=".pdf,.jpg,.jpeg,.png" disabled={busy} onChange={(event) => { void uploadCard(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
            <label className="button button-secondary" aria-disabled={busy}><Camera aria-hidden="true" className="size-4" /> Photograph the card<input type="file" accept="image/*" capture="environment" disabled={busy} onChange={(event) => { void uploadCard(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
          </div>
          <div className="self-pay-choice"><div><strong>Not using insurance?</strong><p>Record that the patient will pay the clinic directly and skip all insurance checks.</p></div><button className="button button-secondary" type="button" disabled={busy} onClick={() => void chooseSelfPay()}><WalletCards aria-hidden="true" className="size-4" /> Continue as self-pay</button></div>
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
          <p className="section-kicker">4 · Anticipated service</p><h2>Build the anticipated service items</h2><p>Enter the visit reason, planned treatment, or exact CPT/HCPCS codes. The appointment email and NCS/EMG guide are optional supporting documents for this example; they do not limit the procedures ClinicForce can review.</p>
          {insuranceCase.next_stage === 'service' && <div className="evidence-upload-grid">
            <UploadTile title="Appointment email thread" hint="Choose a PDF export of the email thread that states new or established patient and the reason for the visit." reads="Fills the visit type, symptoms, condition, and planned test so staff do not need to retype them." future="EHR scheduling API or connected clinic mailbox." accept=".pdf,application/pdf" source={documents.appointment_email} disabled={busy} onFile={(file) => uploadEvidence('appointment_email', 'appointment email', file)} />
            <UploadTile title="NCS/EMG pricing and scope guide" hint="Choose the current NMCT NCS/EMG cash-pay pricing guide PDF." reads="Provides clinic-reviewed study-scope guidance and a cash-price comparison; insured rates still come from the uploaded fee schedule." future="Clinic policy or document-management service—not a payer API." accept=".pdf,application/pdf" source={documents.pricing_guide} disabled={busy} onFile={(file) => uploadEvidence('pricing_guide', 'NCS/EMG guide', file)} />
          </div>}
          {insuranceCase.next_stage === 'service' && <div className="clinical-context-form"><label>Visit type<select value={visitType} onChange={(event) => setVisitType(event.target.value as 'new' | 'established')}><option value="new">New patient</option><option value="established">Established patient</option></select></label><label htmlFor="clinical-context">Condition, symptoms, body area, planned treatment, or exact CPT/HCPCS code<textarea id="clinical-context" value={clinicalContext} onChange={(event) => setClinicalContext(event.target.value)} placeholder="For example: planned procedure 12345" /></label><button className="button button-secondary" type="button" disabled={busy || clinicalContext.trim().length < 3} onClick={() => void suggestServices()}>{insuranceCase.service_lines.length ? 'Update related CPT codes' : 'Show related CPT codes'}</button></div>}
          {insuranceCase.next_stage === 'service' && <div className="source-plan"><strong>How code suggestions are built</strong><p>The visit reason comes from the uploaded appointment or what staff enter. Reviewed local rules may suggest candidates; staff can also enter any exact CPT/HCPCS code. Prices come only from the uploaded fee-schedule CSV, and prior-authorization results come only from the uploaded payer response.</p><small>Unknown procedures stay separate and require clinician review. These are pre-visit candidates, not final coding.</small></div>}
          {insuranceCase.next_stage === 'service' && insuranceCase.service_lines.length > 0 && !insuranceCase.service_lines.some((line) => !line.code.startsWith('992')) && <p className="estimate-disclaimer">No procedure matched the reviewed local rules. ClinicForce has not guessed a procedure; only office-visit candidates are shown.</p>}
          {serviceGroups.length > 0 && <div className="service-items" aria-label="Anticipated service items">{serviceGroups.map((group) => <fieldset className="service-item" key={group.id}><legend><span>{group.name}</span><small>{group.status === 'scheduled' ? 'Scheduled' : 'If elected after examination'}</small></legend><p>{group.status === 'scheduled' ? 'Choose the most likely visit level for estimation. Final coding follows the completed encounter.' : 'Estimate now, but do not collect for this item until the clinician confirms it will be performed.'}</p><div className="service-options">{group.lines.map((line) => {
            const isAlternative = line.code.startsWith('992') || ncsCodes.has(line.code) || (botoxAdminCodes.has(line.code) && line.code !== '64643')
            return <label key={line.code} className={selectedCodes.includes(line.code) ? 'selected' : ''}><input type={isAlternative ? 'radio' : 'checkbox'} name={line.code.startsWith('992') ? 'office-service' : ncsCodes.has(line.code) ? 'ncs-range' : isAlternative ? 'botox-administration' : `service-${line.code}`} value={line.code} disabled={insuranceCase.next_stage !== 'service'} checked={selectedCodes.includes(line.code)} onChange={() => toggleCode(line.code)} /><span><strong>{line.code}</strong>{line.plain_description && <span>{line.plain_description}</span>}{line.recommendation_reason && <small>Why it appears: {line.recommendation_reason}</small>}<small>Uploaded Blue Choice PPO fee schedule: {line.allowed_cents == null ? line.evidence_note : `${money(line.allowed_cents)} · ${line.description}`}</small></span></label>
          })}</div></fieldset>)}</div>}
          {insuranceCase.next_stage === 'service' && currentSelectionError && <p className="service-validation" role="alert"><AlertTriangle aria-hidden="true" /> <span><strong>Fix this combination</strong>{currentSelectionError}</span></p>}
          {insuranceCase.next_stage === 'service' && insuranceCase.service_lines.length > 0 && <button className="button button-primary" disabled={busy || selectedCodes.length === 0 || Boolean(currentSelectionError)} type="button" onClick={() => void confirmService()}>Confirm service items</button>}
        </>}
        {viewStage === 'authorization' && insuranceCase && <>
          <p className="section-kicker">5 · Referral & authorization</p><h2>{stageCopy.authorization.title}</h2><p>Referral and prior authorization are different checks. The uploaded Availity response answers prior authorization only for CPT/HCPCS codes included in that query. The supplied files do not establish this member’s referral rule, so ClinicForce keeps that result in verify status.</p>
          <a className="evidence-link" href="https://www.bcbstx.com/provider/education/education/tools/availity-authorizations" target="_blank" rel="noreferrer">BCBSTX referral and authorization information <ExternalLink aria-hidden="true" /></a>
          <p className="estimate-disclaimer">Future replacement: the Availity Authorizations integration can check requirements electronically. Until then, the uploaded coverage response and official BCBSTX guidance are used.</p>
          <div className="authorization-list">{selectedServiceGroups.map((group) => <article key={group.id}><header><strong>{group.name}</strong><small>{group.status === 'if_elected' ? 'If elected' : 'Scheduled'} · {group.lines.map((line) => line.code).join(' + ')}</small></header><div><span>Referral</span><b>Not established by the uploaded files—verify the member’s plan rule</b></div>{group.lines.map((line) => <div className="authorization-code" key={line.code}><span>CPT/HCPCS {line.code}</span><b>{line.authorization_status === 'not_required' ? 'No prior authorization required in uploaded response' : line.authorization_status === 'required' ? 'Prior authorization required in uploaded response' : 'Not included in uploaded query—verify before scheduling'}</b><small>{line.authorization_reference}</small></div>)}</article>)}</div>
          <FactList facts={stageFacts} />
          {insuranceCase.next_stage === 'authorization' && <button className="button button-primary" type="button" disabled={busy} onClick={() => void advance('authorization')}>{stageCopy.authorization.action}</button>}
        </>}
        {(viewStage === 'benefits' || viewStage === 'estimate') && insuranceCase && insuranceCase.payment_path !== 'self_pay' && <StagePanel stage={viewStage} insuranceCase={insuranceCase} facts={stageFacts} active={insuranceCase.next_stage === viewStage} busy={busy} onAdvance={advance} />}
        {error && <p className="form-error" role="alert">{error}</p>}
        {!error && <p className="insurance-status" role="status">{busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <ShieldCheck className="size-4" aria-hidden="true" />}{message}</p>}
      </main>
    </div>
    {insuranceCase?.appointment && <footer className="insurance-appointment"><strong>{insuranceCase.appointment.appointment_type} · {insuranceCase.appointment.reason}</strong><span>{insuranceCase.appointment.date} · {insuranceCase.appointment.provider} · {insuranceCase.appointment.location}</span></footer>}
    {currentIndex > -1 && <span className="visually-hidden">Current step {currentIndex + 1} of {steps.length}</span>}
  </section>
}

function UploadTile({ title, hint, reads, future, accept, source, disabled, onFile }: { title: string; hint: string; reads: string; future: string; accept: string; source?: NonNullable<InsuranceCase['documents']>[string]; disabled: boolean; onFile: (file?: File) => Promise<void> }) {
  return <label className={`evidence-upload${source ? ' complete' : ''}`}><span className="upload-icon">{source ? <Check aria-hidden="true" /> : <Upload aria-hidden="true" />}</span><span><strong>{source ? `${title} added` : title}</strong><small className="upload-file">{source?.filename ?? hint}</small>{source && <small className="source-time"><b>Source time:</b> <time dateTime={source.observed_at}>{dateTime(source.observed_at)}</time></small>}<small><b>ClinicForce reads:</b> {reads}</small><small><b>Future replacement:</b> {future}</small></span><input type="file" accept={accept} disabled={disabled || Boolean(source)} onChange={(event) => { void onFile(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
}

function FactList({ facts }: { facts: InsuranceCase['facts'] }) {
  if (!facts.length) return null
  return <section className="evidence-reasoning" aria-label="Explanation and sources"><header><FileCheck2 aria-hidden="true" /><div><h3>How ClinicForce reached this</h3><p>Plain-language notes explain why the evidence matters. Each answer names the uploaded file and exact page or section that supports it.</p></div></header><dl className="insurance-facts">{facts.map((fact) => <div key={`${fact.stage}-${fact.label}`}><dt>{fact.label}</dt><dd>{fact.value}{fact.explanation && <p className="fact-explanation">{fact.explanation}</p>}<Citation fact={fact} /></dd></div>)}</dl></section>
}

function Citation({ fact }: { fact: InsuranceCase['facts'][number] }) {
  const isWebsite = fact.citation.reference.startsWith('https://')
  return <small><b>Source:</b> {isWebsite ? <a href={fact.citation.reference} target="_blank" rel="noreferrer">{fact.citation.source_filename} <ExternalLink aria-hidden="true" /></a> : <>{fact.citation.source_filename} · {fact.citation.reference}</>} · <b>Source time:</b> <time dateTime={fact.citation.observed_at}>{dateTime(fact.citation.observed_at)}</time></small>
}

function StagePanel({ stage, insuranceCase, facts, active, busy, onAdvance }: {
  stage: 'benefits' | 'estimate'; insuranceCase: InsuranceCase; facts: InsuranceCase['facts']; active: boolean; busy: boolean
  onAdvance: (stage: Exclude<InsuranceStage, 'intake' | 'service'>) => Promise<void>
}) {
  const copy = stageCopy[stage]
  const estimate = insuranceCase.estimate
  const collectToday = estimate?.collect_today_cents ?? estimate?.patient_cents
  const normalShare = estimate && estimate.estimate_available !== false
    ? (estimate.deductible_applied_cents ?? 0) + (estimate.nominal_coinsurance_cents ?? 0) + (estimate.copay_cents ?? 0)
    : null
  const optionalItems = estimate?.service_item_estimates?.filter((item) => item.status === 'if_elected') ?? []
  const action = estimate?.estimate_available === false ? 'VERIFY BEFORE COLLECTING'
    : collectToday === 0 ? 'DO NOT COLLECT TODAY'
      : `COLLECT ${money(collectToday)} TODAY`
  const network = facts.find((fact) => fact.label === 'Patient network result')?.value ?? 'Network result documented in the evidence below'
  return <><p className="section-kicker">{steps.findIndex((step) => step.id === stage) + 1} · {steps.find((step) => step.id === stage)?.label}</p><h2>{copy.title}</h2><p>{copy.detail}</p>
    {stage === 'estimate' && estimate && <>
      <section className={`collection-decision${estimate.estimate_available === false ? ' verify' : ''}`} aria-labelledby="collection-decision-heading"><div><span id="collection-decision-heading">Collect today</span><strong>{estimate.estimate_available === false ? 'Not estimated' : money(collectToday)}</strong><p>{estimate.estimate_available === false ? <AlertTriangle aria-hidden="true" /> : <Check aria-hidden="true" />} {action}</p></div><dl><div><dt>Why</dt><dd>{estimate.estimate_available === false ? estimate.unavailable_reason : estimate.individual_oop_remaining_cents === 0 ? 'Patient has $0.00 remaining individual out-of-pocket responsibility.' : `Current benefits estimate the patient responsibility at ${money(collectToday)}.`}</dd></div><div><dt>Expected insurance if all performed</dt><dd>{estimate.estimate_available === false ? 'Not estimated' : money(estimate.plan_cents)}</dd></div><div><dt>Anticipated services</dt><dd>{estimate.service_codes?.join(' + ')}</dd></div><div><dt>Estimate confidence</dt><dd className="confidence-value">{estimate.confidence?.toUpperCase() ?? 'MEDIUM'}<small>{estimate.confidence_reason}</small></dd></div></dl><small>Calculated <time dateTime={estimate.calculated_at}>{dateTime(estimate.calculated_at)}</time></small></section>
      {optionalItems.map((item) => <section className="elected-estimate" key={item.id}><div><span>If elected</span><strong>{item.name}</strong><small>{item.codes.join(' + ')} · Confirm after the clinician’s examination</small></div><div><span>Collect only if performed</span><strong>{item.estimate_available ? money(item.patient_cents) : 'Verify amount'}</strong><small>{item.estimate_available ? `Separate scenario using current benefits · plan ${money(item.plan_cents)}` : 'Amount not available'}</small></div></section>)}
      {estimate.estimate_available !== false && <details className="estimate-details"><summary>See calculation</summary><div className="estimate-calculation"><p>Combined scenario if every selected service is performed; “Collect today” includes scheduled items only.</p><dl><div><dt>Contracted / allowed amount</dt><dd>{money(estimate.allowed_cents)}</dd></div><div><dt>Deductible applied</dt><dd>{money(estimate.deductible_applied_cents)}</dd></div><div><dt>Normal patient share before OOP limit</dt><dd>{money(normalShare)}</dd></div><div><dt>Normal coinsurance</dt><dd>{money(estimate.nominal_coinsurance_cents)}</dd></div><div><dt>OOP adjustment</dt><dd>{estimate.oop_cap_adjustment_cents == null ? 'Not applied' : money(estimate.oop_cap_adjustment_cents)}</dd></div><div><dt>Patient estimate if all are performed</dt><dd>{money(estimate.patient_cents)}</dd></div><div className="calculation-total"><dt>Collect from patient now</dt><dd>{money(collectToday)}</dd></div><div><dt>Estimated plan responsibility if all are performed</dt><dd>{money(estimate.plan_cents)}</dd></div></dl>{estimate.line_items && estimate.line_items.length > 0 && <div className="estimate-lines"><h3>Allowed amount by CPT / HCPCS</h3><dl>{estimate.line_items.map((line) => <div key={line.code}><dt><strong>{line.code}</strong><span>{line.description}</span></dt><dd>{money(line.allowed_cents)}</dd></div>)}</dl></div>}</div></details>}
      {estimate.plain_explanation && <details className="estimate-details"><summary>Why ClinicForce recommends {money(collectToday)}</summary><ol className="decision-reasons"><li><strong>Network:</strong> {network}.</li><li><strong>Reviewed price:</strong> The anticipated services total {money(estimate.allowed_cents)}.</li><li><strong>Normal cost share:</strong> Before the out-of-pocket limit, the calculated patient share is {money(normalShare)}.</li><li><strong>Benefit-limit adjustment:</strong> The uploaded response shows {money(estimate.individual_oop_remaining_cents)} individual out-of-pocket remaining.</li><li><strong>Recommendation:</strong> {estimate.plain_explanation}</li></ol></details>}
      <section className="estimate-certainty" aria-labelledby="estimate-certainty-heading"><h3 id="estimate-certainty-heading">What we know—and what could change</h3><div><div><strong>Known before the visit</strong><ul><li>{network}</li><li>{money(estimate.individual_deductible_remaining_cents)} individual deductible remaining</li><li>{money(estimate.individual_oop_remaining_cents)} individual OOP remaining</li><li>{estimate.service_codes?.join(' + ')} anticipated</li></ul></div><div><strong>Could change after the visit</strong><ul><li>Final CPT / HCPCS codes and units</li><li>Whether “if elected” procedures are performed</li><li>Medical-necessity and authorization review</li><li>Claim adjudication and coordination of benefits</li></ul></div></div></section>
      <details className="estimate-details evidence-details"><summary>View source evidence</summary><FactList facts={facts} /></details>
    </>}
    {stage === 'benefits' && <FactList facts={facts} />}
    {stage === 'benefits' && estimate && estimate.estimate_available !== false && <p className="calculation-line">{money(estimate.allowed_cents)} allowed − {money(estimate.patient_cents)} estimated patient responsibility = {money(estimate.plan_cents)} estimated plan responsibility. Individual deductible remaining: {money(estimate.individual_deductible_remaining_cents)}. Individual out-of-pocket remaining: {money(estimate.individual_oop_remaining_cents)}.</p>}
    {stage === 'estimate' && insuranceCase.documents?.pricing_guide && <CashPrices source={insuranceCase.documents.pricing_guide} clinicalContext={insuranceCase.clinical_context} />}
    {stage === 'estimate' && estimate && <p className="estimate-disclaimer">{estimate.disclaimer}</p>}
    {active && stage === 'benefits' && <button className="button button-primary" type="button" disabled={busy} onClick={() => void onAdvance(stage)}>{stageCopy.benefits.action}</button>}
  </>
}

function CashPrices({ source, clinicalContext = '' }: { source: NonNullable<InsuranceCase['documents']>[string]; clinicalContext?: string }) {
  const packages = source.findings?.cash_packages ?? []
  if (!packages.length) return null
  const context = clinicalContext.toLowerCase()
  const match = packages.find((item) =>
    ((context.includes('both feet') || context.includes('both legs')) && item.name.includes('Both Legs')) ||
    (context.includes('both hands') && item.name.includes('Both Hands')) ||
    ((context.includes('one hand') || context.includes('carpal tunnel')) && item.name.includes('One Hand')) ||
    (context.includes('neck') && context.includes('arm') && item.name.includes('Neck-Related')) ||
    (context.includes('back') && context.includes('leg') && item.name.includes('Back-Related')) ||
    ((context.includes('widespread') || context.includes('generalized')) && item.name.includes('Comprehensive')) ||
    (context.includes('single nerve') && item.name.includes('Single Nerve'))
  )
  return <section className="cash-prices" aria-labelledby="cash-prices-heading"><div><p className="section-kicker">Self-pay comparison</p><h3 id="cash-prices-heading">{match ? 'Closest NCS/EMG package for the described symptoms' : 'Current NCS/EMG cash-pay packages'}</h3><p>{match ? `${match.name}: ${money(match.price_cents)}.` : 'Choose the package that best matches the body area and symptoms.'} The guide says each package includes a focused evaluation, nerve-conduction studies, needle EMG when clinically indicated, interpretation, report, and results discussion.</p></div><dl>{packages.map((item) => <div key={item.name} className={item === match ? 'recommended' : undefined}><dt><strong>{item.name}</strong><span>{item.best_for}</span></dt><dd>{money(item.price_cents)}</dd></div>)}</dl><small><b>Source:</b> {source.filename} · page 1 · <b>Source time:</b> <time dateTime={source.observed_at}>{dateTime(source.observed_at)}</time></small></section>
}
