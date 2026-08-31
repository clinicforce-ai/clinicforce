import { ArrowLeft, CheckCircle2, Play, ShieldCheck, Upload } from 'lucide-react'
import { useState, type ChangeEvent } from 'react'
import {
  ApiError,
  addLocalV2Document,
  createLocalV2Case,
  reviewLocalV2Document,
  runLocalV2Stage,
  type LocalV2Case,
  type LocalV2Document,
  type LocalV2DocumentKind,
  type LocalV2StageResult,
} from '../services/api'

interface InsuranceVerificationProps {
  onBack: () => void
}

type WorkflowStep = 'insurance_details' | 'eligibility' | 'network' | 'service' | 'authorization' | 'fee_schedule' | 'calculation'

const steps: WorkflowStep[] = ['insurance_details', 'eligibility', 'network', 'service', 'authorization', 'fee_schedule', 'calculation']
const labels: Record<WorkflowStep, string> = {
  insurance_details: 'Insurance details',
  eligibility: 'Eligibility',
  network: 'Network status',
  service: 'Anticipated service',
  authorization: 'Referral and prior authorization',
  fee_schedule: 'Fee schedule',
  calculation: 'Patient responsibility',
}
const documentForStep: Partial<Record<WorkflowStep, { kind: LocalV2DocumentKind; label: string; help: string; exclude: string }>> = {
  insurance_details: { kind: 'insurance_card', label: 'Insurance-card PDF', help: 'Member ID card for this patient. If there are two sides, combine them into one PDF.', exclude: 'Do not use an eligibility printout or provider agreement.' },
  eligibility: { kind: 'eligibility_printout', label: 'Dated eligibility and benefits response', help: 'Run for the visit date. It must show coverage status plus member ID and payer/group details.', exclude: 'Do not re-upload the member ID card.' },
  network: { kind: 'provider_contract', label: 'Clinic–payer provider participation agreement', help: 'Executed agreement that names the clinic/provider and payer, and describes participation or provider networks.', exclude: 'Do not use the patient’s coverage printout.' },
  fee_schedule: { kind: 'fee_schedule', label: 'Current numeric CPT/HCPCS rate sheet', help: 'Rate sheet for this payer, plan, provider, location, and effective date, with service-line amounts.', exclude: 'A provider agreement that only references compensation or Attachment D is not enough.' },
}
const outOfNetworkBenefitsDocument = { kind: 'out_of_network_benefits' as const, label: 'Out-of-network benefits response', help: 'Dated payer benefits response that specifically states the out-of-network benefit for this plan and visit.', exclude: 'Only upload this after Network status is confirmed as out of network.' }
const authorizationRequirementsDocument = { kind: 'authorization_requirements' as const, label: 'Payer referral and authorization requirements response', help: 'Payer portal or clearinghouse response for the planned service that states whether a referral and prior authorization are required.', exclude: 'Do not use a clinic note, scheduling message, or email.' }
const referralDocument = { kind: 'referral' as const, label: 'Referral order', help: 'Order or referral record from the referring clinician for this patient and specialist/service.', exclude: 'Do not use a payer authorization approval in place of a referral.' }
const authorizationDocument = { kind: 'authorization' as const, label: 'Payer prior-authorization determination', help: 'Payer approval for this service, provider, date range, and approved units, with its case or authorization reference.', exclude: 'Do not use a request submission, clinic note, or email.' }
const fieldLabel = (field: string) => field.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())

