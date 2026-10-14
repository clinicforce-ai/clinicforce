import type { InsuranceV1Model } from './useInsuranceV1Model'
import { label, authorizationLabel } from './v1-display'

export default function AuthorizationStage({
  model,
  insuranceCase,
}: {
  model: InsuranceV1Model
  insuranceCase: NonNullable<InsuranceV1Model['insuranceCase']>
}) {
  const { busy, advance, reviewing, documents } = model
  return (
    <>
      <p className="section-kicker">5 · Authorization & referral</p>
      <h2>Review requirements for your selected codes</h2>
      <p>
        These findings come from the benefits and agreement you already uploaded. No separate
        authorization document is needed for this review. A requirement is not an approval; a
        missing finding does not mean “not required.”
      </p>
      <div className="authorization-list">
        {insuranceCase.service_lines
          .filter((line) => line.selected)
          .map((line) => (
            <article key={line.code}>
              <strong>
                {line.code} · {line.description}
              </strong>
              <span>Authorization: {authorizationLabel(line.authorization_status)}</span>
              <span>Referral: {authorizationLabel(line.referral_status)}</span>
              {line.requirement_evidence?.map((entry, index) => (
                <details className="source-excerpt" key={index}>
                  <summary>
                    {label(entry.kind)} · {entry.network} · {entry.source_filename} ·{' '}
                    {entry.reference}
                  </summary>
                  <blockquote>{entry.quote}</blockquote>
                </details>
              ))}
            </article>
          ))}
      </div>
      {!!documents.contract?.findings?.policy_terms?.length && (
        <details className="source-excerpt">
          <summary>Review agreement conditions for authorization and referrals</summary>
          <p>
            These clauses may depend on the member’s plan. They are not automatic yes/no findings
            for every code.
          </p>
          {documents.contract.findings.policy_terms.map((term, index) => (
            <section key={index}>
              <small>
                {documents.contract.filename} · {term.reference}
              </small>
              <blockquote>{term.quote}</blockquote>
            </section>
          ))}
        </details>
      )}
      {!reviewing && (
        <button
          className="button button-primary"
          type="button"
          disabled={busy}
          onClick={() => advance('authorization')}
        >
          Review and show pay estimate
        </button>
      )}
    </>
  )
}
