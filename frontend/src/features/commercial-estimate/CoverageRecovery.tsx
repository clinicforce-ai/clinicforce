import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export default function CoverageRecovery({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const {
    readOnly,
    candidateSuggestion,
    setCandidateSuggestion,
    payerSuggestion,
    setPayerSuggestion,
    busy,
    recovering,
    selectCoverage,
    failed,
    failure,
    guidance,
    unavailableReason,
  } = model
  return (
    <>
      {!!result.recovery?.payer_routes?.length && (
        <section className="visit-recovery">
          <h3>Payer suggestions</h3>
          <p>
            Optional suggestions from the payer directory. A selected route still needs a successful
            eligibility response.
          </p>
          {result.recovery.payer_routes.length >= 2 && (
            <label>
              Payer suggestions
              <select
                value={
                  result.recovery.payer_routes.some((row) => row.id === payerSuggestion)
                    ? payerSuggestion
                    : ''
                }
                onChange={(event) => setPayerSuggestion(event.target.value)}
              >
                <option value="">View a payer suggestion</option>
                {result.recovery.payer_routes.map((route) => (
                  <option key={route.id} value={route.id}>
                    {route.name} · {route.payer_id || route.id}
                  </option>
                ))}
              </select>
            </label>
          )}
          {result.recovery.payer_routes
            .filter(
              (route) => result.recovery!.payer_routes!.length < 2 || route.id === payerSuggestion,
            )
            .map((route) => (
              <article key={route.id}>
                <strong>{route.name}</strong>
                <p>
                  Payer ID: {route.payer_id || route.id} · Stedi route: {route.id}
                </p>
                {!!route.names.length && <p>Also known as: {route.names.join(', ')}</p>}
                {!!route.aliases.length && <p>Payer ID aliases: {route.aliases.join(', ')}</p>}
                <p>
                  {route.match_basis === 'directory_suggestion'
                    ? 'Suggested name match — review against the card'
                    : 'Matches the recorded payer name or ID'}
                  {route.eligibility === 'ENROLLMENT_REQUIRED'
                    ? ' · Provider enrollment required'
                    : ''}
                </p>
                {!readOnly && (
                  <button
                    disabled={!!busy || recovering || result.recovery?.state === 'indeterminate'}
                    onClick={() => void selectCoverage(route.id, true)}
                  >
                    Check eligibility with this payer
                  </button>
                )}
              </article>
            ))}
        </section>
      )}
      {failed && (
        <section className="visit-recovery">
          <h3>
            {recovering
              ? 'Finding coverage…'
              : result.cash_fallback
                ? 'Automatic insurance checks finished'
                : failure?.title || 'Coverage check incomplete'}
          </h3>
          <p>{guidance?.reason || unavailableReason}</p>
          {result.recovery?.message && <p>{result.recovery.message}</p>}
          {result.recovery && (result.recovery.reason_code || result.recovery.reference) && (
            <small className="visit-muted">
              {result.recovery.reason_code?.replaceAll('_', ' ')}
              {result.recovery.reason_code && result.recovery.reference ? ' · ' : ''}
              {result.recovery.reference ? `Stedi discovery ${result.recovery.reference}` : ''}
            </small>
          )}
        </section>
      )}
      {!!result.recovery?.candidates?.length && (
        <section className="visit-recovery" aria-label="Returned insurance candidates">
          <h3>Returned insurance candidates</h3>
          <p>
            Returned policies are retained, including inactive policies. Suggestions are optional;
            automatic estimation does not wait for selection.
          </p>
          {result.recovery.candidates.length >= 2 && (
            <label>
              Insurance policy suggestions
              <select
                value={
                  result.recovery.candidates.some((row) => row.id === candidateSuggestion)
                    ? candidateSuggestion
                    : ''
                }
                onChange={(event) => setCandidateSuggestion(event.target.value)}
              >
                <option value="">View a policy suggestion</option>
                {result.recovery.candidates.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.payer || 'Unknown payer'} ·{' '}
                    {candidate.member_id || 'Member ID not returned'} ·{' '}
                    {candidate.coverage_evidence?.status.toLowerCase() || 'Status unknown'}
                  </option>
                ))}
              </select>
            </label>
          )}
          {result.recovery.candidates
            .filter(
              (candidate) =>
                result.recovery!.candidates!.length < 2 || candidate.id === candidateSuggestion,
            )
            .map((candidate) => (
              <article key={candidate.id}>
                <strong>
                  {candidate.payer || 'Payer not returned'} ·{' '}
                  {candidate.coverage_evidence &&
                  ['INACTIVE', 'EXPIRED', 'FUTURE', 'CONFLICT'].includes(
                    candidate.coverage_evidence.status,
                  )
                    ? candidate.coverage_evidence.status.toLowerCase()
                    : candidate.verification_status === 'VERIFIED'
                      ? 'Identity and coverage evidence matched'
                      : 'Coverage not verified'}
                </strong>
                <p>{candidate.plans.join(' · ')}</p>
                <dl>
                  <div>
                    <dt>Candidate member ID</dt>
                    <dd>{candidate.member_id || 'Not returned'}</dd>
                  </div>
                  <div>
                    <dt>Group</dt>
                    <dd>{candidate.group_number || 'Not returned'}</dd>
                  </div>
                  {candidate.coverage_evidence && (
                    <div>
                      <dt>Coverage dates</dt>
                      <dd>
                        {candidate.coverage_evidence.start || 'Start not returned'} –{' '}
                        {candidate.coverage_evidence.end || 'End not returned'}
                      </dd>
                    </div>
                  )}
                  <div>
                    <dt>Policyholder</dt>
                    <dd>
                      {[candidate.subscriber?.firstName, candidate.subscriber?.lastName]
                        .filter(Boolean)
                        .join(' ') || 'Not returned'}
                    </dd>
                  </div>
                  <div>
                    <dt>Policyholder DOB</dt>
                    <dd>{candidate.subscriber?.dateOfBirth || 'Unknown'}</dd>
                  </div>
                  {candidate.dependent && (
                    <div>
                      <dt>Dependent</dt>
                      <dd>
                        {[candidate.dependent.firstName, candidate.dependent.lastName]
                          .filter(Boolean)
                          .join(' ')}{' '}
                        · DOB {candidate.dependent.dateOfBirth || 'Unknown'}
                      </dd>
                    </div>
                  )}
                  <div>
                    <dt>Source</dt>
                    <dd>{candidate.source?.replaceAll('_', ' ') || 'Saved insurance discovery'}</dd>
                  </div>
                </dl>
                {!!candidate.reason_codes?.length && (
                  <ul>
                    {candidate.reason_codes.map((reason) => (
                      <li key={reason}>{reason.replaceAll('_', ' ').toLowerCase()}</li>
                    ))}
                  </ul>
                )}
                {!readOnly && (
                  <button
                    disabled={
                      !!busy ||
                      recovering ||
                      result.recovery?.state === 'indeterminate' ||
                      candidate.reason_codes?.some((reason) =>
                        [
                          'PATIENT_NAME_MISMATCH',
                          'PATIENT_DOB_MISMATCH',
                          'PATIENT_DOB_MISSING',
                          'RELATIONSHIP_CONFLICT',
                        ].includes(reason),
                      )
                    }
                    onClick={() => void selectCoverage(candidate.id)}
                  >
                    Check this policy
                  </button>
                )}
              </article>
            ))}
        </section>
      )}
      {!!result.insurance_hints?.length && (
        <section className="visit-recovery">
          <h3>Recorded insurance clues</h3>
          <p>These details can support an inquiry; they do not verify active coverage.</p>
          {result.insurance_hints.map((hint, index) => (
            <p key={index}>
              {hint.payer || 'Payer unknown'} · Member ID {hint.member_id || 'unknown'} ·{' '}
              {hint.sources.join(', ')}
            </p>
          ))}
        </section>
      )}
      {result.insurance_diagnosis && (
        <section className="visit-recovery" aria-label="Insurance check diagnosis">
          <h3>Insurance check status</h3>
          <p>
            Identity: {result.insurance_diagnosis.identityStatus.replaceAll('_', ' ').toLowerCase()}{' '}
            · Eligibility:{' '}
            {result.insurance_diagnosis.eligibilityStatus.replaceAll('_', ' ').toLowerCase()} ·
            Insurance estimate:{' '}
            {result.insurance_diagnosis.pricingStatus.replaceAll('_', ' ').toLowerCase()}
          </p>
          {result.insurance_diagnosis.rawCandidateCount != null && (
            <p>
              {result.insurance_diagnosis.rawCandidateCount} returned ·{' '}
              {result.insurance_diagnosis.retainedCandidateCount} retained ·{' '}
              {result.insurance_diagnosis.verifiedCandidateCount} matched candidates
            </p>
          )}
          {result.insurance_diagnosis.enrollment && (
            <p>
              Provider enrollment:{' '}
              {result.insurance_diagnosis.enrollment.status.replaceAll('_', ' ').toLowerCase()}
            </p>
          )}
          {!!result.insurance_diagnosis.pricingReasons.length && (
            <ul>
              {result.insurance_diagnosis.pricingReasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {result.cash_fallback && <p>{result.cash_fallback.reason}</p>}
        </section>
      )}
    </>
  )
}
