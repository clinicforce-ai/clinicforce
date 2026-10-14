import { ShieldCheck, Network, FileText, Wallet } from 'lucide-react'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { RequirementCard } from './EstimatePanels'
import { money, range, visitDate, visitTime } from './format'

export default function CommercialOverview({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const {
    readOnly,
    busy,
    recovering,
    saveReview,
    cashPrice,
    planName,
    tooEarly,
    policyStatus,
    contractEvidence,
    contractUrl,
    cash,
    coverageTone,
    calculating,
    previousEstimate,
    guidance,
    contractCandidate,
    networkLabel,
    networkTone,
    billingInstruction,
    amount,
    deferred,
    amountText,
    amountLabel,
    amountParts,
    officeDeductible,
    summaryExplanation,
    anticipatedService,
  } = model
  return (
    <section id="visit-estimate" className="visit-overview" aria-labelledby="payment-heading">
      <div className="overview-heading">
        <h2 id="payment-heading">{cash ? 'Self-pay estimate' : 'Arrival collection'}</h2>
        {!readOnly && (
          <div className="payment-toggle" aria-label="Payment method">
            <button
              aria-pressed={!cash}
              disabled={!!busy || recovering}
              onClick={() => {
                if (cash) void saveReview({ payment_mode: 'insurance' })
              }}
            >
              <ShieldCheck size={16} />
              Insurance
            </button>
            <button
              aria-pressed={cash}
              disabled={!!busy || recovering}
              onClick={() => {
                if (!cash) void saveReview({ payment_mode: 'cash' })
              }}
            >
              <Wallet size={16} />
              Cash
            </button>
          </div>
        )}
      </div>
      <div className="overview-amounts">
        <div
          className={`arrival-total${!amount && !calculating && !tooEarly ? ' is-blocked' : ''}`}
        >
          <span>{amountLabel}</span>
          <strong className={!amount ? 'amount-pending' : undefined} role="status">
            {amountText}
          </strong>
          <p>{summaryExplanation}</p>
          {!cash &&
            amount &&
            !previousEstimate &&
            (amountParts?.copay_cents != null || guidance?.basis === 'office_copay') && (
              <dl className="arrival-money-picture" aria-label="What the arrival amount includes">
                <div>
                  <dt>Copay</dt>
                  <dd>
                    {amountParts?.copay_cents != null
                      ? money(amountParts.copay_cents)
                      : guidance?.basis === 'office_copay' && guidance.amount_cents != null
                        ? money(guidance.amount_cents)
                        : '—'}
                  </dd>
                </div>
                <div>
                  <dt>Deductible</dt>
                  <dd>
                    {amountParts?.deductible_cents != null
                      ? money(amountParts.deductible_cents)
                      : officeDeductible === 'DOES_NOT_APPLY'
                        ? 'Does not apply'
                        : officeDeductible === 'ALREADY_MET'
                          ? 'Already met'
                          : 'See assumptions'}
                  </dd>
                </div>
                <div>
                  <dt>Coinsurance</dt>
                  <dd>
                    {amountParts?.coinsurance_cents != null
                      ? money(amountParts.coinsurance_cents)
                      : 'See assumptions'}
                  </dd>
                </div>
              </dl>
            )}
          {cash && !amount && !!result.cash_schedule?.packages.length && !readOnly && (
            <button
              className="cash-package-shortcut"
              onClick={() => {
                const detail = document.getElementById(
                  'visit-detail-services',
                ) as HTMLDetailsElement | null
                if (detail) detail.open = true
                detail?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }}
            >
              View {result.cash_schedule.packages.length} clinic cash packages
            </button>
          )}
          {result.payment_explanation?.notice && !previousEstimate && (
            <p className="visit-identity-notice">{result.payment_explanation.notice}</p>
          )}
          {guidance?.billing_note && (
            <details open={readOnly || undefined}>
              <summary>eCW billing note · {visitDate(guidance.billing_note.updated_at)}</summary>
              <blockquote>{guidance.billing_note.text}</blockquote>
            </details>
          )}
          {!cash && (
            <details className="summary-assumptions" open={readOnly || undefined}>
              <summary>Assumptions used</summary>
              <p>
                {previousEstimate
                  ? 'Saved result · reconfirm before collection.'
                  : billingInstruction
                    ? 'Follow the recorded billing instruction for this visit.'
                    : deferred
                      ? 'No insurance advance today · bill after the claim.'
                      : amount
                        ? 'Advance estimate for the selected services.'
                        : calculating
                          ? 'Checking applicable benefits…'
                          : tooEarly
                            ? 'Appointment found · check within 7 days.'
                            : 'No amount calculated.'}
              </p>
              <p>
                {guidance?.patient_message ||
                  'The final patient share follows the insurer’s processed claim.'}
              </p>
              {result.encounter_summary && (
                <dl className="arrival-breakdown" aria-label="Patient cost breakdown">
                  <div>
                    <dt>Copay included</dt>
                    <dd>
                      {result.encounter_summary.components.copay_cents == null
                        ? 'Unknown'
                        : money(result.encounter_summary.components.copay_cents)}
                    </dd>
                  </div>
                  <div>
                    <dt>Deductible included</dt>
                    <dd>
                      {result.encounter_summary.components.deductible_cents == null
                        ? 'Unknown'
                        : money(result.encounter_summary.components.deductible_cents)}
                    </dd>
                  </div>
                  <div>
                    <dt>Coinsurance included</dt>
                    <dd>
                      {result.encounter_summary.components.coinsurance_cents == null
                        ? 'Unknown'
                        : money(result.encounter_summary.components.coinsurance_cents)}
                    </dd>
                  </div>
                  <div>
                    <dt>Prior payments</dt>
                    <dd>
                      {result.encounter_summary.payments_verified_cents == null
                        ? 'Not available'
                        : `−${money(result.encounter_summary.payments_verified_cents)}`}
                    </dd>
                  </div>
                  <div>
                    <dt>
                      {result.encounter_summary.conditional
                        ? 'Conditional patient estimate'
                        : 'Full patient estimate'}
                    </dt>
                    <dd>
                      {result.encounter_summary.estimated_total_patient_min_cents != null &&
                      result.encounter_summary.estimated_total_patient_max_cents != null
                        ? range(
                            result.encounter_summary.estimated_total_patient_min_cents,
                            result.encounter_summary.estimated_total_patient_max_cents,
                          )
                        : 'Not yet calculated'}
                    </dd>
                  </div>
                  <div>
                    <dt>Estimated later balance</dt>
                    <dd>
                      {result.encounter_summary.estimated_later_balance_cents == null
                        ? 'Unknown'
                        : money(result.encounter_summary.estimated_later_balance_cents)}
                    </dd>
                  </div>
                </dl>
              )}
              {!!guidance?.patient_actions.length && (
                <div>
                  <b>Tell the patient:</b>
                  <ul>
                    {guidance.patient_actions.map((action) => (
                      <li key={action}>{action}</li>
                    ))}
                  </ul>
                </div>
              )}
              {!!guidance?.assumptions.length && (
                <ul>
                  {guidance.assumptions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}
            </details>
          )}
        </div>
        <div className="arrival-context">
          <span className="visit-label">Anticipated service</span>
          <h3>{anticipatedService || result.reason || 'Service not established'}</h3>
          {result.reason && result.reason !== anticipatedService && (
            <p>eCW visit reason: {result.reason}</p>
          )}
          <p>
            {readOnly
              ? 'Service plan recorded for this appointment.'
              : 'Based on the scheduled appointment. Modify the services below if the planned care changes.'}
          </p>
        </div>
      </div>
      <div className="visit-snapshots" aria-label="Coverage summary">
        <div className={coverageTone}>
          <ShieldCheck size={20} aria-hidden="true" />
          <span>
            Coverage<strong>{policyStatus}</strong>
            <small>
              {result?.benefit_source
                ? `Checked via ${result.benefit_source}`
                : 'Payer result unavailable'}
            </small>
          </span>
        </div>
        <div className={networkTone}>
          <Network size={20} aria-hidden="true" />
          <span>
            Network<strong>{networkLabel}</strong>
            {result?.network_evidence?.reason && <small>{result.network_evidence.reason}</small>}
            {contractCandidate && !result?.network_evidence?.reason && (
              <small>{contractCandidate} contract could not be matched to this visit</small>
            )}
            {contractUrl && (
              <small>
                <a href={contractUrl} target="_blank" rel="noopener noreferrer">
                  {contractEvidence?.related_document_id
                    ? 'Clinic Library contract'
                    : `${contractEvidence?.network_name || 'Payer plan'} contract`}
                </a>
              </small>
            )}
          </span>
        </div>
        <RequirementCard
          title="Referral"
          evidence={result.requirement_evidence?.referral}
          icon={<FileText size={20} aria-hidden="true" />}
          readOnly={readOnly}
        />
        <RequirementCard
          title="Prior authorization"
          evidence={result.requirement_evidence?.authorization}
          icon={<ShieldCheck size={20} aria-hidden="true" />}
          readOnly={readOnly}
        />
      </div>
      <p className="arrival-sources">
        Sources: eCW
        {tooEarly
          ? ' · Payer check deferred until closer to the visit'
          : result.checked_at
            ? ` + ${result.benefit_source || 'payer source not retained'} · benefits checked ${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}`
            : ' · Current payer benefits unavailable'}
      </p>
      <div className="overview-plan-name">
        <ShieldCheck size={16} aria-hidden="true" />
        <span>
          {cash
            ? 'Clinic self-pay schedule'
            : planName || result.payer || 'Policy details not returned'}
        </span>
        {!cash && cashPrice && <span className="cash-comparison">Cash option {cashPrice}</span>}
      </div>
    </section>
  )
}
