/** Human-readable labels and display order for grouped Epic resource types. */
import { codedText } from './display-text'
import { clinicalResourceDetail } from './fhir-detail-clinical'
import { careResourceDetail } from './fhir-detail-care'
import { administrativeResourceDetail } from './fhir-detail-administrative'
import type { EpicResourceDetail } from './fhir-detail-types'

export const epicResourceGroupMeta: Record<
  string,
  { label: string; singular: string; order: number }
> = {
  Patient: { label: 'Patient demographics', singular: 'Patient', order: 0 },
  Condition: { label: 'Conditions & diagnoses', singular: 'Condition', order: 1 },
  AllergyIntolerance: { label: 'Allergies & intolerances', singular: 'Allergy', order: 2 },
  MedicationRequest: { label: 'Medications', singular: 'Medication', order: 3 },
  Immunization: { label: 'Immunizations', singular: 'Immunization', order: 4 },
  Observation: { label: 'Lab results & vitals', singular: 'Observation', order: 5 },
  DiagnosticReport: { label: 'Diagnostic reports', singular: 'Diagnostic report', order: 6 },
  Encounter: { label: 'Encounters & visits', singular: 'Encounter', order: 7 },
  CarePlan: { label: 'Care plans', singular: 'Care plan', order: 8 },
  CareTeam: { label: 'Care teams', singular: 'Care team', order: 9 },
  Coverage: { label: 'Insurance coverage', singular: 'Coverage', order: 10 },
  Goal: { label: 'Health goals', singular: 'Goal', order: 11 },
  Device: { label: 'Devices', singular: 'Device', order: 12 },
  DocumentReference: { label: 'Documents', singular: 'Document', order: 13 },
}

export function groupEpicResources(resources: Array<Record<string, unknown>>): Array<{
  type: string
  meta: (typeof epicResourceGroupMeta)[string]
  resources: Array<Record<string, unknown>>
}> {
  const groups: Record<string, Array<Record<string, unknown>>> = {}
  for (const resource of resources) {
    const type = typeof resource.resourceType === 'string' ? resource.resourceType : 'Unknown'
    ;(groups[type] ??= []).push(resource)
  }
  return Object.entries(groups)
    .map(([type, items]) => ({
      type,
      meta: epicResourceGroupMeta[type] ?? { label: type, singular: type, order: 99 },
      resources: items,
    }))
    .sort((a, b) => a.meta.order - b.meta.order)
}

/** Extract a structured summary object from any FHIR resource Epic returns. */
export function epicResourceDetail(resource: Record<string, unknown>): EpicResourceDetail {
  const resourceType =
    typeof resource.resourceType === 'string' ? resource.resourceType : 'FHIR resource'
  const detail =
    clinicalResourceDetail(resourceType, resource) ??
    careResourceDetail(resourceType, resource) ??
    administrativeResourceDetail(resourceType, resource)
  if (detail) return detail
  const fallbackName =
    codedText(resource.code) ??
    (typeof resource.description === 'string' ? resource.description : null) ??
    (typeof resource.status === 'string' ? resource.status : null)
  return { title: fallbackName ?? 'Clinical data retrieved', details: [] }
}

export function hasEpicPdfLink(resource: Record<string, unknown>): boolean {
  if (resource.resourceType !== 'DocumentReference' || !Array.isArray(resource.content))
    return false
  return resource.content.some((item) => {
    if (
      !item ||
      typeof item !== 'object' ||
      !('attachment' in item) ||
      !item.attachment ||
      typeof item.attachment !== 'object'
    )
      return false
    const attachment = item.attachment as { contentType?: unknown; url?: unknown }
    return (
      typeof attachment.url === 'string' &&
      attachment.url.trim().length > 0 &&
      String(attachment.contentType ?? '')
        .toLowerCase()
        .split(';', 1)[0] === 'application/pdf'
    )
  })
}

export function epicAvailabilityMessage(availability: Record<string, string>): string {
  const unavailable = Object.entries(availability)
    .filter(([, value]) => value !== 'ok')
    .map(([type, value]) => {
      if (value === 'empty') return `${type}: Epic returned no records`
      if (value === 'forbidden') return `${type}: access not granted`
      if (value === 'unavailable') return `${type}: not available from this hospital`
      return `${type}: ${value}`
    })
  return unavailable.join(' · ') || 'All requested supported resource families responded.'
}
