import { codedText, dateLabel } from './display-text'
import type { EpicResourceDetail } from './fhir-detail-types'

export function administrativeResourceDetail(
  resourceType: string,
  resource: Record<string, unknown>,
): EpicResourceDetail | null {
  if (resourceType === 'DocumentReference') {
    const content = Array.isArray(resource.content) ? resource.content : []
    const attachment = content.find(
      (item) => item && typeof item === 'object' && 'attachment' in item,
    )
    const title =
      attachment &&
      typeof attachment === 'object' &&
      'attachment' in attachment &&
      attachment.attachment &&
      typeof attachment.attachment === 'object'
        ? (attachment.attachment as { title?: unknown }).title
        : null
    const docType = codedText(resource.type)
    const desc = typeof resource.description === 'string' ? resource.description : null
    const docDate = typeof resource.date === 'string' ? dateLabel(resource.date) : null
    return {
      title: docType ?? desc ?? (typeof title === 'string' ? title : null) ?? 'Clinical document',
      details: [docDate, desc && docType ? desc : null].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Patient') {
    const names = Array.isArray(resource.name) ? resource.name : []
    const firstName = names.find((name) => name && typeof name === 'object') as
      { text?: unknown; given?: unknown; family?: unknown } | undefined
    const name =
      typeof firstName?.text === 'string'
        ? firstName.text
        : [
            ...(Array.isArray(firstName?.given)
              ? firstName.given.filter((part): part is string => typeof part === 'string')
              : []),
            typeof firstName?.family === 'string' ? firstName.family : '',
          ]
            .filter(Boolean)
            .join(' ')
    const birthDate =
      typeof resource.birthDate === 'string' ? `Born: ${dateLabel(resource.birthDate)}` : null
    const gender = typeof resource.gender === 'string' ? `Sex: ${resource.gender}` : null
    return { title: name || 'Patient', details: [birthDate, gender].filter(Boolean) as string[] }
  }

  // Generic fallback
  return null
}
