import type { StaffWorkspaceModel } from './useStaffWorkspaceModel'
import { ArrowLeft, Check } from 'lucide-react'
import { dateLabel } from './staff-workspace-utils'

export default function StaffPatientMatchScreen({
  model,
  intake,
}: {
  model: StaffWorkspaceModel
  session: NonNullable<StaffWorkspaceModel['session']>
  intake: NonNullable<StaffWorkspaceModel['intake']>
}) {
  const {
    fields,
    candidates,
    match,
    setMatch,
    identityConfirmed,
    setIdentityConfirmed,
    ecwMatch,
    busy,
    heading,
    usesAutomaticRecord,
    navigate,
    retryEcwLookup,
    savePatient,
  } = model
  return (
    <div className="staff-narrow">
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Confirm patient · 2 of 2</div>
          <h1 tabIndex={-1} ref={heading}>
            {intake.source === 'ecw_lookup'
              ? 'eCW patient found'
              : candidates.length
                ? 'Confirm the clinic record'
                : intake.expected_patient_id || usesAutomaticRecord
                  ? 'Confirm the clinic record'
                  : 'Create the clinic record'}
          </h1>
          <p className="staff-muted">
            {fields.given_name} {fields.family_name} · {fields.birth_date}
          </p>
        </div>
      </header>
      {intake.source === 'ecw_lookup' && intake.fields.ecw_patient_id && (
        <section className="staff-panel">
          <h2>Verified in eClinicalWorks</h2>
          <p className="staff-muted">
            {ecwMatch
              ? `${ecwMatch.patient.given_name} ${ecwMatch.patient.family_name} · ${ecwMatch.patient.birth_date}`
              : 'The patient identity was verified in eCW. Appointments will refresh after you confirm the clinic record.'}
          </p>
          {ecwMatch?.patient.ecw_account_number && (
            <p>
              <strong>eCW ACCT: {ecwMatch.patient.ecw_account_number}</strong>
            </p>
          )}
          {ecwMatch &&
            (ecwMatch.appointments.length ? (
              <p className="staff-small">
                Next scheduled visit: {dateLabel(ecwMatch.appointments[0].start)}
                {ecwMatch.appointments[0].reason ? ` · ${ecwMatch.appointments[0].reason}` : ''}
              </p>
            ) : (
              <p className="staff-alert staff-warning">
                eCW found this patient but returned no upcoming appointment. An estimate needs a
                scheduled visit.
              </p>
            ))}
        </section>
      )}
      <form className="staff-panel" onSubmit={savePatient}>
        <h2>
          {candidates.length
            ? 'Possible clinic records'
            : intake.source === 'ecw_lookup'
              ? 'No ClinicForce record linked yet'
              : intake.expected_patient_id
                ? 'Patient requested by the clinic'
                : usesAutomaticRecord
                  ? 'Clinic record created from this upload'
                  : 'No matching clinic patient found'}
        </h2>
        <p className="staff-muted">
          {intake.source === 'ecw_lookup'
            ? 'Choose the correct ClinicForce record for the patient found in eCW. This does not create a new eCW patient.'
            : candidates.length
              ? 'Compare the details with the patient. Similar records are not linked automatically.'
              : 'Save this patient to your clinic. This does not create a patient login or an eClinicalWorks record.'}
        </p>
        {candidates.map((patient) => (
          <label className="staff-match-option" key={patient.id}>
            <input
              type="radio"
              name="patient-match"
              value={patient.id}
              checked={match === patient.id}
              onChange={() => setMatch(patient.id)}
              disabled={Boolean(busy)}
              required
            />
            <span>
              <strong>
                {patient.given_name} {patient.family_name}
              </strong>
              <small>Date of birth: {patient.birth_date}</small>
              <small>Clinic record: {patient.id}</small>
            </span>
          </label>
        ))}
        <label className="staff-match-option">
          <input
            type="radio"
            name="patient-match"
            value="new"
            checked={match === 'new'}
            onChange={() => setMatch('new')}
            disabled={Boolean(busy)}
            required
          />
          <span>
            <strong>
              {intake.expected_patient_id
                ? 'Use the patient this link was requested for'
                : usesAutomaticRecord
                  ? 'Confirm the clinic record created from this upload'
                  : intake.source === 'ecw_lookup'
                    ? 'Create a ClinicForce record for this eCW patient'
                    : 'Create a new clinic patient record'}
            </strong>
            <small>
              {intake.expected_patient_id
                ? 'The submitted name and date of birth must match the requested patient.'
                : usesAutomaticRecord
                  ? 'Confirm this record without creating a duplicate patient or a patient login.'
                  : 'Keep this intake separate from existing patients.'}
            </small>
          </span>
        </label>
        <label className="staff-confirmation">
          <input
            type="checkbox"
            checked={identityConfirmed}
            onChange={(event) => setIdentityConfirmed(event.target.checked)}
            required
            disabled={Boolean(busy)}
          />
          <span>I confirmed the patient's name, date of birth, and selected record.</span>
        </label>
        <div className="staff-actions">
          <button
            type="button"
            className="staff-text-button"
            onClick={() => {
              if (intake.source === 'ecw_lookup') retryEcwLookup(intake)
              else navigate('review')
            }}
            disabled={Boolean(busy)}
          >
            <ArrowLeft size={18} />{' '}
            {intake.source === 'ecw_lookup' ? 'Change eCW search' : 'Edit details'}
          </button>
          <button
            className="staff-primary"
            disabled={Boolean(busy) || !identityConfirmed || !match}
          >
            <Check size={18} />{' '}
            {intake.source === 'ecw_lookup'
              ? 'Continue to eCW appointment'
              : 'Save patient and continue'}
          </button>
        </div>
      </form>
    </div>
  )
}
