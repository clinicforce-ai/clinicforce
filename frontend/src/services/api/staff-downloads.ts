import { ApiError, detailFrom } from './client'
import { type StaffWorkflowDocument } from './staff-contracts'

export const downloadStaffDocument = (intakeId: string, documentId: string, filename: string) =>
  downloadStaffFile(
    `/api/staff/intakes/${encodeURIComponent(intakeId)}/documents/${encodeURIComponent(documentId)}`,
    filename,
  )

export const downloadStaffLibraryDocument = (documentId: string, filename: string) =>
  downloadStaffFile(`/api/staff/library/${encodeURIComponent(documentId)}/document`, filename)

export const downloadStaffWorkflowDocument = (
  intakeId: string,
  document: StaffWorkflowDocument,
) => {
  if (
    !document.download_url.startsWith(
      `/api/staff/intakes/${encodeURIComponent(intakeId)}/workflow-documents/`,
    )
  )
    return Promise.reject(
      new Error('This document link is not part of the selected patient intake.'),
    )
  return downloadStaffFile(document.download_url, document.filename)
}

async function downloadStaffFile(path: string, filename: string) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    signal: AbortSignal.timeout(45_000),
  })
  if (!response.ok)
    throw new ApiError(
      response.status,
      detailFrom(await response.json().catch(() => null), 'Unable to download this document.'),
    )
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
