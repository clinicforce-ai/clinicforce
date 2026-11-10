import { RefreshCw } from 'lucide-react'

export default function ClinicalPatientFilters({
  query,
  cardStatus,
  busy,
  onQuery,
  onCardStatus,
  onRefresh,
}: {
  query: string
  cardStatus: string
  busy: boolean
  onQuery: (value: string) => void
  onCardStatus: (value: string) => void
  onRefresh: () => void
}) {
  return (
    <div className="staff-search clinical-search">
      <label className="staff-field">
        <span>Find a clinical patient</span>
        <input
          type="search"
          placeholder="Name, ACCT, email, or date of birth"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
        />
      </label>
      <label className="staff-field">
        <span>Insurance card status</span>
        <select value={cardStatus} onChange={(event) => onCardStatus(event.target.value)}>
          <option value="">All card statuses</option>
          <option value="received">Card received</option>
          <option value="missing">Card missing</option>
        </select>
      </label>
      <button onClick={onRefresh} disabled={busy}>
        <RefreshCw size={17} /> Refresh patients
      </button>
    </div>
  )
}
