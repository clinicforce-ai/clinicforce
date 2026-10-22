import type { ReactNode } from 'react'
import { FileText, Network, ShieldCheck, Wallet } from 'lucide-react'
import type { PrevisitEstimate, RequirementEvidence } from '../../services/api/commercial-estimator'
import RequirementSourceLink from './RequirementSourceLink'
import { money, visitDate } from './format'
import { patientBenefitsBound } from './identityBinding'
import ConflictingOopRows from './ConflictingOopRows'

export function ApplicableBenefits({
  result,
  readOnly = false,
}: {
  result: PrevisitEstimate
  readOnly?: boolean
}) {
  const services = result.collection_guidance?.services || []
  const otherNetworkTerms = (result.benefits?.reported_options || []).filter((row) =>
    ['N', 'NO', 'OUT_OF_NETWORK'].includes(row.network),
  )
  const identityMismatch =
    result.benefits?.active === true && !patientBenefitsBound(result.identity)
  const payerOrderMissing = result.recovery?.reason_code === 'multiple_active_policies'
  const historicalOnly = result.benefits?.historical_only === true
  const alternatives = result.price_range?.lines || []
  const sharedAlternativeTerms =
    result.price_range?.mode === 'alternatives' &&
    services.length > 1 &&
    alternatives.length === services.length &&
    services.every(
      (row, index) =>
        (/^992(?:0[2-5]|1[2-5])$/.test(row.code) || /^959(?:0[7-9]|1[0-3])$/.test(row.code)) &&
        alternatives[index].code === row.code,
    ) &&
    services.every((row) =>
      /^959/.test(services[0].code) ? /^959/.test(row.code) : /^992/.test(row.code),
    ) &&
    !alternatives.some((line) => line.benefit_basis === 'Procedure-specific payer benefit') &&
    new Set(services.map((row) => JSON.stringify({ ...row, id: undefined, code: undefined })))
      .size === 1 &&
    new Set(alternatives.map((line) => JSON.stringify(line.benefits))).size === 1
  const displayedServices = sharedAlternativeTerms ? services.slice(0, 1) : services
  const responseRow = (id?: string | null) => id && <small>Response row: {id}</small>
  const oopConflictReason = (annual: boolean) =>
    result.benefits?.review_reasons?.find((reason) =>
      reason.startsWith(
        annual
          ? 'The payer returned a conflicting annual individual out-of-pocket maximum'
          : 'The payer returned conflicting remaining individual out-of-pocket balances',
      ),
    ) ||
    `The payer returned conflicting ${annual ? 'annual individual out-of-pocket maximum' : 'remaining individual out-of-pocket balance'} rows. Ask the payer which value applies to this plan, benefit scope, network tier, coverage level and visit date.`
  return (
    <section>
      <h3>
        {result.payment_mode === 'cash'
          ? 'Insurance benefits returned for review'
          : historicalOnly
            ? 'Previously saved payer benefit evidence'
            : identityMismatch ||
                payerOrderMissing ||
                result.price_range?.mode === 'alternatives' ||
                !result.codes?.length
              ? 'Payer benefit evidence'
              : 'Benefits used for this visit'}
      </h3>
      <p>
        {result.payment_mode === 'cash'
          ? 'These benefits do not set the clinic cash price.'
          : historicalOnly
            ? 'The protected payer response could not be reopened. These saved terms are for review only. Rerun the payer check before collection.'
            : identityMismatch || payerOrderMissing
              ? `Payer benefit rows were returned. Resolve ${identityMismatch && payerOrderMissing ? 'patient identity and payer order' : identityMismatch ? 'patient identity' : 'payer order'} before applying member balances.`
              : result.price_range?.mode === 'alternatives' || !result.codes?.length
                ? 'Payer benefit rows were returned. The clinician-documented CPT is needed to apply them.'
                : 'Matched to the selected services, visit date and location.'}
      </p>
      {!identityMismatch &&
        !payerOrderMissing &&
        !!result.benefits?.oop_annual_conflict_ids?.length && (
          <div className="service-evidence">
            {oopConflictReason(true)}
            <ConflictingOopRows benefits={result.benefits} annual />
          </div>
        )}
      {!identityMismatch &&
        !payerOrderMissing &&
        !!result.benefits?.oop_remaining_conflict_ids?.length && (
          <div className="service-evidence">
            {oopConflictReason(false)}
            <ConflictingOopRows benefits={result.benefits} annual={false} />
          </div>
        )}
      {!identityMismatch &&
        !payerOrderMissing &&
        displayedServices.map((row) => {
          const terms = result.price_range?.lines?.find((line) => line.id === row.id)?.benefits
          return (
            <article className="service-evidence" key={row.id}>
              <h4>
                {sharedAlternativeTerms
                  ? `Shared ${/^959/.test(row.code) ? 'nerve-study' : 'office-visit'} terms for CPT alternatives`
                  : `CPT ${row.code} · ${row.benefit}`}
              </h4>
              {sharedAlternativeTerms && (
                <p>
                  {row.benefit} for CPTs {services.map((service) => service.code).join(', ')}.
                </p>
              )}
              <dl>
                <div>
                  <dt>Benefit source</dt>
                  <dd>
                    {row.benefit_source || result.benefit_source || 'Payer source not retained'}
                  </dd>
                </div>
                {row.copay_cents != null && (
                  <div>
                    <dt>Applicable copay</dt>
                    <dd>
                      {money(row.copay_cents)}
                      {responseRow(terms?.copay_id)}
                    </dd>
                  </div>
                )}
                {row.coinsurance_percent != null && (
                  <div>
                    <dt>Coinsurance</dt>
                    <dd>
                      {row.coinsurance_percent}%{responseRow(terms?.coinsurance_id)}
                    </dd>
                  </div>
                )}
                {row.deductible_remaining_cents != null && (
                  <div>
                    <dt>Deductible remaining</dt>
                    <dd>
                      {money(row.deductible_remaining_cents)}
                      {responseRow(terms?.deductible_id)}
                    </dd>
                  </div>
                )}
                {row.deductible_annual_cents != null && (
                  <div>
                    <dt>Annual deductible</dt>
                    <dd>
                      {money(row.deductible_annual_cents)}
                      {responseRow(terms?.deductible_annual_id)}
                    </dd>
                  </div>
                )}
                <div>
                  <dt>Deductible for this service</dt>
                  <dd>
                    {
                      {
                        DOES_NOT_APPLY: 'Does not apply',
                        APPLIES: 'Applies first',
                        ALREADY_MET: 'Already met',
                        UNKNOWN: 'Payer did not specify for this service',
                      }[row.deductible_applicability || 'UNKNOWN']
                    }
                    {responseRow(terms?.deductible_applicability_ids?.join(' · '))}
                  </dd>
                </div>
                {row.oop_remaining_cents != null && (
                  <div>
                    <dt>Out-of-pocket remaining</dt>
                    <dd>
                      {money(row.oop_remaining_cents)}
                      {responseRow(terms?.oop_id)}
                    </dd>
                  </div>
                )}
              </dl>
              {!!row.selected_benefit_ids?.length && (
                <details open={readOnly || undefined}>
                  <summary>Response rows used</summary>
                  <small>{row.selected_benefit_ids.join(' · ')}</small>
                </details>
              )}
              {row.oop_remaining_cents === 0 && (
                <p>
                  The payer reports $0 remaining on this out-of-pocket benefit. This figure alone
                  does not set today’s collection amount for the selected care and provider network.
                </p>
              )}
            </article>
          )
        })}
      {!services.length && !identityMismatch && !payerOrderMissing && (
        <p>
          {result.benefits?.active
            ? 'Payer benefit rows were returned. No planned CPT was selected.'
            : 'Payer benefit rows for the planned service were not returned.'}
        </p>
      )}
      {!identityMismatch &&
        !payerOrderMissing &&
        result.benefits?.network_status !== 'out_of_network' &&
        !!otherNetworkTerms.length && (
          <div className="service-evidence">
            <h4>Other network terms reported by the plan</h4>
            <p>
              The plan also returned out-of-network terms. They do not show whether this doctor is
              out of network.
            </p>
            <ul>
              {otherNetworkTerms.map((row) => (
                <li key={row.id}>
                  {row.procedure_code
                    ? `CPT ${row.procedure_code}`
                    : `Service category ${row.service_type_code || 'not stated'}`}{' '}
                  · {row.kind.replaceAll('_', ' ')}{' '}
                  {row.percent != null
                    ? `${row.percent}%`
                    : row.amount_cents != null
                      ? money(row.amount_cents)
                      : 'amount not returned'}
                </li>
              ))}
            </ul>
          </div>
        )}
    </section>
  )
}

