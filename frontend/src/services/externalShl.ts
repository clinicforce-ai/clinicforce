import { fetchExternalShlJwe, fetchManifest, type PatientSession, type ShlManifestFile } from './api'
import type { SmartHealthLink } from './shl'

const MAX_DECRYPTED_BYTES = 5 * 1024 * 1024
const SUPPORTED_FHIR = new Set([
  'Patient', 'AllergyIntolerance', 'MedicationStatement', 'MedicationRequest', 'Condition',
  'Observation', 'Immunization', 'Procedure', 'Coverage', 'DiagnosticReport', 'DocumentReference',
])

export interface ExternalShlReview {
  sourceHost: string
  label?: string
  resources: Record<string, unknown>[]
  resourceCounts: Array<{ type: string; count: number }>
  provenance: string[]
  conflicts: string[]
  apiAccessOffers: string[]
  canImport: boolean
}

function base64UrlBytes(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]*$/.test(value)) throw new Error('The issuer supplied invalid base64url data.')
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0))
}

function utf8(value: string): Uint8Array { return new TextEncoder().encode(value) }

function combine(left: Uint8Array, right: Uint8Array): Uint8Array {
  const output = new Uint8Array(left.byteLength + right.byteLength)
  output.set(left)
  output.set(right, left.byteLength)
  return output
}

function bufferCopy(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength)
  copy.set(value)
  return copy.buffer
}

async function readStream(stream: ReadableStream<Uint8Array>): Promise<Uint8Array> {
  const reader = stream.getReader()
  const chunks: Uint8Array[] = []
  let length = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    length += value.byteLength
    if (length > MAX_DECRYPTED_BYTES) {
      await reader.cancel()
      throw new Error('The decrypted SHL file exceeds the 5 MB safety limit.')
    }
    chunks.push(value)
  }
  const output = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.byteLength }
  return output
}

async function inflateRaw(value: Uint8Array): Promise<Uint8Array> {
  if (!('DecompressionStream' in window)) throw new Error('This browser cannot open the issuer’s compressed Smart Health Link file.')
  const stream = new Blob([bufferCopy(value)]).stream().pipeThrough(new DecompressionStream('deflate-raw'))
  return readStream(stream)
}

interface DecryptedFile { contentType: string; plaintext: string }

async function decryptJwe(compact: string, key: string, expectedContentType?: string): Promise<DecryptedFile> {
  const parts = compact.split('.')
  if (parts.length !== 5 || parts[1] !== '') throw new Error('The issuer supplied an invalid compact JWE.')
  let header: Record<string, unknown>
  try { header = JSON.parse(new TextDecoder().decode(base64UrlBytes(parts[0]))) as Record<string, unknown> } catch { throw new Error('The issuer supplied an unreadable JWE header.') }
  if (header.alg !== 'dir' || header.enc !== 'A256GCM' || typeof header.cty !== 'string') throw new Error('The issuer used an unsupported JWE protection scheme.')
  if (expectedContentType && header.cty !== expectedContentType) throw new Error('The encrypted file type does not match its manifest entry.')
  if (!['application/fhir+json', 'application/smart-health-card', 'application/smart-api-access'].includes(header.cty)) throw new Error('The encrypted file has an unsupported content type.')
  const rawKey = base64UrlBytes(key)
  if (rawKey.byteLength !== 32) throw new Error('The Smart Health Link has an invalid decryption key.')
  const iv = base64UrlBytes(parts[2])
  const ciphertext = combine(base64UrlBytes(parts[3]), base64UrlBytes(parts[4]))
  let plaintext: Uint8Array
  try {
    const cryptoKey = await crypto.subtle.importKey('raw', bufferCopy(rawKey), 'AES-GCM', false, ['decrypt'])
    plaintext = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: bufferCopy(iv), additionalData: bufferCopy(utf8(parts[0])), tagLength: 128 }, cryptoKey, bufferCopy(ciphertext)))
  } catch { throw new Error('ClinicForce could not decrypt this Smart Health Link. Check the QR code and passcode.') }
  if (header.zip === 'DEF') plaintext = await inflateRaw(plaintext)
  if (plaintext.byteLength > MAX_DECRYPTED_BYTES) throw new Error('The decrypted SHL file exceeds the 5 MB safety limit.')
  return { contentType: header.cty, plaintext: new TextDecoder().decode(plaintext) }
}

