import type { RequirementEvidence } from '../../services/api/commercial-estimator'

export default function RequirementContact({ evidence }: { evidence?: RequirementEvidence }) {
  if (!evidence?.call_script || !evidence.contact_source) return null
  return (
    <div className="service-requirement-contact">
      <p>
        Confirm with {evidence.contact_department || 'provider services'}:{' '}
        {evidence.contact_phone ? (
          <a href={`tel:${evidence.contact_phone.replace(/[^\d]/g, '')}`}>
            {evidence.contact_phone}
          </a>
        ) : (
          'use the number on the member card'
        )}
        . Check for a delegated administrator.
      </p>
      <a href={evidence.contact_source.url} target="_blank" rel="noopener noreferrer">
        Official contact source
      </a>
      <details>
        <summary>Call script</summary>
        <textarea
          aria-label="Copyable provider call script"
          readOnly
          value={evidence.call_script}
        />
      </details>
    </div>
  )
}
