import { CheckCircle2 } from 'lucide-react'

export default function ExternalImportComplete({ onImported }: { onImported: () => void }) {
  return (
    <section className="narrow-panel animate-enter" aria-labelledby="external-imported-heading">
      <div className="panel status-panel">
        <CheckCircle2 aria-hidden="true" className="external-success-icon" />
        <p className="eyebrow">External records saved</p>
        <h1 id="external-imported-heading">Your reviewed records were added.</h1>
        <p className="lead-small">
          They are marked as external Smart Health Link data for reconciliation in ClinicForce.
        </p>
        <button className="button button-primary" type="button" onClick={onImported}>
          Return to ClinicForce
        </button>
      </div>
    </section>
  )
}
