export interface SmartHealthLink {
  manifestUrl: string
  key: string
  label?: string
  expiresAt?: number
  flags: string
  requiresPasscode: boolean
  directFile: boolean
  legacyPayloadEncoding: boolean
}

const BASE64URL = /^[A-Za-z0-9_-]+$/
const LEGACY_PAYLOAD = /^[A-Za-z0-9+/_-]+={1,2}$/
const KNOWN_FLAGS = new Set(['L', 'P', 'U'])

function base64UrlBytes(value: string): Uint8Array {
  if (!BASE64URL.test(value))
    throw new Error('The Smart Health Link payload is not base64url encoded.')
  const padded =
    value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (value.length % 4)) % 4)
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0))
}

function shlinkFromScannedValue(value: string): string {
  const trimmed = value.trim()
  if (/^shlink:\//i.test(trimmed)) return trimmed
  try {
    const viewer = new URL(trimmed)
    if (viewer.hash && /^#shlink:\//i.test(viewer.hash)) return viewer.hash.slice(1)
  } catch {
    /* The bare shlink URI is handled above. */
  }
  throw new Error('Please scan a QR code containing a Smart Health Link.')
}

function decodeShlPayload(encoded: string): { bytes: Uint8Array; legacyPayloadEncoding: boolean } {
  if (BASE64URL.test(encoded))
    return { bytes: base64UrlBytes(encoded), legacyPayloadEncoding: false }

  // Some deployed generators use browser btoa() and leave its trailing padding
  // in the QR. SHL v1 requires unpadded base64url, so ClinicForce never emits
  // this form. Accept this narrowly defined legacy envelope for interoperability;
  // all URL, key, JWE, and FHIR validation remains strict below.
  if (!LEGACY_PAYLOAD.test(encoded))
    throw new Error('The Smart Health Link payload is not base64url encoded.')
  const unpadded = encoded.replace(/=+$/, '')
  if (unpadded.length % 4 === 1)
    throw new Error('The Smart Health Link payload is not valid base64.')
  const normalized = unpadded.replace(/\+/g, '-').replace(/\//g, '_')
  return { bytes: base64UrlBytes(normalized), legacyPayloadEncoding: true }
}

function decodePayload(encoded: string): SmartHealthLink {
  let decoded: { bytes: Uint8Array; legacyPayloadEncoding: boolean }
  let payload: unknown
  try {
    decoded = decodeShlPayload(encoded)
    payload = JSON.parse(new TextDecoder().decode(decoded.bytes))
  } catch {
    throw new Error('This QR code does not contain a readable Smart Health Link payload.')
  }
  if (!payload || typeof payload !== 'object')
    throw new Error('This Smart Health Link payload is invalid.')
  const value = payload as Record<string, unknown>
  if (value.v !== undefined && value.v !== 1)
    throw new Error('This Smart Health Link uses a version ClinicForce does not support.')
  if (typeof value.url !== 'string' || value.url.length > 128)
    throw new Error('This Smart Health Link has an invalid manifest URL.')
  let manifest: URL
  try {
    manifest = new URL(value.url)
  } catch {
    throw new Error('This Smart Health Link has an invalid manifest URL.')
  }
  if (manifest.protocol !== 'https:' || manifest.username || manifest.password)
    throw new Error('Smart Health Links must use an HTTPS manifest URL.')
  if (
    typeof value.key !== 'string' ||
    value.key.length !== 43 ||
    !BASE64URL.test(value.key) ||
    base64UrlBytes(value.key).length !== 32
  ) {
    throw new Error('This Smart Health Link has an invalid decryption key.')
  }
  if (value.label !== undefined && (typeof value.label !== 'string' || value.label.length > 80))
    throw new Error('This Smart Health Link has an invalid label.')
  if (value.exp !== undefined && (!Number.isSafeInteger(value.exp) || (value.exp as number) <= 0))
    throw new Error('This Smart Health Link has an invalid expiry.')
  if (typeof value.exp === 'number' && value.exp <= Math.floor(Date.now() / 1000))
    throw new Error('This Smart Health Link has expired. Ask the issuer for a new code.')
  const flags = value.flag === undefined ? '' : value.flag
  if (
    typeof flags !== 'string' ||
    !/^[A-Z]*$/.test(flags) ||
    [...flags].some((flag, index) => flags.indexOf(flag) !== index)
  ) {
    throw new Error('This Smart Health Link has invalid flags.')
  }
  if (flags.includes('P') && flags.includes('U'))
    throw new Error('This Smart Health Link has incompatible passcode and direct-file flags.')
  // SHL v1 directs receivers to ignore future safe-to-ignore flags. We still
  // retain them for disclosure in the confirmation screen.
  void [...flags].filter((flag) => !KNOWN_FLAGS.has(flag))
  return {
    manifestUrl: manifest.toString(),
    key: value.key,
    label: typeof value.label === 'string' ? value.label : undefined,
    expiresAt: typeof value.exp === 'number' ? value.exp : undefined,
    flags,
    requiresPasscode: flags.includes('P'),
    directFile: flags.includes('U'),
    legacyPayloadEncoding: decoded.legacyPayloadEncoding,
  }
}

export function parseSmartHealthLink(value: string): SmartHealthLink {
  const match = shlinkFromScannedValue(value).match(/^shlink:\/([A-Za-z0-9+/_-]+={0,2})$/i)
  if (!match) throw new Error('This Smart Health Link has an invalid URI format.')
  return decodePayload(match[1])
}

export function isClinicforceIssuedLink(link: SmartHealthLink): boolean {
  return new URL(link.manifestUrl).origin === window.location.origin
}

export function manifestIdFromUrl(manifestUrl: string): string {
  const match = new URL(manifestUrl).pathname.match(
    /\/manifest\/([A-Za-z0-9_-]{43}|[0-9a-f-]{36})$/i,
  )
  if (!match)
    throw new Error('This Smart Health Link does not identify a valid ClinicForce shared record.')
  return match[1]
}
