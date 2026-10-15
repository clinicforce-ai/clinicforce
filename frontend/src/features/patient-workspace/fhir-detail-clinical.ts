import { codedText, dateLabel } from './display-text'
import type { EpicResourceDetail } from './fhir-detail-types'

export function clinicalResourceDetail(
  resourceType: string,
  resource: Record<string, unknown>,
): EpicResourceDetail | null {
  if (resourceType === 'Condition') {
    const name =
      codedText(resource.code) ??
      (typeof resource.description === 'string' ? resource.description : null)
    const status = codedText(resource.clinicalStatus)
    const severity = codedText(resource.severity)
    const category = Array.isArray(resource.category)
      ? resource.category
          .map((cat) => codedText(cat))
          .filter(Boolean)
          .join(', ')
      : null
    const onset =
      typeof resource.onsetDateTime === 'string'
        ? `Onset: ${dateLabel(resource.onsetDateTime)}`
        : null
    return {
      title: name ?? 'Unnamed condition',
      details: [status, severity, category, onset].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'AllergyIntolerance') {
    const substance = codedText(resource.code)
    const status = codedText(resource.clinicalStatus)
    const verification = codedText(resource.verificationStatus)
    const criticality =
      typeof resource.criticality === 'string' ? `Criticality: ${resource.criticality}` : null
    const categoryArr = Array.isArray(resource.category)
      ? resource.category.filter((c): c is string => typeof c === 'string').join(', ')
      : null
    const reactions = Array.isArray(resource.reaction)
      ? resource.reaction
          .map((r) => {
            if (!r || typeof r !== 'object') return null
            const manifestations = Array.isArray((r as Record<string, unknown>).manifestation)
              ? ((r as Record<string, unknown>).manifestation as unknown[])
                  .map((m) => codedText(m))
                  .filter(Boolean)
                  .join(', ')
              : null
            return manifestations
          })
          .filter(Boolean)
          .join('; ')
      : null
    return {
      title: substance ?? 'Unknown allergen',
      details: [
        status,
        verification,
        categoryArr ? `Type: ${categoryArr}` : null,
        criticality,
        reactions ? `Reactions: ${reactions}` : null,
      ].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'MedicationRequest') {
    const medName =
      codedText(resource.medicationCodeableConcept) ??
      (typeof resource.medicationReference === 'object' && resource.medicationReference
        ? codedText(
            (resource.medicationReference as Record<string, unknown>).display
              ? { text: (resource.medicationReference as Record<string, unknown>).display }
              : resource.medicationReference,
          )
        : null)
    const status = typeof resource.status === 'string' ? resource.status : null
    const intent = typeof resource.intent === 'string' ? resource.intent : null
    const authored =
      typeof resource.authoredOn === 'string'
        ? `Prescribed: ${dateLabel(resource.authoredOn)}`
        : null
    const dosageInstructions = Array.isArray(resource.dosageInstruction)
      ? resource.dosageInstruction
          .map((d) => {
            if (!d || typeof d !== 'object') return null
            const text =
              typeof (d as Record<string, unknown>).text === 'string'
                ? ((d as Record<string, unknown>).text as string)
                : null
            return text
          })
          .filter(Boolean)
          .join('; ')
      : null
    const requester =
      resource.requester && typeof resource.requester === 'object'
        ? typeof (resource.requester as Record<string, unknown>).display === 'string'
          ? `Prescribed by: ${(resource.requester as Record<string, unknown>).display}`
          : null
        : null
    return {
      title: medName ?? 'Unnamed medication',
      details: [
        status,
        intent !== 'order' ? intent : null,
        dosageInstructions,
        authored,
        requester,
      ].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'Immunization') {
    const vaccine = codedText(resource.vaccineCode)
    const status = typeof resource.status === 'string' ? resource.status : null
    const date =
      typeof resource.occurrenceDateTime === 'string'
        ? `Date: ${dateLabel(resource.occurrenceDateTime)}`
        : typeof resource.occurrenceString === 'string'
          ? `Date: ${resource.occurrenceString}`
          : null
    const site = codedText(resource.site)
    const primarySource =
      typeof resource.primarySource === 'boolean'
        ? resource.primarySource
          ? 'Primary source'
          : 'Historical record'
        : null
    return {
      title: vaccine ?? 'Unnamed vaccine',
      details: [status, date, site ? `Site: ${site}` : null, primarySource].filter(
        Boolean,
      ) as string[],
    }
  }

  if (resourceType === 'Observation') {
    const name = codedText(resource.code)
    const status =
      typeof resource.status === 'string' && resource.status !== 'final' ? resource.status : null
    const effectiveDate =
      typeof resource.effectiveDateTime === 'string' ? dateLabel(resource.effectiveDateTime) : null
    let valueStr: string | null = null
    if (resource.valueQuantity && typeof resource.valueQuantity === 'object') {
      const q = resource.valueQuantity as { value?: unknown; unit?: unknown }
      if (q.value !== undefined)
        valueStr = `${q.value}${typeof q.unit === 'string' ? ` ${q.unit}` : ''}`
    } else if (typeof resource.valueString === 'string') {
      valueStr = resource.valueString
    } else if (resource.valueCodeableConcept) {
      valueStr = codedText(resource.valueCodeableConcept)
    }
    const interpretation = Array.isArray(resource.interpretation)
      ? resource.interpretation
          .map((i) => codedText(i))
          .filter(Boolean)
          .join(', ')
      : null
    const refRange = Array.isArray(resource.referenceRange)
      ? resource.referenceRange
          .map((r) => {
            if (!r || typeof r !== 'object') return null
            const rr = r as {
              low?: { value?: unknown; unit?: unknown }
              high?: { value?: unknown; unit?: unknown }
              text?: unknown
            }
            if (typeof rr.text === 'string') return rr.text
            const low = rr.low?.value !== undefined ? `${rr.low.value}` : null
            const high = rr.high?.value !== undefined ? `${rr.high.value}` : null
            const unit =
              typeof rr.low?.unit === 'string'
                ? ` ${rr.low.unit}`
                : typeof rr.high?.unit === 'string'
                  ? ` ${rr.high.unit}`
                  : ''
            if (low && high) return `Ref: ${low}–${high}${unit}`
            if (low) return `Ref: ≥${low}${unit}`
            if (high) return `Ref: ≤${high}${unit}`
            return null
          })
          .filter(Boolean)
          .join('; ')
      : null
    // Handle component observations (e.g. blood pressure systolic/diastolic)
    const components = Array.isArray(resource.component)
      ? resource.component
          .map((c) => {
            if (!c || typeof c !== 'object') return null
            const comp = c as Record<string, unknown>
            const cName = codedText(comp.code)
            let cValue: string | null = null
            if (comp.valueQuantity && typeof comp.valueQuantity === 'object') {
              const q = comp.valueQuantity as { value?: unknown; unit?: unknown }
              if (q.value !== undefined)
                cValue = `${q.value}${typeof q.unit === 'string' ? ` ${q.unit}` : ''}`
            } else if (typeof comp.valueString === 'string') {
              cValue = comp.valueString
            }
            return cName && cValue ? `${cName}: ${cValue}` : null
          })
          .filter(Boolean)
          .join(' / ')
      : null
    return {
      title: name ?? 'Unnamed observation',
      details: [
        valueStr ? `Value: ${valueStr}` : null,
        components,
        interpretation ? `Interpretation: ${interpretation}` : null,
        refRange,
        effectiveDate,
        status,
      ].filter(Boolean) as string[],
    }
  }

  if (resourceType === 'DiagnosticReport') {
    const name = codedText(resource.code)
    const status = typeof resource.status === 'string' ? resource.status : null
    const date =
      typeof resource.effectiveDateTime === 'string' ? dateLabel(resource.effectiveDateTime) : null
    const conclusion = typeof resource.conclusion === 'string' ? resource.conclusion : null
    const category = Array.isArray(resource.category)
      ? resource.category
          .map((cat) => codedText(cat))
          .filter(Boolean)
          .join(', ')
      : null
    return {
      title: name ?? 'Unnamed report',
      details: [status, category, date, conclusion ? `Conclusion: ${conclusion}` : null].filter(
        Boolean,
      ) as string[],
    }
  }

  return null
}
