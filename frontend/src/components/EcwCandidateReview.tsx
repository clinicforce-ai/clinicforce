import { useEffect, useRef, useState } from 'react'
import type { EcwCandidate, EcwLookupDetails } from '../services/api/staff'

export default function EcwCandidateReview({ candidates, entered, busy, onConfirm, onCancel }: {
  candidates: EcwCandidate[]
  entered: EcwLookupDetails
  busy: boolean
  onConfirm: (candidate: EcwCandidate) => void
  onCancel: () => void
}) {
  const [selected, setSelected] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [])
  const candidate = candidates.find(row => row.id === selected)
  return <section className="staff-panel" aria-labelledby="ecw-review-heading">
    <h2 id="ecw-review-heading" ref={heading} tabIndex={-1}>Confirm the eCW patient</h2>
    <p role="status">Review the returned details before continuing. No patient has been linked yet.</p>
    <p>You entered: <strong>{entered.given_name} {entered.middle_initial} {entered.family_name}</strong> · DOB {entered.birth_date}</p>
    <form onSubmit={event => { event.preventDefault(); if (candidate && confirmed && !busy) onConfirm(candidate) }}>
      <fieldset disabled={busy} className="ecw-candidate-list">
        <legend>Select the intended patient</legend>
        {candidates.map(row => <label key={row.id} className="ecw-candidate-option">
          <input type="radio" name="ecw-candidate" value={row.id} checked={selected === row.id}
            onChange={() => { setSelected(row.id); setConfirmed(false) }} />
          <span><strong>{row.given_name} {row.family_name}</strong><br />
            DOB {row.birth_date}{row.ecw_account_number && <> · eCW account {row.ecw_account_number}</>}<br />
            <small>{row.match_kind === 'similar_first_name' ? 'Similar first name; same last name and date of birth.' : 'Matching name and date of birth; more than one record was found.'}</small>
          </span>
        </label>)}
      </fieldset>
      <label className="ecw-candidate-option">
        <input type="checkbox" checked={confirmed} disabled={!candidate || busy} onChange={event => setConfirmed(event.target.checked)} />
        <span>I verified the displayed details and confirm this is the intended patient.</span>
      </label>
      <div className="staff-actions">
        <button className="staff-primary" disabled={!candidate || !confirmed || busy}>{busy ? 'Verifying patient…' : 'Confirm patient and continue'}</button>
        <button type="button" disabled={busy} onClick={onCancel}>None of these — edit search</button>
      </div>
    </form>
  </section>
}
