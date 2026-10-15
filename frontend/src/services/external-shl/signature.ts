import { base64UrlBytes, bufferCopy, inflateRaw, utf8 } from './crypto'

type IssuerJwk = JsonWebKey & { kid?: string }

async function fetchJwks(issuer: string): Promise<IssuerJwk[]> {
  let issuerUrl: URL
  try {
    issuerUrl = new URL(issuer)
  } catch {
    throw new Error('The SMART Health Card has an invalid issuer.')
  }
  if (issuerUrl.protocol !== 'https:' || issuerUrl.username || issuerUrl.password)
    throw new Error('The SMART Health Card issuer must use HTTPS.')
  const jwksUrl = new URL('/.well-known/jwks.json', issuerUrl).toString()
  let response: Response
  try {
    response = await fetch(jwksUrl, {
      credentials: 'omit',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
    })
  } catch {
    throw new Error(
      'The SMART Health Card issuer could not provide verification keys to this browser.',
    )
  }
  if (!response.ok)
    throw new Error('The SMART Health Card issuer did not provide verification keys.')
  const text = await response.text()
  if (text.length > 1_000_000)
    throw new Error('The SMART Health Card issuer returned too many verification keys.')
  let payload: unknown
  try {
    payload = JSON.parse(text)
  } catch {
    throw new Error('The SMART Health Card issuer returned invalid verification keys.')
  }
  const keys =
    payload && typeof payload === 'object' ? (payload as { keys?: unknown }).keys : undefined
  if (!Array.isArray(keys))
    throw new Error('The SMART Health Card issuer returned no verification keys.')
  return keys.filter((key): key is IssuerJwk => Boolean(key && typeof key === 'object'))
}

export async function verifySmartHealthCard(jws: string): Promise<Record<string, unknown>> {
  const parts = jws.split('.')
  if (parts.length !== 3) throw new Error('The SMART Health Card signature is malformed.')
  let header: Record<string, unknown>
  let payloadBytes = base64UrlBytes(parts[1])
  try {
    header = JSON.parse(new TextDecoder().decode(base64UrlBytes(parts[0]))) as Record<
      string,
      unknown
    >
  } catch {
    throw new Error('The SMART Health Card header is invalid.')
  }
  if (header.alg !== 'ES256' || typeof header.kid !== 'string')
    throw new Error('This SMART Health Card uses an unsupported signature algorithm.')
  if (header.zip === 'DEF') payloadBytes = await inflateRaw(payloadBytes)
  let payload: Record<string, unknown>
  try {
    payload = JSON.parse(new TextDecoder().decode(payloadBytes)) as Record<string, unknown>
  } catch {
    throw new Error('The SMART Health Card payload is invalid.')
  }
  if (typeof payload.iss !== 'string')
    throw new Error('The SMART Health Card does not identify its issuer.')
  const jwk = (await fetchJwks(payload.iss)).find(
    (candidate) =>
      candidate.kid === header.kid && candidate.kty === 'EC' && candidate.crv === 'P-256',
  )
  if (!jwk) throw new Error('The SMART Health Card issuer did not provide the signing key.')
  try {
    const publicKey = await crypto.subtle.importKey(
      'jwk',
      jwk,
      { name: 'ECDSA', namedCurve: 'P-256' },
      false,
      ['verify'],
    )
    const valid = await crypto.subtle.verify(
      { name: 'ECDSA', hash: 'SHA-256' },
      publicKey,
      bufferCopy(base64UrlBytes(parts[2])),
      bufferCopy(utf8(`${parts[0]}.${parts[1]}`)),
    )
    if (!valid) throw new Error('invalid')
  } catch {
    throw new Error('The SMART Health Card signature could not be verified.')
  }
  return payload
}
