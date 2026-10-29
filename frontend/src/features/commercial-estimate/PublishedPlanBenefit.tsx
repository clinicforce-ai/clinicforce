import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'

export default function PublishedPlanBenefit({
  result,
  show,
}: {
  result: NonNullable<CommercialEstimateModel['result']>
  show: boolean
}) {
  const benefits = result.benefits
  const rule = benefits?.published_plan_rule
  const product = result.plan_details?.stedi?.identified_product
  if (
    !show ||
    !['FEP Blue Basic PSHB', 'Humana Value Choice PPO H7617-057'].includes(product || '') ||
    benefits?.active !== true ||
    !rule ||
    benefits.published_preferred_office_copay_cents == null
  ) {
    return null
  }

  if (product === 'Humana Value Choice PPO H7617-057')
    return (
      <details className="financial-breakdown">
        <summary>Published plan rule</summary>
        <p className="visit-muted" aria-label="Published plan benefit">
          Published 2026 Humana H7617-057 specialist office benefit:{' '}
          <strong>{money(benefits.published_preferred_office_copay_cents)}</strong> in network. The
          plan has no medical deductible; its in-network out-of-pocket limit is $6,800. Confirm this
          provider’s network status and visit conditions before collection.{' '}
          <a href={rule.url} target="_blank" rel="noreferrer">
            Humana plan source
          </a>
          .
        </p>
      </details>
    )
  return (
    <details className="financial-breakdown">
      <summary>Published plan rule</summary>
      <p className="visit-muted" aria-label="Published plan benefit">
        Published 2026 FEP Blue Basic PSHB benefit:{' '}
        <strong>{money(benefits.published_preferred_office_copay_cents)}</strong> per Preferred
        specialist office visit. This applies only when the rendering provider is FEP Preferred and
        other plan conditions hold; it is not the confirmed amount to collect.
        {benefits.deductible_applies === false &&
          ' This plan has no annual medical deductible.'}{' '}
        <a href={rule.url} target="_blank" rel="noreferrer">
          OPM plan source
        </a>
        .
      </p>
    </details>
  )
}
