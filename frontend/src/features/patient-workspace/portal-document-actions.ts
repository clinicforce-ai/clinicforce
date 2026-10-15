import {
  deletePatientDocument,
  deletePatientDocuments,
  downloadPatientDocument,
  downloadPatientDocumentsArchive,
  importGoogleDriveChanges,
} from '../../services/api/documents'
import { type PatientDocument } from '../../services/api/patient-document-types'
import { messageFrom } from './error-text'
import type { PortalState } from './portal-state'

export function createPortalDocumentActions(state: PortalState) {
  const {
    password,
    session,
    setSession,
    selectedDocumentIds,
    setSelectedDocumentIds,
    setBusy,
    setError,
    setNotice,
    clearFeedback,
  } = state
  const openDocument = async (document: PatientDocument) => {
    if (!session) return
    clearFeedback()
    setBusy('download')
    try {
      const url = URL.createObjectURL(
        await downloadPatientDocument(session.patient_id, password, document.id),
      )
      window.open(url, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to open this report.'))
    } finally {
      setBusy(null)
    }
  }

  const importDriveChanges = async () => {
    if (!session) return
    clearFeedback()
    setBusy('import')
    try {
      const workspace = await importGoogleDriveChanges(session.patient_id, password)
      setSession(workspace)
      setNotice(workspace.drive_sync_message ?? 'Drive changes were imported securely.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to import the detected Drive files.'))
    } finally {
      setBusy(null)
    }
  }

  const deleteDocument = async (document: PatientDocument) => {
    if (
      !session ||
      !window.confirm(
        `Delete ${document.filename}? This removes it from ClinicForce and its selected storage.`,
      )
    )
      return
    clearFeedback()
    setBusy('delete')
    try {
      const workspace = await deletePatientDocument(session.patient_id, password, document.id)
      setSession(workspace)
      setSelectedDocumentIds((current) => {
        const next = new Set(current)
        next.delete(document.id)
        return next
      })
      setNotice(workspace.drive_sync_message ?? 'Report deleted.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to delete this report.'))
    } finally {
      setBusy(null)
    }
  }

  const toggleDocumentSelection = (documentId: number) => {
    setSelectedDocumentIds((current) => {
      const next = new Set(current)
      if (next.has(documentId)) next.delete(documentId)
      else next.add(documentId)
      return next
    })
  }

  const toggleAllDocumentSelection = () => {
    if (!session) return
    setSelectedDocumentIds((current) =>
      current.size === session.documents.length
        ? new Set()
        : new Set(session.documents.map((document) => document.id)),
    )
  }

  const downloadSelectedDocuments = async () => {
    if (!session || selectedDocumentIds.size === 0) return
    clearFeedback()
    setBusy('bulk-download')
    try {
      const archive = await downloadPatientDocumentsArchive(
        session.patient_id,
        password,
        Array.from(selectedDocumentIds),
      )
      const url = URL.createObjectURL(archive)
      const linkElement = document.createElement('a')
      linkElement.href = url
      linkElement.download = 'clinicforce-reports.zip'
      document.body.appendChild(linkElement)
      linkElement.click()
      linkElement.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
      setNotice(
        `${selectedDocumentIds.size} selected report${selectedDocumentIds.size === 1 ? '' : 's'} downloaded as a ZIP file.`,
      )
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to download the selected reports.'))
    } finally {
      setBusy(null)
    }
  }

  const deleteSelectedDocuments = async () => {
    if (!session || selectedDocumentIds.size === 0) return
    const count = selectedDocumentIds.size
    if (
      !window.confirm(
        `Delete ${count} selected report${count === 1 ? '' : 's'}? This removes them from ClinicForce and their selected storage.`,
      )
    )
      return
    clearFeedback()
    setBusy('bulk-delete')
    try {
      const workspace = await deletePatientDocuments(
        session.patient_id,
        password,
        Array.from(selectedDocumentIds),
      )
      setSession(workspace)
      setSelectedDocumentIds(new Set())
      setNotice(workspace.drive_sync_message ?? 'Selected reports deleted.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to delete the selected reports.'))
    } finally {
      setBusy(null)
    }
  }

  return {
    openDocument,
    importDriveChanges,
    deleteDocument,
    toggleDocumentSelection,
    toggleAllDocumentSelection,
    downloadSelectedDocuments,
    deleteSelectedDocuments,
  }
}