export default function InsuranceVerification({ onBack }: InsuranceVerificationProps) {
  const [card, setCard] = useState<File | null>(null)
  const [stageFile, setStageFile] = useState<File | null>(null)
  const [caseData, setCaseData] = useState<LocalV2Case | null>(null)
  const [active, setActive] = useState<WorkflowStep>('insurance_details')
  const [result, setResult] = useState<LocalV2StageResult | null>(null)
  const [service, setService] = useState('')
  const [networkStatus, setNetworkStatus] = useState('unknown')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const fail = (caught: unknown) => setError(caught instanceof ApiError ? caught.message : 'The insurance review could not be completed.')
  const refresh = (next: LocalV2Case) => {
    setCaseData(next)
    if (next.service) setService(next.service)
    setNetworkStatus(next.network_status)
    setStageFile(null)
    setError('')
  }
  const selected = (event: ChangeEvent<HTMLInputElement>) => setStageFile(event.target.files?.[0] ?? null)
  const documentOfKind = (kind: LocalV2DocumentKind) => caseData?.documents.filter((document) => document.kind === kind).at(-1)
  const authorizationRequirements = documentOfKind('authorization_requirements')
  const referralEvidence = documentOfKind('referral')
  const authorizationEvidence = documentOfKind('authorization')
  const planRule = caseData?.plan_rule
  const referralRequired = planRule?.rule?.referral_required.toLowerCase().startsWith('yes') || authorizationRequirements?.fields.referral_requirement === 'required'
  const payerAuthorizationRequired = authorizationRequirements?.fields.authorization_requirement === 'required'
  const authorizationEvidenceNeeded = referralRequired && (!referralEvidence || !referralEvidence.reviewed)
    ? referralDocument
    : !authorizationRequirements || !authorizationRequirements.reviewed
      ? authorizationRequirementsDocument
      : payerAuthorizationRequired && (!authorizationEvidence || !authorizationEvidence.reviewed)
        ? authorizationDocument
        : undefined
  const stageDocument = active === 'authorization' ? authorizationEvidenceNeeded : active === 'network' && networkStatus === 'out_of_network' ? outOfNetworkBenefitsDocument : documentForStep[active]
  const currentDocument = stageDocument && documentOfKind(stageDocument.kind)
  const currentCard = caseData?.documents.filter((document) => document.kind === 'insurance_card').at(-1)
  const statusFor = (step: WorkflowStep) => {
    if (step === 'insurance_details') return currentCard?.reviewed ? 'passed' : 'review'
    return caseData?.stages[step]?.status ?? 'Not started'
  }
  const activeIndex = steps.indexOf(active)
  const priorComplete = steps.slice(0, activeIndex).every((step) => statusFor(step) === 'passed')

  const create = async () => {
    if (!card) {
      setError('Upload the insurance card to begin.')
      return
    }
    setBusy(true)
    setError('')
    try {
      refresh(await createLocalV2Case({ insurance_card: card }))
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const upload = async () => {
    if (!caseData || !stageDocument || !stageFile) {
      setError(`Upload ${stageDocument?.label ?? 'the required evidence'} to continue.`)
      return
    }
    setBusy(true)
    try {
      refresh(await addLocalV2Document(caseData.id, stageDocument.kind, stageFile))
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const review = async (document: LocalV2Document) => {
    if (!caseData) return
    setBusy(true)
    try {
      refresh(await reviewLocalV2Document(caseData.id, document))
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const run = async () => {
    if (!caseData || active === 'insurance_details') return
    setBusy(true)
    setError('')
    try {
      const next = await runLocalV2Stage(caseData.id, active, {
        service,
      })
      setResult(next)
      if (active === 'network') setNetworkStatus(next.inputs.network_status ?? 'unknown')
      setCaseData({ ...caseData, plan_rule: next.inputs.plan_rule ?? caseData.plan_rule, stages: { ...caseData.stages, [active]: next } })
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const continueTo = (step: WorkflowStep) => {
    setActive(step)
    setResult(caseData?.stages[step] ?? null)
    setStageFile(null)
  }

  return <section className="insurance-page insurance-workbench animate-enter" aria-labelledby="insurance-heading">
    <button className="back-control" type="button" onClick={onBack}><ArrowLeft className="size-4" /> Back to clinical summary</button>
    <header className="insurance-hero workbench-hero">
      <div>
        <p className="eyebrow"><ShieldCheck className="size-4" /> Insurance estimate · staff review</p>
        <h1 id="insurance-heading">Patient cost-estimate workflow.</h1>
        <p>Complete one source check, review its result, then continue to the next check.</p>
      </div>
    </header>

    {!caseData ? <section className="workbench-current">
      <p className="section-kicker">Step 1 of 7 · insurance details</p>
      <h2>Start with the insurance card.</h2>
      <div className="stage-form">
        <label className="insurance-file-field"><span>Insurance-card PDF</span><span className="insurance-file-control"><input type="file" accept="application/pdf" onChange={(event) => setCard(event.target.files?.[0] ?? null)} /><span className="button button-secondary insurance-file-button"><Upload className="size-4" /> Choose file</span><span className={card ? 'insurance-file-name' : 'insurance-file-placeholder'}>{card?.name ?? 'No file selected'}</span></span></label>
      </div>
      {error && <p className="form-error workbench-error">{error}</p>}
      <button className="button button-primary workbench-continue" onClick={create} type="button" disabled={busy}>
        {busy ? 'Reading card…' : <><Upload className="size-4" /> Create estimate case</>}
      </button>
    </section> : <div className="workbench-layout">
      <nav className="workbench-progress" aria-label="Estimate workflow">
        <p className="section-kicker">Workflow</p>
        {steps.map((step, index) => {
          const previous = steps[index - 1]
          const disabled = Boolean(previous && statusFor(previous) !== 'passed')
          return <button className={`workbench-stage ${active === step ? 'workbench-stage-active' : ''}`} disabled={disabled} onClick={() => continueTo(step)} type="button" key={step}>
            <span>{String(index + 1).padStart(2, '0')} · {labels[step]}</span>
            <span>{statusFor(step)}</span>
          </button>
        })}
      </nav>
      <main className="workbench-current">
        <p className="section-kicker">Step {String(activeIndex + 1).padStart(2, '0')} of 07</p>
        <h2>{labels[active]}</h2>
        {!priorComplete && <p className="form-error">Complete the preceding workflow step before continuing.</p>}
        {priorComplete && <>
          {stageDocument && !currentDocument && <section className="stage-content">
            <p>{stageDocument.help}</p>
            <p>{stageDocument.exclude}</p>
            <label className="insurance-file-field"><span>{stageDocument.label}</span><span className="insurance-file-control"><input type="file" accept="application/pdf" onChange={selected} /><span className="button button-secondary insurance-file-button"><Upload className="size-4" /> Choose file</span><span className={stageFile ? 'insurance-file-name' : 'insurance-file-placeholder'}>{stageFile?.name ?? 'No file selected'}</span></span></label>
            <button className="button button-primary workbench-continue" type="button" onClick={upload} disabled={busy || !stageFile}><Upload className="size-4" /> Upload for this step</button>
          </section>}
          {currentDocument && !currentDocument.reviewed && <section className="stage-result stage-result-review">
            <div>
              <h3>Review extracted document details</h3>
              <p>{currentDocument.filename}</p>
              {Object.entries(currentDocument.fields).length > 0 ? <dl className="result-facts">{Object.entries(currentDocument.fields).map(([key, value]) => <div key={key}><dt>{fieldLabel(key)}</dt><dd>{value}</dd></div>)}</dl> : <p>No labelled values were detected. Review the original printout before confirming.</p>}
              {currentDocument.rate_lines?.length ? <div className="rate-lines"><h4>Contracted rates in this response</h4><table><thead><tr><th>Code</th><th>Service</th><th>Allowed amount</th></tr></thead><tbody>{currentDocument.rate_lines.map((line) => <tr key={line.code}><td>{line.code}</td><td>{line.description}</td><td>{line.amount}</td></tr>)}</tbody></table></div> : null}
              {currentDocument.evidence_pages.length > 0 && <p>Relevant pages: {currentDocument.evidence_pages.join(', ')}</p>}
              <button className="button button-secondary workbench-continue" type="button" onClick={() => review(currentDocument)} disabled={busy}><CheckCircle2 className="size-4" /> Confirm document evidence</button>
            </div>
          </section>}
          {active === 'insurance_details' && currentDocument?.reviewed && <section className="stage-result stage-result-passed"><div><p className="section-kicker">Result</p><h3>Insurance-card evidence confirmed</h3><p>{currentDocument.fields.member_name ? `Patient name read from card: ${currentDocument.fields.member_name}` : 'Patient name is not printed in readable card text. Confirm the patient from the scheduled appointment before the coverage check.'}</p><button className="button button-secondary workbench-continue" type="button" onClick={() => continueTo('eligibility')}>Continue to Eligibility</button></div></section>}
          {active === 'service' && <><p>Record the one booked service this estimate covers: the appointment visit or a separately scheduled procedure.</p>{caseData.appointment?.reason && <dl className="result-facts"><div><dt>Reason for visit</dt><dd>{caseData.appointment.reason}</dd></div><div><dt>Date and location</dt><dd>{caseData.appointment.date_of_service} · {caseData.appointment.place_of_service}</dd></div></dl>}<label className="stage-form">Planned service<input value={service} onChange={(event) => setService(event.target.value)} placeholder="e.g., New patient neurology visit" /></label></>}
          {active === 'authorization' && planRule?.rule && <section className="stage-result stage-result-review"><div><p className="section-kicker">Plan requirements</p><h3>{planRule.rule.payer} · {planRule.rule.plan_group}</h3><p>Referral: {planRule.rule.referral_required}. {planRule.rule.referral_detail}</p><p>Prior authorization: {planRule.rule.prior_authorization_needed_for}</p></div></section>}
          {active !== 'insurance_details' && (!stageDocument || currentDocument?.reviewed) && <button className="button button-primary workbench-continue" type="button" onClick={run} disabled={busy}><Play className="size-4" /> Run this check</button>}
          {error && <p className="form-error workbench-error">{error}</p>}
          {result && <section className={`stage-result stage-result-${result.status}`}><div><p className="section-kicker">Result</p><h3>{labels[result.stage as WorkflowStep]}</h3><p>{result.decision}</p>{result.recorded_collection && <dl className="result-facts"><div><dt>Recorded collection instruction</dt><dd>{result.recorded_collection.amount}</dd></div><div><dt>Source</dt><dd>{result.recorded_collection.source}</dd></div></dl>}{result.evidence_conflicts?.length ? <div className="stage-actions-needed"><strong>Items to resolve before quoting a final balance</strong><ul>{result.evidence_conflicts.map((conflict) => <li key={conflict}>{conflict}</li>)}</ul></div> : null}{result.next_step && <button className="button button-secondary workbench-continue" type="button" onClick={() => continueTo(result.next_step as WorkflowStep)}>Continue to {labels[result.next_step as WorkflowStep]}</button>}</div></section>}
        </>}
      </main>
    </div>}
  </section>
}
