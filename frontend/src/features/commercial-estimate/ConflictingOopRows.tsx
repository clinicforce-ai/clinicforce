import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

type Benefits = NonNullable<PrevisitEstimate['benefits']>

export default function ConflictingOopRows({
  benefits,
  annual,
}: {
  benefits: Benefits
  annual: boolean
}) {
  const ids =
    (annual ? benefits.oop_annual_conflict_ids : benefits.oop_remaining_conflict_ids) || []
  const rows = ids.map((id) => ({
    id,
    value: benefits.reported_options?.find((row) => row.id === id),
  }))
  const scope = (row: NonNullable<(typeof rows)[number]['value']>) =>
    [
      row.plan_name || (row.plan_index != null ? `Plan ${row.plan_index + 1}` : 'Plan not named'),
      (row.coverage_level || 'coverage level not returned').replaceAll('_', ' ').toLowerCase(),
      (row.network || 'network not returned').replaceAll('_', ' ').toLowerCase(),
      (row.time_period || 'period not returned').replaceAll('_', ' ').toLowerCase(),
      row.service_type_code
        ? `service category ${row.service_type_code}`
        : 'service category not returned',
      Object.entries(row.effective_dates || {}).length
        ? `effective dates ${Object.entries(row.effective_dates || {})
            .map(
              ([key, value]) =>
                `${key
                  .replaceAll('.', ' ')
                  .replace(/([a-z])([A-Z])/g, '$1 $2')
                  .toLowerCase()} ${value}`,
            )
            .join(', ')}`
        : 'effective dates not returned',
    ].join(' · ')
  const scopes = rows.map(({ value }) => value && scope(value)).filter(Boolean)
  const sharedScope = scopes.length === rows.length && new Set(scopes).size === 1 ? scopes[0] : null
  return (
    <div className="oop-conflict-rows">
      <strong>Conflicting {annual ? 'annual' : 'remaining'} out-of-pocket rows</strong>
      {sharedScope && <p>{sharedScope}</p>}
      <ul>
        {rows.map(({ id, value }) => (
          <li key={id}>
            {value?.amount_cents != null ? money(value.amount_cents) : 'Amount not retained'}
            {!sharedScope && value && ` · ${scope(value)}`}
            <small>Response row: {id}</small>
          </li>
        ))}
      </ul>
    </div>
  )
}
