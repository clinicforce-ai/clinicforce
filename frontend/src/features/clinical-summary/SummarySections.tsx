import { Hospital, Pill, type LucideIcon } from 'lucide-react'
import {
  type CareEvent,
  type ClinicalItem,
  type ClinicalMeasure,
  type Medication,
} from '../../services/api/clinical-models'
import { SourceNote } from './SourceNote'

export function MeasureTable({
  rows,
  title,
  testLabel,
  icon: Icon,
}: {
  rows: ClinicalMeasure[]
  title: string
  testLabel: string
  icon: LucideIcon
}) {
  if (!rows.length) return null
  const hasSources = rows.some((row) => Boolean(row.citation))
  const hasUnit = rows.some((row) => row.unit)
  const hasDate = rows.some((row) => row.date)
  const hasDetails = rows.some((row) => row.details)
  return (
    <section className="summary-section">
      <div className="summary-section-title">
        <span>
          <Icon aria-hidden="true" />
        </span>
        <h2>{title}</h2>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{testLabel}</th>
              <th>Value</th>
              {hasUnit && <th>Unit</th>}
              {hasDate && <th>Date</th>}
              {hasDetails && <th>Notes</th>}
              {hasSources && <th>Original source</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.name}-${index}`}>
                <td>{row.name}</td>
                <td>{row.value}</td>
                {hasUnit && <td>{row.unit ?? '—'}</td>}
                {hasDate && <td>{row.date ?? '—'}</td>}
                {hasDetails && <td>{row.details ?? '—'}</td>}
                {hasSources && (
                  <td>
                    <SourceNote citation={row.citation} />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function MedicationTable({ rows }: { rows: Medication[] }) {
  if (!rows.length) return null
  const hasSources = rows.some((row) => Boolean(row.citation))
  const hasNotes = rows.some((row) => Boolean(row.details))
  return (
    <section className="summary-section">
      <div className="summary-section-title">
        <span>
          <Pill aria-hidden="true" />
        </span>
        <h2>Medications</h2>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Medication</th>
              <th>Dosage</th>
              {hasNotes && <th>Notes</th>}
              {hasSources && <th>Original source</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.name}-${index}`}>
                <td>{row.name}</td>
                <td>{row.dosage}</td>
                {hasNotes && <td>{row.details ?? '—'}</td>}
                {hasSources && (
                  <td>
                    <SourceNote citation={row.citation} />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function ClinicalItemList({
  rows,
  title,
  icon: Icon,
}: {
  rows: ClinicalItem[]
  title: string
  icon: LucideIcon
}) {
  if (!rows.length) return null
  return (
    <section className="summary-section">
      <div className="summary-section-title">
        <span>
          <Icon aria-hidden="true" />
        </span>
        <h2>{title}</h2>
      </div>
      <ul className="summary-list">
        {rows.map((row, index) => (
          <li key={`${row.name}-${index}`}>
            <strong>{row.name}</strong>
            {row.details && <p>{row.details}</p>}
            <SourceNote citation={row.citation} />
          </li>
        ))}
      </ul>
    </section>
  )
}

export function CareEvents({ rows }: { rows: CareEvent[] }) {
  if (!rows.length) return null
  const hasSources = rows.some((row) => Boolean(row.citation))
  const hasDate = rows.some((row) => Boolean(row.date))
  const hasDetails = rows.some((row) => Boolean(row.details))
  return (
    <section className="summary-section">
      <div className="summary-section-title">
        <span>
          <Hospital aria-hidden="true" />
        </span>
        <h2>Care visits</h2>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Facility</th>
              {hasDate && <th>Date</th>}
              {hasDetails && <th>Details</th>}
              {hasSources && <th>Original source</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.facility}-${index}`}>
                <td>{row.facility}</td>
                {hasDate && <td>{row.date ?? '—'}</td>}
                {hasDetails && <td>{row.details ?? '—'}</td>}
                {hasSources && (
                  <td>
                    <SourceNote citation={row.citation} />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
