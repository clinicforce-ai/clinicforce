import type { PrevisitEstimate } from '../../services/api/commercial-estimator'

/** Apply the clinic's Aetna rule only when Stedi identified the saved reference plan. */
export function aetnaReferenceRequirement(
  result: PrevisitEstimate,
  kind: 'referral' | 'authorization',
  code: string,
): string | null {
  const reference = result.reference_only ? result.reference_benefits : null
  if (reference?.active !== true || !/\baetna\b/i.test(result.payer || '')) return null
  if (result.aetna_diagnostic_reference && ['95908', '95886'].includes(code)) return null
  if (kind === 'authorization')
    return ['64612', '64615'].includes(code)
      ? 'required; approval status unresolved (clinic Aetna rule)'
      : 'not required under clinic Aetna rule'
  const plan = (reference.plan_details?.names || []).join(' ')
  const hmo = /\bHMO\b|network.only/i.test(plan)
  const ppo = /\bPPO\b/i.test(plan)
  if (hmo === ppo) return null
  return hmo
    ? 'required; referral status unresolved (clinic Aetna rule)'
    : 'not required under clinic Aetna rule'
}
