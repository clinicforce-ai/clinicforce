import { type EcwRecord, type FhirResource } from '../../services/api/patient-provider-types'
import { codedText, dateLabel } from './display-text'
import { ecwImportableResourceTypes } from './record-meta'
import { epicResourceDetail } from './fhir-display'

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null
const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' ? (value as Record<string, unknown>) : {}

export function healowDetail(resource: FhirResource): { title: string; details: string[] } {
  const detail = epicResourceDetail(resource)
  if (resource.resourceType === 'Coverage') {
    return {
      ...detail,
      title: detail.title.trim() || 'Insurance coverage',
      details: [
        ...(!detail.title.trim() ? ['Insurer name not supplied by the practice'] : []),
        ...detail.details,
      ],
    }
  }
  if (resource.resourceType === 'Condition') {
    return {
      ...detail,
      details: detail.details.map((value) =>
        value
          .replace(/Problem List Item|problem-list-item/gi, 'On the problem list')
          .replace(/Encounter Diagnosis|encounter-diagnosis/gi, 'Recorded at a visit'),
      ),
    }
  }
  if (resource.resourceType === 'ServiceRequest' || resource.resourceType === 'Procedure') {
    const order = resource.resourceType === 'ServiceRequest'
    const date =
      text(order ? resource.authoredOn : resource.performedDateTime) ??
      text(object(resource.performedPeriod).start)
    return {
      title: codedText(resource.code) ?? (order ? 'Test or referral order' : 'Procedure'),
      details: [
        text(resource.status)
          ? `${order ? 'Order' : 'Procedure'} status: ${resource.status}`
          : null,
        date ? dateLabel(date) : null,
        text(object(resource.requester).display),
        ...(Array.isArray(resource.note) ? resource.note.map((n) => text(object(n).text)) : []),
      ].filter((v): v is string => Boolean(v)),
    }
  }
  if (resource.resourceType === 'MedicationDispense') {
    return {
      title:
        codedText(resource.medicationCodeableConcept) ??
        text(object(resource.medicationReference).display) ??
        'Dispensed medication',
      details: [
        'Pharmacy record',
        text(resource.status),
        text(resource.whenHandedOver)
          ? `Dispensed ${dateLabel(String(resource.whenHandedOver))}`
          : null,
        ...(Array.isArray(resource.dosageInstruction)
          ? resource.dosageInstruction.map((d) => text(object(d).text))
          : []),
      ].filter((v): v is string => Boolean(v)),
    }
  }
  if (resource.resourceType === 'DocumentReference') {
    const authors = Array.isArray(resource.author)
      ? resource.author
          .map((a) => text(object(a).display))
          .filter(Boolean)
          .join(', ')
      : ''
    const type = object(resource.type)
    const codedTitle = codedText({ coding: type.coding })
    const title =
      detail.title === 'CCD Document' ? (codedTitle ?? 'Clinical summary') : detail.title
    return {
      title: title === 'H & P note' ? 'History and physical note' : title,
      details: [
        ...detail.details,
        ...(authors ? [`From ${authors}`] : []),
        'Document details only',
      ],
    }
  }
  if (resource.resourceType === 'CareTeam') {
    const members = Array.isArray(resource.participant)
      ? resource.participant.map((value) => {
          const participant = object(value)
          const role = Array.isArray(participant.role)
            ? participant.role.map(codedText).filter(Boolean).join(', ')
            : ''
          return `${role || 'Team member'}: ${text(object(participant.member).display) ?? 'Name not supplied'}`
        })
      : []
    return {
      title: detail.title,
      details: [text(resource.status), ...new Set(members)].filter((v): v is string => Boolean(v)),
    }
  }
  if (resource.resourceType === 'MedicationRequest' && detail.title === 'Unnamed medication') {
    return { ...detail, title: 'Prescribed medication' }
  }
  return detail
}

// Read the FHIR XHTML narrative as text. Never mount provider HTML in the page.
export function carePlanNotes(resource: FhirResource): string | null {
  const narrative = text(object(resource.text).div)
  if (resource.resourceType !== 'CarePlan' || !narrative) return null
  const document = new DOMParser().parseFromString(narrative, 'application/xhtml+xml')
  if (document.querySelector('parsererror'))
    return 'The practice supplied notes in a format that could not be displayed.'
  document.querySelectorAll('script, style, iframe, object').forEach((node) => node.remove())
  document
    .querySelectorAll('p, div, tr, li, br, h1, h2, h3, span')
    .forEach((node) => node.appendChild(document.createTextNode('\n')))
  document
    .querySelectorAll('td, th')
    .forEach((node) => node.appendChild(document.createTextNode(' · ')))
  return (
    document.documentElement.textContent
      ?.replace(/[ \t]+/g, ' ')
      .replace(/ · \n/g, '\n')
      .replace(/\n+/g, '\n')
      .trim() || null
  )
}

export function reviewResources(record: EcwRecord): FhirResource[] {
  const seen = new Set<string>()
  return record.bundle.entry
    .map((e) => e.resource)
    .filter((resource) => {
      if (
        !resource ||
        (!ecwImportableResourceTypes.has(String(resource.resourceType)) &&
          resource.resourceType !== 'Patient')
      )
        return false
      const key = resource.id ? `${resource.resourceType}/${resource.id}` : JSON.stringify(resource)
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
}
