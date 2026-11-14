import type { PrebookingPlan } from '../../services/api/prebooking-eligibility'

type Props = {
  plan: PrebookingPlan
  selected: Record<string, number>
  busy: boolean
  checking: boolean
  confirmed: boolean
  payerSelected: boolean
  onToggle: (code: string, group?: PrebookingPlan['groups'][number]) => void
  onUnits: (code: string, units: number) => void
  onConfirm: (confirmed: boolean) => void
}

export default function StaffPrebookingPlan({
  plan,
  selected,
  busy,
  checking,
  confirmed,
  payerSelected,
  onToggle,
  onUnits,
  onConfirm,
}: Props) {
  const hasChoices = plan.groups.some((group) => group.codes.length > 0)
  const labels = new Map(
    plan.groups.flatMap((group) => group.codes.map((item) => [item.code, item.label] as const)),
  )
  return (
    <section className="prebooking-plan" aria-labelledby="prebooking-plan-title">
      <h2 id="prebooking-plan-title">2. Review possible care</h2>
      <p className="staff-muted">{plan.message}</p>
      {plan.groups.map((group, index) => (
        <div key={`${group.label}:${index}`} className="prebooking-service-group">
          <h3>{group.label}</h3>
          {group.description && <p className="staff-small staff-muted">{group.description}</p>}
          {group.codes.map((candidate) => (
            <label key={candidate.code} className="prebooking-service-choice">
              <input
                type="checkbox"
                checked={candidate.code in selected}
                disabled={busy}
                onChange={() => onToggle(candidate.code, group)}
              />
              <span>
                <strong>{candidate.label}</strong>
                {candidate.role !== 'consultation' && (
                  <em className="prebooking-procedure-label">Potential extra procedure or test</em>
                )}{' '}
                · {candidate.reason}
                <small className="prebooking-code-reference">
                  Coding reference {candidate.code}
                </small>
              </span>
            </label>
          ))}
        </div>
      ))}
      {!hasChoices && (
        <p className="prebooking-detail-needed">
          A specific service cannot be priced from this reason yet. Add any test, treatment, study
          count, or visit details discussed with the clinician to the reason above, then find
          possible care again. No code is needed.
        </p>
      )}
      {plan.notes.map((note, index) => (
        <p key={index} className="staff-small staff-muted">
          {note}
        </p>
      ))}
      {Object.keys(selected).length > 0 && (
        <div className="prebooking-selected-services">
          {Object.entries(selected).map(([code, units]) => (
            <div key={code}>
              <strong>{labels.get(code) || 'Planned service'}</strong>
              <small className="prebooking-code-reference">{code}</small>
              <label>
                Units{' '}
                <input
                  type="number"
                  min="1"
                  max="4"
                  value={units}
                  disabled={busy}
                  onChange={(event) => onUnits(code, Number(event.target.value))}
                />
              </label>
              <button type="button" className="staff-text-button" onClick={() => onToggle(code)}>
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
      <label className="prebooking-confirm">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={busy || !Object.keys(selected).length}
          onChange={(event) => onConfirm(event.target.checked)}
        />{' '}
        I reviewed these anticipated services and units with the clinician.
      </label>
      <button
        className="staff-primary"
        disabled={busy || !confirmed || !payerSelected || !Object.keys(selected).length}
      >
        {checking ? 'Checking with payer…' : 'Check benefits and estimate cost'}
      </button>
      <p className="staff-small staff-muted">
        This may send several service-specific Stedi inquiries. ClinicForce does not save this
        result; Stedi retains its transaction records.
      </p>
    </section>
  )
}
