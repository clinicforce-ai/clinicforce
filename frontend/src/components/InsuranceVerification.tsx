import { AlertTriangle, ArrowLeft, Check, FileCheck2, Files, ShieldCheck, Upload } from 'lucide-react'
import { useState } from 'react'
import {
  confirmInsuranceIntake, confirmInsuranceService, createInsuranceCase, runInsuranceStage,
  suggestInsuranceServices, uploadInsuranceEvidence, type InsuranceCase, type InsuranceStage,
} from '../services/api'

interface Props { onBack: () => void }

const steps: Array<{ id: InsuranceStage; label: string }> = [
  { id: 'intake', label: 'Card PDF' }, { id: 'eligibility', label: 'Card review' },
  { id: 'network', label: 'Coverage and rates' }, { id: 'service', label: 'Procedures' },
  { id: 'authorization', label: 'Authorization' }, { id: 'benefits', label: 'Estimate' },
]

const money = (cents: number | null | undefined) => cents == null ? 'Not stated' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
const dateTime = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
const label = (key: string) => key.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase())
const authorizationLabel = (status: string | undefined) => status === 'not_required' ? 'Not required' : status === 'required' ? 'Required' : 'Not stated in the uploaded coverage PDF'

export default function InsuranceVerification({ onBack }: Props) {
  const [insuranceCase, setInsuranceCase] = useState<InsuranceCase | null>(null)
  const [draft, setDraft] = useState({ member_name: '', member_id: '', group_number: '' })
  const [selectedCodes, setSelectedCodes] = useState<string[]>([])
  const [clinicalContext, setClinicalContext] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('Upload the patient’s insurance-card PDF to begin.')
  const [error, setError] = useState('')

  const replaceCase = (next: InsuranceCase, success: string) => {
    setInsuranceCase(next)
    setDraft(next.ocr)
    setClinicalContext(next.clinical_context ?? '')
    setSelectedCodes(next.service_lines.filter(line => line.selected).map(line => line.code))
    setMessage(success)
    setError('')
  }
  const perform = async (work: () => Promise<InsuranceCase>, success: string) => {
    setBusy(true); setError('')
    try { replaceCase(await work(), success) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The uploaded document could not be processed.') }
    finally { setBusy(false) }
  }
  const uploadCard = (file?: File) => {
    if (!file) return
    setMessage('Reading the insurance-card PDF locally…')
    void perform(() => createInsuranceCase(file), 'Card fields read from the uploaded PDF are ready for review.')
  }
  const uploadEvidence = (kind: 'coverage' | 'fee_schedule' | 'clinical', file?: File) => {
    if (!insuranceCase || !file) return
    setMessage(`Reading ${file.name} locally…`)
    void perform(() => uploadInsuranceEvidence(insuranceCase.id, kind, file), `${file.name} was read and saved to this review.`)
  }
  const confirmCard = () => insuranceCase && void perform(
    () => confirmInsuranceIntake(insuranceCase.id, { ...draft, confirmed: false }),
    'Card fields confirmed from the uploaded PDF.',
  )
  const advance = (stage: Exclude<InsuranceStage, 'intake' | 'service'>) => insuranceCase && void perform(
    () => runInsuranceStage(insuranceCase.id, stage), 'Uploaded-document review completed.',
  )
  const confirmServices = () => insuranceCase && void perform(
    () => confirmInsuranceService(insuranceCase.id, selectedCodes), 'CPT/HCPCS codes confirmed for this review.',
  )
  const requestSuggestions = () => insuranceCase && void perform(
    () => suggestInsuranceServices(insuranceCase.id, clinicalContext), 'CPT/HCPCS suggestions are ready for clinician review.',
  )
  const toggleCode = (code: string) => setSelectedCodes(current => current.includes(code) ? current.filter(value => value !== code) : [...current, code])

  const complete = insuranceCase?.status === 'complete'
  const stage = insuranceCase?.next_stage ?? 'intake'
  const facts = insuranceCase?.facts ?? []
  const documents = insuranceCase?.documents ?? {}
  const estimate = insuranceCase?.estimate
  const completed = new Set(insuranceCase?.stage_decisions.map(item => item.stage) ?? [])
  if (insuranceCase?.ocr.confirmed) completed.add('intake')

  return <section className="insurance-beta" aria-labelledby="insurance-heading">
    <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to clinical summary</button>
    <header className="insurance-beta-hero">
      <div><p className="eyebrow"><ShieldCheck aria-hidden="true" className="size-4" /> Document-based insurance review</p><h1 id="insurance-heading">Review uploaded insurance documents</h1><p>Benefits and rates come from uploaded PDFs and CSVs. CPT suggestions use the patient’s entered condition or clinical PDF and always require clinician confirmation.</p></div>
      <div className="beta-session"><span><i aria-hidden="true" /> {insuranceCase ? 'Review in progress' : 'Secure review'}</span><strong>{insuranceCase ? dateTime(insuranceCase.created_at) : 'One patient'}</strong><small>Session expires in 24 hours</small></div>
    </header>
    <nav className="insurance-stepper" aria-label="Insurance verification progress">{steps.map((item, index) => <div key={item.id} className={completed.has(item.id) ? 'complete' : item.id === stage ? 'active' : ''}><span>{completed.has(item.id) ? <Check aria-hidden="true" /> : index + 1}</span><strong>{item.label}</strong></div>)}</nav>
    <p className="sr-status" aria-live="polite">{busy ? 'Working.' : error || message}</p>
    <main className="insurance-stage-card">
      {error && <p className="form-error" role="alert"><AlertTriangle aria-hidden="true" className="size-4" /> {error}</p>}
      {stage === 'intake' && !insuranceCase && <>
        <p className="section-kicker">1 · Card PDF</p><h2>Upload the insurance card</h2><p>Upload a PDF containing the card. ClinicForce reads the PDF locally and displays only extracted fields.</p>
        <label className="button button-primary" aria-disabled={busy}><Upload aria-hidden="true" className="size-4" /> Choose card PDF<input type="file" accept="application/pdf,.pdf" disabled={busy} onChange={event => { uploadCard(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
      </>}
      {stage === 'intake' && insuranceCase && !complete && <>
        <p className="section-kicker">1 · Card PDF</p><h2>Fields read from the card PDF</h2><p>Confirm only what is present in the uploaded card.</p>
        <dl className="insurance-facts card-details">{Object.entries(insuranceCase.ocr).filter(([key, value]) => key !== 'confirmed' && value).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{String(value)}</dd></div>)}</dl>
        <button className="button button-primary" type="button" disabled={busy} onClick={confirmCard}><FileCheck2 aria-hidden="true" className="size-4" /> Confirm extracted card fields</button>
      </>}
      {stage === 'eligibility' && <>
        <p className="section-kicker">2 · Card review</p><h2>Card PDF confirmed</h2><FactList facts={facts.filter(fact => fact.stage === 'intake')} />
        <button className="button button-primary" type="button" disabled={busy} onClick={() => advance('eligibility')}>Continue to uploaded coverage</button>
      </>}
      {stage === 'network' && insuranceCase && <>
        <p className="section-kicker">3 · Coverage and rates</p><h2>Upload source documents</h2><p>The coverage PDF supplies benefit values and any CPT/HCPCS entries it contains. The rate CSV supplies CPT/HCPCS codes and their amounts.</p>
        <div className="evidence-upload-grid">
          <UploadTile title="Coverage PDF" hint="A searchable coverage/benefit PDF for this member." accept="application/pdf,.pdf" source={documents.coverage} busy={busy} onFile={file => uploadEvidence('coverage', file)} />
          <UploadTile title="Rate CSV" hint="A CSV with procedure-code and amount columns." accept="text/csv,.csv" source={documents.fee_schedule} busy={busy} onFile={file => uploadEvidence('fee_schedule', file)} />
        </div>
        <FactList facts={facts.filter(fact => fact.stage === 'network')} />
        <button className="button button-primary" type="button" disabled={busy || !documents.coverage || !documents.fee_schedule} onClick={() => advance('network')}>Read CPT/HCPCS codes from uploaded documents</button>
      </>}
      {stage === 'service' && insuranceCase && <>
        <p className="section-kicker">4 · Procedures</p><h2>CPT/HCPCS suggestions</h2><p>Enter the patient’s disease or upload a searchable clinical PDF. ClinicForce suggests only codes present in the uploaded rate CSV; a clinician must confirm the final selection.</p>
        <label className="field"><span>Patient condition or reason for visit</span><textarea value={clinicalContext} disabled={busy} onChange={event => setClinicalContext(event.target.value)} placeholder="Example: disease, symptoms, or reason for visit" rows={4} /></label>
        <div className="evidence-upload-grid"><UploadTile title="Clinical PDF" hint="A searchable PDF describing the patient’s condition or visit." accept="application/pdf,.pdf" source={documents.clinical} busy={busy} onFile={file => uploadEvidence('clinical', file)} /></div>
        <button className="button button-secondary" type="button" disabled={busy || (!clinicalContext.trim() && !documents.clinical)} onClick={requestSuggestions}>Suggest CPT/HCPCS codes</button>
        {insuranceCase.service_lines.length > 0 && <><div className="service-items">{insuranceCase.service_lines.map(line => <label className={selectedCodes.includes(line.code) ? 'selected' : ''} key={line.code}><input type="checkbox" checked={selectedCodes.includes(line.code)} disabled={busy} onChange={() => toggleCode(line.code)} /><span><strong>{line.code}</strong><span>{line.description}</span>{line.recommendation_reason && <small>Why ClinicForce suggested it: {line.recommendation_reason}</small>}<small>{money(line.allowed_cents)} · {line.source_document}</small></span></label>)}</div>
        <button className="button button-primary" type="button" disabled={busy || selectedCodes.length === 0} onClick={confirmServices}>Confirm selected codes</button></>}
      </>}
      {stage === 'authorization' && insuranceCase && <>
        <p className="section-kicker">5 · Authorization</p><h2>Authorization entries in the coverage PDF</h2><p>Only authorization entries explicitly found for the selected codes are shown.</p>
        <div className="authorization-list">{insuranceCase.service_lines.filter(line => line.selected).map(line => <article key={line.code}><strong>{line.code}</strong><span>{line.description}</span><small>{authorizationLabel(line.authorization_status)}</small></article>)}</div>
        <button className="button button-primary" type="button" disabled={busy} onClick={() => advance('authorization')}>Continue to estimate</button>
      </>}
      {stage === 'benefits' && !complete && <>
        <p className="section-kicker">6 · Estimate</p><h2>Calculate from uploaded sources</h2><p>The calculation stops if a required coverage value is not explicit in the uploaded PDF.</p>
        <button className="button button-primary" type="button" disabled={busy} onClick={() => advance('benefits')}>Calculate estimate</button>
      </>}
      {stage === 'intake' && insuranceCase?.ocr.confirmed && null}
      {insuranceCase?.status === 'complete' && estimate && <section className="collection-decision" aria-labelledby="estimate-heading"><p className="section-kicker">Estimate</p><h2 id="estimate-heading">Uploaded-document estimate</h2>{estimate.estimate_available === false ? <p>{estimate.unavailable_reason}</p> : <><section className="estimate-lines" aria-label="Selected CPT and HCPCS code estimates"><h3>Selected code estimates</h3><dl>{estimate.line_items?.map(item => <div key={item.code}><dt><strong>{item.code}</strong><span>{item.description}</span></dt><dd>{money(item.allowed_cents)}<small>Deductible {money(item.deductible_applied_cents)} · Coinsurance {money(item.coinsurance_cents)}<br />Patient {money(item.patient_cents)} · Plan {money(item.plan_cents)}</small></dd></div>)}</dl></section><div className="decision-amounts"><div><span>Estimated patient responsibility</span><strong>{money(estimate.patient_cents)}</strong></div><div><span>Estimated plan responsibility</span><strong>{money(estimate.plan_cents)}</strong></div></div><dl className="insurance-facts"><div><dt>Total allowed amount</dt><dd>{money(estimate.allowed_cents)}</dd></div><div><dt>Total deductible applied</dt><dd>{money(estimate.deductible_applied_cents)}</dd></div><div><dt>Total coinsurance</dt><dd>{money(estimate.nominal_coinsurance_cents)}</dd></div></dl></>}<FactList facts={facts.filter(fact => ['benefits', 'estimate'].includes(fact.stage))} /></section>}
    </main>
  </section>
}

function UploadTile({ title, hint, accept, source, busy, onFile }: { title: string; hint: string; accept: string; source?: NonNullable<InsuranceCase['documents']>[string]; busy: boolean; onFile: (file?: File) => void }) {
  return <label className={`evidence-upload${source ? ' complete' : ''}`}><span className="upload-icon">{source ? <Check aria-hidden="true" /> : <Upload aria-hidden="true" />}</span><span><strong>{source ? `${title} uploaded` : title}</strong><small>{source?.filename ?? hint}</small>{source && <small><b>Source time:</b> {dateTime(source.observed_at)}</small>}</span><input type="file" accept={accept} disabled={busy} onChange={event => { onFile(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
}

function FactList({ facts }: { facts: InsuranceCase['facts'] }) {
  if (!facts.length) return null
  return <section className="evidence-reasoning" aria-label="Uploaded-document facts"><header><Files aria-hidden="true" /><div><h3>Extracted source values</h3><p>Each value identifies the uploaded file used.</p></div></header><dl className="insurance-facts">{facts.map(fact => <div key={`${fact.stage}-${fact.label}`}><dt>{fact.label}</dt><dd>{fact.value}<small><b>Source:</b> {fact.citation.source_filename} · {fact.citation.reference}</small></dd></div>)}</dl></section>
}
