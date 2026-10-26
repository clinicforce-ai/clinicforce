import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { ClinicalPatient } from '../../services/api/staff-patients'

type Period = 'today' | 'upcoming' | 'past'
type DateGroup = [string, ClinicalPatient[]]

const PREVIEW_DATES = 3
const DATES_PER_PAGE = 5
const periods: Period[] = ['today', 'upcoming', 'past']
const labels: Record<Period, string> = {
  today: 'Today’s patients',
  upcoming: 'Upcoming patients',
  past: 'Past patients',
}
const emptyLabels: Record<Period, string> = {
  today: 'No patients today.',
  upcoming: 'No upcoming patients.',
  past: 'No past patients.',
}

export default function ClinicalPatientSections({
  groups,
  renderGroup,
}: {
  groups: Record<Period, DateGroup[]>
  renderGroup: (day: string, patients: ClinicalPatient[]) => ReactNode
}) {
  const [selected, setSelected] = useState<Period | null>(null)
  const [page, setPage] = useState(1)
  const selectedHeading = useRef<HTMLHeadingElement>(null)
  const returnTo = useRef<Period | null>(null)
  useEffect(() => {
    if (selected) selectedHeading.current?.focus()
    else if (returnTo.current) {
      document.getElementById(`clinical-${returnTo.current}-title`)?.focus()
      returnTo.current = null
    }
  }, [selected])

  if (!selected)
    return periods.map((period) => (
      <section key={period} aria-labelledby={`clinical-${period}-title`}>
        <h3 id={`clinical-${period}-title`} className="clinical-period-title" tabIndex={-1}>
          {labels[period]}
        </h3>
        {groups[period]
          .slice(0, PREVIEW_DATES)
          .map(([day, patients]) => renderGroup(day, patients))}
        {!groups[period].length && <p className="staff-small staff-muted">{emptyLabels[period]}</p>}
        <button
          className="staff-text-button clinical-see-more"
          aria-label={`See more ${labels[period].toLowerCase()}`}
          onClick={() => {
            setSelected(period)
            setPage(1)
          }}
        >
          See more
          <ChevronRight size={17} aria-hidden="true" />
        </button>
      </section>
    ))

  const dateGroups = groups[selected]
  const pageCount = Math.max(1, Math.ceil(dateGroups.length / DATES_PER_PAGE))
  const currentPage = Math.min(page, pageCount)
  const pageStart = (currentPage - 1) * DATES_PER_PAGE
  return (
    <section aria-labelledby="clinical-selected-period-title">
      <button
        className="staff-text-button clinical-back-to-periods"
        onClick={() => {
          returnTo.current = selected
          setSelected(null)
        }}
      >
        <ChevronLeft size={17} aria-hidden="true" /> All patient dates
      </button>
      <h3
        id="clinical-selected-period-title"
        className="clinical-period-title"
        tabIndex={-1}
        ref={selectedHeading}
      >
        {labels[selected]}
      </h3>
      {dateGroups
        .slice(pageStart, pageStart + DATES_PER_PAGE)
        .map(([day, patients]) => renderGroup(day, patients))}
      {!dateGroups.length && <p className="staff-small staff-muted">{emptyLabels[selected]}</p>}
      {pageCount > 1 && (
        <nav className="clinical-pagination" aria-label={`${labels[selected]} date pages`}>
          <span className="staff-small staff-muted" role="status">
            Showing {pageStart + 1}–{Math.min(pageStart + DATES_PER_PAGE, dateGroups.length)} of{' '}
            {dateGroups.length} date groups
          </span>
          <div className="clinical-page-buttons">
            <button
              aria-label="Previous visit date page"
              disabled={currentPage === 1}
              onClick={() => setPage(currentPage - 1)}
            >
              <ChevronLeft size={18} aria-hidden="true" />
            </button>
            {Array.from({ length: pageCount }, (_, index) => index + 1).map((number) => (
              <button
                key={number}
                aria-label={`Visit date page ${number}`}
                aria-current={number === currentPage ? 'page' : undefined}
                onClick={() => setPage(number)}
              >
                {number}
              </button>
            ))}
            <button
              aria-label="Next visit date page"
              disabled={currentPage === pageCount}
              onClick={() => setPage(currentPage + 1)}
            >
              <ChevronRight size={18} aria-hidden="true" />
            </button>
          </div>
        </nav>
      )}
    </section>
  )
}