export function AppointmentNotes({ result }: { result: PrevisitEstimate }) {
  const fields = result.appointment_context?.imported_fields || []
  const isStatus = (label: string) =>
    /^(?:(?:appointment|encounter|visit)[ _-]+)?status$/i.test(label.trim())
  const statuses = fields.filter((field) => isStatus(field.label))
  const details = fields.filter((field) => !isStatus(field.label))
  return (
    <section className="visit-imported">
      <h3>{result.reason || 'Scheduled visit'}</h3>
      <p className="visit-muted">
        {[
          result.appointment_context?.rendering_provider?.name,
          result.appointment_context?.location,
        ]
          .filter(Boolean)
          .join(' · ')}
      </p>
      {statuses.map((field, index) => (
        <p className="visit-muted" key={index}>
          Appointment status: {field.text}
        </p>
      ))}
      {details.map((field, index) => (
        <article key={`${field.source}:${index}`}>
          <h3>{field.label}</h3>
          <p>{field.text}</p>
          <small>From eCW{field.updated_at ? ` · ${visitDate(field.updated_at)}` : ''}</small>
        </article>
      ))}
    </section>
  )
}

export function CostPicture({ result }: { result: PrevisitEstimate }) {
  const price = result.price_range
  const patient = price?.patient_min_cents,
    insurer = price?.plan_min_cents
  const inNetwork =
    result.benefits?.network_status === 'in_network' &&
    ['clinic_library_contract', 'payer_participation_message'].includes(
      result.benefits.network_source || '',
    )
  const split =
    inNetwork &&
    price?.complete &&
    price.mode === 'services' &&
    patient != null &&
    insurer != null &&
    patient === price.patient_max_cents &&
    insurer === price.plan_max_cents &&
    patient + insurer > 0
  if (result.payment_mode === 'cash')
    return (
      <div className="cost-picture">
        <Wallet size={36} aria-hidden="true" />
        <h3>Clinic cash price</h3>
        <p>{result.cash_quote?.message || 'The clinic’s saved price for the selected services.'}</p>
      </div>
    )
  if (inNetwork && result.planning_estimate?.basis === 'out_of_pocket_limit')
    return (
      <div className="cost-picture cost-limit">
        <div className="cost-emblem">
          <ShieldCheck size={42} aria-hidden="true" />
        </div>
        <h3>Out-of-pocket limit reached</h3>
        <p>
          Payer reports <strong>$0 remaining</strong> for in-network care.
        </p>
        <span>Covered services · assumptions apply</span>
      </div>
    )
  if (result.benefits?.network_status === 'out_of_network')
    return (
      <div className="cost-picture">
        <Network size={36} aria-hidden="true" />
        <h3>Out-of-network benefits</h3>
        <p>
          {price?.complete
            ? 'The known share uses the payer’s allowed amount. Extra doctor charges are still unknown.'
            : 'The plan returned benefit terms, but the payer’s allowed price is still unknown.'}
        </p>
      </div>
    )
  if (split)
    return (
      <div className="cost-picture">
        <span className="visit-label">Estimated cost sharing</span>
        <div
          className="cost-share-bar"
          role="img"
          aria-label={`Patient ${money(patient)}, insurer ${money(insurer)}`}
        >
          <span style={{ flex: patient }} />
          <span style={{ flex: insurer }} />
        </div>
        <dl className="cost-share-legend">
          <div>
            <dt>
              <i />
              Patient
            </dt>
            <dd>{money(patient)}</dd>
          </div>
          <div>
            <dt>
              <i />
              Insurance
            </dt>
            <dd>{money(insurer)}</dd>
          </div>
        </dl>
        <p>
          Contracted visit total <strong>{money(patient + insurer)}</strong>
        </p>
      </div>
    )
  return (
    <div className="cost-picture">
      <Network size={36} aria-hidden="true" />
      <h3>{result.benefits?.active ? 'Policy benefits received' : 'Coverage check'}</h3>
      <p>
        {result.benefits?.active
          ? 'A final insured amount also depends on the service, provider network and clinic contract.'
          : 'The saved clinic cash price is available separately when provided.'}
      </p>
    </div>
  )
}

