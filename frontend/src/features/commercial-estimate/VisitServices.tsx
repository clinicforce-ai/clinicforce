import { RequirementDecision } from './RequirementDecision'
import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { clinicalName, money } from './service-code'

export default function VisitServices({
  result,
  readOnly = false,
}: {
  result: PrevisitEstimate
  readOnly?: boolean
}) {
  const lines = result.price_range?.lines || []
  if (!lines.length) return null
  const cash = result.payment_mode === 'cash'
  const rateSource = (value?: string) =>
    value?.startsWith('library:')
      ? result.library_pricing?.find((document) => document.document_id === value.slice(8))
          ?.label || value
      : value
  return (
    <section className="visit-services">
      <div className="visit-section-heading">
        <h2>Planned services</h2>
      </div>
      <div className="clinical-table-wrap">
        <table className="clinical-service-table">
          <thead>
            <tr>
              <th>Procedure</th>
              {!cash && (
                <>
                  <th>Coverage</th>
                  <th>Referral</th>
                  <th>Prior auth</th>
                </>
              )}
              <th>{cash ? 'Cash price' : 'Estimated patient share'}</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((row, index) => {
              const estimate = result.collection_guidance?.services.find(
                (item) => item.id === row.id,
              )
              const requirement = result.requirement_evidence?.procedures?.find(
                (item) => item.id === row.id || item.code === row.code,
              )
              const partialCopay =
                !cash &&
                result.collection_guidance?.basis === 'office_copay' &&
                index === lines.findIndex((item) => /^992(?:0[2-5]|1[2-5])$/.test(item.code))
              return (
                <tr key={row.id}>
                  <th scope="row">
                    {clinicalName(row)}
                    <small>
                      CPT {row.code}
                      {Number(row.units) > 1 ? ` × ${row.units}` : ''} · Planned from{' '}
                      {row.source || 'visit review'}
                    </small>
                    <small>{estimate?.benefit}</small>
                    {cash ? (
                      (row.cash_cents != null || row.cash_included) && (
                        <small>
                          Cash price source:{' '}
                          {row.cash_source || result.cash_quote?.source || 'Clinic cash schedule'}
                          {row.cash_rule?.locator ? ` · ${row.cash_rule.locator}` : ''}
                        </small>
                      )
                    ) : (
                      <>
                        <small>
                          Benefit source:{' '}
                          {row.benefit_source ||
                            estimate?.benefit_source ||
                            result.benefit_source ||
                            'Not confirmed'}
                        </small>
                        {row.allowed_cents != null ? (
                          <small>
                            Allowed price source:{' '}
                            {row.allowed_basis === 'payer_out_of_network'
                              ? 'Payer out-of-network amount'
                              : 'Clinic contract'}{' '}
                            · {rateSource(row.rate_source) || 'Source not retained'}
                            {row.rate_row_number ? ` · row ${row.rate_row_number}` : ''}
                          </small>
                        ) : (
                          <small>
                            Allowed rate:{' '}
                            {row.rate_resolution?.status === 'RATE_CONFLICT'
                              ? 'Conflicting matched rates'
                              : 'Not established for this CPT and visit scope'}
                          </small>
                        )}
                      </>
                    )}
                  </th>
                  {!cash && (
                    <>
                      <td>
                        {row.benefits?.active === false
                          ? 'Inactive'
                          : row.benefits?.coverage_status_conflict
                            ? 'Conflicting'
                            : row.benefit_basis === 'Procedure-specific payer benefit'
                              ? 'Procedure benefit returned'
                              : 'CPT coverage unverified'}
                      </td>
                      <td>
                        <RequirementDecision evidence={requirement?.referral} readOnly={readOnly} />
                      </td>
                      <td>
                        <RequirementDecision
                          evidence={requirement?.authorization}
                          readOnly={readOnly}
                        />
                      </td>
                    </>
                  )}
                  <td>
                    {cash ? (
                      row.cash_included ? (
                        'Included'
                      ) : row.cash_cents != null ? (
                        money(row.cash_cents)
                      ) : (
                        'Price unavailable'
                      )
                    ) : estimate?.patient_cents != null ? (
                      money(estimate.patient_cents)
                    ) : partialCopay ? (
                      <>
                        {money(result.collection_guidance!.amount_cents)}
                        <small>Office copay only; remaining share unknown</small>
                      </>
                    ) : (
                      'Not calculated'
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {result.price_range?.mode === 'alternatives' && (
        <p>One office consultation. These visit levels are alternatives, not separate charges.</p>
      )}
      {cash && result.cash_quote?.complete && <p>{result.cash_quote.message}</p>}
    </section>
  )
}
