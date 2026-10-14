import type { StaffWorkspaceModel } from './useStaffWorkspaceModel'
import { today } from './staff-workspace-utils'

export default function StaffEcwLookupScreen({
  model,
}: {
  model: StaffWorkspaceModel
  session: NonNullable<StaffWorkspaceModel['session']>
}) {
  const {
    lookupDetails,
    ecwSuggestions,
    ecwSelection,
    setEcwSelection,
    ecwConfirmed,
    setEcwConfirmed,
    busy,
    heading,
    selectedEcwPatient,
    navigate,
    changeEcwSearch,
    startEcwLookup,
    lookupEcwPatient,
  } = model
  return (
    <div className="staff-narrow">
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Add insurance</div>
          <h1 tabIndex={-1} ref={heading}>
            Find patient & estimate
          </h1>
          <p className="staff-muted">
            Enter the patient's name and date of birth, or enter their eCW ACCT number and date of
            birth. Confirm the returned eCW patient before opening their visit.
          </p>
        </div>
      </header>
      <section className="staff-panel">
        <h2>Patient details</h2>
        <form onSubmit={startEcwLookup}>
          <div className="staff-two-columns">
            <label className="staff-field">
              <span>First name</span>
              <input
                required={!lookupDetails.ecw_account_number.trim()}
                maxLength={100}
                autoComplete="given-name"
                value={lookupDetails.given_name}
                onChange={(event) => changeEcwSearch('given_name', event.target.value)}
                disabled={Boolean(busy)}
              />
            </label>
            <label className="staff-field">
              <span>Middle initial (optional)</span>
              <input
                maxLength={10}
                value={lookupDetails.middle_initial}
                onChange={(event) => changeEcwSearch('middle_initial', event.target.value)}
                disabled={Boolean(busy)}
              />
            </label>
            <label className="staff-field">
              <span>Last name</span>
              <input
                required={!lookupDetails.ecw_account_number.trim()}
                maxLength={100}
                autoComplete="family-name"
                value={lookupDetails.family_name}
                onChange={(event) => changeEcwSearch('family_name', event.target.value)}
                disabled={Boolean(busy)}
              />
            </label>
            <label className="staff-field">
              <span>Date of birth</span>
              <input
                required
                type="date"
                max={today()}
                autoComplete="bday"
                value={lookupDetails.birth_date}
                onChange={(event) => changeEcwSearch('birth_date', event.target.value)}
                disabled={Boolean(busy)}
              />
            </label>
            <label className="staff-field">
              <span>eCW account number (optional)</span>
              <input
                maxLength={80}
                value={lookupDetails.ecw_account_number}
                onChange={(event) => changeEcwSearch('ecw_account_number', event.target.value)}
                disabled={Boolean(busy)}
              />
              <small>ACCT number and date of birth are enough; names can be left blank.</small>
            </label>
          </div>
          <button className="staff-primary" disabled={Boolean(busy)}>
            Open insurance estimator
          </button>
        </form>
      </section>
      {ecwSuggestions !== null && (
        <section className="staff-panel" aria-labelledby="ecw-suggestions-title">
          <h2 id="ecw-suggestions-title">
            {ecwSuggestions.length ? 'Choose the eCW patient' : 'No close matches found'}
          </h2>
          <p role="status">
            {ecwSuggestions.length
              ? 'These are possible matches. Compare the name, date of birth and eCW account before continuing.'
              : lookupDetails.ecw_account_number
                ? 'Check the eCW ACCT number and birth date, then search again.'
                : 'Check the spelling and date of birth with the patient, then search again.'}
          </p>
          {ecwSuggestions.length > 0 && (
            <form
              onSubmit={(event) => {
                event.preventDefault()
                if (selectedEcwPatient && ecwConfirmed) void lookupEcwPatient(selectedEcwPatient)
              }}
            >
              <label className="staff-field">
                <span>Similar eCW patients</span>
                <select
                  required
                  value={ecwSelection}
                  onChange={(event) => {
                    setEcwSelection(event.target.value)
                    setEcwConfirmed(false)
                  }}
                  disabled={Boolean(busy)}
                >
                  <option value="">Select a patient</option>
                  {ecwSuggestions.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.given_name} {row.family_name} · DOB {row.birth_date} · eCW ACCT{' '}
                      {row.ecw_account_number || 'unavailable'}
                    </option>
                  ))}
                </select>
              </label>
              {ecwSelection && (
                <p className="staff-small">
                  {selectedEcwPatient?.differences.length
                    ? `Different from your search: ${selectedEcwPatient.differences.join(', ')}.`
                    : 'Verify the returned name, date of birth and eCW account.'}
                </p>
              )}
              <label className="staff-confirmation">
                <input
                  type="checkbox"
                  required
                  checked={ecwConfirmed}
                  onChange={(event) => setEcwConfirmed(event.target.checked)}
                  disabled={Boolean(busy) || !ecwSelection}
                />
                <span>I confirmed this patient's name, date of birth and eCW account.</span>
              </label>
              <button
                className="staff-primary"
                disabled={Boolean(busy) || !ecwSelection || !ecwConfirmed}
              >
                Use selected patient
              </button>
            </form>
          )}
        </section>
      )}
      <button onClick={() => navigate('patients')}>Back to patients</button>
    </div>
  )
}
