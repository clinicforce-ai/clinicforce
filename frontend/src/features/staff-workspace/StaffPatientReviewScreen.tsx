import type { StaffWorkspaceModel } from './useStaffWorkspaceModel'
import { ArrowLeft, ChevronRight, FileText } from 'lucide-react'
import { readStaffInsuranceCard } from '../../services/api/staff-intake'
import { downloadStaffDocument } from '../../services/api/staff-downloads'
import { type StaffPatientFields } from '../../services/api/staff-contracts'
import { patientFieldsFrom } from './staff-workspace-utils'

export default function StaffPatientReviewScreen({
  model,
  intake,
}: {
  model: StaffWorkspaceModel
  session: NonNullable<StaffWorkspaceModel['session']>
  intake: NonNullable<StaffWorkspaceModel['intake']>
}) {
  const {
    setIntake,
    fields,
    setFields,
    busy,
    setNotice,
    heading,
    perform,
    navigate,
    retryEcwLookup,
    findMatches,
    input,
  } = model
  return (
    <div className="staff-narrow">
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Confirm patient · 1 of 2</div>
          <h1 tabIndex={-1} ref={heading}>
            Check the patient details
          </h1>
          <p className="staff-muted">
            {intake.source === 'ecw_lookup'
              ? 'Confirm the patient name and date of birth before looking up the scheduled visit in eCW.'
              : 'The name on the insurance card may belong to the policyholder.'}
          </p>
        </div>
      </header>
      {intake.source === 'ecw_lookup' && !intake.fields.ecw_patient_id && (
        <div className="staff-alert staff-warning">
          This intake was saved before eCW verified the patient.{' '}
          <button type="button" onClick={() => retryEcwLookup(intake)}>
            Search in eCW now
          </button>
        </div>
      )}
      {intake.documents.length > 0 && !intake.patient && (
        <section className="staff-panel">
          <h2>Read the saved insurance card</h2>
          <p className="staff-muted">
            Confirm the patient’s identity separately from the policyholder printed on the card.
            {intake.expected_patient_id
              ? ' This link was requested for an existing patient; verify the record before linking it.'
              : ''}
          </p>
          {intake.expected_patient && (
            <p className="staff-alert staff-warning">
              Requested for{' '}
              <strong>
                {intake.expected_patient.given_name} {intake.expected_patient.family_name}
              </strong>{' '}
              · Date of birth: {intake.expected_patient.birth_date}. Confirm the uploaded details
              match this patient.
            </p>
          )}
          <button
            disabled={Boolean(busy)}
            onClick={() =>
              void perform('read-card', async () => {
                const record = await readStaffInsuranceCard(intake.id)
                const proposals = patientFieldsFrom(record)
                setIntake(record)
                setFields(
                  (current) =>
                    Object.fromEntries(
                      Object.entries(current).map(([key, value]) => [
                        key,
                        value || proposals[key as keyof StaffPatientFields],
                      ]),
                    ) as StaffPatientFields,
                )
                setNotice(
                  'Card reading finished. Review the proposed insurance details before saving.',
                )
              })
            }
          >
            Read insurance card
          </button>
          <p className="staff-small staff-muted staff-source-note">
            Fills missing insurance details from the saved card. You can also enter them manually.
          </p>
        </section>
      )}
      {intake.warning && (
        <div className="staff-alert staff-warning">
          {intake.warning} The uploaded card is saved. Enter or correct the details below.
        </div>
      )}
      <form className="staff-panel" onSubmit={findMatches}>
        <h2>Patient receiving care</h2>
        <p className="staff-small staff-muted">
          Fields marked * are required. Confirm these details with the patient.
        </p>
        <div className="staff-two-columns">
          {input('First name', 'given_name', { required: true })}
          {input('Last name', 'family_name', { required: true })}
          {input('Date of birth', 'birth_date', { required: true, type: 'date' })}
          {intake.source !== 'ecw_lookup' && (
            <label className="staff-field">
              <span>Relationship to policyholder *</span>
              <select
                value={fields.relationship}
                onChange={(event) =>
                  setFields((current) => ({ ...current, relationship: event.target.value }))
                }
                disabled={Boolean(busy)}
              >
                <option value="unknown">Use eCW coverage</option>
                <option value="self">Self</option>
                <option value="spouse">Spouse</option>
                <option value="child">Child</option>
                <option value="other">Other</option>
              </select>
            </label>
          )}
        </div>
        {intake.source !== 'ecw_lookup' && (
          <>
            <h2>Insurance details</h2>
            <p className="staff-small staff-muted">
              Review extracted card text before saving. A card does not confirm current coverage.
            </p>
            <div className="staff-two-columns">
              {input('Insurance company', 'payer', {
                required: !['clinicforce_qr', 'ecw_lookup'].includes(intake.source || ''),
              })}
              {input('Member ID', 'member_id', {
                required: !['clinicforce_qr', 'ecw_lookup'].includes(intake.source || ''),
              })}
              {input('Group number', 'group_number')}
              {input('Policyholder name', 'subscriber_name', {
                required: !['self', 'unknown'].includes(fields.relationship),
              })}
              {!['self', 'unknown'].includes(fields.relationship) &&
                input('Policyholder date of birth', 'subscriber_birth_date', {
                  type: 'date',
                  help: 'Add if known. Some insurers need this for dependent coverage.',
                })}
            </div>
          </>
        )}
        <div className="staff-actions">
          <button
            type="button"
            className="staff-text-button"
            disabled={Boolean(busy)}
            onClick={() => navigate('record')}
          >
            <ArrowLeft size={18} />{' '}
            {intake.source === 'ecw_lookup' ? 'Back to patient' : 'View saved card'}
          </button>
          <button className="staff-primary" disabled={Boolean(busy)}>
            Find clinic record <ChevronRight size={18} />
          </button>
        </div>
      </form>
      {intake.source !== 'ecw_lookup' && (
        <section className="staff-panel">
          <h2>Saved card files</h2>
          {!intake.documents.length && (
            <p className="staff-muted">
              This intake started from a ClinicForce QR. Insurance files can be added in the
              document workflow after confirming the patient.
            </p>
          )}
          {intake.documents.map((doc) => (
            <div className="staff-document" key={doc.id}>
              <span>
                <FileText size={17} /> {doc.filename}
              </span>
              <button
                type="button"
                disabled={Boolean(busy)}
                onClick={() =>
                  void perform('download', () =>
                    downloadStaffDocument(intake.id, doc.id, doc.filename),
                  )
                }
              >
                Download
              </button>
            </div>
          ))}
        </section>
      )}
    </div>
  )
}
