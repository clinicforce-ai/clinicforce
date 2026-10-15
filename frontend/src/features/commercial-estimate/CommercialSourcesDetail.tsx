import { Trash2 } from 'lucide-react'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { FinancialHistory } from '../insurance-history/FinancialHistory'
import { visitDate, visitTime } from './format'

export default function CommercialSourcesDetail({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const {
    intakeId,
    readOnly,
    history,
    historyError,
    historyBusy,
    busy,
    cleanupPending,
    panel,
    setPanel,
    recovering,
    deleteCheck,
    loadHistory,
    price,
    copayEvidenceId,
    copaySource,
    patientPrice,
    policySources,
    coverageSource,
    guidance,
    networkSource,
    emgScenario,
  } = model
  return (
    <details
      open={readOnly || undefined}
      id="visit-detail-sources"
      className="visit-detail-section"
      onToggle={(event) => {
        if (!readOnly && event.currentTarget.open) {
          setPanel('sources')
          if (history === null && !historyBusy) void loadHistory()
        }
      }}
    >
      <summary>Audit trail & history</summary>
      <div className="visit-detail-body">
        <section className="visit-provenance">
          <dl>
            <div>
              <dt>Patient & appointment</dt>
              <dd>
                eClinicalWorks
                <small>
                  {result.ecw_retrieved_at
                    ? `${visitDate(result.ecw_retrieved_at)} · ${visitTime(result.ecw_retrieved_at)}`
                    : 'Not returned'}
                </small>
              </dd>
            </div>
            <div>
              <dt>Payer benefits</dt>
              <dd>
                {result.benefit_source || 'Not returned'}
                <small>
                  {result.checked_at
                    ? `${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}`
                    : 'Not returned'}
                </small>
              </dd>
            </div>
            <div>
              <dt>Clinical Library contract</dt>
              <dd>
                {result.network_evidence?.network_name ||
                  result.network_evidence?.reason ||
                  'Contract match not determined'}
              </dd>
            </div>
            <div>
              <dt>Cash schedule</dt>
              <dd>
                {result.cash_schedule?.source ||
                  result.practice_cash_source?.filename ||
                  'Not returned'}
              </dd>
            </div>
          </dl>
          <p>
            {coverageSource}. {networkSource || 'Provider network source unavailable'}.
          </p>
          {guidance?.basis === 'office_copay' && copayEvidenceId && (
            <p>
              Office copay: {copaySource} response, row {copayEvidenceId}. This is a payer benefit,
              not a Library price.
            </p>
          )}
          {!!result.library_pricing?.length && (
            <details className="library-diagnostics" open={readOnly || undefined}>
              <summary>Library pricing sources and extraction diagnostics</summary>
              {result.library_pricing.map((document) => (
                <div key={document.document_id}>
                  <a
                    href={`/api/staff/library/${encodeURIComponent(document.document_id)}/text`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {document.label}
                  </a>{' '}
                  · {document.summary?.status.replaceAll('_', ' ') || 'Processing'}
                  {document.summary?.issues?.map((issue, index) => (
                    <p key={index}>
                      {issue.locator ? `${issue.locator}: ` : ''}
                      {issue.reason}
                    </p>
                  ))}
                </div>
              ))}
            </details>
          )}
          {(result.insurance_fallback?.payer || result.insurance_fallback?.member_id) && (
            <p>
              Missing{' '}
              {result.insurance_fallback.payer && result.insurance_fallback.member_id
                ? 'payer and member ID'
                : result.insurance_fallback.payer
                  ? 'payer'
                  : 'member ID'}{' '}
              came from the labeled payment preference in eCW General Notes. Payer verification is
              checked separately.
            </p>
          )}
          {policySources.map((source) => (
            <p key={source.url}>
              <a href={source.url} target="_blank" rel="noreferrer">
                {source.title}
              </a>{' '}
              · {source.section}
            </p>
          ))}
          {patientPrice && !emgScenario && (
            <p>
              {price?.conditional
                ? 'Conditional patient share if unresolved requirements are cleared'
                : 'Estimated patient share after the claim'}
              : {patientPrice}.
            </p>
          )}
          {result.eligibility_reconciliation && <p>{result.eligibility_reconciliation.message}</p>}
          {!!result.identity?.differences.length && (
            <p>{result.identity.message || 'Patient identity requires review.'}</p>
          )}
          <p>
            Check ID: {result.check_id || 'Not saved'} · Response reference:{' '}
            {result.response_reference || 'Not returned'} · Estimate revision:{' '}
            {result.revision ?? 'Not saved'}
          </p>
          {!!result.audit_trail?.length && (
            <section>
              <h3>Changes to this estimate</h3>
              <ol>
                {result.audit_trail.map((entry, index) => (
                  <li key={index}>
                    {entry.changes.join(', ')} · {entry.by}
                    {entry.checked_at && (
                      <small>
                        {visitDate(entry.checked_at)} · {visitTime(entry.checked_at)}
                      </small>
                    )}
                  </li>
                ))}
              </ol>
            </section>
          )}
          {!readOnly && (
            <>
              <h3>Previous eCW visits</h3>
              {historyBusy && <p role="status">Reading visit history…</p>}
              {historyError && (
                <p>
                  {historyError}
                  <button onClick={() => void loadHistory()}>Retry</button>
                </p>
              )}
              {history?.map((visit) => (
                <article key={visit.id}>
                  <strong>
                    {visitDate(visit.start)} · {visit.reason || 'Previous visit'}
                  </strong>
                  <p>{visit.coded_services.join(', ')}</p>
                </article>
              ))}
              {history?.length === 0 && <p>No completed visits returned in the last year.</p>}
            </>
          )}
          {!readOnly && panel === 'sources' && result.check_id && (
            <FinancialHistory key={result.check_id} checkId={result.check_id} />
          )}
          {!readOnly && intakeId && result.check_id && (
            <button
              className="visit-delete-button"
              disabled={!!busy || recovering}
              onClick={() => void deleteCheck()}
            >
              <Trash2 size={15} />
              {cleanupPending ? 'Retry file cleanup' : 'Delete saved check'}
            </button>
          )}
        </section>
      </div>
    </details>
  )
}
