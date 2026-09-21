import type { PatientSession } from './patient-models'
import { ApiError, assertWorkspace, json, request } from './client'

export interface ShlManifestFile {
  contentType: 'application/fhir+json' | 'application/smart-health-card' | 'application/smart-api-access'
  embedded?: string
  location?: string
}

const EXTERNAL_SHL_MAX_BYTES = 5 * 1024 * 1024

async function readExternalText(response: Response): Promise<string> {
  const declaredLength = Number(response.headers.get('content-length') ?? '0')
  if (Number.isFinite(declaredLength) && declaredLength > EXTERNAL_SHL_MAX_BYTES) {
    throw new ApiError(413, 'The external Smart Health Link response exceeds the 5 MB safety limit.')
  }
  if (!response.body) return response.text()
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let length = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    length += value.byteLength
    if (length > EXTERNAL_SHL_MAX_BYTES) {
      await reader.cancel()
      throw new ApiError(413, 'The external Smart Health Link response exceeds the 5 MB safety limit.')
    }
    chunks.push(value)
  }
  const content = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) { content.set(chunk, offset); offset += chunk.byteLength }
  return new TextDecoder().decode(content)
}

async function externalFetch(url: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 20_000)
  try {
    return await fetch(url, {
      ...init,
      credentials: 'omit',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError(408, 'The external issuer did not respond within 20 seconds.')
    throw new ApiError(0, 'The external issuer could not be reached from this browser. It may not allow cross-origin access.')
  } finally {
    window.clearTimeout(timeout)
  }
}

function externalHttpsUrl(value: string, label: string): URL {
  let url: URL
  try { url = new URL(value) } catch { throw new ApiError(502, `The issuer supplied an invalid ${label} URL.`) }
  if (url.protocol !== 'https:' || url.username || url.password) throw new ApiError(502, `The issuer supplied a non-HTTPS ${label} URL.`)
  return url
}

export async function fetchManifest(
  manifestUrl: string,
  recipient: string,
  passcode: string | undefined,
  embeddedLengthMax = 1_000_000,
): Promise<ShlManifestFile[]> {
  const url = externalHttpsUrl(manifestUrl, 'manifest')
  const response = await externalFetch(url.toString(), {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ recipient, ...(passcode ? { passcode: passcode.trim() } : {}), embeddedLengthMax }),
  })
  const text = await readExternalText(response)
  let payload: unknown
  try { payload = JSON.parse(text) } catch { throw new ApiError(response.status || 502, 'The external issuer returned an invalid SHL manifest.') }
  if (!response.ok) {
    if (response.status === 401 && payload && typeof payload === 'object' && 'remainingAttempts' in payload) {
      const remaining = (payload as { remainingAttempts?: unknown }).remainingAttempts
      throw new ApiError(401, `The passcode was rejected.${typeof remaining === 'number' ? ` ${remaining} attempt${remaining === 1 ? '' : 's'} remain.` : ''}`)
    }
    if (response.status === 404) throw new ApiError(404, 'This Smart Health Link is no longer active. Ask the issuer for a new code.')
    throw new ApiError(response.status || 502, 'The external issuer rejected the Smart Health Link request.')
  }
  const files = payload && typeof payload === 'object' && 'files' in payload ? (payload as { files?: unknown }).files : undefined
  if (!Array.isArray(files) || !files.length) throw new ApiError(502, 'The external issuer returned no SHL files.')
  return files.map((file) => {
    if (!file || typeof file !== 'object') throw new ApiError(502, 'The external issuer returned an invalid SHL file.')
    const item = file as Record<string, unknown>
    const contentType = typeof item.contentType === 'string' ? item.contentType.split(';', 1)[0].trim().toLowerCase() : ''
    if (contentType !== 'application/fhir+json' && contentType !== 'application/smart-health-card' && contentType !== 'application/smart-api-access') {
      throw new ApiError(415, 'This Smart Health Link contains an unsupported file type.')
    }
    if (typeof item.embedded !== 'string' && typeof item.location !== 'string') throw new ApiError(502, 'The external issuer returned a file without encrypted content.')
    if (typeof item.location === 'string') externalHttpsUrl(item.location, 'file location')
    return { contentType: contentType as ShlManifestFile['contentType'], ...(typeof item.embedded === 'string' ? { embedded: item.embedded } : {}), ...(typeof item.location === 'string' ? { location: item.location } : {}) }
  })
}

export async function fetchExternalShlJwe(location: string): Promise<string> {
  const response = await externalFetch(externalHttpsUrl(location, 'file location').toString(), { method: 'GET', headers: { Accept: 'application/jose' } })
  const jwe = await readExternalText(response)
  if (!response.ok) throw new ApiError(response.status || 502, 'The external encrypted file could not be retrieved.')
  return jwe
}

export async function importExternalShl(bundle: Record<string, unknown>, sourceHost: string): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/external-shl-import', json({ source_host: sourceHost, bundle })))
}
