export function UnavailableUpload() {
  return (
    <section className="staff-panel">
      <h2>We could not open this upload</h2>
      <p>
        Try again if your connection was interrupted. For an expired, used, or revoked link, contact
        your clinic for a replacement.
      </p>
      <button onClick={() => window.location.reload()}>Try again</button>
    </section>
  )
}
