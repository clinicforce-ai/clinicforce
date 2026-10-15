import { verifySmartHealthCard } from './signature'

export function asFhirBundle(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object')
    throw new Error('The decrypted SHL file is not FHIR JSON.')
  const resource = value as Record<string, unknown>
  if (typeof resource.resourceType !== 'string' || !resource.resourceType)
    throw new Error('The decrypted SHL file has no FHIR resourceType.')
  if (resource.resourceType !== 'Bundle')
    return { resourceType: 'Bundle', type: 'collection', entry: [{ resource }] }
  if (!Array.isArray(resource.entry))
    throw new Error('The decrypted FHIR Bundle has no entry array.')
  if (resource.entry.length > 5_000)
    throw new Error('The decrypted FHIR Bundle contains too many resources.')
  for (const entry of resource.entry) {
    if (!entry || typeof entry !== 'object' || !('resource' in entry))
      throw new Error('The decrypted FHIR Bundle has an invalid entry.')
    const candidate = (entry as { resource?: unknown }).resource
    if (
      !candidate ||
      typeof candidate !== 'object' ||
      typeof (candidate as { resourceType?: unknown }).resourceType !== 'string'
    )
      throw new Error('The decrypted FHIR Bundle has an invalid resource.')
  }
  return resource
}

export function fhirResources(bundle: Record<string, unknown>): Record<string, unknown>[] {
  const entries = Array.isArray(bundle.entry) ? bundle.entry : []
  return entries.flatMap((entry) => {
    const resource =
      entry && typeof entry === 'object' ? (entry as { resource?: unknown }).resource : undefined
    return resource &&
      typeof resource === 'object' &&
      typeof (resource as { resourceType?: unknown }).resourceType === 'string'
      ? [resource as Record<string, unknown>]
      : []
  })
}

export function smartCardBundles(value: unknown): Promise<Record<string, unknown>[]> {
  if (
    !value ||
    typeof value !== 'object' ||
    !Array.isArray((value as { verifiableCredential?: unknown }).verifiableCredential)
  ) {
    return Promise.reject(new Error('The SMART Health Card file has no verifiable credentials.'))
  }
  const cards = (value as { verifiableCredential: unknown[] }).verifiableCredential
  return Promise.all(
    cards.map(async (card) => {
      if (typeof card !== 'string')
        throw new Error('The SMART Health Card file contains an invalid credential.')
      const payload = await verifySmartHealthCard(card)
      const bundle = (payload.vc as { credentialSubject?: { fhirBundle?: unknown } } | undefined)
        ?.credentialSubject?.fhirBundle
      return asFhirBundle(bundle)
    }),
  )
}

export function apiAccessOffer(value: unknown): string {
  if (!value || typeof value !== 'object' || typeof (value as { aud?: unknown }).aud !== 'string')
    throw new Error('The SMART API access file is invalid.')
  try {
    return new URL((value as { aud: string }).aud).host
  } catch {
    throw new Error('The SMART API access file has an invalid FHIR server URL.')
  }
}
