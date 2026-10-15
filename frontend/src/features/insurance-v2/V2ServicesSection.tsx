import type { InsuranceV2Model } from './useInsuranceV2Model'
import { AlertTriangle, Check, FileSpreadsheet, RefreshCw, Sparkles } from 'lucide-react'
import { createV2Case, suggestV2Services } from '../../services/api/insurance-v2'
import { money, readable } from './v2-display'
import StaffLibraryPicker from '../staff-library/StaffLibraryPicker'
import type { Choice } from './useInsuranceV2Model'

export default function V2ServicesSection({
  model,
  insuranceCase,
}: {
  model: InsuranceV2Model
  insuranceCase: NonNullable<InsuranceV2Model['insuranceCase']>
}) {
  const {
    staffMode,
    staffIntakeId,
    busy,
    choices,
    setChoices,
    perform,
    stediTemporary,
    ecwPreview,
    visitStatus,
    hasEmSuggestions,
    servicesLocked,
    uploadFees,
    toggle,
    confirmServices,
  } = model
  return (
    <section className="v2-services" aria-labelledby="services-heading">
      <div>
        <p className="section-kicker">
          {ecwPreview ? 'Step 1 · clinical coding review' : 'Services and clinic prices'}
        </p>
        <h2 id="services-heading">
          {ecwPreview
            ? 'Choose the services for this appointment'
            : 'Confirm what is being estimated'}
        </h2>
        <p>
          {ecwPreview
            ? 'Start with codes attached to this eCW appointment. If eCW has not returned a code, ClinicForce can show review-only candidates from the appointment context. A clinician must confirm every selection.'
            : stediTemporary
              ? 'eClinicalWorks coded services are used first. The fee CSV must supply the clinic’s contract allowed amount for each estimated CPT/HCPCS code.'
              : 'eClinicalWorks coded services are used first. The fee CSV supplies the clinic’s billed charge and cash price. It never supplies insurance benefits.'}
        </p>
      </div>
      {ecwPreview && (insuranceCase.inputs.reason || visitStatus || hasEmSuggestions) && (
        <div className="v2-coding-context" aria-label="eClinicalWorks coding context">
          {insuranceCase.inputs.reason && (
            <div>
              <span>Reason returned by eCW</span>
              <strong>{insuranceCase.inputs.reason}</strong>
            </div>
          )}
          {visitStatus && (
            <div>
              <span>Visit status</span>
              <strong>{visitStatus}</strong>
            </div>
          )}
          {hasEmSuggestions && (
            <div>
              <span>E/M level</span>
              <strong>Clinician confirmation required</strong>
              <small>
                Confirm documented medical decision making or qualifying time before choosing an E/M
                level.
              </small>
            </div>
          )}
        </div>
      )}
      {staffMode && (
        <StaffLibraryPicker
          kinds={['fees']}
          disabled={servicesLocked || !!busy}
          onFile={(_kind, file) => uploadFees(file)}
        />
      )}
      {!staffMode && (
        <label className={`v2-fee-upload ${servicesLocked ? 'is-disabled' : ''}`}>
          <FileSpreadsheet size={22} aria-hidden="true" />
          <span>
            <strong>
              {insuranceCase.sources.fees
                ? `${insuranceCase.sources.fees.rate_count} clinic prices loaded`
                : 'Upload clinic fee CSV'}
            </strong>
            <small>
              Columns: CPT/HCPCS, Description, Cash Price, Billed Charge, and/or Contracted Allowed
              Amount. Optional Service Status: Scheduled or Possible.
            </small>
          </span>
          <input
            type="file"
            accept=".csv,text/csv"
            disabled={!!busy || servicesLocked}
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) uploadFees(file)
              event.currentTarget.value = ''
            }}
          />
        </label>
      )}
      {!!insuranceCase.inputs.lines.length && (
        <div className="v2-service-list">
          {insuranceCase.inputs.lines.map((line) => (
            <div key={`${line.code}-${line.service_status}`}>
              <strong>{line.code}</strong>
              <span>{line.description || 'Description not returned'}</span>
              <small>
                {readable(line.service_status || 'scheduled')} · {line.units || 'missing'} unit(s) ·
                billed {line.charge ? `$${line.charge}` : 'charge missing'} · cash{' '}
                {money(line.cash_cents)}
              </small>
            </div>
          ))}
        </div>
      )}
      {servicesLocked && (
        <p className="v2-next-step">
          Services and prices are locked because a payer request already uses them. Start a new case
          to make a change.
        </p>
      )}
      {(insuranceCase.inputs.reason || insuranceCase.inputs.lines.length > 0) &&
        !servicesLocked &&
        (!insuranceCase.suggestions || insuranceCase.suggestions.groups.length > 0) && (
          <button
            className="button button-secondary"
            type="button"
            disabled={!!busy}
            onClick={() =>
              void perform('Finding clinician-reviewable services from the eCW appointment…', () =>
                suggestV2Services(insuranceCase.id, staffIntakeId),
              )
            }
          >
            <Sparkles size={16} aria-hidden="true" />{' '}
            {insuranceCase.suggestions
              ? 'Regenerate eCW service choices'
              : 'Prepare eCW service choices'}
          </button>
        )}
      {insuranceCase.suggestions && (
        <div className="v2-suggestions">
          {!insuranceCase.suggestions.groups.length ? (
            <div className="v2-coding-empty" role="status">
              <strong>No CPT/HCPCS choice yet</strong>
              <p>
                No eCW-coded service is attached to this appointment. Reload after the appointment
                is coded, or ask the clinician to review the visit details.
              </p>
              <button
                className="button button-primary"
                type="button"
                disabled={!!busy}
                onClick={() =>
                  void perform('Reloading the eCW appointment…', () =>
                    createV2Case(undefined, undefined, staffIntakeId),
                  )
                }
              >
                <RefreshCw size={16} aria-hidden="true" /> Reload eCW appointment
              </button>
            </div>
          ) : (
            <>
              <div className="v2-suggestion-warning">
                <AlertTriangle size={18} aria-hidden="true" />
                <span>
                  <strong>Clinician review required.</strong> eCW-coded services are shown first.
                  When eCW has no code, ClinicForce compares the current appointment reason with the
                  most recent completed visit to prepare candidates. It never copies historical
                  services, chooses an E/M level, diagnoses the patient, or establishes medical
                  necessity.
                </span>
              </div>
              {insuranceCase.suggestions.requires_fee_schedule && (
                <div className="v2-fee-required">
                  <strong>Before payer review:</strong> upload the clinic fee CSV to attach billed
                  charges and cash prices to the selected codes.
                </div>
              )}
              {insuranceCase.suggestions.groups.map((group) => (
                <fieldset key={group.id}>
                  <legend>{group.label}</legend>
                  {group.description && <p>{group.description}</p>}
                  {group.codes.map((code) => {
                    const line = insuranceCase.suggestions!.lines.find(
                      (item) => item.code === code,
                    )!
                    const choice = choices[code] || {
                      selected: false,
                      quantity: 1,
                      service_status: 'scheduled' as const,
                    }
                    return (
                      <div
                        className={`v2-suggestion ${choice.selected ? 'selected' : ''}`}
                        key={code}
                      >
                        <label>
                          <input
                            type={group.selection_mode === 'single' ? 'radio' : 'checkbox'}
                            name={group.selection_mode === 'single' ? group.id : code}
                            checked={choice.selected}
                            onChange={() =>
                              toggle(code, group.codes, group.selection_mode === 'single')
                            }
                          />
                          <span>
                            <strong>
                              {code} · {line.description}
                            </strong>
                            <small>{line.recommendation_reason}</small>
                          </span>
                        </label>
                        {choice.selected && (
                          <div className="v2-suggestion-controls">
                            <label>
                              Quantity{' '}
                              <input
                                type="number"
                                min="1"
                                max="10000"
                                value={choice.quantity}
                                onChange={(event) =>
                                  setChoices((current) => ({
                                    ...current,
                                    [code]: {
                                      ...choice,
                                      quantity: Number(event.target.value),
                                    },
                                  }))
                                }
                              />
                            </label>
                            <label>
                              Estimate as{' '}
                              <select
                                value={choice.service_status}
                                onChange={(event) =>
                                  setChoices((current) => ({
                                    ...current,
                                    [code]: {
                                      ...choice,
                                      service_status: event.target
                                        .value as Choice['service_status'],
                                    },
                                  }))
                                }
                              >
                                <option value="scheduled">Scheduled visit</option>
                                <option value="possible">Possible procedure</option>
                              </select>
                            </label>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </fieldset>
              ))}
              {!!insuranceCase.suggestions.notes.length && (
                <ul>
                  {insuranceCase.suggestions.notes.map((note, index) => (
                    <li key={index}>{note}</li>
                  ))}
                </ul>
              )}
              <button
                className="button button-primary"
                type="button"
                disabled={!!busy}
                onClick={confirmServices}
              >
                <Check size={16} aria-hidden="true" /> Save clinician-reviewed services
              </button>
            </>
          )}
        </div>
      )}
    </section>
  )
}
