import type { StaffWorkspaceModel } from './useStaffWorkspaceModel'
import { ArrowLeft, Camera, QrCode, Upload } from 'lucide-react'

export default function StaffCardIntakeScreen({
  model,
}: {
  model: StaffWorkspaceModel
  session: NonNullable<StaffWorkspaceModel['session']>
}) {
  const { onScanQr, files, setFiles, setCamera, busy, setError, navigate, checkFiles, saveCard } =
    model
  return (
    <div className="staff-narrow">
      <section className="staff-panel">
        <h2>Scan or upload insurance card</h2>
        <p className="staff-muted">
          We will read the card and fill the available fields. Confirm the patient identity before
          saving a patient record.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            saveCard(files)
          }}
        >
          <div className="staff-two-columns">
            <div>
              <Camera className="staff-feature-icon" size={30} />
              <h3>Scan the card</h3>
              <button
                className="staff-primary"
                type="button"
                disabled={Boolean(busy)}
                onClick={() => setCamera(true)}
              >
                Open card scanner
              </button>
            </div>
            <div>
              <Upload className="staff-feature-icon" size={30} />
              <h3>Upload a photo or PDF</h3>
              <label className="staff-field">
                <span>Card files</span>
                <input
                  type="file"
                  accept="image/jpeg,image/png,application/pdf"
                  multiple
                  onChange={(event) => {
                    const next = Array.from(event.target.files || [])
                    setFiles(next)
                    setError(next.length ? checkFiles(next) : '')
                  }}
                  disabled={Boolean(busy)}
                  aria-describedby="staff-upload-help"
                />
                <small id="staff-upload-help">
                  JPG, PNG, or PDF · Up to 2 files and 2 pages total · 10 MB per file.
                </small>
              </label>
              <button disabled={Boolean(busy) || !files.length}>Save card and continue</button>
            </div>
          </div>
        </form>
      </section>
      <section className="staff-panel staff-qr-option">
        <div>
          <h2>Patient has a ClinicForce QR?</h2>
          <p className="staff-muted">
            Open the insurance and records they choose to share with their passcode.
          </p>
        </div>
        <button onClick={onScanQr} disabled={Boolean(busy)}>
          <QrCode size={18} /> Scan ClinicForce QR
        </button>
      </section>
      <button
        className="staff-text-button"
        onClick={() => navigate('inbox')}
        disabled={Boolean(busy)}
      >
        <ArrowLeft size={18} /> Back to inbox
      </button>
    </div>
  )
}
