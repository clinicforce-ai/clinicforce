import type { PrevisitEstimate } from '../../services/api/commercial-previsit'
import CostBreakdownTable, { type CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { feeProxyFormula } from './coinsuranceFormula'
import { money } from './format'

export default function CommercialCostTables({
  result,
  cash,
  rows,
}: {
  result: PrevisitEstimate
  cash: boolean
  rows: CostBreakdownRow[]
}) {
  const proxy = result.fee_proxy_estimate
  return (
    <>
      {!cash && result.collection_guidance?.basis !== 'qmb_protection' &&
        proxy?.scenario_kind === 'oap_office_copay_general_coinsurance' && (
          <CignaPlanningTable
            proxy={proxy}
            basis={result.collection_guidance?.basis}
            payments={result.encounter_summary?.payments_verified_cents || 0}
          />
        )}
      <CostBreakdownTable rows={rows} />
    </>
  )
}

function CignaPlanningTable({
  proxy,
  basis,
  payments,
}: {
  proxy: NonNullable<PrevisitEstimate['fee_proxy_estimate']>
  basis?: string
  payments: number
}) {
  const balance = Math.max(0, proxy.patient_share_cents - payments)
  return (
    <section className="cost-breakdown cigna-planning-table" aria-label="Visit cost and collection planning scenario">
      <h3>Visit cost and collection · planning scenario</h3>
      <div className="cost-breakdown-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Visit cost and collection</th>
              <th scope="col">Amount</th>
              <th scope="col">Formula or source</th>
            </tr>
          </thead>
          <tbody>
            {proxy.lines?.map((line, index) => (
              <tr key={`${line.code}-${index}`}>
                <th scope="row">{line.code} × {line.units || 1}</th>
                <td>{money(line.reference_cents)}</td>
                <td>BCBSTX office fee reference</td>
              </tr>
            ))}
            <tr><th scope="row">Reference total</th><td>{money(proxy.reference_total_cents)}</td><td>{proxy.lines?.map((line) => money(line.reference_cents)).join(' + ')}</td></tr>
            <tr><th scope="row">Individual deductible applied</th><td>{money(proxy.deductible_cents)}</td><td>Cigna reports $0 individual deductible remaining</td></tr>
            <tr><th scope="row">Specialist office copay</th><td>{money(proxy.copay_cents)}</td><td>One payer-reported copay per visit; application to these tests is assumed</td></tr>
            <tr><th scope="row">Diagnostic coinsurance in this scenario</th><td>{proxy.coinsurance_percent}%</td><td>General Cigna plan row; assumed for the selected tests</td></tr>
            <tr className="is-total"><th scope="row">{basis === 'claim_after_service' ? 'Conditional patient share' : 'Planned amount to collect at visit'}</th><td>{money(balance)} planning estimate</td><td>{feeProxyFormula(proxy, payments).total}</td></tr>
          </tbody>
        </table>
      </div>
      <p className="cigna-planning-note">Conditional estimate using BCBS Texas reference fees, not Cigna allowed amounts. Obtain Cigna’s Explanation of Estimate before collection.</p>
    </section>
  )
}