function asFhirBundle(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object') throw new Error('The decrypted SHL file is not FHIR JSON.')
  const resource = value as Record<string, unknown>
  if (typeof resource.resourceType !== 'string' || !resource.resourceType) throw new Error('The decrypted SHL file has no FHIR resourceType.')
  if (resource.resourceType !== 'Bundle') return { resourceType: 'Bundle', type: 'collection', entry: [{ resource }] }
  if (!Array.isArray(resource.entry)) throw new Error('The decrypted FHIR Bundle has no entry array.')
  if (resource.entry.length > 5_000) throw new Error('The decrypted FHIR Bundle contains too many resources.')
  for (const entry of resource.entry) {
    if (!entry || typeof entry !== 'object' || !('resource' in entry)) throw new Error('The decrypted FHIR Bundle has an invalid entry.')
    const candidate = (entry as { resource?: unknown }).resource
    if (!candidate || typeof candidate !== 'object' || typeof (candidate as { resourceType?: unknown }).resourceType !== 'string') throw new Error('The decrypted FHIR Bundle has an invalid resource.')
  }
  return resource
}

function fhirResources(bundle: Record<string, unknown>): Record<string, unknown>[] {
  const entries = Array.isArray(bundle.entry) ? bundle.entry : []
  return entries.flatMap((entry) => {
    const resource = entry && typeof entry === 'object' ? (entry as { resource?: unknown }).resource : undefined
    return resource && typeof resource === 'object' && typeof (resource as { resourceType?: unknown }).resourceType === 'string' ? [resource as Record<string, unknown>] : []
  })
}

type IssuerJwk = JsonWebKey & { kid?: string }

