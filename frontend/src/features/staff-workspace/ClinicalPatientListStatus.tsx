import { formatVisitDay } from './clinical-patient-groups'

export default function ClinicalPatientListStatus({
  todayReadFinished,
  todayReadFailed,
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
  todayReadFinished: boolean
  todayReadFailed: boolean
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
      <p className="staff-small staff-muted clinical-visit-note">
        Each appointment date links to the same patient profile. Today’s schedule is checked first;
        choose an exact date to load it directly from eCW.
      </p>
      {!todayReadFinished && (
        <p className="staff-small staff-muted" role="status">
          Checking today’s appointments first…
        </p>
      )}
      {todayReadFailed && (
        <p className="staff-small staff-muted" role="alert">
          Today’s schedule could not be checked together. Patient dates will appear as individual
          histories load.
        </p>
      )}
      {progress.total > 0 && (
        <p className="staff-small staff-muted" role="status">
          {progress.done < progress.total
            ? `Checking eCW visits: ${progress.done} of ${progress.total} patient histories loaded… Dates may change when the refresh finishes.`
            : `eCW visit checks finished for ${progress.total} patients.`}
        </p>
      )}
      {progress.failed > 0 && progress.done === progress.total && (
        <p className="staff-small staff-muted" role="alert">
          {progress.failed} patient {progress.failed === 1 ? 'history was' : 'histories were'}{' '}
          unavailable. Some dates may be missing; select Refresh patients to retry.
        </p>
      )}
      {remainingDateChecks > 0 && progress.done === progress.total && (
        <button onClick={onNextBatch}>
          Check appointment dates for {Math.min(remainingDateChecks, 200)} more patients
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
