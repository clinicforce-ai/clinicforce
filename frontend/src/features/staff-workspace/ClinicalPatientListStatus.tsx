import { formatVisitDay } from './clinical-patient-groups'

export default function ClinicalPatientListStatus({
  progress,
  remainingDateChecks,
  onNextBatch,
  period,
  lookupDay,
  today,
  dateLookupLoading,
  dateLookupError,
  selectedLookupDay,
  selectedDayIsEmpty,
  onLookupDay,
  onLoadDate,
}: {
  progress: { done: number; total: number; failed: number }
  remainingDateChecks: number
  onNextBatch: () => void
  period: 'upcoming' | 'past' | null
  lookupDay: string
  today: string
  dateLookupLoading: boolean
  dateLookupError: string
  selectedLookupDay: string | null
  selectedDayIsEmpty: boolean
  onLookupDay: (day: string) => void
  onLoadDate: () => void
}) {
  return (
    <>
      {remainingDateChecks > 0 && progress.done === progress.total && (
        <button onClick={onNextBatch}>
          Check appointment dates for {Math.min(remainingDateChecks, 25)} more patients
        </button>
      )}
      {period && (
        <div className="staff-search clinical-search">
          <label className="staff-field">
            <span>
              {period === 'past' ? 'Previous appointment date' : 'Upcoming appointment date'}
            </span>
            <input
              type="date"
              value={lookupDay}
              max={period === 'past' ? today : undefined}
              min={period === 'upcoming' ? today : undefined}
              onChange={(event) => onLookupDay(event.target.value)}
            />
          </label>
          <button
            disabled={
              dateLookupLoading ||
              !lookupDay ||
              (period === 'past' ? lookupDay >= today : lookupDay <= today)
            }
            onClick={onLoadDate}
          >
            {dateLookupLoading ? 'Loading date…' : 'Load this date from eCW'}
          </button>
        </div>
      )}
      {dateLookupError && (
        <p className="staff-small staff-muted" role="alert">
          {dateLookupError}
        </p>
      )}
      {selectedLookupDay && selectedDayIsEmpty && period && (
        <p className="staff-small staff-muted" role="status">
          No linked clinic patients have a verified appointment on{' '}
          {formatVisitDay(selectedLookupDay)}.
        </p>
      )}
    </>
  )
}
