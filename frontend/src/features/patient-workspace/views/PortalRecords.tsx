import type { PortalState } from '../portal-state'
import type { createPortalAccountActions } from '../portal-account-actions'
import { ClipboardPlus, LoaderCircle, Plus } from 'lucide-react'
import { categoryMeta, recordMeta } from '../record-meta'
import { dateLabel } from '../display-text'
import { type RecordCategory } from '../../../services/api/patient-document-types'

export function PortalRecords({
  state,
  account,
}: {
  state: PortalState
  account: ReturnType<typeof createPortalAccountActions>
}) {
  const { session, category, setCategory, form, workspaceTab, busy, setError, setField } = state
  const { saveRecord } = account
  if (!session) return null
  const needsName = category !== 'hospital'
  const needsResult = category === 'lab' || category === 'vital'
  return (
    <section
      className="section-card"
      data-workspace-panel
      aria-labelledby="detail-heading"
      hidden={workspaceTab !== 'records'}
    >
      <div className="section-heading">
        <div>
          <p className="section-kicker">Structured details</p>
          <h2 id="detail-heading">Health details</h2>
        </div>
        <ClipboardPlus aria-hidden="true" className="section-icon" />
      </div>
      {session.records.length ? (
        <ul className="record-list">
          {session.records.map((record) => (
            <li key={record.id}>
              <span className="record-number">{recordMeta(record.category).icon}</span>
              <div>
                <strong>{record.label}</strong>
                <p>
                  {recordMeta(record.category).label} · added {dateLabel(record.created_at)}
                </p>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="empty-block compact">
          <ClipboardPlus aria-hidden="true" />
          <p>
            <strong>Add a health detail</strong>
            <br />
            Include care visits, lab results, medications, and more.
          </p>
        </div>
      )}
      <form className="record-form" onSubmit={saveRecord} noValidate>
        <fieldset>
          <legend>Add a detail</legend>
          <div className="category-picker">
            {(Object.keys(categoryMeta) as RecordCategory[]).map((key) => (
              <button
                className={category === key ? 'selected' : ''}
                type="button"
                key={key}
                onClick={() => {
                  setCategory(key)
                  setError('')
                }}
              >
                <span>{categoryMeta[key].icon}</span>
                {categoryMeta[key].label}
              </button>
            ))}
          </div>
        </fieldset>
        <p className="field-help mb-4">{categoryMeta[category].help}</p>
        <div className="form-grid">
          {category === 'hospital' && (
            <div className="field full">
              <label htmlFor="hospital">
                Hospital or clinic <em>Required</em>
              </label>
              <input
                id="hospital"
                value={form.hospital}
                onChange={(event) => setField('hospital', event.target.value)}
                placeholder="e.g. City General Hospital"
              />
            </div>
          )}
          {needsName && (
            <div className="field">
              <label htmlFor="record-name">
                {category === 'lab' ? 'Test name' : category === 'vital' ? 'Measurement' : 'Name'}{' '}
                <em>Required</em>
              </label>
              <input
                id="record-name"
                value={form.name}
                onChange={(event) => setField('name', event.target.value)}
                placeholder={
                  category === 'lab'
                    ? 'e.g. Hemoglobin A1c'
                    : category === 'vital'
                      ? 'e.g. Blood pressure'
                      : 'Enter a name'
                }
              />
            </div>
          )}
          {needsResult && (
            <div className="field">
              <label htmlFor="record-value">
                Result <em>Required</em>
              </label>
              <input
                id="record-value"
                value={form.value}
                onChange={(event) => setField('value', event.target.value)}
                placeholder="e.g. 5.8 or Negative"
              />
            </div>
          )}
          {needsResult && (
            <div className="field">
              <label htmlFor="record-unit">
                Unit <span>Optional</span>
              </label>
              <input
                id="record-unit"
                value={form.unit}
                onChange={(event) => setField('unit', event.target.value)}
                placeholder="e.g. %, mg/dL"
              />
            </div>
          )}
          {category === 'medication' && (
            <div className="field">
              <label htmlFor="dosage">
                Dosage <span>Optional</span>
              </label>
              <input
                id="dosage"
                value={form.dosage}
                onChange={(event) => setField('dosage', event.target.value)}
                placeholder="e.g. Take once daily"
              />
            </div>
          )}
          <div className="field">
            <label htmlFor="record-date">
              Date <span>Optional</span>
            </label>
            <input
              id="record-date"
              type="date"
              value={form.date}
              onChange={(event) => setField('date', event.target.value)}
            />
          </div>
          <div className="field full">
            <label htmlFor="details">
              Notes <span>Optional</span>
            </label>
            <textarea
              id="details"
              value={form.details}
              onChange={(event) => setField('details', event.target.value)}
              placeholder="Anything you want your clinician to know"
            />
          </div>
        </div>
        <button className="button button-secondary" type="submit" disabled={busy === 'record'}>
          {busy === 'record' ? (
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <Plus aria-hidden="true" className="size-4" />
          )}{' '}
          Add health detail
        </button>
      </form>
    </section>
  )
}
