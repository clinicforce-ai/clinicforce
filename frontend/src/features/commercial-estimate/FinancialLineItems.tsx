import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

export default function FinancialLineItems({ result }: { result: PrevisitEstimate }) {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  if (!lines.length) return null
  return (
    <div className="financial-line-items">
      <h4>Planned services</h4>
      {lines.map((line) => {
        const share = line.calculation
        const allowed =
          line.rate_resolution?.status === 'RESOLVED' &&
          ['clinic_contract', 'payer_out_of_network', 'cms_original_medicare_estimate'].includes(
            line.allowed_basis || '',
          )
            ? line.allowed_cents
            : null
        return (
          <section key={line.id}>
            <strong>
              CPT/HCPCS {line.code} × {line.units || 1}
            </strong>
            <dl className="arrival-money-picture">
              <div>
                <dt>
                  {line.allowed_basis === 'cms_original_medicare_estimate'
                    ? 'CMS Medicare benchmark estimate'
                    : 'Payer allowed amount'}
                </dt>
                <dd>{allowed != null ? money(allowed) : 'Not established'}</dd>
              </div>
              {share && (
                <>
                  <div>
                    <dt>Copay</dt>
                    <dd>{money(share.copay_cents)}</dd>
                  </div>
                  <div>
                    <dt>Deductible portion</dt>
                    <dd>{money(share.deductible_cents)}</dd>
                  </div>
                  <div>
                    <dt>Coinsurance</dt>
                    <dd>
                      {share.coinsurance_percent ?? line.benefits.coinsurance_percent ?? '—'}% of{' '}
                      {share.coinsurance_base_cents != null
                        ? money(share.coinsurance_base_cents)
                        : 'unresolved base'}{' '}
                      = {money(share.coinsurance_cents)}
                    </dd>
                  </div>
                  <div>
                    <dt>Estimated plan share</dt>
                    <dd>{money(share.plan_cents)}</dd>
                  </div>
                  <div>
                    <dt>Estimated patient share</dt>
                    <dd>{money(share.patient_cents)}</dd>
                  </div>
                </>
              )}
            </dl>
            {!!line.benefits.selected_benefit_ids?.length && (
              <small>Benefit response rows: {line.benefits.selected_benefit_ids.join(', ')}</small>
            )}
          </section>
        )
      })}
    </div>
  )
}
