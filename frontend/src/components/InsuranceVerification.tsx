import { AlertTriangle, ArrowLeft, Check, Files, ShieldCheck, Upload } from 'lucide-react'
import { useState } from 'react'
import {
  confirmInsuranceIntake, confirmInsuranceService, createInsuranceCase, runInsuranceStage,
  suggestInsuranceServices, uploadInsuranceEvidence, type InsuranceCase, type InsuranceStage,
} from '../services/api'

interface Props { onBack: () => void }

const steps: Array<{ id: InsuranceStage; label: string }> = [
  { id: 'intake', label: 'Insurance card' }, { id: 'eligibility', label: 'Card review' },
  { id: 'network', label: 'Benefits & rates' }, { id: 'service', label: 'Select CPT codes' },
  { id: 'authorization', label: 'Authorization & referral' },
  { id: 'estimate', label: 'Pay estimate' },
]
const imageOrPdf = '.pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png'
const money = (cents: number | null | undefined) => cents == null ? 'Not available' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
const label = (key: string) => key.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase())
const authorizationLabel = (status: string | undefined) => status === 'not_required' ? 'Not required' : status === 'required' ? 'Required' : status === 'conditional' ? 'Conditional · review source terms' : 'Not established by the uploaded documents'
const timestamp = (value: string) => Number.isNaN(Date.parse(value)) ? value : new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZoneName: undefined })

