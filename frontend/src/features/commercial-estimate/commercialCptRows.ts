import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { VisitPricedLine } from '../../services/api/commercial-visit-types'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { serviceRowLabel } from '../insurance-estimate/serviceRowLabel'
import { aetnaSignatureOfficeNoDeductible } from './payerReportedPartialCopay'
import type { bluecardOfficeDeductibleScenario } from './bluecardOfficeDeductibleScenario'
import { money, range } from './format'
import { ticTreatmentRows } from './ticTreatmentRows'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export function commercialAllowedTotalRow(
  result: PrevisitEstimate,
  options: {
    healthselectZero: boolean
    cardPlanningCopay: number | null
    dsnpOffice: boolean
    automaticCashFallback: boolean
    lineAllowedTotal: number | null
    bluecardDeductible: ReturnType<typeof bluecardOfficeDeductibleScenario>
    botoxPreview: PrevisitEstimate['botox_preview']
  },
): CostBreakdownRow {
  const {
    healthselectZero,
    cardPlanningCopay,
    dsnpOffice,
    automaticCashFallback,
    lineAllowedTotal,
    bluecardDeductible,
    botoxPreview,
  } = options
  const price = result.price_range
  const clinicQuote =
    result.fee_proxy_estimate?.basis === 'cigna_clinic_fee_quote' ? result.fee_proxy_estimate : null
  if (clinicQuote && lineAllowedTotal == null) {
    const allCigna = clinicQuote.lines?.every((line) => line.source === 'Cigna clinic contract')
    return {
      label: 'Total payer allowed',
      value: allCigna
        ? `${money(clinicQuote.reference_total_cents)} clinic Cigna contract`
        : 'Not fully priced by Cigna · BCBS Texas proxy used for other CPTs',
    }
  }
  if (
    ['blue_ppo_family_deductible_quote', 'cigna_ppo_deductible_quote'].includes(
      result.collection_guidance?.collection_rule?.mode || '',
    ) &&
    lineAllowedTotal == null
  ) {
    return {
      label: 'Total payer allowed',
      value: `Not established · ${money(result.collection_guidance!.clinic_fee_reference_cents!)} clinic fee reference`,
    }
  }
  const trsDeposit =
    result.collection_guidance?.collection_rule?.mode === 'trs_hd_office_refundable_deposit'
  return {
    label: 'Total payer allowed',
    value: trsDeposit
      ? `Not established · ${money(result.collection_guidance!.reference_total_cents!)} clinic fee reference`
      : healthselectZero
        ? 'Not returned · not needed for the conditional $0 cost share'
        : cardPlanningCopay != null
          ? 'Payer allowance pending'
          : dsnpOffice
            ? 'Not established · UHC allowance not returned'
            : automaticCashFallback
              ? 'Not established'
              : price?.allowed_min_cents != null && price.allowed_max_cents != null
                ? range(price.allowed_min_cents, price.allowed_max_cents)
                : lineAllowedTotal != null
                  ? money(lineAllowedTotal)
                  : bluecardDeductible
                    ? `Not established · ${money(bluecardDeductible.reference)} clinic fee reference`
                    : botoxPreview
                      ? `${money(botoxPreview.allowed_cents)} · clinic fee row`
                      : /\bhumana\b/i.test(result.payer || '')
                        ? 'Not established'
                        : 'Not fully priced',
  }
}

export function commercialCashRows(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
  lines: VisitPricedLine[],
): CostBreakdownRow[] {
  return [
    { label: 'Payment method', value: 'Self-pay' },
    {
      label: 'Planned services',
      value: lines.length
        ? lines
            .map((line) =>
              serviceRowLabel({
                description: line.description,
                reason: result.reason || model.anticipatedService,
                code: line.code,
                units: line.units,
              }),
            )
            .join('; ')
        : result.cash_quote?.package_label || 'Not selected',
    },
    { label: 'Clinic cash price', value: model.cashPrice || 'Not quoted' },
  ]
}

export function allowedCapReductionRows(
  cents: number | null | undefined,
  calculated: boolean,
): CostBreakdownRow[] {
  return calculated && (cents ?? 0) > 0
    ? [{ label: 'Payer allowed amount reduction', value: `−${money(cents!)}` }]
    : []
}

export function commercialVisitReason(result: PrevisitEstimate, fallback: string): string {
  const reason = result.reason || fallback || 'Not recorded'
  return result.service_plan?.kind === 'emg_ncs' &&
    !/\b(?:emg|ncs|electromyograph|nerve conduction)\b/i.test(reason)
    ? `${reason} · EMG/NCS evaluation`
    : reason
}

export function commercialCashAlternativeRow(
  price: PrevisitEstimate['price_range'],
  cashPrice: string | null,
  automaticCashFallback: boolean,
  pricedCashLines: VisitPricedLine[],
): CostBreakdownRow {
  return {
    label: 'Cash alternative',
    value:
      automaticCashFallback && cashPrice
        ? cashPrice
        : price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null
          ? range(price.cash_min_cents, price.cash_max_cents)
          : pricedCashLines.length === 1 && /^9920[2-5]$/.test(pricedCashLines[0].code)
            ? `${money(pricedCashLines[0].cash_cents!)} new-patient office visit`
            : 'Not quoted',
    detail: 'Separate self-pay price; not used as the insurance allowed amount',
  }
}

