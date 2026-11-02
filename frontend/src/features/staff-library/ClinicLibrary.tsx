import { useEffect, useState, type FormEvent, type RefObject } from 'react'
import {
  ArrowLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  Folder,
  RefreshCw,
  Upload,
} from 'lucide-react'
import { downloadStaffLibraryDocument } from '../../services/api/staff-downloads'
import {
  getStaffLibrary,
  getStaffMissingRates,
  retireStaffRateVersion,
  uploadStaffLibrary,
  type MissingRateSummary,
  type StaffLibraryDocument,
} from '../../services/api/staff-library'
import { type StaffSession } from '../../services/api/staff-contracts'

type FolderKind = 'contract' | 'fee_schedule' | 'self_pay'
const folders: Array<{ kind: FolderKind; title: string; description: string }> = [
  {
    kind: 'contract',
    title: 'Insurance contracts',
    description: 'Payer agreements and network terms',
  },
  {
    kind: 'fee_schedule',
    title: 'Fee schedules',
    description: 'Contracted rates and payment schedules',
  },
  { kind: 'self_pay', title: 'Self-pay rates', description: 'Clinic cash prices and packages' },
]
const dateLabel = (value: string) =>
  new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })

export default function ClinicLibrary({
  session,
  library,
  setLibrary,
  busy,
  perform,
  refresh,
  heading,
  setError,
  setNotice,
}: {
  session: StaffSession
  library: StaffLibraryDocument[] | null
  setLibrary: (
    value:
      StaffLibraryDocument[] | ((current: StaffLibraryDocument[] | null) => StaffLibraryDocument[]),
  ) => void
  busy: string
  perform: (name: string, action: () => Promise<void>) => Promise<void>
  refresh: () => void
  heading: RefObject<HTMLHeadingElement | null>
  setError: (message: string) => void
  setNotice: (message: string) => void
}) {
  const [folder, setFolder] = useState<FolderKind | null>(null)
  const [missingRates, setMissingRates] = useState<{
    practiceId: string
    summary: MissingRateSummary
  } | null>(null)
  const [missingRatesErrorFor, setMissingRatesErrorFor] = useState<string | null>(null)
  const currentMissingRates =
    missingRates?.practiceId === session.practice_id ? missingRates.summary : null
  const selected = folders.find((item) => item.kind === folder)
  const visible = library?.filter((doc) => doc.kind === folder) || []

  useEffect(() => {
    if (session.role !== 'admin') return
    let active = true
    void getStaffMissingRates()
      .then((value) => {
        if (active) setMissingRates({ practiceId: session.practice_id, summary: value })
      })
      .catch(() => {
        if (active) setMissingRatesErrorFor(session.practice_id)
      })
    return () => {
      active = false
    }
  }, [session.practice_id, session.role])

  function refreshAll() {
    refresh()
    if (session.role === 'admin')
      void getStaffMissingRates()
        .then((value) => {
          setMissingRates({ practiceId: session.practice_id, summary: value })
          setMissingRatesErrorFor(null)
        })
        .catch(() => setMissingRatesErrorFor(session.practice_id))
  }

  function retireRateVersion(id: string, payer: string) {
    if (
      !window.confirm(
        `Retire the active ${payer} rate version? Matching insured visits will not use these allowances until another version is active.`,
      )
    )
      return
    void perform('retire-rate', async () => {
      await retireStaffRateVersion(id)
      setLibrary((await getStaffLibrary()).documents)
      setNotice(
        `${payer} rate version retired. Review a current contract schedule before activating new allowances.`,
      )
    })
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!folder) return
    const form = event.currentTarget
    const data = new FormData(form)
    const from = String(data.get('effective_from') || '')
    const to = String(data.get('effective_to') || '')
    if (from && to && to < from) {
      setError('The effective end date must be on or after the start date.')
      return
    }
    data.set('kind', folder)
    void perform('library-upload', async () => {
      const document = await uploadStaffLibrary(data)
      setLibrary((current) => [document, ...(current || [])])
      form.reset()
      setNotice(
        `${selected?.title || 'Clinic document'} file saved. Processing status will appear here.`,
      )
    })
  }

  return (
    <div className="clinic-library">
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Practice references</div>
          <h1 tabIndex={-1} ref={heading}>
            Clinic library
          </h1>
          <p className="staff-muted">
            Contracts and prices for {session.practice_name}, organized by type.
          </p>
        </div>
        <button onClick={refreshAll} disabled={Boolean(busy)}>
          <RefreshCw size={18} /> Refresh
        </button>
      </header>
      {selected && (
        <nav className="clinic-library-breadcrumb" aria-label="Library location">
          <button type="button" onClick={() => setFolder(null)}>
            <ArrowLeft size={17} /> All folders
          </button>
          <span aria-hidden="true">/</span>
          <span aria-current="page">{selected.title}</span>
        </nav>
      )}
      {!selected ? (
        <>
          <section className="staff-panel">
            <div className="staff-section-heading">
              <h2>Folders</h2>
            </div>
            <p className="staff-muted">Choose a folder to view or add clinic documents.</p>
            <div className="clinic-library-folders">
              {folders.map((item) => (
                <button
                  type="button"
                  className="clinic-library-folder"
                  key={item.kind}
                  onClick={() => setFolder(item.kind)}
                >
                  <span className="clinic-library-folder-icon">
                    <Folder size={25} strokeWidth={1.8} aria-hidden="true" />
                  </span>
                  <span className="clinic-library-folder-copy">
                    <strong>{item.title}</strong>
                    <small>{item.description}</small>
                    <span>
                      {library === null
                        ? 'Loading files…'
                        : `${library.filter((doc) => doc.kind === item.kind).length} ${library.filter((doc) => doc.kind === item.kind).length === 1 ? 'file' : 'files'}`}
                    </span>
                  </span>
                  <ChevronRight size={20} aria-hidden="true" />
                </button>
              ))}
            </div>
          </section>
          {session.role === 'admin' && (
            <section className="staff-panel" aria-label="Recent unresolved CPT prices">
              <h2>Recent unresolved CPT prices</h2>
              <p className="staff-small staff-muted">
                These gaps come from saved clinic estimates. A missing price may reflect a missing
                schedule or an unverified payer, product, provider, date or location match. Upload
                an approved scoped source, then recheck the affected visits; saved results stay
                unchanged.
              </p>
              {missingRatesErrorFor === session.practice_id ? (
                <p>Pricing gaps could not be loaded. Select Refresh to try again.</p>
              ) : !currentMissingRates ? (
                <p>Loading pricing gaps…</p>
              ) : currentMissingRates.gaps.length === 0 ? (
                <p>
                  No unresolved CPT prices in {currentMissingRates.scanned_case_count} recent
                  estimate cases.
                </p>
              ) : (
                <>
                  <ul>
                    {currentMissingRates.gaps.slice(0, 12).map((item) => (
                      <li key={`${item.payer}:${item.code}:${item.kind}`}>
                        <strong>CPT {item.code}</strong> · {item.payer} ·{' '}
                        {item.kind === 'insured_allowed'
                          ? 'Insured allowed rate'
                          : 'Clinic cash price'}{' '}
                        unavailable in {item.estimate_count} saved{' '}
                        {item.estimate_count === 1 ? 'estimate' : 'estimates'}
                      </li>
                    ))}
                  </ul>
                  {currentMissingRates.group_count > 12 && (
                    <p>
                      {currentMissingRates.group_count - 12} more payer/CPT gaps are included in the
                      API inventory.
                    </p>
                  )}
                  {currentMissingRates.scan_limit_reached && (
                    <p>Showing gaps from the latest {currentMissingRates.scan_limit} cases only.</p>
                  )}
                  <div className="staff-actions">
                    <button type="button" onClick={() => setFolder('fee_schedule')}>
                      Review fee schedules
                    </button>
                    <button type="button" onClick={() => setFolder('self_pay')}>
                      Review self-pay rates
                    </button>
                  </div>
                </>
              )}
            </section>
          )}
        </>
      ) : (
        <>
          <section className="staff-panel">
            <div className="staff-section-heading">
              <h2>{selected.title}</h2>
              {library !== null && (
                <span className="staff-small staff-muted">
                  {visible.length} {visible.length === 1 ? 'file' : 'files'}
                </span>
              )}
            </div>
            <p className="staff-small staff-muted">
              Only files saved to {session.practice_name} appear here. Supported final prices are
              processed automatically; any missing information or conflicts appear with the file.
            </p>
            {library === null ? (
              <p className="staff-muted" role="status">
                {busy === 'library'
                  ? 'Loading clinic documents…'
                  : 'Clinic documents have not been loaded. Select Refresh to try again.'}
              </p>
            ) : visible.length === 0 ? (
              <div className="staff-empty">
                <Folder size={30} aria-hidden="true" />
                <h3>No files in this folder</h3>
                <p>
                  {session.role === 'admin'
                    ? `Add a file to ${selected.title.toLowerCase()} below.`
                    : 'Your clinic administrator can add files here.'}
                </p>
              </div>
            ) : (
              <div className="clinic-library-files">
                {visible.map((doc) => (
                  <article className="staff-document" key={doc.id}>
                    <span>
                      <FileText size={18} aria-hidden="true" />
                      <span>
                        <strong>{doc.label}</strong>
                        <small>
                          {doc.payer ? `${doc.payer} · ` : ''}
                          {doc.filename} · Added {dateLabel(doc.created_at)}
                        </small>
                        <small>
                          Effective {doc.effective_from || 'date not specified'}
                          {doc.effective_to ? ` to ${doc.effective_to}` : ''}
                        </small>
                        {doc.extraction_summary && (
                          <small>
                            {doc.extraction_summary.status === 'read'
                              ? `${doc.extraction_summary.page_count} pages extracted`
                              : doc.extraction_summary.status === 'reading'
                                ? 'Processing document…'
                                : doc.extraction_summary.status === 'needs_retry'
                                  ? 'Processing interrupted · automatic retry pending'
                                  : `${doc.extraction_summary.status.replaceAll('_', ' ')} · ${doc.extraction_summary.usable_rules || 0} usable rules`}
                          </small>
                        )}
                        {doc.extraction_summary?.issues?.map((issue, index) => (
                          <small key={index}>
                            {issue.locator ? `${issue.locator}: ` : ''}
                            {issue.reason}
                          </small>
                        ))}
                        {doc.estimator_uses?.length ? (
                          doc.estimator_uses.map((use) => (
                            <small key={use.id}>
                              <strong>
                                {use.status === 'active'
                                  ? 'Active in estimator'
                                  : use.status === 'draft'
                                    ? 'Rates awaiting activation'
                                    : use.status === 'retired'
                                      ? 'Retired rate version'
                                      : 'Previous rate version'}{' '}
                                ·{' '}
                                {use.type === 'cash_rates'
                                  ? 'Clinic self-pay prices'
                                  : `${use.payer} contract allowances`}
                              </strong>
                              Effective {use.effective_from}
                              {use.effective_to ? ` to ${use.effective_to}` : ''}
                              {use.note && <span> · {use.note}</span>}
                            </small>
                          ))
                        ) : (
                          <small>
                            Saved reference
                            {doc.kind === 'contract'
                              ? ' · No active CPT allowance linked to this agreement. Review its processing status for dependencies.'
                              : ''}
                          </small>
                        )}
                      </span>
                    </span>
                    <div className="staff-inline-actions">
                      <a
                        className="staff-mail-link"
                        href={`/api/staff/library/${encodeURIComponent(doc.id)}/document?preview=true`}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label={`Open ${doc.label} in a new tab`}
                      >
                        <ExternalLink size={16} aria-hidden="true" /> Open
                      </a>
                      <a
                        className="staff-mail-link"
                        href={`/api/staff/library/${encodeURIComponent(doc.id)}/text`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Read extracted text
                      </a>
                      {session.role === 'admin' &&
                        doc.estimator_uses
                          ?.filter(
                            (use) => use.type === 'contract_rates' && use.status === 'active',
                          )
                          .map((use) => (
                            <button
                              key={use.id}
                              disabled={Boolean(busy)}
                              onClick={() => retireRateVersion(use.id, use.payer || 'contract')}
                            >
                              Retire outdated rates
                            </button>
                          ))}
                      <button
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void perform('download', () =>
                            downloadStaffLibraryDocument(doc.id, doc.filename),
                          )
                        }
                      >
                        Download
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
          {session.role === 'admin' && (
            <form className="staff-panel" onSubmit={upload}>
              <h2>Add to {selected.title.toLowerCase()}</h2>
              <div className="staff-two-columns">
                <label className="staff-field">
                  <span>Label *</span>
                  <input
                    name="label"
                    required
                    placeholder={
                      folder === 'contract'
                        ? '2026 payer agreement'
                        : folder === 'fee_schedule'
                          ? '2026 office fee schedule'
                          : '2026 clinic cash rates'
                    }
                    disabled={Boolean(busy)}
                  />
                </label>
                <label className="staff-field">
                  <span>Payer, if applicable</span>
                  <input name="payer" disabled={Boolean(busy)} />
                </label>
                <label className="staff-field">
                  <span>File *</span>
                  <input
                    type="file"
                    name="file"
                    accept=".csv,.pdf,.docx,.xlsx,.jpg,.jpeg,.png"
                    required
                    disabled={Boolean(busy)}
                  />
                  <small>PDF, Word, Excel, CSV, JPG or PNG · Up to 15 MB.</small>
                </label>
                <div />
                <label className="staff-field">
                  <span>Effective from</span>
                  <input name="effective_from" type="date" disabled={Boolean(busy)} />
                </label>
                <label className="staff-field">
                  <span>Effective to</span>
                  <input name="effective_to" type="date" disabled={Boolean(busy)} />
                </label>
              </div>
              <button className="staff-primary" disabled={Boolean(busy)}>
                <Upload size={18} /> Save to {selected.title.toLowerCase()}
              </button>
            </form>
          )}
        </>
      )}
    </div>
  )
}
