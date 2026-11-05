import type { PrebookingPlan } from '../../services/api/prebooking-eligibility'

type Props = {
  plan: PrebookingPlan
  selected: Record<string, number>
  busy: boolean
  checking: boolean
  manualCode: string
  confirmed: boolean
  payerSelected: boolean
  onToggle: (code: string, group?: PrebookingPlan['groups'][number]) => void
  onManualCode: (value: string) => void
  onAddCode: () => void
  onUnits: (code: string, units: number) => void
  onConfirm: (confirmed: boolean) => void
}

export default function StaffPrebookingPlan({
  plan,
  selected,
  busy,
  checking,
  manualCode,
  confirmed,
  payerSelected,
  onToggle,
  onManualCode,
  onAddCode,
  onUnits,
  onConfirm,
}: Props) {
  return (
    <section className="prebooking-plan" aria-labelledby="prebooking-plan-title">
      <h2 id="prebooking-plan-title">2. Confirm anticipated services</h2>
      <p className="staff-muted">{plan.message}</p>
      {plan.groups.map((group, index) => (
        <div key={`${group.label}:${index}`} className="prebooking-service-group">
          <h3>{group.label}</h3>
          {group.codes.map((candidate) => (
            <label key={candidate.code} className="prebooking-service-choice">
              <input
                type="checkbox"
                checked={candidate.code in selected}
                disabled={busy}
                onChange={() => onToggle(candidate.code, group)}
              />
              <span>
                <strong>{candidate.code}</strong>
                {candidate.role !== 'consultation' && (
                  <em className="prebooking-procedure-label">Potential extra procedure or test</em>
                )}{' '}
                · {candidate.reason}
              </span>
            </label>
          ))}
        </div>
      ))}
      {plan.notes.map((note, index) => (
        <p key={index} className="staff-small staff-muted">
          {note}
        </p>
      ))}
      <div className="prebooking-manual-code">
        <label className="staff-field">
          <span>Add a clinician-confirmed CPT/HCPCS code</span>
          <input
            value={manualCode}
            maxLength={5}
            disabled={busy}
            onChange={(event) => onManualCode(event.target.value.toUpperCase())}
          />
        </label>
        <button type="button" className="staff-text-button" disabled={busy} onClick={onAddCode}>
          Add code
        </button>
      </div>
      {Object.keys(selected).length > 0 && (
        <div className="prebooking-selected-services">
          {Object.entries(selected).map(([code, units]) => (
            <div key={code}>
              <strong>{code}</strong>
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
