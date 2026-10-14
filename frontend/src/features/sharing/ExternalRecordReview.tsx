import { ArrowLeft, FileCheck2, LoaderCircle, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { PatientSession } from '../../services/api/patient-session-types'
import type { ExternalShlReview } from '../../services/external-shl'

interface Props {
  review: ExternalShlReview
  session: PatientSession | null
  error: string
  busy: boolean
  onCancel: () => void
  onSave: () => void
}
export default function ExternalRecordReview({
  review,
  session,
  error,
  busy,
  onCancel,
  onSave,
}: Props) {
  return (
    <section className="narrow-panel animate-enter" aria-labelledby="external-review-heading">
      <button className="back-control" type="button" onClick={onCancel}>
        <ArrowLeft aria-hidden="true" className="size-4" /> Cancel import
      </button>
      <div className="panel external-review-panel">
        <p className="eyebrow">
          <FileCheck2 aria-hidden="true" className="size-4" /> Review before saving
        </p>
        <h1 id="external-review-heading">Nothing has been saved yet.</h1>
        <p className="lead-small">
          From <strong>{review.sourceHost}</strong>
          {review.label ? ` · ${review.label}` : ''}
        </p>
        {review.apiAccessOffers.length > 0 && (
          <div className="external-warning" role="status">
            <ShieldAlert aria-hidden="true" />
            <p>
              This link offers live SMART API access to{' '}
              <strong>{review.apiAccessOffers.join(', ')}</strong>. ClinicForce will not connect or
              save anything from that offer in this version.
            </p>
          </div>
        )}
        <div className="external-review-block">
          <h2>Records ready for review</h2>
          {review.resourceCounts.length ? (
            <ul>
              {review.resourceCounts.map(({ type, count }) => (
                <li key={type}>
                  <strong>{count}</strong> {type}
                </li>
              ))}
            </ul>
          ) : (
            <p>No importable FHIR R4 records were supplied.</p>
          )}
        </div>
        <div className="external-review-block">
          <h2>Source and trust</h2>
          <ul>
            {review.provenance.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div className="external-review-block">
          <h2>Potential conflicts</h2>
          <ul>
            {review.conflicts.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          {!review.conflicts.length && <p>No matching patient-entered record labels were found.</p>}
        </div>
        {!session && (
          <div className="external-warning">
            <ShieldAlert aria-hidden="true" />
            <p>
              Sign in to the ClinicForce patient workspace that should receive these records before
              you can save them.
            </p>
          </div>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="external-actions">
          <button className="button button-secondary" type="button" onClick={onCancel}>
            Discard
          </button>
          <button
            className="button button-primary"
            type="button"
            onClick={onSave}
            disabled={!review.canImport || busy}
          >
            {busy ? (
              <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            ) : (
              <ShieldCheck aria-hidden="true" className="size-4" />
            )}
            Save reviewed records
          </button>
        </div>
      </div>
    </section>
  )
}
