import type { PrevisitEstimate } from '../../services/api/commercial-previsit'
import { memberSource } from './format'
import { uhcNmCardPlan } from './uhcNmOfficeReference'

export default function CommercialInsurerIdentity({
  result,
  planName,
  planSource,
  payerSource,
  secondaryPayers,
}: {
  result: PrevisitEstimate
  planName: string | null | undefined
  planSource: string | null | undefined
  payerSource: string | null | undefined
  secondaryPayers: string[]
}) {
  const cardPlan = uhcNmCardPlan(result)
  const label = cardPlan
    ? 'UnitedHealthcare Medicare Advantage PPO'
    : result.payer ||
      (result.recovery?.candidates?.length === 1
        ? `${result.recovery.candidates[0].payer || 'Unknown payer'} · returned policy`
        : result.recovery?.candidates?.length
          ? `${result.recovery.candidates.length} policy suggestions`
          : 'Not returned')
  return (
    <>
      {label}
      {cardPlan && (
        <small>
          Insurance card issuer: UnitedHealthcare · Optum Care administers this plan under LIFE1
        </small>
      )}
      {result.payer && (
        <small>
          {cardPlan ? 'eCW selected payor' : 'Insurer source'}:{' '}
          {cardPlan ? `${result.payer} · ` : ''}
          {memberSource(payerSource)}
        </small>
      )}
      <small>{planName}</small>
      {planSource && <small>Plan source: {planSource}</small>}
      {!!secondaryPayers.length && (
        <small>
          {cardPlan ? 'Other eCW entry (order unverified)' : 'Secondary eCW policy'}:{' '}
          {secondaryPayers.join(' · ')}
        </small>
      )}
    </>
  )
}
