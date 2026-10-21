import type { BotoxReview } from '../../services/api/commercial-visit-types'

export function BotoxClinicalReview({
  value,
  onChange,
  drugSelected,
  officeSelected,
}: {
  value: BotoxReview
  onChange: (value: BotoxReview) => void
  drugSelected: boolean
  officeSelected: boolean
}) {
  const set = <K extends keyof BotoxReview>(key: K, field: BotoxReview[K]) =>
    onChange({ ...value, [key]: field })
  return (
    <section className="cash-package-options" aria-labelledby="botox-review-title">
      <h3 id="botox-review-title">Botox clinical review</h3>
      <p className="visit-muted">
        Confirm the planned care from the chart. Medication received at the clinic does not
        establish the dose given or a billable drug line.
      </p>
      <label>
        Chart or order reference
        <input
          value={value.evidence_reference || ''}
          maxLength={500}
          onChange={(event) => set('evidence_reference', event.target.value)}
        />
      </label>
      <label>
        Modifier coding review reference
        <input
          value={value.modifier_reference || ''}
          maxLength={500}
          onChange={(event) => set('modifier_reference', event.target.value)}
        />
      </label>
      <label>
        Documented medical indication
        <input
          value={value.indication || ''}
          maxLength={500}
          onChange={(event) => set('indication', event.target.value)}
        />
      </label>
      <label>
        Injection site or anatomical region
        <input
          value={value.anatomical_region || ''}
          maxLength={500}
          onChange={(event) => set('anatomical_region', event.target.value)}
        />
      </label>
      <label>
        Encounter evidence
        <select
          value={value.encounter_status || ''}
          onChange={(event) =>
            set('encounter_status', event.target.value as BotoxReview['encounter_status'])
          }
        >
          <option value="">Choose status</option>
          <option value="planned">Planned care</option>
          <option value="performed">Documented performed care</option>
        </select>
      </label>
      <label>
        Drug source
        <select
          value={value.supply_source || ''}
          onChange={(event) =>
            set('supply_source', event.target.value as BotoxReview['supply_source'])
          }
        >
          <option value="">Choose source</option>
          <option value="clinic">Clinic supplied</option>
          <option value="specialty_pharmacy">Specialty pharmacy supplied</option>
          <option value="unknown">Unknown</option>
        </select>
      </label>
      {drugSelected && (
        <>
          <label>
            Planned or administered brand
            <select
              value={value.brand || ''}
              onChange={(event) => set('brand', event.target.value as BotoxReview['brand'])}
            >
              <option value="">Choose brand</option>
              <option value="Botox">Botox</option>
              <option value="other">Other brand</option>
              <option value="unknown">Unknown</option>
            </select>
          </label>
          {value.encounter_status === 'planned' && (
            <label>
              Planned HCPCS units for this visit
              <input
                type="number"
                min="1"
                max="10000"
                value={value.planned_units ?? ''}
                onChange={(event) =>
                  set('planned_units', event.target.value ? Number(event.target.value) : undefined)
                }
              />
            </label>
          )}
          {value.encounter_status === 'performed' && (
            <>
              <label>
                Administered units
                <input
                  type="number"
                  min="1"
                  max="10000"
                  value={value.administered_units ?? ''}
                  onChange={(event) =>
                    set(
                      'administered_units',
                      event.target.value ? Number(event.target.value) : undefined,
                    )
                  }
                />
              </label>
              <label>
                Documented discarded units
                <input
                  type="number"
                  min="0"
                  max="10000"
                  value={value.discarded_units ?? ''}
                  onChange={(event) =>
                    set(
                      'discarded_units',
                      event.target.value ? Number(event.target.value) : undefined,
                    )
                  }
                />
              </label>
            </>
          )}
          <p className="visit-muted">
            Confirm HCPCS units separately above. Discarded medication needs a payer-specific coding
            review; stock received is not the dose.
          </p>
        </>
      )}
      {officeSelected && (
        <label>
          <input
            type="checkbox"
            checked={value.separate_em_documented || false}
            onChange={(event) => set('separate_em_documented', event.target.checked)}
          />
          Separate office evaluation is documented and coding reviewed
        </label>
      )}
    </section>
  )
}
