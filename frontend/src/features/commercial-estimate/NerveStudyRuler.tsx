import type { VisitCode } from '../../services/api/commercial-estimator'
import { clinicalName } from './service-code'

export function NerveStudyRuler({
  selectedNerve,
  studyCount,
  busy,
  onCountChange,
}: {
  selectedNerve?: VisitCode
  studyCount: number
  busy: boolean
  onCountChange: (count: number) => void
}) {
  return (
    <div className="nerve-study-ruler">
      <div className="nerve-study-ruler-heading">
        <label htmlFor="nerve-study-count">Number of nerve conduction studies</label>
        <strong>
          {selectedNerve
            ? `${clinicalName(selectedNerve)} · CPT ${selectedNerve.code}`
            : 'No level selected'}
        </strong>
      </div>
      <input
        id="nerve-study-count"
        type="range"
        min="0"
        max="13"
        step="1"
        value={studyCount}
        disabled={busy}
        aria-valuetext={
          studyCount
            ? `${studyCount === 13 ? '13 or more' : studyCount} ${studyCount === 1 ? 'study' : 'studies'}, ${selectedNerve ? clinicalName(selectedNerve) : 'no level selected'}`
            : 'No nerve study selected'
        }
        onChange={(event) => onCountChange(Number(event.target.value))}
      />
      <div className="nerve-study-ruler-marks" aria-hidden="true">
        {Array.from({ length: 14 }, (_, count) => (
          <span key={count} className={studyCount === count && selectedNerve ? 'is-active' : ''}>
            {count === 0 ? '–' : count === 13 ? '13+' : count}
          </span>
        ))}
      </div>
      <p className="visit-muted">
        Drag the handle to a study count. The left end clears the nerve study. Changes save
        automatically.
      </p>
    </div>
  )
}
