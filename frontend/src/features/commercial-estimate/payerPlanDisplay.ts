import type { PrevisitEstimate } from '../../services/api/commercial-estimator'

export function payerPlanDisplay(result: PrevisitEstimate | null) {
  const referenceNames = result?.reference_only
    ? result.reference_benefits?.plan_details?.names || []
    : []
  const planName =
    referenceNames.join(' · ') ||
    result?.plan_details?.stedi.names?.join(' · ') ||
    result?.plan_details?.ecw.plan?.filter((value) => value !== 'CI').join(' · ') ||
    (result?.plan !== 'CI' ? result?.plan : null)
  const planSource = referenceNames.length
    ? 'Stedi payer reference; verify applicability to the appointment date'
    : result?.plan_details?.stedi.names?.length
      ? `${result.benefit_source || result.plan_details.stedi.source || 'Payer'} eligibility response`
      : planName
        ? 'eCW policy record'
        : null
  return { planName, planSource }
}
