import { RequirementDecision } from './RequirementDecision'
import { requirementForLine } from './requirementForLine'
import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { clinicalName, money } from './service-code'
import { patientBenefitsBound } from './identityBinding'

export default function VisitServices({
  result,
  readOnly = false,
  insuredAmountSupported = false,
}: {
  result: PrevisitEstimate
  readOnly?: boolean
  insuredAmountSupported?: boolean
}) {
  const lines = result.price_range?.lines || []
  if (!lines.length) return null
  const cash = result.payment_mode === 'cash'
  const payerForAction = (!readOnly && result.stedi_payer?.name) || result.payer
  const alternatives = result.price_range?.mode === 'alternatives'
  const showInsuredShare =
    insuredAmountSupported &&
    result.collection_guidance?.basis !== 'claim_after_service' &&
    !['deferred_billing', 'action_required'].includes(result.collection_guidance?.status || '')
  const patientMatched = patientBenefitsBound(result.identity)
  const payerOrderMissing = result.recovery?.reason_code === 'multiple_active_policies'
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
              const requirement = requirementForLine(
                result.requirement_evidence?.procedures,
                row.id,
                row.code,
              )
              const officeCopayAdvance =
                showInsuredShare &&
                result.collection_guidance?.basis === 'office_copay' &&
                /^992(?:0[2-5]|1[2-5])$/.test(row.code) &&
                (alternatives ||
                  index === lines.findIndex((item) => /^992(?:0[2-5]|1[2-5])$/.test(item.code)))
              const partialCopay = officeCopayAdvance && !alternatives
              const coverage =
                row.benefits?.active === false
                  ? 'Inactive'
                  : result.benefits?.active === true && !patientMatched && payerOrderMissing
                    ? 'Payer coverage returned. Patient match and payer order unresolved'
                    : result.benefits?.active === true && !patientMatched
                      ? 'Payer coverage returned. Patient match unresolved'
                      : result.benefits?.active === true && payerOrderMissing
                        ? 'Payer coverage returned. Payer order unresolved'
                        : row.benefits?.coverage_status_conflict
                          ? 'Conflicting'
                          : row.benefit_basis === 'Procedure-specific payer benefit'
                            ? 'Procedure benefit returned'
                            : estimate?.selected_benefit_ids?.length
                              ? 'Service-category benefit returned'
                              : row.benefits?.active === true
                                ? 'Active coverage. Service benefit not identified'
                                : 'Service benefit not identified'
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
                            'Payer source unavailable'}
                        </small>
                        {row.allowed_cents != null ? (
                          <small>
                            Allowed price source:{' '}
                            {row.allowed_basis === 'cms_original_medicare_estimate'
                              ? 'CMS Original Medicare benchmark estimate'
                              : row.allowed_basis === 'payer_out_of_network'
                                ? 'Payer out-of-network amount'
                                : 'Clinic contract'}{' '}
                            · {rateSource(row.rate_source) || 'Source not retained'}
                            {row.rate_row_number ? ` · row ${row.rate_row_number}` : ''}
                          </small>
                        ) : officeCopayAdvance && alternatives ? null : (
                          <small>
                            Allowed rate:{' '}
                            {officeCopayAdvance
                              ? 'Not needed for this office copay advance'
                              : row.rate_resolution?.status === 'RATE_CONFLICT'
                                ? 'Conflicting matched rates'
                                : 'Not established for this CPT and visit scope'}
                          </small>
                        )}
                      </>
                    )}
                  </th>
                  {!cash && (
                    <>
                      <td>{coverage}</td>
                      <td>
                        <RequirementDecision
                          evidence={requirement?.referral}
                          readOnly={readOnly}
                          code={row.code}
                          kind="referral"
                          payerName={payerForAction}
                          needsCodeSelection={result.price_range?.mode === 'alternatives'}
                          lineRequirement={row.benefits?.requirements?.referral}
                        />
                      </td>
                      <td>
                        <RequirementDecision
                          evidence={requirement?.authorization}
                          readOnly={readOnly}
                          code={row.code}
                          kind="prior authorization"
                          payerName={payerForAction}
                          needsCodeSelection={result.price_range?.mode === 'alternatives'}
                          lineRequirement={row.benefits?.requirements?.authorization}
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
                    ) : alternatives ? (
                      'Select one CPT'
                    ) : showInsuredShare && estimate?.patient_cents != null ? (
                      money(estimate.patient_cents)
                    ) : partialCopay ? (
                      <>
                        {money(result.collection_guidance!.amount_cents)}
                        <small>
                          Office copay only. Other service shares follow the processed claim and
                          member protections
                        </small>
                      </>
                    ) : result.collection_guidance?.basis === 'claim_after_service' ? (
                      'No advance today under clinic policy'
                    ) : result.collection_guidance?.basis === 'cigna_fee_proxy_advance' ? (
                      'Included in visit amount'
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
      {alternatives && (
        <p>
          One office consultation. Choose the documented CPT. These alternatives are not separate
          charges
          {showInsuredShare && result.collection_guidance?.basis === 'office_copay'
            ? '. The office copay shown above applies once. Allowed rate is not needed for the office copay advance.'
            : '.'}
        </p>
      )}
      {cash && result.cash_quote?.complete && <p>{result.cash_quote.message}</p>}
    </section>
  )
}