export function RequirementCard({
  title,
  evidence,
  icon,
  readOnly = false,
  needsCodeSelection = false,
  plannedCodes = [],
  payerName,
}: {
  title: string
  evidence?: RequirementEvidence
  icon: ReactNode
  readOnly?: boolean
  needsCodeSelection?: boolean
  plannedCodes?: string[]
  payerName?: string | null
}) {
  const status = evidence?.status || 'Verification required'
  const codeLabel = `${plannedCodes.length === 1 ? 'CPT' : 'CPTs'} ${plannedCodes.join(', ')}`
  const missingRuleAction = plannedCodes.length
    ? `Ask ${payerName || 'the payer'} whether ${codeLabel} ${plannedCodes.length === 1 ? 'needs' : 'need'} ${title.toLowerCase()}`
    : 'Document the planned CPT in eCW'
  const reason =
    evidence?.reason ||
    'The saved plan information does not state this rule for the selected services.'
  const sources = evidence?.sources || []
  return (
    <div
      className={
        status === 'Not required'
          ? 'is-confirmed'
          : status === 'Required' && evidence?.approval_status === 'Verified'
            ? 'is-confirmed'
            : 'is-review'
      }
    >
      {icon}
      <div className="requirement-content">
        {title}
        <strong>
          {status === 'Required'
            ? 'Yes'
            : status === 'Not required'
              ? 'No'
              : needsCodeSelection
                ? 'Choose CPT level'
                : missingRuleAction}
        </strong>
        {evidence && (
          <small>
            {title === 'Referral'
              ? `Referral obtained: ${evidence.referral_obtained || 'UNKNOWN'}`
              : `PA approval: ${evidence.pa_approval || 'UNVERIFIED'}`}
          </small>
        )}
        <details className="requirement-evidence" open={readOnly || undefined}>
          <summary>Why and sources</summary>
          <p>{reason}</p>
          {evidence?.member_specific_check && <p>Needs member-specific check.</p>}
          {sources.map((source, index) => (
            <p key={`${source.document_id || source.label}:${source.page || index}`}>
              <RequirementSourceLink source={source} />
            </p>
          ))}
          {!sources.length && <p>No source states a rule for this service.</p>}
          {evidence?.approval_reference && (
            <p>
              Approval reference: {evidence.approval_reference}
              {evidence.approval_verified_at
                ? ` · verified ${visitDate(evidence.approval_verified_at)}`
                : ''}
            </p>
          )}
          {evidence?.verification && <p>{evidence.verification}</p>}
          {evidence?.contact && <p>{evidence.contact}</p>}
          {evidence?.verify_url && (
            <p>
              <a href={evidence.verify_url} target="_blank" rel="noopener noreferrer">
                Open payer portal
              </a>
            </p>
          )}
        </details>
      </div>
    </div>
  )
}

