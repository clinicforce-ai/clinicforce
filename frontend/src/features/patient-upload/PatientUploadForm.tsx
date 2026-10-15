import type { FormEvent } from 'react'
import { Camera, ShieldCheck } from 'lucide-react'
import type { UploadClinic } from '../../services/api/insurance-upload'
import { getInsuranceUploadAccount } from '../../services/api/insurance-upload'
import type { PatientField } from './patient-upload-types'

interface Props {
  clinic: UploadClinic
  account: Awaited<ReturnType<typeof getInsuranceUploadAccount>>
  files: File[]
  busy: boolean
  cardMessage: string
  expiresAt: string
  field: (name: PatientField) => string
  edit: (name: PatientField, value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onChooseFiles: (files: File[]) => void
  onOpenCamera: () => void
}

export default function PatientUploadForm({
  clinic,
  account,
  files,
  busy,
  cardMessage,
  expiresAt,
  field,
  edit,
  onSubmit,
  onChooseFiles,
  onOpenCamera,
}: Props) {
  return (
    <form onSubmit={onSubmit}>
      <section className="staff-panel">
        <div className="staff-section-heading">
          <h2>Your insurance card</h2>
          <ShieldCheck size={22} aria-hidden="true" />
        </div>
        <p className="staff-muted">
          Add the front and back if available. Use a clear photo or a card PDF.
        </p>
        <button type="button" onClick={() => onOpenCamera()} disabled={busy}>
          <Camera size={18} /> Scan with camera
        </button>
        <label className="staff-field patient-card-input">
          <span>Or choose card files</span>
          <input
            type="file"
            accept="image/jpeg,image/png,application/pdf"
            multiple
            onChange={(event) => void onChooseFiles(Array.from(event.target.files || []))}
            disabled={busy}
          />
          <small>JPG, PNG, or PDF · Up to 2 files and 2 pages total · 10 MB per file.</small>
        </label>
        {files.length > 0 && (
          <ul className="patient-file-list">
            {files.map((file, index) => (
              <li key={`${file.name}-${index}`}>{file.name}</li>
            ))}
          </ul>
        )}
      </section>
      <section className="staff-panel">
        <h2>Patient receiving care</h2>
        <p className="staff-small staff-muted">
          We fill available details from your signed-in account and card. Review them carefully if
          the card shows a different policyholder.
        </p>
        {cardMessage && (
          <p className="staff-small staff-muted" role="status">
            {cardMessage}
          </p>
        )}
        <div className="staff-two-columns">
          <label className="staff-field">
            <span>First name *</span>
            <input
              name="given_name"
              value={field('given_name')}
              onChange={(event) => edit('given_name', event.target.value)}
              autoComplete="given-name"
              maxLength={100}
              required
              disabled={busy}
            />
          </label>
          <label className="staff-field">
            <span>Last name *</span>
            <input
              name="family_name"
              value={field('family_name')}
              onChange={(event) => edit('family_name', event.target.value)}
              autoComplete="family-name"
              maxLength={100}
              required
              disabled={busy}
            />
          </label>
          <label className="staff-field">
            <span>Date of birth *</span>
            <input
              name="birth_date"
              value={field('birth_date')}
              onChange={(event) => edit('birth_date', event.target.value)}
              type="date"
              autoComplete="bday"
              min="1900-01-01"
              max={new Date().toLocaleDateString('en-CA')}
              required
              disabled={busy}
            />
          </label>
          <label className="staff-field">
            <span>Member ID, if available</span>
            <input
              name="member_id"
              value={field('member_id')}
              onChange={(event) => edit('member_id', event.target.value)}
              autoComplete="off"
              maxLength={80}
              disabled={busy}
            />
          </label>
          <label className="staff-field">
            <span>Contact email for your clinic (optional)</span>
            <input
              name="email"
              value={field('email')}
              onChange={(event) => edit('email', event.target.value)}
              type="email"
              autoComplete="email"
              maxLength={254}
              disabled={busy}
            />
            <small>Your clinic can use this address to contact you about this submission.</small>
          </label>
        </div>
        {account.available && (
          <label className="staff-confirmation">
            <input type="checkbox" name="link_account" value="true" disabled={busy} />
            <span>
              Also connect this submission to my signed-in ClinicForce account: {account.given_name}{' '}
              {account.family_name}
              {account.birth_date ? ` · ${account.birth_date}` : ''}. The patient name and date of
              birth must match this account.
            </span>
          </label>
        )}
        <label className="staff-confirmation">
          <input type="checkbox" required disabled={busy} />
          <span>
            I am the patient or am authorized to submit on their behalf. I agree to send these
            details and card files to {clinic.name} for insurance review.
          </span>
        </label>
        <button className="staff-primary staff-full" disabled={busy || !files.length}>
          Send to clinic
        </button>
        {expiresAt && (
          <p className="staff-small staff-muted patient-expiry">
            Link expires {new Date(expiresAt).toLocaleString()}.
          </p>
        )}
      </section>
    </form>
  )
}
