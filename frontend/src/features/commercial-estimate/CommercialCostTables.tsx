import type { PrevisitEstimate } from '../../services/api/commercial-previsit'
import CostBreakdownTable, { type CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { feeProxyFormula } from './coinsuranceFormula'
import { money } from './format'
import { uhcLppoOfficeScenario } from './uhcLppoOfficeScenario'
import { uhcNmOfficeReference } from './uhcNmOfficeReference'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { uhcQmbOfficeRows } from './uhcQmbOfficeTable'

export default function CommercialCostTables({
  result,
  model,
  rows,
}: {
  result: PrevisitEstimate
  model: CommercialEstimateModel
  rows: CostBreakdownRow[]
}) {
  const cash = model.cash
  const lppo =
    !cash && !model.previousEstimate && !model.calculating ? uhcLppoOfficeScenario(result) : null
  const nmOffice = uhcNmOfficeReference(result, model)
  const proxy = result.fee_proxy_estimate
  const aetnaFamilyZero =
    result.collection_guidance?.amount_cents === 0 &&
    result.collection_guidance.collection_rule?.mode === 'aetna_choice_family_oop_deferral'
  const reported = result.benefits?.reported_options || []
  const diagnosticCopays = [
    ...new Set(
      reported
        .filter(
          (row) =>
            row.kind === 'copay' &&
            row.network === 'IN_NETWORK' &&
            row.service_type_code === '73' &&
            Number.isInteger(row.amount_cents),
        )
        .map((row) => row.amount_cents!),
    ),
  ].sort((a, b) => a - b)
  const diagnosticCoinsurance = reported.filter(
    (row) =>
      row.kind === 'coinsurance' && row.network === 'IN_NETWORK' && row.service_type_code === '73',
  )
  const displayedRows = aetnaFamilyZero
    ? rows.map((row) =>
        row.label === 'Estimated patient share'
          ? {
              ...row,
              label: 'Conditional patient share',
              value: '$0.00 if covered in-network',
              detail: result.collection_guidance?.reason,
            }
          : row.label === 'Copay' && diagnosticCopays.length
            ? {
                ...row,
                value: `${diagnosticCopays.map(money).join(' / ')} returned by diagnostic category`,
                detail:
                  'No CPT-specific EMG copay was identified; the covered in-network share is capped by the reported family out-of-pocket balance.',
              }
            : row.label === 'Coinsurance' &&
                diagnosticCoinsurance.length &&
                diagnosticCoinsurance.every((entry) => Number(entry.percent) === 0)
              ? { ...row, value: '0% across returned in-network diagnostic categories' }
              : row,
      )
    : rows
  const planning = result.aetna_office_planning
  const telemedicineReference = planning?.coinsurance_scope === 'telemedicine_reference'
  const cappedOffice =
    result.collection_guidance?.collection_rule?.mode === 'aetna_office_individual_oop_deferral' &&
    !!planning
  const visibleRows = displayedRows
    .filter((row) => cash || row.label !== 'Cash alternative')
    .map((row) => {
      if (!planning || (model.supportedAmount && !cappedOffice)) return row
      if (row.label === 'Total payer allowed')
        return {
          ...row,
          label: 'Clinic Aetna fee reference',
          value: money(planning.allowed_cents),
          detail:
            'Approved clinic schedule for the proposed office CPT; final payer allowance follows the claim.',
        }
      if (row.label === 'Copay' && cappedOffice && telemedicineReference)
        return {
          ...row,
          detail:
            'The $0 specialist copay row is marked telemedicine. The in-person office copay was not returned; the medical out-of-pocket cap drives the $0 check-in decision.',
        }
      if (row.label === 'Coinsurance')
        return {
          ...row,
          value: telemedicineReference
            ? `${planning.coinsurance_percent}% telemedicine reference; in-person rate not returned`
            : planning.coinsurance_percent == null
              ? 'Not returned for the in-person specialist visit'
              : `${planning.coinsurance_percent}% in-network specialist office visit`,
          detail: telemedicineReference
            ? 'Aetna marked this specialist percentage as telemedicine. It illustrates the fee calculation but does not establish the in-person office rate.'
            : planning.copay_cents != null
              ? planning.coinsurance_percent == null
                ? 'The payer returned a telemedicine coinsurance row, which does not establish an in-person office rate. The displayed split assumes the fixed copay is the only cost share.'
                : 'The reported specialist office copay sets the conditional patient share; zero coinsurance adds nothing.'
              : 'The payer also returned a separate telemedicine rate; the planned visit is in person.',
        }
      if (
        row.label === 'Calculated Aetna share for this office visit' ||
        row.label === 'Estimated patient share'
      )
        return {
          ...row,
          label: 'Conditional patient share',
          value: cappedOffice
            ? money(0)
            : planning.copay_cents != null
              ? money(planning.copay_cents)
              : `${money(planning.deductible_applies_cents)} if deductible applies; ${money(planning.deductible_waived_cents)} if waived`,
          detail: cappedOffice
            ? telemedicineReference
              ? `The telemedicine reference gives ${planning.coinsurance_percent}% × ${money(planning.allowed_cents)} = ${money(planning.gross_coinsurance_cents || 0)} illustratively. The individual in-network medical maximum has ${money(0)} remaining, so covered office cost-sharing is capped at ${money(0)} regardless of the in-person rate.`
              : `${planning.coinsurance_percent}% × ${money(planning.allowed_cents)} = ${money(planning.gross_coinsurance_cents || 0)} before the out-of-pocket cap; min(${money(planning.gross_coinsurance_cents || 0)}, ${money(0)} individual in-network remaining) = ${money(0)} for covered care.`
            : planning.copay_cents != null
              ? `${money(planning.copay_cents)} reported copay + $0.00 remaining deductible${planning.coinsurance_percent == null ? '; in-person coinsurance was not returned. Copay-only scenario.' : ' + 0% office coinsurance.'} Prior-date planning only.`
              : 'The office-visit row does not establish whether the plan-wide deductible applies to this CPT.',
        }
      return row
    })
  if (cappedOffice) {
    const gross = planning!.gross_coinsurance_cents || 0
    const insertAt = visibleRows.findIndex((row) => row.label === 'Amount to collect at visit')
    visibleRows.splice(
      insertAt < 0 ? visibleRows.length : insertAt,
      0,
      {
        label: telemedicineReference
          ? 'Illustrative coinsurance before cap'
          : 'Gross coinsurance before cap',
        value: money(gross),
        detail: `${planning!.coinsurance_percent}% × ${money(planning!.allowed_cents)} clinic fee reference${telemedicineReference ? '; the source row is telemedicine and does not establish the in-person rate' : ''}.`,
      },
      {
        label: telemedicineReference
          ? 'Illustrative out-of-pocket adjustment'
          : 'Out-of-pocket adjustment',
        value: `−${money(gross)}`,
        detail: 'The individual in-network medical out-of-pocket remainder is $0 for covered care.',
      },
      {
        label: 'Estimated Aetna share',
        value: money(planning!.allowed_cents),
        detail:
          'Modeled plan share if the proposed CPT is covered in-network and the clinic fee reference equals the payer allowance; the processed claim sets the final amount.',
      },
    )
  } else if (planning?.plan_share_cents != null && !model.supportedAmount)
    visibleRows.push({
      label: 'Estimated Aetna share',
      value: money(planning.plan_share_cents),
      detail: `${money(planning.allowed_cents)} clinic Aetna fee reference less ${money(planning.copay_cents!)} patient copay, if covered in-network${planning.coinsurance_percent == null ? ' and the fixed copay is the only office cost share' : ''}. This is paid by Aetna after an eligible claim, not collected from the patient.`,
    })
  const finalRows = nmOffice
    ? visibleRows.map((row) => {
        if (row.label === 'Coverage')
          return {
            ...row,
            value: 'Card identifies UHC Medicare Advantage PPO · eligibility unverified',
            detail:
              'The electronic search did not establish inactive coverage. The payer-specific check needs provider enrollment review.',
          }
        if (row.label === 'Payer order')
          return {
            ...row,
            value: 'One card plan referenced · eCW entries need reconciliation',
            detail:
              'Optum Care and UnitedHealthcare may describe one administered plan. Confirm payer order before billing.',
          }
        if (row.label === 'Copay')
          return {
            ...row,
            value: `${money(nmOffice.inNetworkCopay)} in-network / ${money(nmOffice.outOfNetworkCopay)} out-of-network · published specialist copays`,
            detail:
              'UnitedHealthcare 2026 plan summary; Optum Care administers this plan. Member coverage and provider tier were not verified.',
          }
        if (row.label === 'Estimated patient share')
          return {
            ...row,
            value: 'Not established',
            detail:
              'The applicable copay depends on the verified provider network tier and covered service. Final responsibility follows the claim.',
          }
        if (row.label.startsWith('Amount to collect at visit'))
          return {
            ...row,
            label: 'Amount to collect at visit',
            value: 'Not determined · verify provider network',
            detail:
              'Confirm active coverage, provider network, QMB protection, other coverage and prior payments before charging. The $150 cash price is separate.',
          }
        return row
      })
    : lppo
      ? visibleRows.map((row) => {
          if (row.label === 'Total payer allowed')
            return {
              ...row,
              value: `Not confirmed by UHC · ${money(lppo.fee)} clinic Medicare fee reference`,
              detail:
                'The clinic Medicare schedule is a planning reference, not a UnitedHealthcare allowance.',
            }
          if (row.label === 'Coinsurance')
            return {
              ...row,
              value: `${lppo.percent}% reported · ${money(lppo.share)} provisional`,
              detail: `${lppo.percent}% × ${money(lppo.fee)} clinic Medicare fee reference.`,
            }
          if (row.label === 'Estimated patient share')
            return {
              ...row,
              value: `${money(lppo.share)} provisional`,
              detail:
                'The reported deductible has $0 remaining; the payer allowance and final claim share are unconfirmed.',
            }
          if (row.label === 'Amount to collect at visit')
            return {
              ...row,
              label: 'Amount to collect at visit · provisional',
              value: `${money(lppo.balance)} provisional · verify before charging`,
              detail: `${lppo.percent}% × ${money(lppo.fee)} clinic Medicare fee reference = ${money(lppo.share)}${lppo.paid ? `; less ${money(lppo.paid)} already paid` : ''}. Reconcile the policy conflict, network, QMB protection and payer order before charging.`,
            }
          return row
        })
      : visibleRows
  return (
    <>
      {!cash &&
        result.collection_guidance?.basis !== 'qmb_protection' &&
        (proxy?.scenario_kind === 'oap_office_copay_general_coinsurance' ||
          proxy?.scenario_kind === 'oap_diagnostic_office_copay') && (
          <CignaPlanningTable
            proxy={proxy}
            basis={result.collection_guidance?.basis}
            payments={result.encounter_summary?.payments_verified_cents || 0}
          />
        )}
      <CostBreakdownTable
        rows={
          !cash && !model.previousEstimate
            ? uhcQmbOfficeRows(result, finalRows) || finalRows
            : finalRows
        }
        open={
          (model.supportedAmount ||
            !!lppo ||
            !!nmOffice ||
            !!planning ||
            result.collection_guidance?.basis === 'published_uhc_tx_qmb_office') &&
          !cash
        }
      />
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
    <details
      className="cost-breakdown cigna-planning-table"
      aria-label="Visit cost and collection planning scenario"
    >
      <summary>Visit cost and collection · planning scenario</summary>
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
                <th scope="row">
                  {line.code} × {line.units || 1}
                </th>
                <td>{money(line.reference_cents)}</td>
                <td>BCBSTX office fee reference</td>
              </tr>
            ))}
            <tr>
              <th scope="row">Reference total</th>
              <td>{money(proxy.reference_total_cents)}</td>
              <td>{proxy.lines?.map((line) => money(line.reference_cents)).join(' + ')}</td>
            </tr>
            <tr>
              <th scope="row">Individual deductible applied</th>
              <td>{money(proxy.deductible_cents)}</td>
              <td>Cigna reports $0 individual deductible remaining</td>
            </tr>
            <tr>
              <th scope="row">Specialist office copay</th>
              <td>{money(proxy.copay_cents)}</td>
              <td>
                {proxy.scenario_kind === 'oap_diagnostic_office_copay'
                  ? 'One payer-reported diagnostic specialist copay per office visit'
                  : 'One payer-reported copay per visit; application to these tests is assumed'}
              </td>
            </tr>
            <tr>
              <th scope="row">Diagnostic coinsurance in this scenario</th>
              <td>{proxy.coinsurance_percent}%</td>
              <td>
                {proxy.scenario_kind === 'oap_diagnostic_office_copay'
                  ? 'Cigna diagnostic office benefit row; CPT applicability remains conditional'
                  : 'General Cigna plan row; assumed for the selected tests'}
              </td>
            </tr>
            <tr className="is-total">
              <th scope="row">
                {basis === 'claim_after_service'
                  ? 'Conditional patient share'
                  : 'Planned amount to collect at visit'}
              </th>
              <td>{money(balance)} planning estimate</td>
              <td>{feeProxyFormula(proxy, payments).total}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="cigna-planning-note">
        Conditional estimate using BCBS Texas reference fees, not Cigna allowed amounts. Obtain
        Cigna’s Explanation of Estimate before collection.
      </p>
    </details>
  )
}