export function RequirementCards({
  result,
  readOnly = false,
  payerName,
}: {
  result: PrevisitEstimate
  readOnly?: boolean
  payerName?: string | null
}) {
  const plannedCodes = result.codes?.map((row) => row.code)
  const needsCodeSelection = result.price_range?.mode === 'alternatives'
  const bothAwaitCode =
    result.requirement_evidence?.referral?.status === 'Verification required' &&
    result.requirement_evidence?.authorization?.status === 'Verification required' &&
    (needsCodeSelection || !plannedCodes?.length)
  if (bothAwaitCode)
    return (
      <div className="is-review">
        <FileText size={20} aria-hidden="true" />
        <div className="requirement-content">
          Referral and prior authorization
          <strong>
            {needsCodeSelection
              ? result.provisional_scenario?.kind === 'ncs_study_count'
                ? 'Choose a planning study-count level to narrow these rules'
                : 'Choose an anticipated CPT level to narrow these rules'
              : 'Document the planned CPT in eCW'}
          </strong>
        </div>
      </div>
    )
  return (
    <>
      <RequirementCard
        title="Referral"
        evidence={result.requirement_evidence?.referral}
        icon={<FileText size={20} aria-hidden="true" />}
        readOnly={readOnly}
        needsCodeSelection={needsCodeSelection}
        plannedCodes={plannedCodes}
        payerName={payerName}
      />
      <RequirementCard
        title="Prior authorization"
        evidence={result.requirement_evidence?.authorization}
        icon={<ShieldCheck size={20} aria-hidden="true" />}
        readOnly={readOnly}
        needsCodeSelection={needsCodeSelection}
        plannedCodes={plannedCodes}
        payerName={payerName}
      />
    </>
  )
}
