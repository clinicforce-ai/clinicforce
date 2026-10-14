import { useState } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  FileText,
  LoaderCircle,
  RefreshCw,
  UserRound,
} from 'lucide-react'
import { type EcwRecord } from '../../services/api/patient-provider-types'
import { dateLabel } from './display-text'
import { ecwImportableResourceTypes } from './record-meta'
import { carePlanNotes, healowDetail, reviewResources } from './healow-review'
import './healow-review.css'

const sections = [
  {
    label: 'Conditions & diagnoses',
    types: ['Condition'],
    help: 'Practice-recorded diagnoses. A condition may appear on both the problem list and a visit record.',
  },
  {
    label: 'Visits & appointments',
    types: ['Encounter'],
    help: 'Visit history and scheduled appointments, including cancellations.',
  },
  {
    label: 'Results & measurements',
    types: ['Observation', 'DiagnosticReport'],
    help: 'Test results, vital signs, and other measurements returned by the practice.',
  },
  {
    label: 'Medications',
    types: ['MedicationRequest', 'MedicationDispense'],
    help: 'Prescriptions and pharmacy dispensing records; these may include past medications.',
  },
  {
    label: 'Allergies',
    types: ['AllergyIntolerance'],
    help: 'Allergies and intolerances recorded by the practice.',
  },
  {
    label: 'Orders & referrals',
    types: ['ServiceRequest'],
    help: 'Requests for tests, procedures, or referrals. An order is not a completed test result.',
  },
  {
    label: 'Insurance',
    types: ['Coverage'],
    help: 'Coverage recorded in the chart. This does not verify current benefits or payment.',
  },
  {
    label: 'Care plans & care team',
    types: ['CarePlan', 'CareTeam', 'Goal'],
    help: 'Plans, goals, and active care-team members returned by the practice.',
  },
  {
    label: 'Procedures',
    types: ['Procedure'],
    help: 'Documented procedures and their recorded status.',
  },
  { label: 'Devices', types: ['Device'], help: 'Medical devices recorded by the practice.' },
  {
    label: 'Immunizations',
    types: ['Immunization'],
    help: 'Vaccination records returned by the practice.',
  },
  {
    label: 'Documents',
    types: ['DocumentReference'],
    help: 'Document titles and dates only. Document contents and downloadable PDFs are not included in this connection.',
  },
]
const statusText: Record<string, string> = {
  empty: 'No records returned',
  forbidden: 'Access was not granted',
  unavailable: 'Not available from this practice',
  partial: 'Some records could not load',
  truncated: 'More records may be available',
  error: 'Could not load this category',
}
const familyNames: Record<string, string> = {
  ...Object.fromEntries(sections.flatMap((s) => s.types.map((t) => [t, s.label]))),
  CarePlan: 'Care plans',
  CareTeam: 'Care team',
  Goal: 'Health goals',
  DiagnosticReport: 'Diagnostic reports',
  Observation: 'Results & measurements',
  MedicationRequest: 'Prescriptions',
  MedicationDispense: 'Pharmacy records',
}
export function HealowRecordReview({
  record,
  busy,
  onRetry,
  onDisconnect,
  onImport,
}: {
  record: EcwRecord
  busy: boolean
  onRetry: () => void
  onDisconnect: () => void
  onImport: () => void
}) {
  const [confirmed, setConfirmed] = useState(false)
  const resources = reviewResources(record)
  const patient = resources.find((r) => r.resourceType === 'Patient')
  const identity = patient ? healowDetail(patient) : null
  const count = resources.filter((r) =>
    ecwImportableResourceTypes.has(String(r.resourceType)),
  ).length
  const issues = Object.entries(record.availability).filter(
    ([type, state]) => type !== 'Provenance' && !['ok', 'empty'].includes(state),
  )
  const denied = issues.some(([, state]) => state === 'forbidden')
  const populated = sections.filter((s) =>
    resources.some((r) => s.types.includes(String(r.resourceType))),
  )
  const unpopulated = sections.filter(
    (s) =>
      s.types.some((t) => t in record.availability) &&
      !resources.some((r) => s.types.includes(String(r.resourceType))),
  )
  const renderSection = (section: (typeof sections)[number]) => {
    const items = resources.filter((r) => section.types.includes(String(r.resourceType)))
    const failures = section.types.filter(
      (t) => record.availability[t] && !['ok', 'empty'].includes(record.availability[t]),
    )
    return (
      <details
        className="healow-category"
        key={section.label}
        open={
          items.length > 0 &&
          ['Conditions & diagnoses', 'Results & measurements'].includes(section.label)
        }
      >
        <summary>
          <span>{section.label}</span>
          <span className="healow-category-count">
            {items.length
              ? `${items.length} record${items.length === 1 ? '' : 's'}`
              : failures.length
                ? 'Could not fully load'
                : 'No records returned'}
            <ChevronDown aria-hidden="true" />
          </span>
        </summary>
        <div className="healow-category-body">
          <p className="healow-explanation">{section.help}</p>
          {failures.length > 0 && (
            <p className="healow-category-warning">
              {failures
                .map(
                  (t) =>
                    `${familyNames[t] ?? t}: ${statusText[record.availability[t]] ?? 'Could not load'}`,
                )
                .join(' · ')}
            </p>
          )}
          {items.length ? (
            <ul className="healow-records">
              {items.map((resource, index) => {
                const detail = healowDetail(resource)
                const notes = carePlanNotes(resource)
                return (
                  <li key={`${resource.resourceType}-${resource.id ?? index}`}>
                    <h4>{detail.title}</h4>
                    {detail.details.length > 0 && (
                      <ul className="healow-record-details">
                        {detail.details.map((line, i) => (
                          <li key={i}>{line}</li>
                        ))}
                      </ul>
                    )}
                    {notes && <p className="healow-plan-notes">{notes}</p>}
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="healow-explanation">
              {failures.length
                ? 'Records in this category could not be fully retrieved. Try loading them again.'
                : 'The practice returned no records in this category. This does not confirm that you have none.'}
            </p>
          )}
        </div>
      </details>
    )
  }
  return (
    <div className="healow-review">
      <header className="healow-review-heading">
        <div>
          <p className="section-kicker">Connected to your practice</p>
          <h3>Review your health records</h3>
          <p>
            Practice {record.source.practice_code} · Retrieved{' '}
            {dateLabel(record.source.retrieved_at)}
          </p>
        </div>
        <span className="healow-connected">
          <CheckCircle2 aria-hidden="true" /> Connected
        </span>
      </header>
      <div className="healow-patient">
        <UserRound aria-hidden="true" />
        <div>
          <p className="healow-eyebrow">Patient returned by Healow</p>
          <h3>{identity?.title ?? 'Patient identity could not be loaded'}</h3>
          <p>{identity?.details.join(' · ')}</p>
          <p className="healow-explanation">
            Check the name and birth date before saving. If this is not the right patient,
            disconnect and sign in to the correct practice account.
          </p>
        </div>
      </div>
      {issues.length > 0 && (
        <div className="healow-load-notice" role="status">
          <AlertCircle aria-hidden="true" />
          <div>
            <strong>
              {count
                ? 'Some categories need another try'
                : 'Your clinical records could not be loaded'}
            </strong>
            <p>
              {[...new Set(issues.map(([type]) => familyNames[type] ?? 'Patient identity'))].join(
                ', ',
              )}
              .
            </p>
            <p>
              {denied
                ? 'Healow denied access to at least one category. You can reconnect to review the sharing permissions.'
                : 'This does not mean the records are missing or that permission was denied.'}
            </p>
          </div>
        </div>
      )}
      <div className="healow-review-toolbar">
        <span>
          <FileText aria-hidden="true" /> {count} clinical record{count === 1 ? '' : 's'} to review
        </span>
        <button className="button button-secondary" type="button" disabled={busy} onClick={onRetry}>
          {busy ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw aria-hidden="true" />
          )}{' '}
          Reload records
        </button>
      </div>
      <p className="healow-explanation">
        These records reflect what the practice shared. Diagnoses, dates, and statuses are shown as
        recorded; they are not a new medical assessment.
      </p>
      <div className="healow-categories">{populated.map(renderSection)}</div>
      {unpopulated.length > 0 && (
        <details className="healow-other">
          <summary>Other categories ({unpopulated.length})</summary>
          <div className="healow-categories">{unpopulated.map(renderSection)}</div>
        </details>
      )}
      <footer className="healow-save">
        <label>
          <input
            type="checkbox"
            checked={confirmed}
            disabled={busy || !identity}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>I recognize this patient and have reviewed the records I am saving.</span>
        </label>
        <p>Nothing is saved until you choose Save. Saved records keep their Healow source label.</p>
        <div className="healow-save-actions">
          <button
            className="button button-secondary"
            type="button"
            disabled={busy}
            onClick={onDisconnect}
          >
            Disconnect without saving
          </button>
          <button
            className="button button-primary"
            type="button"
            disabled={busy || !confirmed || !identity || count === 0}
            onClick={onImport}
          >
            {busy && <LoaderCircle className="animate-spin" aria-hidden="true" />} Save {count}{' '}
            record{count === 1 ? '' : 's'}
          </button>
        </div>
      </footer>
    </div>
  )
}
