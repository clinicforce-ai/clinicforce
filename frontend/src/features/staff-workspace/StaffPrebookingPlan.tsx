import type { PrebookingPlan } from '../../services/api/prebooking-eligibility'

type Props = {
  plan: PrebookingPlan
  selected: Record<string, number>
  busy: boolean
  onToggle: (code: string, group?: PrebookingPlan['groups'][number]) => void
  onUnits: (code: string, units: number) => void
}

export default function StaffPrebookingPlan({ plan, selected, busy, onToggle, onUnits }: Props) {
  const baseline = plan.groups
    .flatMap((group) => group.codes)
    .find((code) => code.selected_by_default)
  const extras = plan.groups
    .map((group) => ({ ...group, codes: group.codes.filter((code) => !code.selected_by_default) }))
    .filter((group) => group.codes.length)

  return (
    <section className="prebooking-plan" aria-labelledby="prebooking-plan-title">
      <h2 id="prebooking-plan-title">Services in this estimate</h2>
      {baseline && (
        <div className="prebooking-baseline">
          <strong>Office visit · {baseline.code}</strong>
          <span>{baseline.reason}</span>
        </div>
      )}
      {extras.length > 0 && (
        <div className="prebooking-extras">
          <h3>Possible additions from the visit reason</h3>
          <p className="staff-small staff-muted">
            Include only services the clinic expects to provide.
          </p>
          {extras.map((group, index) => (
            <div key={`${group.label}:${index}`} className="prebooking-service-group">
              <h4>{group.label}</h4>
              {group.codes.map((candidate) => (
                <div key={candidate.code} className="prebooking-extra-row">
                  <label className="prebooking-service-choice">
                    <input
                      type="checkbox"
                      checked={candidate.code in selected}
                      disabled={busy}
                      onChange={() => onToggle(candidate.code, group)}
                    />
                    <span>
                      <strong>{candidate.code}</strong> · {candidate.reason}
                    </span>
                  </label>
                  {candidate.code in selected && (
                    <label className="prebooking-extra-units">
                      Units
                      <input
                        type="number"
                        min="1"
                        max="4"
                        value={selected[candidate.code]}
                        disabled={busy}
                        onChange={(event) => onUnits(candidate.code, Number(event.target.value))}
                      />
                    </label>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
