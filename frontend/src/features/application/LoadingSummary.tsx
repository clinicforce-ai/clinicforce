export function LoadingSummary() {
  return (
    <section className="narrow-panel" aria-live="polite">
      <div className="panel status-panel">
        <span className="spinner" aria-hidden="true" />
        <p className="eyebrow">Preparing the shared record</p>
        <h1>Decrypting the clinical summary</h1>
        <p className="lead-small">
          This can take a moment while ClinicForce safely prepares the information for review.
        </p>
      </div>
    </section>
  )
}
