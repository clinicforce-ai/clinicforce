import { codedText, dateLabel } from './display-text'
import type { EpicResourceDetail } from './fhir-detail-types'

export function careResourceDetail(
  resourceType: string,
  resource: Record<string, unknown>,
): EpicResourceDetail | null {
  if (resourceType === 'Encounter') {
    const encounterClass =
      resource.class && typeof resource.class === 'object'
        ? (((resource.class as { display?: unknown }).display as string) ??
          ((resource.class as { code?: unknown }).code as string) ??
          null)
        : null
    const status = typeof resource.status === 'string' ? resource.status : null
    const typeArr = Array.isArray(resource.type)
      ? resource.type.map((t) => codedText(t)).filter(Boolean)
      : []
    const period =
      resource.period && typeof resource.period === 'object'
        ? (() => {
            const p = resource.period as { start?: string; end?: string }
            return typeof p.start === 'string'
              ? `${dateLabel(p.start)}${typeof p.end === 'string' ? ` – ${dateLabel(p.end)}` : ''}`
              : null
          })()
        : null
    const reasonArr = Array.isArray(resource.reasonCode)
      ? resource.reasonCode.map((r) => codedText(r)).filter(Boolean)
      : []
    return {
      title: typeArr[0] ?? encounterClass ?? 'Clinical encounter',
      details: [
        status,
        encounterClass && typeArr[0] ? `Class: ${encounterClass}` : null,
        period,
        reasonArr.length ? `Reason: ${reasonArr.join(', ')}` : null,
      ].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'CarePlan') {
    const title =
      typeof resource.title === 'string'
        ? resource.title
        : codedText(Array.isArray(resource.category) ? resource.category[0] : null)
    const status = typeof resource.status === 'string' ? resource.status : null
    const intent = typeof resource.intent === 'string' ? resource.intent : null
    const description = typeof resource.description === 'string' ? resource.description : null
    const activities = Array.isArray(resource.activity)
      ? resource.activity
          .map((a) => {
            if (!a || typeof a !== 'object') return null
            const detail = (a as Record<string, unknown>).detail
            if (!detail || typeof detail !== 'object') return null
            const actDesc =
              typeof (detail as Record<string, unknown>).description === 'string'
                ? ((detail as Record<string, unknown>).description as string)
                : null
            const actCode = codedText((detail as Record<string, unknown>).code)
            return actDesc ?? actCode
          })
          .filter(Boolean)
          .join('; ')
      : null
    return {
      title: title ?? 'Care plan',
      details: [status, intent, description, activities ? `Activity: ${activities}` : null].filter(
        Boolean,
      ) as string[],
    }
  }

  if (resourceType === 'CareTeam') {
    const name = typeof resource.name === 'string' ? resource.name : null
    const status = typeof resource.status === 'string' ? resource.status : null
    const participants = Array.isArray(resource.participant)
      ? resource.participant
          .slice(0, 5)
          .map((p) => {
            if (!p || typeof p !== 'object') return null
            const member = (p as Record<string, unknown>).member
            return member &&
              typeof member === 'object' &&
              typeof (member as Record<string, unknown>).display === 'string'
              ? ((member as Record<string, unknown>).display as string)
              : null
          })
          .filter(Boolean)
          .join(', ')
      : null
    return {
      title: name ?? 'Care team',
      details: [status, participants ? `Members: ${participants}` : null].filter(
        Boolean,
      ) as string[],
    }
  }

  if (resourceType === 'Coverage') {
    const payor = Array.isArray(resource.payor)
      ? resource.payor
          .map((p) =>
            p && typeof p === 'object' && typeof (p as Record<string, unknown>).display === 'string'
              ? ((p as Record<string, unknown>).display as string)
              : null,
          )
          .filter(Boolean)
          .join(', ')
      : null
    const status = typeof resource.status === 'string' ? resource.status : null
    const subscriberId =
      typeof resource.subscriberId === 'string' ? `Subscriber ID: ${resource.subscriberId}` : null
    const relationship = codedText(resource.relationship)
    return {
      title: payor ?? 'Insurance coverage',
      details: [status, subscriberId, relationship ? `Relationship: ${relationship}` : null].filter(
        Boolean,
      ) as string[],
    }
  }

  if (resourceType === 'Goal') {
    const description =
      codedText(resource.description) ??
      (typeof resource.description === 'string' ? resource.description : null)
    const lifecycleStatus =
      typeof resource.lifecycleStatus === 'string' ? resource.lifecycleStatus : null
    const startDate =
      typeof resource.startDate === 'string' ? `Start: ${dateLabel(resource.startDate)}` : null
    const targetDate = Array.isArray(resource.target)
      ? resource.target
          .map((t) =>
            t && typeof t === 'object' && typeof (t as Record<string, unknown>).dueDate === 'string'
              ? `Target: ${dateLabel((t as Record<string, unknown>).dueDate as string)}`
              : null,
          )
          .filter(Boolean)
          .join(', ')
      : null
    return {
      title: description ?? 'Health goal',
      details: [lifecycleStatus, startDate, targetDate].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Device') {
    const deviceName = Array.isArray(resource.deviceName)
      ? resource.deviceName
          .map((d) =>
            d && typeof d === 'object' && typeof (d as Record<string, unknown>).name === 'string'
              ? ((d as Record<string, unknown>).name as string)
              : null,
          )
          .filter(Boolean)[0]
      : null
    const type = codedText(resource.type)
    const status = typeof resource.status === 'string' ? resource.status : null
    const manufacturer =
      typeof resource.manufacturer === 'string' ? `Manufacturer: ${resource.manufacturer}` : null
    const model = typeof resource.modelNumber === 'string' ? `Model: ${resource.modelNumber}` : null
    return {
      title: deviceName ?? type ?? 'Medical device',
      details: [status, manufacturer, model].filter(Boolean) as string[],
    }
  }

  return null
}
