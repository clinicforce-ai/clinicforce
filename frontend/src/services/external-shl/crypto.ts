export const MAX_DECRYPTED_BYTES = 5 * 1024 * 1024
export const SUPPORTED_FHIR = new Set([
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

export function base64UrlBytes(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]*$/.test(value)) throw new Error('The issuer supplied invalid base64url data.')
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0))
}

export function utf8(value: string): Uint8Array { return new TextEncoder().encode(value) }

export function combine(left: Uint8Array, right: Uint8Array): Uint8Array {
  const output = new Uint8Array(left.byteLength + right.byteLength)
  output.set(left)
  output.set(right, left.byteLength)
  return output
}

export function bufferCopy(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength)
  copy.set(value)
  return copy.buffer
}

export async function readStream(stream: ReadableStream<Uint8Array>): Promise<Uint8Array> {
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

export async function inflateRaw(value: Uint8Array): Promise<Uint8Array> {
  if (!('DecompressionStream' in window)) throw new Error('This browser cannot open the issuer’s compressed Smart Health Link file.')
  const stream = new Blob([bufferCopy(value)]).stream().pipeThrough(new DecompressionStream('deflate-raw'))
  return readStream(stream)
}

export interface DecryptedFile { contentType: string; plaintext: string }

export async function decryptJwe(compact: string, key: string, expectedContentType?: string): Promise<DecryptedFile> {
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
