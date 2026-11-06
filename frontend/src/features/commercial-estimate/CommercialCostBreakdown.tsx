import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { serviceRowLabel } from '../insurance-estimate/serviceRowLabel'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { coinsuranceFormula, feeProxyFormula } from './coinsuranceFormula'
import CommercialCostTables from './CommercialCostTables'
import { collectionAtVisitRow, pendingBluecardOfficeCopay } from './collectionAtVisitRow'
import { money, range } from './format'
import { commercialRequirementRows } from './commercialRequirementStatus'
import * as copay from './payerReportedPartialCopay'
import { memberGroupRow, referenceCoverageRow } from './referenceCoverageRow'
import { limitSummary, unknown } from './limitSummary'
import { resolvedAllowedTotal } from './resolvedAllowedTotal'
import {
  commercialCashAlternativeRow,
  commercialCptRows,
  commercialVisitReason,
} from './commercialCptRows'
import { commercialOopRows, familyOopLimitApplied } from './commercialOopRows'
import { familyDeductibleRow } from './familyDeductibleRow'
import { suggestedDiagnosisRows } from './suggestedDiagnosisRows'
import { cignaCardReference, cignaCardRows } from './cignaCardReference'
import * as costState from './commercialCostState'
export default function CommercialCostBreakdown({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: PrevisitEstimate
}) {
  const benefits = result.reference_benefits ?? result.benefits
  const reportedResult = result.reference_benefits ? { ...result, benefits } : result
  const { price_range: price, fee_proxy_estimate: feeProxy } = result
  const gehaZero = costState.publishedGehaZero(result)
  const botoxPreview = !model.cash && !model.previousEstimate ? result.botox_preview : null
  const combinedPlan = !model.cash && !model.previousEstimate ? result.combined_emg_plan : null
  const cignaFeeProxy = feeProxy?.basis === 'bcbstx_cigna_fee_proxy'
  const cignaFamilyOffice =
    cignaFeeProxy && feeProxy.scenario_kind === 'oap_office_family_deductible'
  const cignaOfficeScenario = cignaFeeProxy && Boolean(feeProxy.scenario_kind) && !cignaFamilyOffice
  const lines = price?.mode === 'services' ? price.lines || [] : []
  const cptRows = commercialCptRows(result, lines, botoxPreview)
  const familyOopApplied = familyOopLimitApplied(result)
  const automaticCashFallback = model.cash && result.cash_fallback != null
  const summary = result.encounter_summary
  const proxyFormula = feeProxy && feeProxyFormula(feeProxy, summary?.payments_verified_cents || 0)
  const individualDeductible = limitSummary(reportedResult, 'deductible', 'INDIVIDUAL')
  const surgicalIndividualDeductible = limitSummary(reportedResult, 'deductible', 'INDIVIDUAL', '2')
  const reportedCoinsurance = copay.reportedCoinsurance(reportedResult, lines)
  const reportedOfficeCopay = copay.specialistOfficeCopay(reportedResult)
  const singleOfficeCopayLine = copay.singleOfficeCopayLine(lines)
  const aetnaNoOfficeDeductible = copay.aetnaNoOfficeDeductible(result, singleOfficeCopayLine)
  const officeReferenceCopay = copay.aetnaOfficeReferenceCopay(result, model)
  const humanaReferenceCopay = copay.publishedHumanaOfficeCopay(result, model)
  const partialOfficeCopay = copay.payerReportedPartialCopay(result, model)
  const pendingAetnaCopay = copay.aetnaPendingOfficeCopay(result, model)
  const priorDateAetnaCopay = copay.aetnaPriorDateReferenceCopay(result, model)
  const pendingBluecardCopay = pendingBluecardOfficeCopay(result, model)
  const cardReferences = [partialOfficeCopay, officeReferenceCopay, humanaReferenceCopay]
  const cardPlanningCopay = copay.visibleCardOfficePlanningCopay(result, model, cardReferences)
  const cignaTelevisit = copay.cignaTelevisitCopay(result, model, lines)
  const cignaCard = cignaCardReference(result, model, benefits)
  const components = summary?.components
  const diagnosticCopay = copay.diagnosticCopay(result, lines)
  const pricedCashLines = lines.filter((line) => line.cash_cents != null && !line.cash_included)
  const lineAllowedTotal = resolvedAllowedTotal(lines)
  const calculated = costState.calculatedShare(result, model, automaticCashFallback)
  const { temporaryRateReference, aetnaRateReference } = costState.visibleRateReference(result)
  const aetnaPlanning = copay.visibleAetnaDiagnosticPlanning(result, model)
  const aetnaCopayPlanning = copay.aetnaOfficePlanningCopay(result, model, lines)
  const memberGroup = memberGroupRow(result)
  const insuredRows: CostBreakdownRow[] = [
    referenceCoverageRow(result, model, benefits),
    ...(memberGroup ? [memberGroup] : []),
    { label: 'Network', value: model.networkLabel },
    {
      label: result.reason ? 'Reason for visit' : 'Planned service',
      value: commercialVisitReason(result, model.anticipatedService || ''),
    },
    ...suggestedDiagnosisRows(result.reason),
    cptRows.cpt,
    ...cptRows.candidates,
    ...(lines.length
      ? lines.map((line) => {
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
            ['clinic_contract', 'payer_out_of_network', 'cms_original_medicare_estimate'].includes(
              line.allowed_basis || '',
            )
          return {
            label: serviceRowLabel({
              description: line.description,
              reason: result.reason || model.anticipatedService,
              code: line.code,
              units: line.units,
            }),
            value:
              resolved && line.allowed_cents != null
                ? money(line.allowed_cents)
                : proxyRate
                  ? `${money(proxyRate.unit_reference_cents * (Number(line.units) || 1))} ${aetnaRateReference ? 'Aetna fee reference' : 'BCBS Texas reference'}`
                  : 'Allowed amount unavailable',
            detail:
              resolved && line.unit_allowed_cents != null
                ? `${money(line.unit_allowed_cents)} per unit × ${line.units || 1}`
                : proxyRate
                  ? `${money(proxyRate.unit_reference_cents)} per unit × ${line.units || 1}; ${result.payer || 'payer'} allowed amount unknown`
                  : line.description || undefined,
          }
        })
      : [cptRows.unpriced]),
    {
      label: 'Total payer allowed',
      value:
        cardPlanningCopay != null
          ? 'Payer allowance pending'
          : automaticCashFallback
            ? 'Not established'
            : price?.allowed_min_cents != null && price.allowed_max_cents != null
              ? range(price.allowed_min_cents, price.allowed_max_cents)
              : lineAllowedTotal != null
                ? money(lineAllowedTotal)
                : botoxPreview
                  ? `${money(botoxPreview.allowed_cents)} · clinic fee row`
                  : /\bhumana\b/i.test(result.payer || '')
                    ? 'Not established'
                    : 'Not fully priced',
    },
    ...(temporaryRateReference?.lines.length
      ? [
          {
            label: feeProxy
              ? 'BCBS Texas diagnostic fee proxy total'
              : aetnaRateReference
                ? 'Aetna fee reference'
                : 'Blue Cross fee reference',
            value: feeProxy
              ? money(feeProxy.reference_total_cents)
              : aetnaRateReference
                ? `${money(temporaryRateReference.lines.reduce((sum, line) => sum + line.unit_reference_cents * (Number(line.units) || 1), 0))} total reference`
                : `${money(temporaryRateReference.lines.reduce((sum, line) => sum + line.unit_reference_cents * (Number(line.units) || 1), 0))} total reference; ${temporaryRateReference.lines
                    .map((line) => `CPT ${line.code}: ${money(line.unit_reference_cents)} per unit`)
                    .join('; ')}`,
            detail: temporaryRateReference.message,
          },
        ]
      : []),
    {
      label: 'Copay',
      value: aetnaPlanning
        ? `${money(aetnaPlanning.copay_cents / lines.length)} per diagnostic test × ${lines.length} = ${money(aetnaPlanning.copay_cents)}`
        : familyOopApplied
          ? '$0 payable after family out-of-pocket limit'
          : combinedPlan
            ? `${money(combinedPlan.office_copay_cents)} specialist follow-up`
            : cignaOfficeScenario
              ? `${money(feeProxy!.copay_cents)} specialist office visit · assumed once`
              : feeProxy != null
                ? cignaFeeProxy && feeProxy.copay_unconfirmed
                  ? `${money(0)} assumed for calculation · no CPT copay returned`
                  : feeProxy.copay_cents
                    ? `${money(feeProxy.copay_cents)} specialist follow-up; $0.00 diagnostic copay`
                    : cignaFamilyOffice
                      ? `${money(0)} assumed for calculation · no fixed office copay returned`
                      : '$0.00 diagnostic copay'
                : botoxPreview
                  ? 'Not returned for office injection'
                  : officeReferenceCopay != null
                    ? `${money(officeReferenceCopay)} specialist office visit · payer-reported reference`
                    : humanaReferenceCopay != null
                      ? `${money(humanaReferenceCopay)} in-network specialist office visit · published plan reference`
                      : diagnosticCopay != null
                        ? `${money(diagnosticCopay)} once per day`
                        : calculated && components?.copay_cents != null
                          ? money(components.copay_cents)
                          : cignaTelevisit != null
                            ? `${money(cignaTelevisit.copay)} specialist video visit · payer-reported`
                            : singleOfficeCopayLine
                              ? money(singleOfficeCopayLine.benefits.copay_cents!)
                              : lines.some((line) => line.benefits?.copay_cents != null)
                                ? lines.map(copay.formatCopayLine).join('; ')
                                : !lines.length && reportedOfficeCopay != null
                                  ? `${money(reportedOfficeCopay)} specialist office visit · payer-reported`
                                  : cardPlanningCopay != null
                                    ? `${money(cardPlanningCopay)} specialist office visit · printed on card`
                                    : cignaCard
                                      ? `${money(cignaCard.nontier1_specialist_copay_cents)} non-Tier-1 / ${money(cignaCard.tier1_specialist_copay_cents)} Tier 1 specialist office · card reference`
                                      : unknown,
      detail: botoxPreview
        ? 'No injection copay was included in this provisional calculation'
        : gehaZero
          ? 'Stedi reported a $0 office copay. The matched 2026 G.E.H.A High PSHB Medicare Advantage plan also publishes a $0 in-network specialist visit.'
          : humanaReferenceCopay != null
            ? result.card_check?.reference_only
              ? 'Published Humana H7617-057 plan reference from an upload awaiting staff confirmation. The printed member number has not been verified against the active eCW policy. Confirm the card and Medicare billing protection before collection.'
              : result.card_check?.status === 'conflict'
                ? 'Published Humana H7617-057 plan reference; the card member number differs from the active eCW policy. Confirm the correct card and Medicare billing protection before collection.'
                : 'Published Humana H7617-057 benefit; confirm provider participation and visit conditions before collection'
            : aetnaPlanning
              ? aetnaPlanning.benefit_source.label
              : cignaOfficeScenario
                ? 'Cigna reported this office copay; applicability to the selected diagnostic services is an assumption'
                : cignaFeeProxy && feeProxy?.copay_unconfirmed
                  ? 'No CPT copay row was returned; this calculation uses $0'
                  : diagnosticCopay != null
                    ? 'One payer diagnostic copay for the selected testing; final share follows the claim'
                    : result.collection_guidance?.basis === 'out_of_pocket_limit'
                      ? 'Plan copay before the applicable out-of-pocket limit; $0 due for covered care'
                      : calculated &&
                          components?.copay_cents === 0 &&
                          !lines.some((line) => line.benefits?.copay_cents != null)
                        ? 'No separate copay was applied to the priced CPTs'
                        : singleOfficeCopayLine
                          ? `CPT ${singleOfficeCopayLine.code} specialist office copay. Confirm visit-date coverage, applicable billing protections, and prior payments before collecting.`
                          : cignaTelevisit != null
                            ? 'Current in-network Cigna specialist office benefit; separate contracted-vendor telehealth copay does not apply.'
                            : !lines.length && reportedOfficeCopay != null
                              ? 'Payer category term; applicability to planned CPTs and visit date is not confirmed'
                              : cardPlanningCopay != null
                                ? 'Card-based office suggestion; payer applicability and imaging cost are unresolved'
                                : cignaCard
                                  ? 'The card prints tiered office copays. Confirm the provider tier and a separately performed office visit; neither amount is a diagnostic CPT copay.'
                                  : undefined,
    },
    ...cignaCardRows(cignaCard, cignaFeeProxy),
    {
      label: 'Individual deductible',
      value: cignaFamilyOffice
        ? 'Not applicable · family-only accumulator'
        : aetnaNoOfficeDeductible
          ? '$0.00 for in-network specialist office visit'
          : gehaZero
            ? '$0.00 in-network annual · published plan; Stedi did not return a member deductible row'
            : humanaReferenceCopay != null &&
                benefits?.deductible_applies !== true &&
                benefits?.deductible_annual_cents == null
              ? '$0.00 annual · no medical deductible (published plan)'
              : feeProxy != null &&
                  !cignaFeeProxy &&
                  feeProxy.basis !== 'bcbstx_uhc_in_network_fee_proxy'
                ? `${money(feeProxy.deductible_cents)} annual · published group plan`
                : [
                    benefits?.deductible_annual_cents != null
                      ? `${money(benefits.deductible_annual_cents)} annual`
                      : null,
                    benefits?.deductible_remaining_cents != null
                      ? `${money(benefits.deductible_remaining_cents)} remaining`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(' · ') ||
                  (individualDeductible.value !== unknown
                    ? individualDeductible.value
                    : surgicalIndividualDeductible.value !== unknown
                      ? `${surgicalIndividualDeductible.value} · surgical services`
                      : unknown),
      detail: aetnaNoOfficeDeductible
        ? 'The 2026 Aetna Signature PPO H2293-016 plan has no in-network deductible. A payer-reported general deductible may apply to certain out-of-network services.'
        : gehaZero
          ? 'The published G.E.H.A High Medicare Advantage benefit lists no in-network medical deductible for covered care.'
          : botoxPreview
            ? 'Individual balance not returned; provisional scenario uses the payer-reported met family deductible'
            : cignaFeeProxy
              ? cignaOfficeScenario
                ? `${money(0)} applied in this provisional scenario from the general individual remaining balance; CPT applicability is not confirmed`
                : cignaFamilyOffice
                  ? `${money(feeProxy?.deductible_cents || 0)} applied from the exact CPT family-only remaining deductible`
                  : `${money(feeProxy?.deductible_cents || 0)} applied in this provisional scenario; exact CPT Stedi responses reported no remaining individual deductible`
              : feeProxy != null
                ? feeProxy.deductible_source
                : result.collection_guidance?.basis === 'out_of_pocket_limit'
                  ? individualDeductible.value === unknown &&
                    benefits?.deductible_annual_cents == null
                    ? 'No deductible payment due after the out-of-pocket limit; the payer did not report an office-specific deductible rule'
                    : 'No deductible payment due for this covered office visit after the out-of-pocket limit'
                  : calculated && components?.deductible_cents != null
                    ? `${money(components.deductible_cents)} applied to this visit`
                    : individualDeductible.unassigned
                      ? 'Payer-reported plan amount; medical applicability needs confirmation'
                      : surgicalIndividualDeductible.value !== unknown
                        ? 'Payer did not report an office-specific deductible rule; surgical balance does not establish one'
                        : lines.length &&
                            lines.every((line) => line.benefits?.deductible_applies === false)
                          ? 'Does not apply to the selected CPTs'
                          : 'Office deductible applicability or amount is unresolved for the selected CPTs',
    },
    familyDeductibleRow(
      reportedResult,
      gehaZero,
      humanaReferenceCopay,
      familyOopApplied,
      cignaFamilyOffice ? feeProxy!.deductible_cents : null,
    ),
    {
      label: 'Coinsurance',
      value: aetnaPlanning
        ? `0% applied · ${money(0)}`
        : gehaZero
          ? '0% reported · $0.00 for this covered office visit'
          : combinedPlan
            ? `${combinedPlan.coinsurance_percent}% · ${money(combinedPlan.diagnostic_coinsurance_cents)}`
            : botoxPreview
              ? `${botoxPreview.coinsurance_percent}% · ${money(botoxPreview.patient_share_cents)} provisional`
              : feeProxy != null
                ? `${feeProxy.coinsurance_percent}%${cignaOfficeScenario ? ' assumed' : ''} · ${money(feeProxy.coinsurance_cents)} on the fee proxy`
                : result.collection_guidance?.basis === 'out_of_pocket_limit'
                  ? reportedCoinsurance != null
                    ? `${reportedCoinsurance}% reported; $0 payable after out-of-pocket limit`
                    : '$0 payable after out-of-pocket limit'
                  : calculated && components?.coinsurance_cents != null
                    ? money(components.coinsurance_cents)
                    : reportedCoinsurance != null
                      ? `${reportedCoinsurance}% reported; dollar amount pending`
                      : cignaCard?.in_network_coinsurance_split
                        ? `${cignaCard.in_network_coinsurance_split} printed in-network split · CPT share pending`
                        : humanaReferenceCopay != null
                          ? '40% out of network · in-network office benefit is a copay'
                          : unknown,
      detail: aetnaPlanning
        ? 'The planning formula uses a fixed diagnostic copay rather than office visit coinsurance'
        : combinedPlan
          ? `${combinedPlan.coinsurance_percent}% × ${money(combinedPlan.diagnostic_allowed_cents)} = ${money(combinedPlan.diagnostic_coinsurance_cents)}`
          : botoxPreview
            ? `${botoxPreview.coinsurance_percent}% × ${money(botoxPreview.allowed_cents)} = ${money(botoxPreview.patient_share_cents)}`
            : feeProxy != null
              ? proxyFormula?.coinsurance ||
                `${money(feeProxy.reference_total_cents)} × ${feeProxy.coinsurance_percent}% = ${money(feeProxy.coinsurance_cents)}`
              : calculated
                ? coinsuranceFormula(result) ||
                  (components?.coinsurance_cents === 0
                    ? 'No coinsurance applied after deductible'
                    : undefined)
                : !lines.length && reportedCoinsurance != null
                  ? 'Payer-reported office visit term; diagnostic testing share is not established'
                  : cignaCard?.in_network_coinsurance_split
                    ? 'Printed card split only; confirm which percentage and Cigna allowance apply to each selected diagnostic CPT.'
                    : undefined,
    },
    ...(calculated && (components?.allowed_cap_adjustment_cents ?? 0) > 0
      ? [
          {
            label: 'Payer allowed amount reduction',
            value: `−${money(components!.allowed_cap_adjustment_cents!)}`,
          },
        ]
      : []),
    ...commercialOopRows(
      result,
      reportedResult,
      benefits,
      familyOopApplied,
      calculated,
      humanaReferenceCopay,
    ),
    ...commercialRequirementRows(result, aetnaRateReference, !!cignaCard?.specialist_referral_note),
    commercialCashAlternativeRow(price, model.cashPrice, automaticCashFallback, pricedCashLines),
  ]
  const patientShare = automaticCashFallback
    ? officeReferenceCopay != null || humanaReferenceCopay != null
      ? `${money(officeReferenceCopay ?? humanaReferenceCopay!)} office-only reference; collection pending`
      : cardPlanningCopay != null
        ? `${money(cardPlanningCopay)} office-only card suggestion`
        : 'Not fully calculated'
    : combinedPlan
      ? `${money(combinedPlan.patient_share_cents)} planning estimate`
      : ['qmb_protection', 'out_of_pocket_limit', 'published_geha_pshb_zero_office'].includes(
            result.collection_guidance?.basis || '',
          ) && result.collection_guidance?.amount_cents === 0
        ? money(0)
        : cignaTelevisit != null
          ? `${money(cignaTelevisit.share)} office copay estimate`
          : calculated &&
              summary?.estimated_total_patient_min_cents != null &&
              summary.estimated_total_patient_max_cents != null
            ? range(
                summary.estimated_total_patient_min_cents,
                summary.estimated_total_patient_max_cents,
              )
            : aetnaPlanning
              ? `${money(aetnaPlanning.patient_share_cents)} planning estimate`
              : botoxPreview
                ? `${money(botoxPreview.patient_share_cents)} provisional · CPT ${botoxPreview.code} scenario`
                : feeProxy != null
                  ? `${money(feeProxy.patient_share_cents)} provisional · BCBS Texas fee proxy`
                  : humanaReferenceCopay != null
                    ? `${money(humanaReferenceCopay)} conditional in-network office copay · collection pending`
                    : aetnaCopayPlanning != null
                      ? `${money(aetnaCopayPlanning)} provisional office copay`
                      : priorDateAetnaCopay != null
                        ? `${money(priorDateAetnaCopay)} office copay · pending verification`
                        : pendingBluecardCopay != null
                          ? `${money(lines[0].benefits.copay_cents!)} office-only estimate`
                          : model.estimatedShare ||
                            (partialOfficeCopay != null
                              ? `${money(model.officeCopayLine!.benefits.copay_cents!)} office-only planning estimate`
                              : cardPlanningCopay != null
                                ? `${money(cardPlanningCopay)} office-only card suggestion`
                                : cignaCard
                                  ? `${money(cignaCard.nontier1_specialist_copay_cents)} non-Tier-1 office-only reference; diagnostic share pending`
                                  : null) ||
                            (model.provisionalAmount != null
                              ? money(model.provisionalAmount)
                              : model.patientPrice || 'Not fully calculated')
  const rows: CostBreakdownRow[] =
    model.cash && !automaticCashFallback
      ? [
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
      : [
          ...insuredRows,
          {
            label:
              cignaFeeProxy || summary?.conditional
                ? 'Conditional patient share'
                : 'Estimated patient share',
            value: patientShare,
            detail: automaticCashFallback
              ? cardPlanningCopay != null
                ? 'Printed specialist copay for one office visit; planned imaging and final payer share are unresolved.'
                : 'Insured share awaits a matching payer check and selected CPTs'
              : combinedPlan
                ? `${money(combinedPlan.office_copay_cents)} office copay + ${combinedPlan.coinsurance_percent}% × ${money(combinedPlan.diagnostic_allowed_cents)} diagnostic allowance. No additional deductible or out-of-pocket reduction was applied where remaining balances were not returned.`
                : gehaZero
                  ? 'Published 2026 G.E.H.A High PSHB Medicare Advantage benefit for one covered in-network office visit. Stedi omitted member deductible and out-of-pocket rows; the UHC allowed amount remains unknown.'
                  : cignaTelevisit != null
                    ? 'One specialist copay with $0 office deductible and 0% office coinsurance. Final patient liability is capped by the processed allowed charge and may change after the claim.'
                    : result.collection_guidance?.basis === 'out_of_pocket_limit'
                      ? 'Covered in-network share is capped by the applicable out-of-pocket limit'
                      : aetnaPlanning
                        ? `min(${money(aetnaPlanning.copay_cents)} diagnostic copays + ${money(aetnaPlanning.deductible_cents)} deductible + ${money(aetnaPlanning.coinsurance_cents)} coinsurance, ${money(aetnaPlanning.estimated_allowed_cents)} fee reference, ${money(aetnaPlanning.oop_remaining_cents)} remaining out-of-pocket) = ${money(aetnaPlanning.patient_share_cents)} before prior payments. ${aetnaPlanning.message}`
                        : botoxPreview
                          ? `One procedure unit at ${money(botoxPreview.allowed_cents)} from ${botoxPreview.rate_source}; ${botoxPreview.coinsurance_percent}% office-injection coinsurance from Stedi. Confirm coding and individual benefit limits before charging.`
                          : feeProxy != null
                            ? feeProxy.message
                            : aetnaCopayPlanning != null
                              ? `${money(aetnaCopayPlanning)} copay + ${money(0)} deductible + 0% × Aetna allowed amount = ${money(aetnaCopayPlanning)} before the unknown allowance cap. Confirm the visit conditions and final claim amount.`
                              : priorDateAetnaCopay != null
                                ? 'Payer-reported office copay after recorded payments. The saved response is for another date; confirm visit-date coverage, provider network, Medicare billing protection and any other services before charging. Full patient responsibility remains unresolved.'
                                : pendingBluecardCopay != null
                                  ? 'One payer-reported specialist office copay. The selected visit has no applicable deductible or additional coinsurance; the final share remains subject to the allowed charge and processed claim.'
                                  : calculated
                                    ? 'Copay + deductible applied + coinsurance − allowed and out-of-pocket reductions'
                                    : cardPlanningCopay != null
                                      ? 'Printed specialist copay for one office visit; planned imaging and final payer share are unresolved.'
                                      : cignaCard
                                        ? 'Card-printed office copay only. Diagnostic CPT share remains unknown; Cigna allowed amounts and visit-date benefits are needed.'
                                        : 'Full share awaits applicable deductible and out-of-pocket terms',
          },
          ...(summary?.payments_verified_cents != null && summary.payments_verified_cents > 0
            ? [{ label: 'Already paid', value: `−${money(summary.payments_verified_cents)}` }]
            : []),
        ]
  rows.push(
    collectionAtVisitRow(result, model, {
      combinedPlan,
      feeProxy,
      aetnaPlanning,
      aetnaCopayPlanning,
      officeReferenceCopay,
      humanaReferenceCopay,
      partialOfficeCopay,
      pendingAetnaCopay,
      priorDateAetnaCopay,
      pendingBluecardCopay,
      cardPlanningCopay,
      cignaCard,
      automaticCashFallback,
      cignaFeeProxy,
      proxyFormula,
    }),
  )
  return <CommercialCostTables result={result} cash={model.cash} rows={rows} />
}
