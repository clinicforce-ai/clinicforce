import type { PortalState } from '../portal-state'
import type { createPortalAccountActions } from '../portal-account-actions'
import type { createPortalDocumentActions } from '../portal-document-actions'
import {
  Cloud,
  Download,
  Eye,
  FileText,
  FolderOpen,
  LockKeyhole,
  LoaderCircle,
  ShieldCheck,
  Trash2,
  Upload,
} from 'lucide-react'
import { dateLabel } from '../display-text'
import { ProviderLogo } from '../ProviderLogo'

export function PortalDocuments({
  state,
  account,
  document,
}: {
  state: PortalState
  account: ReturnType<typeof createPortalAccountActions>
  document: ReturnType<typeof createPortalDocumentActions>
}) {
  const { session, files, workspaceTab, selectedDocumentIds, busy, fileInput } = state
  const { chooseFiles, upload, connectGoogleDrive, selectStorage } = account
  const {
    openDocument,
    importDriveChanges,
    deleteDocument,
    toggleDocumentSelection,
    toggleAllDocumentSelection,
    downloadSelectedDocuments,
    deleteSelectedDocuments,
  } = document
  if (!session) return null
  return (
    <section
      className="section-card"
      data-workspace-panel
      aria-labelledby="reports-heading"
      hidden={workspaceTab !== 'records'}
    >
      <div className="section-heading">
        <div>
          <p className="section-kicker">Source documents</p>
          <h2 id="reports-heading">PDF reports</h2>
        </div>
        <FileText aria-hidden="true" className="section-icon" />
      </div>
      <div className="storage-choice" aria-label="Choose where new reports are stored">
        <div>
          <strong>
            {session.storage_provider === 'google_drive'
              ? 'Your Google Drive is selected'
              : 'ClinicForce secure storage is selected'}
          </strong>
          <p>
            {session.storage_provider === 'google_drive'
              ? 'Drive contains encrypted ClinicForce files. Open reports here to read them.'
              : 'New PDFs stay in ClinicForce encrypted document storage.'}
          </p>
        </div>
        <div className="storage-actions">
          <button
            className="button button-secondary"
            type="button"
            disabled={busy === 'drive' || session.storage_provider === 's3'}
            onClick={() => void selectStorage('s3')}
          >
            Use ClinicForce storage
          </button>
          <button
            className="button button-secondary"
            type="button"
            disabled={busy === 'drive' || session.storage_provider === 'google_drive'}
            onClick={() => void selectStorage('google_drive')}
          >
            <ProviderLogo provider="google" />{' '}
            {session.drive_connected ? 'Use my Drive' : 'Connect Google Drive'}
          </button>
        </div>
      </div>
      {session.storage_provider === 'google_drive' && session.drive_access_upgrade_required && (
        <div className="feedback feedback-error" role="status">
          <Cloud aria-hidden="true" />
          <span>
            Reconnect Google Drive to enable secure folder import. Your existing reports remain
            available.
          </span>
          <button
            className="button button-secondary"
            type="button"
            disabled={busy === 'drive'}
            onClick={() => void connectGoogleDrive()}
          >
            Reconnect Drive
          </button>
        </div>
      )}
      {session.storage_provider === 'google_drive' &&
        session.drive_pending_import_count > 0 &&
        !session.drive_access_upgrade_required && (
          <div className="feedback feedback-success" role="status">
            <ShieldCheck aria-hidden="true" />
            <span>
              {session.drive_pending_import_count} Drive PDF
              {session.drive_pending_import_count === 1 ? '' : 's'} need secure import. Plaintext
              originals will be replaced with encrypted ClinicForce files.
            </span>
            <button
              className="button button-secondary"
              type="button"
              disabled={busy === 'import'}
              onClick={() => void importDriveChanges()}
            >
              {busy === 'import' ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <LockKeyhole aria-hidden="true" className="size-4" />
              )}{' '}
              Import securely
            </button>
          </div>
        )}
      {session.documents.length ? (
        <>
          <div className="document-selection-toolbar">
            <label className="select-all-control">
              <input
                type="checkbox"
                checked={selectedDocumentIds.size === session.documents.length}
                onChange={toggleAllDocumentSelection}
                disabled={busy === 'bulk-download' || busy === 'bulk-delete'}
              />
              <span>Select all reports</span>
            </label>
            {selectedDocumentIds.size > 0 && (
              <div className="document-bulk-actions" role="status">
                <span>{selectedDocumentIds.size} selected</span>
                <button
                  className="button button-secondary"
                  type="button"
                  onClick={() => void downloadSelectedDocuments()}
                  disabled={busy === 'bulk-download' || busy === 'bulk-delete'}
                >
                  {busy === 'bulk-download' ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Download className="size-4" aria-hidden="true" />
                  )}{' '}
                  Download ZIP
                </button>
                <button
                  className="button button-danger"
                  type="button"
                  onClick={() => void deleteSelectedDocuments()}
                  disabled={busy === 'bulk-download' || busy === 'bulk-delete'}
                >
                  {busy === 'bulk-delete' ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Trash2 className="size-4" aria-hidden="true" />
                  )}{' '}
                  Delete
                </button>
              </div>
            )}
          </div>
          <ul className="item-list">
            {session.documents.map((document) => (
              <li key={document.id}>
                <label className="document-select" aria-label={`Select ${document.filename}`}>
                  <input
                    type="checkbox"
                    checked={selectedDocumentIds.has(document.id)}
                    onChange={() => toggleDocumentSelection(document.id)}
                    disabled={busy === 'bulk-download' || busy === 'bulk-delete'}
                  />
                </label>
                <div className="file-mark">
                  <FileText aria-hidden="true" />
                </div>
                <div className="min-w-0 flex-1">
                  <button
                    type="button"
                    className="document-name"
                    onClick={() => void openDocument(document)}
                    disabled={busy === 'download'}
                  >
                    {document.filename}
                  </button>
                  <p>
                    {dateLabel(document.uploaded_at)} · {document.resource_count} extracted item
                    {document.resource_count === 1 ? '' : 's'}
                  </p>
                </div>
                <button
                  className="item-action"
                  type="button"
                  onClick={() => void openDocument(document)}
                  disabled={busy === 'download'}
                  aria-label={`Open ${document.filename}`}
                >
                  {busy === 'download' ? (
                    <LoaderCircle className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Eye aria-hidden="true" />
                  )}
                </button>
                <button
                  className="item-action"
                  type="button"
                  onClick={() => void deleteDocument(document)}
                  disabled={busy === 'delete'}
                  aria-label={`Delete ${document.filename}`}
                >
                  {busy === 'delete' ? (
                    <LoaderCircle className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Trash2 aria-hidden="true" />
                  )}
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <div className="empty-block">
          <FolderOpen aria-hidden="true" />
          <p>
            <strong>No reports yet</strong>
            <br />
            Upload a PDF to keep it with this workspace.
          </p>
        </div>
      )}
      <div className="upload-zone">
        <input
          ref={fileInput}
          id="medical-pdf"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          onChange={(event) => chooseFiles(Array.from(event.target.files ?? []))}
        />
        <label htmlFor="medical-pdf">
          <Upload aria-hidden="true" />
          <span>
            <strong>
              {session.storage_provider === 'google_drive'
                ? 'Upload to my Drive'
                : 'Choose PDF reports'}
            </strong>
            <small>PDF only · up to 15 MB each</small>
          </span>
        </label>
        {files.length > 0 && (
          <div className="selected-files">
            <span>
              {files.length} report{files.length === 1 ? '' : 's'} ready to add
            </span>
            <button
              className="button button-secondary"
              type="button"
              disabled={busy === 'upload'}
              onClick={() => void upload()}
            >
              {busy === 'upload' ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Upload aria-hidden="true" className="size-4" />
              )}{' '}
              Add reports
            </button>
          </div>
        )}
      </div>
    </section>
  )
}
