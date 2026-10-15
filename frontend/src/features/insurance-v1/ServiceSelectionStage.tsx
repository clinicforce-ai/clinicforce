import type { InsuranceV1Model } from './useInsuranceV1Model'
import { UploadTile } from './InsuranceV1Details'
import { imageOrPdf, money } from './v1-display'
import { confirmInsuranceService, suggestInsuranceServices } from '../../services/api/insurance-v1'

export default function ServiceSelectionStage({
  model,
  insuranceCase,
}: {
  model: InsuranceV1Model
  insuranceCase: NonNullable<InsuranceV1Model['insuranceCase']>
}) {
  const {
    staffIntakeId,
    selectedCodes,
    setSelectedCodes,
    quantities,
    setQuantities,
    contextEdited,
    setContextEdited,
    suggestionsStale,
    setSuggestionsStale,
    clinicalContext,
    setClinicalContext,
    visitType,
    setVisitType,
    busy,
    perform,
    uploadEvidence,
    reviewing,
    disabled,
    documents,
    groups,
    visitCodes,
    activeGroups,
    missingCompanions,
    missingQuantity,
  } = model
  return (
    <>
      <p className="section-kicker">4 · Select CPT codes</p>
      <h2>Describe the visit or upload clinical notes</h2>
      <p>
        ClinicForce suggests CPT codes from the condition or visit information you provide. These
        are suggestions—not facts extracted from the insurance card. Review and confirm the
        appropriate codes.
      </p>
      <div className="clinical-context-form">
        <label>
          <span>Patient symptoms, condition and planned care</span>
          <textarea
            value={clinicalContext}
            disabled={disabled}
            onChange={(event) => {
              setClinicalContext(event.target.value)
              setContextEdited(true)
              setSuggestionsStale(true)
            }}
            rows={5}
          />
        </label>
        <label>
          <span>Visit type, if known</span>
          <select
            value={visitType}
            disabled={disabled}
            onChange={(event) => {
              setVisitType(event.target.value as typeof visitType)
              setContextEdited(true)
              setSuggestionsStale(true)
            }}
          >
            <option value="">Not specified</option>
            <option value="new">New patient</option>
            <option value="established">Established patient</option>
          </select>
        </label>
        <UploadTile
          title="Clinical document · optional if you entered the condition"
          hint="Upload a visit note, referral, appointment email or clinical report describing the condition and planned care. PDF, JPG or PNG · up to 15 MB. This informs CPT suggestions; it does not establish insurance coverage or authorization."
          accept={imageOrPdf}
          source={documents.clinical}
          busy={disabled}
          onFile={(file) => uploadEvidence('clinical', file)}
        />
        {documents.clinical && (
          <>
            <details className="source-excerpt">
              <summary>Review extracted clinical passages</summary>
              {documents.clinical.findings?.clinical_excerpts?.map((entry, index) => (
                <section key={index}>
                  <small>
                    {documents.clinical.filename} · page {entry.page}
                  </small>
                  <blockquote>{entry.quote}</blockquote>
                </section>
              ))}
            </details>
            {!documents.clinical.findings?.clinical_context && (
              <p className="missing-source">
                No patient-specific clinical passages could be verified. Enter the visit details
                above.
              </p>
            )}
            {contextEdited && documents.clinical.findings?.clinical_context && (
              <button
                className="button button-secondary"
                type="button"
                disabled={disabled}
                onClick={() => {
                  setClinicalContext(documents.clinical.findings?.clinical_context ?? '')
                  setVisitType(documents.clinical.findings?.visit_type ?? '')
                  setContextEdited(false)
                  setSuggestionsStale(true)
                }}
              >
                Use extracted visit details
              </button>
            )}
          </>
        )}
        <button
          className="button button-secondary"
          type="button"
          disabled={disabled || (!clinicalContext.trim() && !documents.clinical)}
          onClick={() =>
            void perform(
              () =>
                suggestInsuranceServices(
                  insuranceCase.id,
                  clinicalContext,
                  visitType || undefined,
                  staffIntakeId,
                ),
              'CPT suggestion review finished. Only supported suggestions are listed below.',
              () => {
                setSuggestionsStale(false)
                setQuantities({})
              },
            )
          }
        >
          Suggest CPT/HCPCS codes
        </button>
      </div>
      {insuranceCase.suggestion_notes?.map((note, index) => (
        <p className="missing-source" key={index}>
          Coding review: {note}
        </p>
      ))}
      {insuranceCase.suggestions_reviewed && !insuranceCase.service_lines.length && (
        <p className="missing-source">
          No supported candidates were found in this uploaded fee schedule. Review the clinical
          details and the schedule.
        </p>
      )}
      {insuranceCase.service_lines.length > 0 && (
        <>
          <p>
            These groups are suggested for this case using your clinical details and uploaded fee
            schedule. Confirm the coding relationships, planned extent and units with the clinician;
            suggestions are not a billing or coverage determination.
          </p>
          {groups.map((group) => (
            <fieldset className="v1-code-group" key={group.id} disabled={disabled}>
              <legend>{group.label}</legend>
              <p>{group.description}</p>
              <small>
                {group.selection_mode === 'single'
                  ? 'Choose one alternative, or leave this group unselected.'
                  : 'Select the separate services planned for this visit.'}
              </small>
              {group.requires_groups.length > 0 && (
                <p className="missing-source">
                  Suggested companion:{' '}
                  {group.requires_groups
                    .map((id) => groups.find((item) => item.id === id)?.label)
                    .join(', ')}
                </p>
              )}
              <div className="service-options">
                {insuranceCase.service_lines
                  .filter((line) => group.codes.includes(line.code))
                  .map((line) => (
                    <div key={line.code}>
                      <label className={selectedCodes.includes(line.code) ? 'selected' : ''}>
                        <input
                          type={group.selection_mode === 'single' ? 'radio' : 'checkbox'}
                          name={`cpt-${group.id}`}
                          checked={selectedCodes.includes(line.code)}
                          disabled={disabled}
                          onChange={() =>
                            setSelectedCodes((current) =>
                              group.selection_mode === 'single'
                                ? [
                                    ...current.filter(
                                      (code) =>
                                        !(
                                          group.category === 'visit' ? visitCodes : group.codes
                                        ).includes(code),
                                    ),
                                    line.code,
                                  ]
                                : current.includes(line.code)
                                  ? current.filter((value) => value !== line.code)
                                  : [...current, line.code],
                            )
                          }
                        />
                        <span>
                          <strong>
                            {line.code}
                            {selectedCodes.includes(line.code)
                              ? ` · item ${selectedCodes.indexOf(line.code) + 1}`
                              : ''}
                          </strong>
                          <span>{line.description}</span>
                          <small>
                            {money(line.allowed_cents)} allowed · {documents.fee_schedule?.filename}
                            {line.reference ? ` · ${line.reference}` : ''}
                          </small>
                          {line.recommendation_reason && (
                            <small>ClinicForce suggestion: {line.recommendation_reason}</small>
                          )}
                        </span>
                      </label>
                      {selectedCodes.includes(line.code) && line.requires_quantity && (
                        <label className="v1-quantity">
                          Confirm quantity · {line.unit_label}
                          <input
                            aria-label={`Quantity for ${line.code}`}
                            type="number"
                            min="1"
                            max="10000"
                            step="1"
                            value={quantities[line.code] ?? ''}
                            disabled={disabled}
                            onChange={(event) =>
                              setQuantities((current) => ({
                                ...current,
                                [line.code]: event.target.valueAsNumber,
                              }))
                            }
                          />
                        </label>
                      )}
                    </div>
                  ))}
              </div>
              {activeGroups.has(group.id) && (
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={disabled}
                  onClick={() =>
                    setSelectedCodes((current) =>
                      current.filter((code) => !group.codes.includes(code)),
                    )
                  }
                >
                  Clear {group.label}
                </button>
              )}
            </fieldset>
          ))}
          {missingCompanions.length > 0 && (
            <p className="missing-source" role="status">
              Select the suggested companion service before continuing:{' '}
              {[...new Set(missingCompanions)].join(', ')}.
            </p>
          )}
          {missingQuantity && (
            <p className="missing-source" role="status">
              Confirm the billing quantity for each selected service that requests it. Do not guess
              from the symptoms.
            </p>
          )}
          {suggestionsStale && (
            <p className="missing-source" role="status">
              Visit details changed. Generate suggestions again before confirming codes.
            </p>
          )}
          {!reviewing && (
            <button
              className="button button-primary"
              type="button"
              disabled={
                busy ||
                suggestionsStale ||
                selectedCodes.length === 0 ||
                missingQuantity ||
                missingCompanions.length > 0
              }
              onClick={() =>
                void perform(
                  () =>
                    confirmInsuranceService(
                      insuranceCase.id,
                      selectedCodes,
                      Object.fromEntries(
                        Object.entries(quantities).filter(([code]) => selectedCodes.includes(code)),
                      ),
                      staffIntakeId,
                    ),
                  'Selected codes saved, with visit services first.',
                )
              }
            >
              Confirm {selectedCodes.length || ''} selected codes
            </button>
          )}
        </>
      )}
    </>
  )
}
