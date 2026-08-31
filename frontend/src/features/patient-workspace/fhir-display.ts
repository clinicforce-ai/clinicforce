/** Human-readable labels and display order for grouped Epic resource types. */
import { codedText, dateLabel } from './metadata'

export const epicResourceGroupMeta: Record<string, { label: string; singular: string; order: number }> = {
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

export function groupEpicResources(resources: Array<Record<string, unknown>>): Array<{ type: string; meta: typeof epicResourceGroupMeta[string]; resources: Array<Record<string, unknown>> }> {
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
export function epicResourceDetail(resource: Record<string, unknown>): { title: string; details: string[] } {
  const resourceType = typeof resource.resourceType === 'string' ? resource.resourceType : 'FHIR resource'

  if (resourceType === 'Condition') {
    const name = codedText(resource.code) ?? (typeof resource.description === 'string' ? resource.description : null)
    const status = codedText(resource.clinicalStatus)
    const severity = codedText(resource.severity)
    const category = Array.isArray(resource.category) ? resource.category.map((cat) => codedText(cat)).filter(Boolean).join(', ') : null
    const onset = typeof resource.onsetDateTime === 'string' ? `Onset: ${dateLabel(resource.onsetDateTime)}` : null
    return { title: name ?? 'Unnamed condition', details: [status, severity, category, onset].filter(Boolean) as string[] }
  }

  if (resourceType === 'AllergyIntolerance') {
    const substance = codedText(resource.code)
    const status = codedText(resource.clinicalStatus)
    const verification = codedText(resource.verificationStatus)
    const criticality = typeof resource.criticality === 'string' ? `Criticality: ${resource.criticality}` : null
    const categoryArr = Array.isArray(resource.category) ? resource.category.filter((c): c is string => typeof c === 'string').join(', ') : null
    const reactions = Array.isArray(resource.reaction)
      ? resource.reaction.map((r) => {
          if (!r || typeof r !== 'object') return null
          const manifestations = Array.isArray((r as Record<string, unknown>).manifestation)
            ? ((r as Record<string, unknown>).manifestation as unknown[]).map((m) => codedText(m)).filter(Boolean).join(', ')
            : null
          return manifestations
        }).filter(Boolean).join('; ')
      : null
    return {
      title: substance ?? 'Unknown allergen',
      details: [status, verification, categoryArr ? `Type: ${categoryArr}` : null, criticality, reactions ? `Reactions: ${reactions}` : null].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'MedicationRequest') {
    const medName = codedText(resource.medicationCodeableConcept) ?? (typeof resource.medicationReference === 'object' && resource.medicationReference ? codedText((resource.medicationReference as Record<string, unknown>).display ? { text: (resource.medicationReference as Record<string, unknown>).display } : resource.medicationReference) : null)
    const status = typeof resource.status === 'string' ? resource.status : null
    const intent = typeof resource.intent === 'string' ? resource.intent : null
    const authored = typeof resource.authoredOn === 'string' ? `Prescribed: ${dateLabel(resource.authoredOn)}` : null
    const dosageInstructions = Array.isArray(resource.dosageInstruction)
      ? resource.dosageInstruction.map((d) => {
          if (!d || typeof d !== 'object') return null
          const text = typeof (d as Record<string, unknown>).text === 'string' ? (d as Record<string, unknown>).text as string : null
          return text
        }).filter(Boolean).join('; ')
      : null
    const requester = resource.requester && typeof resource.requester === 'object' ? (typeof (resource.requester as Record<string, unknown>).display === 'string' ? `Prescribed by: ${(resource.requester as Record<string, unknown>).display}` : null) : null
    return {
      title: medName ?? 'Unnamed medication',
      details: [status, intent !== 'order' ? intent : null, dosageInstructions, authored, requester].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Immunization') {
    const vaccine = codedText(resource.vaccineCode)
    const status = typeof resource.status === 'string' ? resource.status : null
    const date = typeof resource.occurrenceDateTime === 'string' ? `Date: ${dateLabel(resource.occurrenceDateTime)}` : (typeof resource.occurrenceString === 'string' ? `Date: ${resource.occurrenceString}` : null)
    const site = codedText(resource.site)
    const primarySource = typeof resource.primarySource === 'boolean' ? (resource.primarySource ? 'Primary source' : 'Historical record') : null
    return {
      title: vaccine ?? 'Unnamed vaccine',
      details: [status, date, site ? `Site: ${site}` : null, primarySource].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Observation') {
    const name = codedText(resource.code)
    const status = typeof resource.status === 'string' && resource.status !== 'final' ? resource.status : null
    const effectiveDate = typeof resource.effectiveDateTime === 'string' ? dateLabel(resource.effectiveDateTime) : null
    let valueStr: string | null = null
    if (resource.valueQuantity && typeof resource.valueQuantity === 'object') {
      const q = resource.valueQuantity as { value?: unknown; unit?: unknown }
      if (q.value !== undefined) valueStr = `${q.value}${typeof q.unit === 'string' ? ` ${q.unit}` : ''}`
    } else if (typeof resource.valueString === 'string') {
      valueStr = resource.valueString
    } else if (resource.valueCodeableConcept) {
      valueStr = codedText(resource.valueCodeableConcept)
    }
    const interpretation = Array.isArray(resource.interpretation) ? resource.interpretation.map((i) => codedText(i)).filter(Boolean).join(', ') : null
    const refRange = Array.isArray(resource.referenceRange)
      ? resource.referenceRange.map((r) => {
          if (!r || typeof r !== 'object') return null
          const rr = r as { low?: { value?: unknown; unit?: unknown }; high?: { value?: unknown; unit?: unknown }; text?: unknown }
          if (typeof rr.text === 'string') return rr.text
          const low = rr.low?.value !== undefined ? `${rr.low.value}` : null
          const high = rr.high?.value !== undefined ? `${rr.high.value}` : null
          const unit = typeof rr.low?.unit === 'string' ? ` ${rr.low.unit}` : (typeof rr.high?.unit === 'string' ? ` ${rr.high.unit}` : '')
          if (low && high) return `Ref: ${low}–${high}${unit}`
          if (low) return `Ref: ≥${low}${unit}`
          if (high) return `Ref: ≤${high}${unit}`
          return null
        }).filter(Boolean).join('; ')
      : null
    // Handle component observations (e.g. blood pressure systolic/diastolic)
    const components = Array.isArray(resource.component)
      ? resource.component.map((c) => {
          if (!c || typeof c !== 'object') return null
          const comp = c as Record<string, unknown>
          const cName = codedText(comp.code)
          let cValue: string | null = null
          if (comp.valueQuantity && typeof comp.valueQuantity === 'object') {
            const q = comp.valueQuantity as { value?: unknown; unit?: unknown }
            if (q.value !== undefined) cValue = `${q.value}${typeof q.unit === 'string' ? ` ${q.unit}` : ''}`
          } else if (typeof comp.valueString === 'string') { cValue = comp.valueString }
          return cName && cValue ? `${cName}: ${cValue}` : null
        }).filter(Boolean).join(' / ')
      : null
    return {
      title: name ?? 'Unnamed observation',
      details: [valueStr ? `Value: ${valueStr}` : null, components, interpretation ? `Interpretation: ${interpretation}` : null, refRange, effectiveDate, status].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'DiagnosticReport') {
    const name = codedText(resource.code)
    const status = typeof resource.status === 'string' ? resource.status : null
    const date = typeof resource.effectiveDateTime === 'string' ? dateLabel(resource.effectiveDateTime) : null
    const conclusion = typeof resource.conclusion === 'string' ? resource.conclusion : null
    const category = Array.isArray(resource.category) ? resource.category.map((cat) => codedText(cat)).filter(Boolean).join(', ') : null
    return {
      title: name ?? 'Unnamed report',
      details: [status, category, date, conclusion ? `Conclusion: ${conclusion}` : null].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Encounter') {
    const encounterClass = resource.class && typeof resource.class === 'object'
      ? ((resource.class as { display?: unknown }).display as string) ?? ((resource.class as { code?: unknown }).code as string) ?? null
      : null
    const status = typeof resource.status === 'string' ? resource.status : null
    const typeArr = Array.isArray(resource.type) ? resource.type.map((t) => codedText(t)).filter(Boolean) : []
    const period = resource.period && typeof resource.period === 'object'
      ? (() => { const p = resource.period as { start?: string; end?: string }; return typeof p.start === 'string' ? `${dateLabel(p.start)}${typeof p.end === 'string' ? ` – ${dateLabel(p.end)}` : ''}` : null })()
      : null
    const reasonArr = Array.isArray(resource.reasonCode) ? resource.reasonCode.map((r) => codedText(r)).filter(Boolean) : []
    return {
      title: typeArr[0] ?? encounterClass ?? 'Clinical encounter',
      details: [status, encounterClass && typeArr[0] ? `Class: ${encounterClass}` : null, period, reasonArr.length ? `Reason: ${reasonArr.join(', ')}` : null].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'CarePlan') {
    const title = typeof resource.title === 'string' ? resource.title : codedText(Array.isArray(resource.category) ? resource.category[0] : null)
    const status = typeof resource.status === 'string' ? resource.status : null
    const intent = typeof resource.intent === 'string' ? resource.intent : null
    const description = typeof resource.description === 'string' ? resource.description : null
    const activities = Array.isArray(resource.activity)
      ? resource.activity.map((a) => {
          if (!a || typeof a !== 'object') return null
          const detail = (a as Record<string, unknown>).detail
          if (!detail || typeof detail !== 'object') return null
          const actDesc = typeof (detail as Record<string, unknown>).description === 'string' ? (detail as Record<string, unknown>).description as string : null
          const actCode = codedText((detail as Record<string, unknown>).code)
          return actDesc ?? actCode
        }).filter(Boolean).join('; ')
      : null
    return { title: title ?? 'Care plan', details: [status, intent, description, activities ? `Activity: ${activities}` : null].filter(Boolean) as string[] }
  }

  if (resourceType === 'CareTeam') {
    const name = typeof resource.name === 'string' ? resource.name : null
    const status = typeof resource.status === 'string' ? resource.status : null
    const participants = Array.isArray(resource.participant)
      ? resource.participant.slice(0, 5).map((p) => {
          if (!p || typeof p !== 'object') return null
          const member = (p as Record<string, unknown>).member
          return member && typeof member === 'object' && typeof (member as Record<string, unknown>).display === 'string' ? (member as Record<string, unknown>).display as string : null
        }).filter(Boolean).join(', ')
      : null
    return { title: name ?? 'Care team', details: [status, participants ? `Members: ${participants}` : null].filter(Boolean) as string[] }
  }

  if (resourceType === 'Coverage') {
    const payor = Array.isArray(resource.payor) ? resource.payor.map((p) => (p && typeof p === 'object' && typeof (p as Record<string, unknown>).display === 'string' ? (p as Record<string, unknown>).display as string : null)).filter(Boolean).join(', ') : null
    const status = typeof resource.status === 'string' ? resource.status : null
    const subscriberId = typeof resource.subscriberId === 'string' ? `Subscriber ID: ${resource.subscriberId}` : null
    const relationship = codedText(resource.relationship)
    return { title: payor ?? 'Insurance coverage', details: [status, subscriberId, relationship ? `Relationship: ${relationship}` : null].filter(Boolean) as string[] }
  }

  if (resourceType === 'Goal') {
    const description = codedText(resource.description) ?? (typeof resource.description === 'string' ? resource.description : null)
    const lifecycleStatus = typeof resource.lifecycleStatus === 'string' ? resource.lifecycleStatus : null
    const startDate = typeof resource.startDate === 'string' ? `Start: ${dateLabel(resource.startDate)}` : null
    const targetDate = Array.isArray(resource.target) ? resource.target.map((t) => (t && typeof t === 'object' && typeof (t as Record<string, unknown>).dueDate === 'string' ? `Target: ${dateLabel((t as Record<string, unknown>).dueDate as string)}` : null)).filter(Boolean).join(', ') : null
    return { title: description ?? 'Health goal', details: [lifecycleStatus, startDate, targetDate].filter(Boolean) as string[] }
  }

  if (resourceType === 'Device') {
    const deviceName = Array.isArray(resource.deviceName) ? resource.deviceName.map((d) => (d && typeof d === 'object' && typeof (d as Record<string, unknown>).name === 'string' ? (d as Record<string, unknown>).name as string : null)).filter(Boolean)[0] : null
    const type = codedText(resource.type)
    const status = typeof resource.status === 'string' ? resource.status : null
    const manufacturer = typeof resource.manufacturer === 'string' ? `Manufacturer: ${resource.manufacturer}` : null
    const model = typeof resource.modelNumber === 'string' ? `Model: ${resource.modelNumber}` : null
    return { title: deviceName ?? type ?? 'Medical device', details: [status, manufacturer, model].filter(Boolean) as string[] }
  }

  if (resourceType === 'DocumentReference') {
    const content = Array.isArray(resource.content) ? resource.content : []
    const attachment = content.find((item) => item && typeof item === 'object' && 'attachment' in item)
    const title = attachment && typeof attachment === 'object' && 'attachment' in attachment && attachment.attachment && typeof attachment.attachment === 'object'
      ? (attachment.attachment as { title?: unknown }).title : null
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
    const firstName = names.find((name) => name && typeof name === 'object') as { text?: unknown; given?: unknown; family?: unknown } | undefined
    const name = typeof firstName?.text === 'string' ? firstName.text : [
      ...(Array.isArray(firstName?.given) ? firstName.given.filter((part): part is string => typeof part === 'string') : []),
      typeof firstName?.family === 'string' ? firstName.family : '',
    ].filter(Boolean).join(' ')
    const birthDate = typeof resource.birthDate === 'string' ? `Born: ${dateLabel(resource.birthDate)}` : null
    const gender = typeof resource.gender === 'string' ? `Sex: ${resource.gender}` : null
    return { title: name || 'Patient', details: [birthDate, gender].filter(Boolean) as string[] }
  }

  // Generic fallback
  const fallbackName = codedText(resource.code) ?? (typeof resource.description === 'string' ? resource.description : null) ?? (typeof resource.status === 'string' ? resource.status : null)
  return { title: fallbackName ?? 'Clinical data retrieved', details: [] }
}

/** Legacy one-line summary kept for eCW review list compatibility. */
export function epicResourceSummary(resource: Record<string, unknown>): string {
  const detail = epicResourceDetail(resource)
  return detail.details.length ? `${detail.title} · ${detail.details.join(' · ')}` : detail.title
}

export function hasEpicPdfLink(resource: Record<string, unknown>): boolean {
  if (resource.resourceType !== 'DocumentReference' || !Array.isArray(resource.content)) return false
  return resource.content.some((item) => {
    if (!item || typeof item !== 'object' || !('attachment' in item) || !item.attachment || typeof item.attachment !== 'object') return false
    const attachment = item.attachment as { contentType?: unknown; url?: unknown }
    return typeof attachment.url === 'string' && attachment.url.trim().length > 0 && String(attachment.contentType ?? '').toLowerCase().split(';', 1)[0] === 'application/pdf'
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
