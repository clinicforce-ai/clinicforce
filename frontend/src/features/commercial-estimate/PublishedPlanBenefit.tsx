import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'

export default function PublishedPlanBenefit({
  result,
}: {
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const benefits = result.benefits
  const rule = benefits?.published_plan_rule
  if (
    result.plan_details?.stedi?.identified_product !== 'FEP Blue Basic PSHB' ||
    benefits?.active !== true ||
    !rule ||
    benefits.published_preferred_office_copay_cents == null
  ) {
    return null
  }

  return (
    <p className="visit-muted" aria-label="Published plan benefit">
      Published 2026 FEP Blue Basic PSHB benefit:{' '}
      <strong>{money(benefits.published_preferred_office_copay_cents)}</strong> per Preferred
      specialist office visit. This applies only when the rendering provider is FEP Preferred and
      other plan conditions hold; it is not the confirmed amount to collect.
      {benefits.deductible_applies === false && ' This plan has no annual medical deductible.'}{' '}
      <a href={rule.url} target="_blank" rel="noreferrer">
        OPM plan source
      </a>
      .
    </p>
  )
}