export default function InsuranceVerification({ onBack }: Props) {
  const [insuranceCase, setInsuranceCase] = useState<InsuranceCase | null>(null)
  const [viewStage, setViewStage] = useState<InsuranceStage | null>(null)
  const [selectedCodes, setSelectedCodes] = useState<string[]>([])
  const [quantities, setQuantities] = useState<Record<string, number>>({})
  const [contextEdited, setContextEdited] = useState(false)
  const [suggestionsStale, setSuggestionsStale] = useState(false)
  const [clinicalContext, setClinicalContext] = useState('')
  const [visitType, setVisitType] = useState<'' | 'new' | 'established'>('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('Start with the patient’s insurance card.')
  const [error, setError] = useState('')

  const perform = async (work: () => Promise<InsuranceCase>, success: string, after?: (next: InsuranceCase) => void) => {
    setBusy(true)
    setError('')
    try {
      const next = await work()
      setInsuranceCase(next)
      setViewStage(null)
      setSelectedCodes(next.service_lines.filter(line => line.selected).map(line => line.code))
      setMessage(next.status === 'complete' ? 'Pay estimate review complete.' : success)
      after?.(next)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The uploaded document could not be processed.')
    } finally { setBusy(false) }
  }
  const uploadCard = (file?: File) => {
    if (!file) return
    if (file.size > 10 * 1024 * 1024) { setError('Choose a card file of 10 MB or less.'); return }
    setMessage('Reading your insurance card…')
    void perform(() => createInsuranceCase(file), 'Card read. Compare the extracted fields with your uploaded card.', () => {
      setClinicalContext(''); setVisitType(''); setContextEdited(false); setSuggestionsStale(false); setQuantities({})
    })
  }
  const uploadEvidence = (kind: string, file?: File) => {
    if (!insuranceCase || !file) return
    if (file.size > 15 * 1024 * 1024) { setError('Choose a document of 15 MB or less.'); return }
    setMessage(`Reading ${file.name}…`)
    void perform(() => uploadInsuranceEvidence(insuranceCase.id, kind, file), `${file.name} added to this review.`, next => {
      if (kind === 'clinical') {
        if (!contextEdited) { setClinicalContext(next.clinical_context ?? ''); setVisitType(next.visit_type ?? '') }
        setQuantities({}); setSuggestionsStale(false)
        setMessage(contextEdited ? 'Clinical document read. Your edited text was kept; review the extracted passages below.' : 'Clinical document read. Review the extracted visit details before requesting codes.')
      }
    })
  }
  const advance = (stage: Exclude<InsuranceStage, 'intake' | 'service'>) => insuranceCase && void perform(
    () => runInsuranceStage(insuranceCase.id, stage), 'Ready for the next step.',
  )
  const activeStage = insuranceCase?.status === 'complete' ? 'estimate' : insuranceCase?.next_stage ?? 'intake'
  const stage = viewStage ?? activeStage
  const reviewing = stage !== activeStage
  const disabled = busy || reviewing
  const facts = insuranceCase?.facts ?? []
  const documents = insuranceCase?.documents ?? {}
  const estimate = insuranceCase?.estimate
  const groups = insuranceCase?.service_groups ?? []
  const visitCodes = groups.filter(group => group.category === 'visit').flatMap(group => group.codes)
  const activeGroups = new Set(groups.filter(group => group.codes.some(code => selectedCodes.includes(code))).map(group => group.id))
  const missingCompanions = groups.filter(group => activeGroups.has(group.id)).flatMap(group => group.requires_groups.filter(id => !activeGroups.has(id))).map(id => groups.find(group => group.id === id)?.label ?? id)
  const missingQuantity = insuranceCase?.service_lines.some(line => selectedCodes.includes(line.code) && line.requires_quantity && (!Number.isInteger(quantities[line.code]) || quantities[line.code] < 1 || quantities[line.code] > 10000))
  const completed = new Set(insuranceCase?.stage_decisions.map(item => item.stage) ?? [])
  if (insuranceCase?.ocr.confirmed) completed.add('intake')
  const cardValues = Object.entries(insuranceCase?.ocr_evidence ?? {}).filter(([key, evidence]) =>
    evidence.value === insuranceCase?.ocr[key as keyof InsuranceCase['ocr']] && evidence.quote.includes(evidence.value))

  const cardReview = insuranceCase && <>
    <dl className="insurance-facts card-details">{cardValues.map(([key, evidence]) => <div key={key}>
      <dt>{label(key)}</dt><dd>{evidence.value}<small>Source: {insuranceCase.card_source?.filename} · page {evidence.page}</small>
        <details className="source-excerpt"><summary>View extracted text</summary><blockquote>{evidence.quote}</blockquote></details>
      </dd>
    </div>)}</dl>
    {['member_name', 'member_id', 'group_number'].filter(key => !cardValues.some(([found]) => found === key)).map(key =>
      <p className="missing-source" key={key}>{label(key)}: not clearly found in this upload.</p>)}
    <p className="missing-source">Only fields found in this card are shown. Benefits that are not printed on the card belong in the benefits upload—not in this card summary.</p>
  </>

  return <section className="insurance-beta insurance-v1" aria-labelledby="insurance-heading">
    <button className="back-control" type="button" disabled={busy} onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to clinical summary</button>
    <header className="insurance-beta-hero">
      <div><p className="eyebrow"><ShieldCheck aria-hidden="true" className="size-4" /> Beta V1 · Uploaded documents</p>
        <h1 id="insurance-heading">From your documents to a pay estimate</h1>
        <p>Upload the patient’s card, benefit details and fee schedule. Review the extracted information, choose CPT codes and see an itemized estimate.</p>
      </div>
      <div className="beta-session"><span><i aria-hidden="true" /> {insuranceCase?.status === 'complete' ? 'Review complete' : 'Document-based review'}</span><strong>Your uploads are the source</strong><small>CPT suggestions are generated separately and require clinician review.</small></div>
    </header>
    <div className="insurance-workspace">
      <nav className="insurance-stepper" aria-label="Insurance verification progress">{steps.map((item, index) => <button
        type="button" key={item.id} aria-current={item.id === stage ? 'step' : undefined}
        disabled={busy || (!completed.has(item.id) && item.id !== activeStage)} onClick={() => setViewStage(item.id)}>
        <span className="step-number">{completed.has(item.id) ? <Check aria-hidden="true" /> : index + 1}</span>
        <span className="step-copy"><strong>{item.label}</strong><small>{completed.has(item.id) ? 'Completed · review' : item.id === activeStage ? 'Current step' : 'Upcoming'}</small></span>
      </button>)}</nav>
      <div className="insurance-layout"><main className="insurance-stage-card" aria-busy={busy}>
        <div className="insurance-stage-toolbar"><div><span>{reviewing ? 'Reviewing completed step' : 'Current step'}</span><strong>{steps.find(item => item.id === stage)?.label}</strong></div>
          <p className="insurance-status" role="status">{busy ? message : error ? 'Please check the message below.' : message}</p>
        </div>
        {error && <p className="form-error" role="alert"><AlertTriangle aria-hidden="true" className="size-4" /> {error}</p>}
        {reviewing && <button className="button button-secondary" type="button" onClick={() => setViewStage(null)}>Return to current step</button>}

        {stage === 'intake' && <>
          <p className="section-kicker">1 · Insurance card</p><h2>{insuranceCase ? 'Check what was read from the card' : 'Upload the patient’s insurance card'}</h2>
          <p>Use a clear image of the card or a PDF containing its front and back. Keep the member name, member ID and group number readable. We show only information extracted from this file.</p>
          {!reviewing && <UploadTile title={insuranceCase ? 'Replace insurance card' : 'Insurance card'} hint="PDF (up to 2 pages), JPG or PNG · maximum 10 MB. Include both sides in one PDF when possible. A benefits statement and fee schedule come in the next steps." accept={imageOrPdf} busy={busy} source={insuranceCase?.card_source} onFile={uploadCard} />}
          {cardReview}
          {insuranceCase && !reviewing && <button className="button button-primary" type="button" disabled={busy || cardValues.length === 0} onClick={() => void perform(
            () => confirmInsuranceIntake(insuranceCase.id, insuranceCase.ocr), 'Card fields confirmed. Benefits and rates will come from your next uploads.',
          )}>Confirm extracted card fields</button>}
        </>}

        {stage === 'eligibility' && <>
          <p className="section-kicker">2 · Card review</p><h2>Card details confirmed</h2>
          <p>An insurance card identifies the member; it does not by itself verify active coverage or current benefit balances. Next, upload the member’s dated benefit details and the applicable fee schedule.</p>
          {cardReview}
          {!reviewing && <button className="button button-primary" type="button" disabled={busy} onClick={() => advance('eligibility')}>Continue to benefits & rates</button>}
        </>}

        {stage === 'network' && <>
          <p className="section-kicker">3 · Benefits & rates</p><h2>Upload the documents behind the estimate</h2>
          <p>These files serve different purposes. The benefits document supplies the member’s coverage and remaining balances; the fee schedule supplies the allowed price for each CPT/HCPCS code.</p>
          <div className="evidence-upload-grid">
            <UploadTile title="Member benefits · required" hint="Upload the dated eligibility/benefits response for this member, such as a payer-portal export. Include the pages showing individual deductible remaining, out-of-pocket remaining and patient cost-sharing. PDF, JPG or PNG · up to 15 MB." accept={imageOrPdf} source={documents.coverage} busy={disabled} onFile={file => uploadEvidence('coverage', file)} />
            <UploadTile title="Fee schedule · required" hint="Upload the applicable rate CSV, with CPT/HCPCS or Procedure Code, Description, and Amount/Allowed Amount/Rate columns. Use the relevant plan and network schedule, with one rate per code. Up to 15 MB." accept="text/csv,.csv" source={documents.fee_schedule} busy={disabled} onFile={file => uploadEvidence('fee_schedule', file)} />
            <UploadTile title="Provider agreement · optional reference" hint="Upload the clinic–payer agreement if you want to review its participation terms alongside the benefits. An agreement alone is not evidence of a numeric CPT rate. PDF, JPG or PNG · up to 15 MB." accept={imageOrPdf} source={documents.contract} busy={disabled} onFile={file => uploadEvidence('contract', file)} />
            <UploadTile title="Self-pay rates · optional" hint="Upload the clinic’s cash-rate CSV to compare prices for the selected codes, or a PDF/image pricing guide for reference. Insurance rates are never used as cash prices. Up to 15 MB." accept={`${imageOrPdf},.csv,text/csv`} source={documents.self_pay} busy={disabled} onFile={file => uploadEvidence('self_pay', file)} />
          </div>
          <FactList facts={facts.filter(fact => fact.stage === 'network')} />
          {documents.coverage && <SourcePages source={documents.coverage} />}
          {documents.contract && <SourcePages source={documents.contract} />}
          {documents.coverage && <p className="missing-source">If a field cannot be read or matched unambiguously, it stays unavailable. No sample benefits or saved payer defaults are used.</p>}
          {!reviewing && <><button className="button button-primary" type="button" disabled={busy || !documents.coverage || !documents.fee_schedule} onClick={() => advance('network')}>Continue to CPT suggestions</button>
            {(!documents.coverage || !documents.fee_schedule) && <p className="missing-source">Upload both required documents to continue.</p>}</>}
        </>}

        {stage === 'service' && insuranceCase && <>
          <p className="section-kicker">4 · Select CPT codes</p><h2>Describe the visit or upload clinical notes</h2>
          <p>ClinicForce suggests CPT codes from the condition or visit information you provide. These are suggestions—not facts extracted from the insurance card. Review and confirm the appropriate codes.</p>
          <div className="clinical-context-form">
            <label><span>Patient symptoms, condition and planned care</span><textarea value={clinicalContext} disabled={disabled} onChange={event => { setClinicalContext(event.target.value); setContextEdited(true); setSuggestionsStale(true) }} rows={5} /></label>
            <label><span>Visit type, if known</span><select value={visitType} disabled={disabled} onChange={event => { setVisitType(event.target.value as typeof visitType); setContextEdited(true); setSuggestionsStale(true) }}><option value="">Not specified</option><option value="new">New patient</option><option value="established">Established patient</option></select></label>
            <UploadTile title="Clinical document · optional if you entered the condition" hint="Upload a visit note, referral, appointment email or clinical report describing the condition and planned care. PDF, JPG or PNG · up to 15 MB. This informs CPT suggestions; it does not establish insurance coverage or authorization." accept={imageOrPdf} source={documents.clinical} busy={disabled} onFile={file => uploadEvidence('clinical', file)} />
            {documents.clinical && <>
              <details className="source-excerpt"><summary>Review extracted clinical passages</summary>{documents.clinical.findings?.clinical_excerpts?.map((entry, index) => <section key={index}><small>{documents.clinical.filename} · page {entry.page}</small><blockquote>{entry.quote}</blockquote></section>)}</details>
              {!documents.clinical.findings?.clinical_context && <p className="missing-source">No patient-specific clinical passages could be verified. Enter the visit details above.</p>}
              {contextEdited && documents.clinical.findings?.clinical_context && <button className="button button-secondary" type="button" disabled={disabled} onClick={() => { setClinicalContext(documents.clinical.findings?.clinical_context ?? ''); setVisitType(documents.clinical.findings?.visit_type ?? ''); setContextEdited(false); setSuggestionsStale(true) }}>Use extracted visit details</button>}
            </>}
            <button className="button button-secondary" type="button" disabled={disabled || (!clinicalContext.trim() && !documents.clinical)} onClick={() => void perform(
              () => suggestInsuranceServices(insuranceCase.id, clinicalContext, visitType || undefined), 'CPT suggestion review finished. Only supported suggestions are listed below.',
              () => { setSuggestionsStale(false); setQuantities({}) },
            )}>Suggest CPT/HCPCS codes</button>
          </div>
          {insuranceCase.suggestion_notes?.map((note, index) => <p className="missing-source" key={index}>Coding review: {note}</p>)}
          {insuranceCase.suggestions_reviewed && !insuranceCase.service_lines.length && <p className="missing-source">No supported candidates were found in this uploaded fee schedule. Review the clinical details and the schedule.</p>}
          {insuranceCase.service_lines.length > 0 && <><p>These groups are suggested for this case using your clinical details and uploaded fee schedule. Confirm the coding relationships, planned extent and units with the clinician; suggestions are not a billing or coverage determination.</p>
            {groups.map(group => <fieldset className="v1-code-group" key={group.id} disabled={disabled}>
              <legend>{group.label}</legend><p>{group.description}</p><small>{group.selection_mode === 'single' ? 'Choose one alternative, or leave this group unselected.' : 'Select the separate services planned for this visit.'}</small>
              {group.requires_groups.length > 0 && <p className="missing-source">Suggested companion: {group.requires_groups.map(id => groups.find(item => item.id === id)?.label).join(', ')}</p>}
              <div className="service-options">{insuranceCase.service_lines.filter(line => group.codes.includes(line.code)).map(line => <div key={line.code}><label className={selectedCodes.includes(line.code) ? 'selected' : ''}>
              <input type={group.selection_mode === 'single' ? 'radio' : 'checkbox'} name={`cpt-${group.id}`} checked={selectedCodes.includes(line.code)} disabled={disabled} onChange={() => setSelectedCodes(current => group.selection_mode === 'single' ? [...current.filter(code => !(group.category === 'visit' ? visitCodes : group.codes).includes(code)), line.code] : current.includes(line.code) ? current.filter(value => value !== line.code) : [...current, line.code])} />
              <span><strong>{line.code}{selectedCodes.includes(line.code) ? ` · item ${selectedCodes.indexOf(line.code) + 1}` : ''}</strong><span>{line.description}</span>
                <small>{money(line.allowed_cents)} allowed · {documents.fee_schedule?.filename}{line.reference ? ` · ${line.reference}` : ''}</small>
                {line.recommendation_reason && <small>ClinicForce suggestion: {line.recommendation_reason}</small>}
              </span>
            </label>{selectedCodes.includes(line.code) && line.requires_quantity && <label className="v1-quantity">Confirm quantity · {line.unit_label}<input aria-label={`Quantity for ${line.code}`} type="number" min="1" max="10000" step="1" value={quantities[line.code] ?? ''} disabled={disabled} onChange={event => setQuantities(current => ({ ...current, [line.code]: event.target.valueAsNumber }))} /></label>}</div>)}</div>
              {activeGroups.has(group.id) && <button className="button button-secondary" type="button" disabled={disabled} onClick={() => setSelectedCodes(current => current.filter(code => !group.codes.includes(code)))}>Clear {group.label}</button>}
            </fieldset>)}
            {missingCompanions.length > 0 && <p className="missing-source" role="status">Select the suggested companion service before continuing: {[...new Set(missingCompanions)].join(', ')}.</p>}
            {missingQuantity && <p className="missing-source" role="status">Confirm the billing quantity for each selected service that requests it. Do not guess from the symptoms.</p>}
            {suggestionsStale && <p className="missing-source" role="status">Visit details changed. Generate suggestions again before confirming codes.</p>}
            {!reviewing && <button className="button button-primary" type="button" disabled={busy || suggestionsStale || selectedCodes.length === 0 || missingQuantity || missingCompanions.length > 0} onClick={() => void perform(
              () => confirmInsuranceService(insuranceCase.id, selectedCodes, Object.fromEntries(Object.entries(quantities).filter(([code]) => selectedCodes.includes(code)))), 'Selected codes saved, with visit services first.',
            )}>Confirm {selectedCodes.length || ''} selected codes</button>}
          </>}
        </>}

        {stage === 'authorization' && insuranceCase && <>
          <p className="section-kicker">5 · Authorization & referral</p><h2>Review requirements for your selected codes</h2>
          <p>These findings come from the benefits and agreement you already uploaded. No separate authorization document is needed for this review. A requirement is not an approval; a missing finding does not mean “not required.”</p>
          <div className="authorization-list">{insuranceCase.service_lines.filter(line => line.selected).map(line => <article key={line.code}>
            <strong>{line.code} · {line.description}</strong><span>Authorization: {authorizationLabel(line.authorization_status)}</span><span>Referral: {authorizationLabel(line.referral_status)}</span>
            {line.requirement_evidence?.map((entry, index) => <details className="source-excerpt" key={index}><summary>{label(entry.kind)} · {entry.network} · {entry.source_filename} · {entry.reference}</summary><blockquote>{entry.quote}</blockquote></details>)}
          </article>)}</div>
          {!!documents.contract?.findings?.policy_terms?.length && <details className="source-excerpt"><summary>Review agreement conditions for authorization and referrals</summary><p>These clauses may depend on the member’s plan. They are not automatic yes/no findings for every code.</p>{documents.contract.findings.policy_terms.map((term, index) => <section key={index}><small>{documents.contract.filename} · {term.reference}</small><blockquote>{term.quote}</blockquote></section>)}</details>}
          {!reviewing && <button className="button button-primary" type="button" disabled={busy} onClick={() => advance('authorization')}>Review and show pay estimate</button>}
        </>}

        {stage === 'benefits' && <>
          <p className="section-kicker">6 · Review benefits</p><h2>Check the source values before calculating</h2>
          <p>These are the benefit values read from your upload. The estimate uses your selected CPT rates and these balances. If a required value is missing or ambiguous, no patient amount will be invented.</p>
          <FactList facts={facts.filter(fact => fact.stage === 'network')} />
          {!reviewing && <button className="button button-primary" type="button" disabled={busy} onClick={() => advance('benefits')}>Calculate pay estimate</button>}
        </>}

        {stage === 'estimate' && estimate && <>
          <p className="section-kicker">6 · Pre-visit pay estimate</p>
          <section className="v1-collection" aria-label="Front desk collection decision">
            <span>Front desk action</span><h2>{estimate.collection_action === 'collect' ? `Collect ${money(estimate.collect_today_cents)} today` : estimate.collection_action === 'do_not_collect' ? 'Do not collect today' : estimate.collection_action === 'out_of_network' ? 'Out of network · review with patient' : 'Verify before collecting'}</h2>
            <p>{estimate.collection_reason}</p>
            {estimate.verification_reasons?.length ? <ul>{estimate.verification_reasons.map(reason => <li key={reason}>{reason}</li>)}</ul> : null}
            <dl className="insurance-facts"><div><dt>Estimated patient share</dt><dd>{money(estimate.patient_cents)}</dd></div><div><dt>Estimated insurance payment</dt><dd>{money(estimate.plan_cents)}</dd></div></dl>
            <p className="missing-source">Estimate confidence: {estimate.confidence ?? 'Not assessed'} · {estimate.confidence_reason}</p>
            <small>Calculated: {timestamp(estimate.calculated_at)} (your local time)</small>
          </section>
          <details className="source-excerpt v1-calculation"><summary>See calculation · normal share and benefit-limit adjustment</summary><dl className="insurance-facts">
            <div><dt>Selected allowed amounts</dt><dd>{money(estimate.allowed_cents)}</dd></div>
            <div><dt>Deductible applied</dt><dd>{money(estimate.deductible_applied_cents)}</dd></div>
            <div><dt>Normal coinsurance</dt><dd>{money(estimate.nominal_coinsurance_cents)}</dd></div>
            <div><dt>Patient share before benefit limits</dt><dd>{money(estimate.normal_patient_share_cents)}</dd></div>
            <div><dt>Individual out-of-pocket remaining</dt><dd>{money(estimate.individual_oop_remaining_cents)}</dd></div>
            <div><dt>Out-of-pocket adjustment</dt><dd>{money(estimate.oop_cap_adjustment_cents)}</dd></div>
            <div><dt>Patient estimate after limits</dt><dd>{money(estimate.patient_cents)}</dd></div>
          </dl><p>The patient estimate applies to eligible covered services. An unresolved requirement means staff should verify before deciding what to collect.</p></details>
          <h3>Your selected codes, item by item</h3>
          <p>Allowed prices come from {documents.fee_schedule?.filename}. Patient and plan shares are calculated from {documents.coverage?.filename}.</p>
          {estimate.estimate_available === false && <p className="form-error" role="status">{estimate.unavailable_reason}</p>}
          <section className="v1-estimate-items" aria-label="Selected CPT and HCPCS code estimates">{estimate.line_items?.map((item, index) => <article key={item.code}>
            <header><span>Item {index + 1} · CPT/HCPCS {item.code}</span><h3>{item.description}</h3></header>
            <dl className="insurance-facts"><div><dt>Allowed price</dt><dd>{money(item.allowed_cents)}</dd></div>
              <div><dt>Rate and quantity</dt><dd>{money(item.unit_allowed_cents ?? item.allowed_cents)} × {item.units ?? 1}</dd></div>
              <div><dt>Estimated patient share</dt><dd>{money(item.patient_cents)}</dd></div><div><dt>Estimated plan share</dt><dd>{money(item.plan_cents)}</dd></div>
            </dl>
            {item.patient_cents != null && <details className="source-excerpt"><summary>Calculation details</summary><p>Deductible applied: {money(item.deductible_applied_cents)}. Coinsurance before the out-of-pocket cap: {money(item.coinsurance_cents)}. Balances are applied in the selected code order.</p></details>}
          </article>)}</section>
          {estimate.estimate_available !== false && <section className="v1-estimate-total" aria-label="Estimate totals"><h3>Estimated total</h3><dl className="insurance-facts">
            <div><dt>All selected allowed prices</dt><dd>{money(estimate.allowed_cents)}</dd></div><div><dt>Patient responsibility</dt><dd>{money(estimate.patient_cents)}</dd></div><div><dt>Plan responsibility</dt><dd>{money(estimate.plan_cents)}</dd></div>
          </dl></section>}
          <section className="v1-estimate-total" aria-label="Self-pay comparison"><h3>Self-pay option</h3>
            {estimate.self_pay?.available ? <><p>Cash prices from {documents.self_pay?.filename}; separate from insurance benefits.</p><dl className="insurance-facts">{estimate.self_pay.line_items.map(item => <div key={item.code}><dt>{item.code}</dt><dd>{money(item.amount_cents)}</dd></div>)}<div><dt>Total self-pay price</dt><dd>{money(estimate.self_pay.total_cents)}</dd></div></dl></> : <p>{documents.self_pay ? 'A cash price could not be matched to every selected code. Review the uploaded pricing guide; no total has been invented.' : 'No cash-rate document was uploaded. Insurance allowed amounts are not self-pay prices.'}</p>}
            {documents.self_pay?.findings?.pages && <SourcePages source={documents.self_pay} />}
          </section>
          <details className="source-excerpt"><summary>What we know · what could change</summary><FactList facts={facts.filter(fact => fact.stage === 'network')} /><p>Final CPTs, units, medical necessity, benefit applicability, coordination of benefits and claim processing can change the final amount. A document’s upload date does not establish that its benefits are current for this visit.</p></details>
          <details className="source-excerpt"><summary>View source evidence and document dates</summary>
            {[...(insuranceCase?.card_source ? [insuranceCase.card_source] : []), ...Object.values(documents)].map(source => <section key={source.id}><h4>{source.filename}</h4><SourceDates source={source} />{source.findings?.pages && <SourcePages source={source} />}</section>)}
            <FactList facts={facts.filter(fact => fact.stage !== 'intake')} />
          </details>
          <p className="estimate-disclaimer">{estimate.disclaimer}</p>
        </>}
      </main></div>
    </div>
  </section>
}

function UploadTile({ title, hint, accept, source, busy, onFile }: { title: string; hint: string; accept: string; source?: NonNullable<InsuranceCase['documents']>[string]; busy: boolean; onFile: (file?: File) => void }) {
  return <label className={`evidence-upload${source ? ' complete' : ''}`}>
    <span className="upload-icon">{source ? <Check aria-hidden="true" /> : <Upload aria-hidden="true" />}</span>
    <span><strong>{title}</strong><small>{hint}</small>{source && <><small className="upload-file">Uploaded: {source.filename}</small><SourceDates source={source} /></>}
      {!busy && <span className="v1-upload-action">{source ? 'Choose replacement file' : 'Choose file'}</span>}
    </span>
    <input aria-label={title} type="file" accept={accept} disabled={busy} onChange={event => { onFile(event.target.files?.[0]); event.currentTarget.value = '' }} />
  </label>
}

function SourceDates({ source }: { source: NonNullable<InsuranceCase['documents']>[string] }) {
  return <span className="v1-source-dates"><small>Uploaded: {timestamp(source.observed_at)} (your local time)</small>
    {source.document_dates?.length ? source.document_dates.map((date, index) => <small key={index}>Document {date.label}: {date.value} · page {date.page}</small>) : <small>Document date: not explicitly found</small>}
  </span>
}

function SourcePages({ source }: { source: NonNullable<InsuranceCase['documents']>[string] }) {
  return <details className="source-excerpt"><summary>Review extracted text · {source.filename}</summary>
    {source.findings?.pages?.map(page => <section key={page.page}><h4>Page {page.page}</h4><pre>{page.text}</pre></section>)}
  </details>
}

function FactList({ facts }: { facts: InsuranceCase['facts'] }) {
  if (!facts.length) return null
  return <section className="evidence-reasoning" aria-label="Uploaded-document facts"><header><Files aria-hidden="true" /><div><h3>Values read from your upload</h3><p>Check the source text for each extracted value.</p></div></header>
    <dl className="insurance-facts">{facts.map(fact => <div key={`${fact.stage}-${fact.label}`}><dt>{fact.label}</dt><dd>{fact.value}<small>Source: {fact.citation.source_filename} · {fact.citation.reference}</small>
      {fact.citation.quote && <details className="source-excerpt"><summary>View extracted text</summary><blockquote>{fact.citation.quote}</blockquote></details>}
    </dd></div>)}</dl>
  </section>
}