async function fetchJwks(issuer: string): Promise<IssuerJwk[]> {
  let issuerUrl: URL
  try { issuerUrl = new URL(issuer) } catch { throw new Error('The SMART Health Card has an invalid issuer.') }
  if (issuerUrl.protocol !== 'https:' || issuerUrl.username || issuerUrl.password) throw new Error('The SMART Health Card issuer must use HTTPS.')
  const jwksUrl = new URL('/.well-known/jwks.json', issuerUrl).toString()
  let response: Response
  try {
    response = await fetch(jwksUrl, { credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer' })
  } catch { throw new Error('The SMART Health Card issuer could not provide verification keys to this browser.') }
  if (!response.ok) throw new Error('The SMART Health Card issuer did not provide verification keys.')
  const text = await response.text()
  if (text.length > 1_000_000) throw new Error('The SMART Health Card issuer returned too many verification keys.')
  let payload: unknown
  try { payload = JSON.parse(text) } catch { throw new Error('The SMART Health Card issuer returned invalid verification keys.') }
  const keys = payload && typeof payload === 'object' ? (payload as { keys?: unknown }).keys : undefined
  if (!Array.isArray(keys)) throw new Error('The SMART Health Card issuer returned no verification keys.')
  return keys.filter((key): key is IssuerJwk => Boolean(key && typeof key === 'object'))
}

async function verifySmartHealthCard(jws: string): Promise<Record<string, unknown>> {
  const parts = jws.split('.')
  if (parts.length !== 3) throw new Error('The SMART Health Card signature is malformed.')
  let header: Record<string, unknown>
  let payloadBytes = base64UrlBytes(parts[1])
  try { header = JSON.parse(new TextDecoder().decode(base64UrlBytes(parts[0]))) as Record<string, unknown> } catch { throw new Error('The SMART Health Card header is invalid.') }
  if (header.alg !== 'ES256' || typeof header.kid !== 'string') throw new Error('This SMART Health Card uses an unsupported signature algorithm.')
  if (header.zip === 'DEF') payloadBytes = await inflateRaw(payloadBytes)
  let payload: Record<string, unknown>
  try { payload = JSON.parse(new TextDecoder().decode(payloadBytes)) as Record<string, unknown> } catch { throw new Error('The SMART Health Card payload is invalid.') }
  if (typeof payload.iss !== 'string') throw new Error('The SMART Health Card does not identify its issuer.')
  const jwk = (await fetchJwks(payload.iss)).find((candidate) => candidate.kid === header.kid && candidate.kty === 'EC' && candidate.crv === 'P-256')
  if (!jwk) throw new Error('The SMART Health Card issuer did not provide the signing key.')
  try {
    const publicKey = await crypto.subtle.importKey('jwk', jwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify'])
    const valid = await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, publicKey, bufferCopy(base64UrlBytes(parts[2])), bufferCopy(utf8(`${parts[0]}.${parts[1]}`)))
    if (!valid) throw new Error('invalid')
  } catch { throw new Error('The SMART Health Card signature could not be verified.') }
  return payload
}

function smartCardBundles(value: unknown): Promise<Record<string, unknown>[]> {
  if (!value || typeof value !== 'object' || !Array.isArray((value as { verifiableCredential?: unknown }).verifiableCredential)) {
    return Promise.reject(new Error('The SMART Health Card file has no verifiable credentials.'))
  }
  const cards = (value as { verifiableCredential: unknown[] }).verifiableCredential
  return Promise.all(cards.map(async (card) => {
    if (typeof card !== 'string') throw new Error('The SMART Health Card file contains an invalid credential.')
    const payload = await verifySmartHealthCard(card)
    const bundle = (payload.vc as { credentialSubject?: { fhirBundle?: unknown } } | undefined)?.credentialSubject?.fhirBundle
    return asFhirBundle(bundle)
  }))
}

function apiAccessOffer(value: unknown): string {
  if (!value || typeof value !== 'object' || typeof (value as { aud?: unknown }).aud !== 'string') throw new Error('The SMART API access file is invalid.')
  try { return new URL((value as { aud: string }).aud).host } catch { throw new Error('The SMART API access file has an invalid FHIR server URL.') }
}

async function resolveFile(file: ShlManifestFile, key: string): Promise<DecryptedFile> {
  const jwe = file.embedded ?? await fetchExternalShlJwe(file.location ?? '')
  return decryptJwe(jwe, key, file.contentType)
}

function conflictLabels(resources: Record<string, unknown>[], session: PatientSession | null): string[] {
  if (!session) return ['Sign in to a ClinicForce patient workspace before confirmation to compare and save this data.']
  const labels = new Set(session.records.map((record) => record.label.trim().toLocaleLowerCase()).filter(Boolean))
  const conflicts = resources.flatMap((resource) => {
    const code = resource.code as { text?: unknown } | undefined
    const medication = resource.medicationCodeableConcept as { text?: unknown } | undefined
    const value = typeof code?.text === 'string' ? code.text : typeof medication?.text === 'string' ? medication.text : null
    return value && labels.has(value.trim().toLocaleLowerCase()) ? [`${value} matches an existing ClinicForce record.`] : []
  })
  return [...new Set(conflicts)]
}

export async function receiveExternalShl(link: SmartHealthLink, passcode: string | undefined, session: PatientSession | null): Promise<ExternalShlReview> {
  const sourceHost = new URL(link.manifestUrl).host
  const files = link.directFile
    ? [{ contentType: '' as const, location: `${link.manifestUrl}${link.manifestUrl.includes('?') ? '&' : '?'}recipient=${encodeURIComponent('ClinicForce')}` }]
    : await fetchManifest(link.manifestUrl, 'ClinicForce', link.requiresPasscode ? passcode : undefined)
  const resources: Record<string, unknown>[] = []
  const provenance = new Set<string>()
  const apiAccessOffers: string[] = []
  for (const file of files) {
    const decrypted = file.contentType ? await resolveFile(file, link.key) : await decryptJwe(await fetchExternalShlJwe(file.location ?? ''), link.key)
    let decoded: unknown
    try { decoded = JSON.parse(decrypted.plaintext) } catch { throw new Error('The decrypted SHL file is not valid JSON.') }
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
  const importable = resources.filter((resource) => SUPPORTED_FHIR.has(String(resource.resourceType)) && resource.resourceType !== 'Patient')
  const counts = [...importable.reduce((counts, resource) => counts.set(String(resource.resourceType), (counts.get(String(resource.resourceType)) ?? 0) + 1), new Map<string, number>()).entries()]
    .map(([type, count]) => ({ type, count })).sort((left, right) => left.type.localeCompare(right.type))
  return {
    sourceHost, label: link.label, resources: importable, resourceCounts: counts, provenance: [...provenance],
    conflicts: conflictLabels(importable, session), apiAccessOffers,
    canImport: Boolean(importable.length) && !apiAccessOffers.length && Boolean(session),
  }
}
