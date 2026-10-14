import { fetchExternalShlJwe, fetchManifest, type ShlManifestFile } from '../api/external'
import type { PatientSession } from '../api/patient-session-types'
import type { SmartHealthLink } from '../shl'
import { decryptJwe, type DecryptedFile, SUPPORTED_FHIR } from './crypto'
import { apiAccessOffer, asFhirBundle, fhirResources, smartCardBundles } from './fhir'
import type { ExternalShlReview } from './types'

async function resolveFile(file: ShlManifestFile, key: string): Promise<DecryptedFile> {
  const jwe = file.embedded ?? (await fetchExternalShlJwe(file.location ?? ''))
  return decryptJwe(jwe, key, file.contentType)
}

function conflictLabels(
  resources: Record<string, unknown>[],
  session: PatientSession | null,
): string[] {
  if (!session)
    return [
      'Sign in to a ClinicForce patient workspace before confirmation to compare and save this data.',
    ]
  const labels = new Set(
    session.records.map((record) => record.label.trim().toLocaleLowerCase()).filter(Boolean),
  )
  const conflicts = resources.flatMap((resource) => {
    const code = resource.code as { text?: unknown } | undefined
    const medication = resource.medicationCodeableConcept as { text?: unknown } | undefined
    const value =
      typeof code?.text === 'string'
        ? code.text
        : typeof medication?.text === 'string'
          ? medication.text
          : null
    return value && labels.has(value.trim().toLocaleLowerCase())
      ? [`${value} matches an existing ClinicForce record.`]
      : []
  })
  return [...new Set(conflicts)]
}

export async function receiveExternalShl(
  link: SmartHealthLink,
  passcode: string | undefined,
  session: PatientSession | null,
): Promise<ExternalShlReview> {
  const sourceHost = new URL(link.manifestUrl).host
  const files = link.directFile
    ? [
        {
          contentType: '' as const,
          location: `${link.manifestUrl}${link.manifestUrl.includes('?') ? '&' : '?'}recipient=${encodeURIComponent('ClinicForce')}`,
        },
      ]
    : await fetchManifest(
        link.manifestUrl,
        'ClinicForce',
        link.requiresPasscode ? passcode : undefined,
      )
  const resources: Record<string, unknown>[] = []
  const provenance = new Set<string>()
  const apiAccessOffers: string[] = []
  for (const file of files) {
    const decrypted = file.contentType
      ? await resolveFile(file, link.key)
      : await decryptJwe(await fetchExternalShlJwe(file.location ?? ''), link.key)
    let decoded: unknown
    try {
      decoded = JSON.parse(decrypted.plaintext)
    } catch {
      throw new Error('The decrypted SHL file is not valid JSON.')
    }
    if (decrypted.contentType === 'application/fhir+json') {
      resources.push(...fhirResources(asFhirBundle(decoded)))
      provenance.add(`External FHIR data from ${sourceHost}; reconcile before relying on it.`)
    } else if (decrypted.contentType === 'application/smart-health-card') {
      const bundles = await smartCardBundles(decoded)
      for (const bundle of bundles) resources.push(...fhirResources(bundle))
      provenance.add(`SMART Health Card signature verified against ${sourceHost}'s issuer keys.`)
    } else if (decrypted.contentType === 'application/smart-api-access') {
      apiAccessOffers.push(apiAccessOffer(decoded))
    }
  }
  const importable = resources.filter(
    (resource) =>
      SUPPORTED_FHIR.has(String(resource.resourceType)) && resource.resourceType !== 'Patient',
  )
  const counts = [
    ...importable
      .reduce(
        (counts, resource) =>
          counts.set(
            String(resource.resourceType),
            (counts.get(String(resource.resourceType)) ?? 0) + 1,
          ),
        new Map<string, number>(),
      )
      .entries(),
  ]
    .map(([type, count]) => ({ type, count }))
    .sort((left, right) => left.type.localeCompare(right.type))
  return {
    sourceHost,
    label: link.label,
    resources: importable,
    resourceCounts: counts,
    provenance: [...provenance],
    conflicts: conflictLabels(importable, session),
    apiAccessOffers,
    canImport: Boolean(importable.length) && !apiAccessOffers.length && Boolean(session),
  }
}
