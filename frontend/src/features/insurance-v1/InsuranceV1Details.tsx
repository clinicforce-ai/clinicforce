import { Check, Files, Upload } from 'lucide-react'
import { type InsuranceCase } from '../../services/api/insurance'
import { timestamp } from './v1-display'

export function UploadTile({
  title,
  hint,
  accept,
  source,
  busy,
  onFile,
}: {
  title: string
  hint: string
  accept: string
  source?: NonNullable<InsuranceCase['documents']>[string]
  busy: boolean
  onFile: (file?: File) => void
}) {
  return (
    <label className={`evidence-upload${source ? ' complete' : ''}`}>
      <span className="upload-icon">
        {source ? <Check aria-hidden="true" /> : <Upload aria-hidden="true" />}
      </span>
      <span>
        <strong>{title}</strong>
        <small>{hint}</small>
        {source && (
          <>
            <small className="upload-file">Uploaded: {source.filename}</small>
            <SourceDates source={source} />
          </>
        )}
        {!busy && (
          <span className="v1-upload-action">
            {source ? 'Choose replacement file' : 'Choose file'}
          </span>
        )}
      </span>
      <input
        aria-label={title}
        type="file"
        accept={accept}
        disabled={busy}
        onChange={(event) => {
          onFile(event.target.files?.[0])
          event.currentTarget.value = ''
        }}
      />
    </label>
  )
}

export function SourceDates({
  source,
}: {
  source: NonNullable<InsuranceCase['documents']>[string]
}) {
  return (
    <span className="v1-source-dates">
      <small>Uploaded: {timestamp(source.observed_at)} (your local time)</small>
      {source.document_dates?.length ? (
        source.document_dates.map((date, index) => (
          <small key={index}>
            Document {date.label}: {date.value} · page {date.page}
          </small>
        ))
      ) : (
        <small>Document date: not explicitly found</small>
      )}
    </span>
  )
}

export function SourcePages({
  source,
}: {
  source: NonNullable<InsuranceCase['documents']>[string]
}) {
  return (
    <details className="source-excerpt">
      <summary>Review extracted text · {source.filename}</summary>
      {source.findings?.pages?.map((page) => (
        <section key={page.page}>
          <h4>Page {page.page}</h4>
          <pre>{page.text}</pre>
        </section>
      ))}
    </details>
  )
}

export function FactList({ facts }: { facts: InsuranceCase['facts'] }) {
  if (!facts.length) return null
  return (
    <section className="evidence-reasoning" aria-label="Uploaded-document facts">
      <header>
        <Files aria-hidden="true" />
        <div>
          <h3>Values read from your upload</h3>
          <p>Check the source text for each extracted value.</p>
        </div>
      </header>
      <dl className="insurance-facts">
        {facts.map((fact) => (
          <div key={`${fact.stage}-${fact.label}`}>
            <dt>{fact.label}</dt>
            <dd>
              {fact.value}
              <small>
                Source: {fact.citation.source_filename} · {fact.citation.reference}
              </small>
              {fact.citation.quote && (
                <details className="source-excerpt">
                  <summary>View extracted text</summary>
                  <blockquote>{fact.citation.quote}</blockquote>
                </details>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