export function commercialFeeReferenceRows(
  rate: PrevisitEstimate['temporary_rate_reference'] | null | undefined,
  feeProxy: PrevisitEstimate['fee_proxy_estimate'],
  dsnpOffice: boolean,
  bluecardDeductible: ReturnType<typeof bluecardOfficeDeductibleScenario>,
  aetnaRateReference: boolean,
  result: PrevisitEstimate,
): CostBreakdownRow[] {
  if (feeProxy?.basis === 'cigna_clinic_fee_quote')
    return [
      {
        label: 'Clinic pricing basis',
        value: `${money(feeProxy.reference_total_cents)} total; ${
          feeProxy.lines
            ?.map((line) => `CPT ${line.code}: ${money(line.reference_cents)} ${line.source}`)
            .join('; ') || ''
        }`,
        detail: feeProxy.message,
      },
    ]
  if (!rate?.lines.length) return []
  const total = rate.lines.reduce(
    (sum, line) => sum + line.unit_reference_cents * (Number(line.units) || 1),
    0,
  )
  const scenarios = result.cigna_office_fee_scenarios
  return [
    {
      label: dsnpOffice
        ? 'BCBS Texas office fee reference'
        : feeProxy
          ? 'BCBS Texas diagnostic fee proxy total'
          : bluecardDeductible
            ? 'Blue Choice PPO fee reference'
            : aetnaRateReference
              ? 'Aetna fee reference'
              : 'Blue Cross fee reference',
      value: feeProxy
        ? money(feeProxy.reference_total_cents)
        : aetnaRateReference
          ? `${money(total)} total reference`
          : `${money(total)} total reference; ${rate.lines
              .map((line) => `CPT ${line.code}: ${money(line.unit_reference_cents)} per unit`)
              .join('; ')}`,
      detail: rate.message,
    },
    ...(scenarios?.lines.length
      ? [
          {
            label: 'Cigna office fee scenarios',
            value: scenarios.lines
              .map((line) => `CPT ${line.code}: ${money(line.patient_share_cents)} conditional`)
              .join('; '),
            detail: `Family deductible remaining: ${money(scenarios.deductible_remaining_cents)}. Coinsurance after deductible: ${scenarios.coinsurance_percent}%. ${scenarios.message}`,
          },
        ]
      : []),
  ]
}

export function commercialCptRows(
  result: PrevisitEstimate,
  lines: VisitPricedLine[],
  preview: PrevisitEstimate['botox_preview'],
  supportedAmount = false,
): { cpt: CostBreakdownRow; candidates: CostBreakdownRow[]; unpriced: CostBreakdownRow } {
  const suggestedCodes = [
    ...new Set(
      (result.suggestions?.groups || [])
        .flatMap((group) => group.codes.map((candidate) => candidate.code))
        .filter(
          (code) =>
            result.available_codes?.some((row) => row.code === code) &&
            (result.service_date < '2014-01-01' || !['64613', '64614'].includes(code)),
        ),
    ),
  ]
  const officeAlternatives = !lines.length
    ? (result.available_codes || [])
        .filter((row) => row.source === 'conditional office E/M alternative')
        .map((row) => row.code)
    : []
  const aetnaOfficeCpt = aetnaSignatureOfficeNoDeductible(
    result,
    lines.length === 1 ? lines[0] : null,
  )
  const dsnpScenario =
    result.fee_proxy_estimate?.basis === 'bcbstx_uhc_dsnp_office_proxy'
      ? result.fee_proxy_estimate
      : null
  const zeroOopAlternatives =
    supportedAmount &&
    result.price_range?.mode === 'alternatives' &&
    result.collection_guidance?.basis === 'out_of_pocket_limit' &&
    result.collection_guidance.amount_cents === 0
  return {
    cpt: {
      label:
        result.service_selection_status === 'preview' &&
        result.service_plan?.method === 'llm' &&
        !aetnaOfficeCpt
          ? 'LLM-proposed CPTs'
          : 'CPTs',
      value: lines.length
        ? lines
            .map(
              (line) =>
                `CPT ${line.code} × ${line.units || 1}${line.modifiers?.length ? ` · modifier ${line.modifiers.join(', ')}` : ''}`,
            )
            .join('; ')
        : dsnpScenario
          ? `No CPT selected · provisional CPT ${dsnpScenario.scenario_cpt} × 1 pricing scenario`
          : 'No CPTs selected',
      detail:
        result.service_selection_status !== 'approved' && !aetnaOfficeCpt
          ? result.service_plan?.method === 'llm'
            ? 'Model proposal used for this estimate; billed codes follow the services actually performed'
            : 'Provisional service scenario used for this estimate'
          : undefined,
    },
    candidates: [
      ...(zeroOopAlternatives
        ? (result.price_range?.lines || []).map((line) => ({
            label: `CPT ${line.code} · choose one`,
            value: `${money(line.allowed_cents!)} clinic allowed · ${money(0)} patient`,
            detail: `${money(line.allowed_cents!)} allowed = ${money(line.calculation!.plan_cents!)} projected plan share + ${money(0)} patient share; min(${line.benefits?.copay_cents != null ? `${money(line.benefits.copay_cents)} reported copay + ` : ''}any applicable deductible and coinsurance, ${money(0)} individual out-of-pocket remaining) = ${money(0)}. These CPTs are alternatives, not separate charges.`,
          }))
        : []),
      ...ticTreatmentRows(result),
      ...(!lines.length && officeAlternatives.length
        ? [
            {
              label: 'Office visit options · choose one',
              value: [...new Set(officeAlternatives)].map((code) => `CPT ${code}`).join('; '),
              detail:
                'These are alternatives for one visit. Select the level supported by planned medical decision making or time; the diagnosis alone does not set it.',
            },
          ]
        : []),
      ...(!lines.length && suggestedCodes.length
        ? [
            {
              label: 'Coding assistant candidates',
              value: suggestedCodes.map((code) => `CPT ${code}`).join('; '),
              detail:
                'Confirm the planned service and coding details before selecting a billable CPT.',
            },
          ]
        : []),
    ],
    unpriced: {
      label: 'Planned CPTs',
      value: preview
        ? `CPT ${preview.code} × 1 · provisional`
        : dsnpScenario
          ? `CPT ${dsnpScenario.scenario_cpt} × 1 · BCBS Texas planning reference`
          : zeroOopAlternatives
            ? 'No CPT selected · priced alternatives shown above'
            : 'No priced CPTs selected',
      detail: preview
        ? 'Procedure-only scenario; Botox drug and separate E/M are excluded'
        : undefined,
    },
  }
}

