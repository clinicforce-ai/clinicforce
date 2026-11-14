import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { patientBenefitsBound } from './identityBinding'

export function payerPlanDisplay(result: PrevisitEstimate | null) {
  const referenceNames = result?.reference_only
    ? result.reference_benefits?.plan_details?.names || []
    : []
  const identified =
    !result?.reference_only &&
    result?.benefits?.active === true &&
    patientBenefitsBound(result.identity)
      ? result.plan_details?.stedi.identified_product
      : null
  const healthselect =
    !result?.reference_only &&
    result?.benefits?.active === true &&
    patientBenefitsBound(result.identity) &&
    result.plan_details?.stedi.group_number === '13546' &&
    result.benefits.published_plan_rule?.url ===
      'https://ers.texas.gov/retirees/health-benefits/healthselect-medicare-advantage'
  const stedi = result?.plan_details?.stedi
  const choicePlusNetwork =
    !result?.reference_only &&
    result?.benefits?.active === true &&
    patientBenefitsBound(result.identity) &&
    /^united\s*health\s*care$/i.test(result.payer || '') &&
    !stedi?.names?.length &&
    stedi?.group_number &&
    /\b(?:employee|employer)\b/i.test(stedi.group_name || '') &&
    stedi.network_names?.length === 1 &&
    stedi.network_names[0].replace(/[^a-z0-9]/gi, '').toLowerCase() === 'unitedhealthcarechoiceplus'
      ? `UnitedHealthcare Choice Plus network${
          stedi.response_payer_name && !/^united\s*health\s*care$/i.test(stedi.response_payer_name)
            ? ` · ${stedi.response_payer_name} eligibility response`
            : ''
        }`
      : null
  const planName =
    referenceNames.join(' · ') ||
    (healthselect ? 'HealthSelect Medicare Advantage PPO · H2001-817-000' : null) ||
    identified ||
    result?.plan_details?.stedi.names?.join(' · ') ||
    choicePlusNetwork ||
    result?.plan_details?.ecw.plan?.filter((value) => value !== 'CI').join(' · ') ||
    (result?.plan !== 'CI' ? result?.plan : null)
  const planSource = referenceNames.length
    ? 'Stedi payer reference; verify applicability to the appointment date'
    : healthselect
      ? result?.card_check?.status === 'matched'
        ? 'Matched insurance card, patient-bound Stedi eligibility and ERS plan benefits'
        : 'Patient-bound Stedi group and ERS plan benefits'
      : identified === 'Humana Value Choice PPO H7617-057'
        ? 'Matched insurance card CMS plan ID and patient-bound Stedi eligibility response'
        : choicePlusNetwork
          ? 'Patient-bound Stedi network; product name not returned'
          : result?.plan_details?.stedi.names?.length
            ? `${result.benefit_source || result.plan_details.stedi.source || 'Payer'} eligibility response`
            : planName
              ? 'eCW policy record'
              : null
  return { planName, planSource }
}