export function commercialPricedCptRows({
  result,
  lines,
  fallback,
  temporaryRateReference,
  aetnaRateReference,
  bluecardDeductible,
  automaticCashFallback,
  anticipatedService,
}: {
  result: PrevisitEstimate
  lines: VisitPricedLine[]
  fallback: CostBreakdownRow
  temporaryRateReference: PrevisitEstimate['temporary_rate_reference'] | null | undefined
  aetnaRateReference: boolean
  bluecardDeductible: ReturnType<typeof bluecardOfficeDeductibleScenario>
  automaticCashFallback: boolean
  anticipatedService: string | null | undefined
}): CostBreakdownRow[] {
  if (!lines.length) return [fallback]
  return lines.map((line) => {
    const clinicQuoteLine =
      result.fee_proxy_estimate?.basis === 'cigna_clinic_fee_quote'
        ? result.fee_proxy_estimate.lines?.find(
            (row) => row.code === line.code && row.units === line.units,
          )
        : null
    const trsDeposit =
      result.collection_guidance?.collection_rule?.mode === 'trs_hd_office_refundable_deposit'
    const proxyRate = temporaryRateReference?.lines.find(
      (rate) =>
        rate.id === line.id &&
        rate.code === line.code &&
        rate.units === line.units &&
        (rate.modifiers || []).join() === (line.modifiers || []).join(),
    )
    const resolved =
      !automaticCashFallback &&
      line.rate_resolution?.status === 'RESOLVED' &&
      [
        'clinic_contract',
        'payer_out_of_network',
        'medicare_fee_schedule',
        'aetna_fee_schedule',
      ].includes(line.allowed_basis || '')
    return {
      label: serviceRowLabel({
        description: line.description,
        reason: result.reason || anticipatedService,
        code: line.code,
        units: line.units,
      }),
      value: clinicQuoteLine
        ? `${money(clinicQuoteLine.reference_cents)} ${clinicQuoteLine.source === 'Cigna clinic contract' ? 'Cigna contract allowance' : 'BCBS Texas reference'}`
        : resolved && line.allowed_cents != null
          ? trsDeposit
            ? `${money(result.collection_guidance!.reference_total_cents!)} clinic fee reference`
            : money(line.allowed_cents)
          : proxyRate
            ? `${money(proxyRate.unit_reference_cents * (Number(line.units) || 1))} ${bluecardDeductible ? 'Blue Choice PPO fee reference' : aetnaRateReference ? 'Aetna fee reference' : 'BCBS Texas reference'}`
            : 'Allowed amount unavailable',
      detail: clinicQuoteLine
        ? `${money(clinicQuoteLine.reference_cents / (Number(line.units) || 1))} per unit × ${line.units || 1}; ${clinicQuoteLine.source === 'Cigna clinic contract' ? 'clinic-supplied Cigna rate' : 'Cigna allowance unknown; refundable clinic proxy'}`
        : trsDeposit
          ? `${money(result.collection_guidance!.reference_total_cents!)} per unit × 1; insured allowed amount unknown`
          : resolved && line.unit_allowed_cents != null
            ? `${money(line.unit_allowed_cents)} per unit × ${line.units || 1}`
            : proxyRate
              ? `${money(proxyRate.unit_reference_cents)} per unit × ${line.units || 1}; ${result.payer || 'payer'} allowed amount unknown`
              : line.description || undefined,
    }
  })
}
