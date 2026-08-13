export interface SmartHealthLink { manifestUrl: string; key: string; label?: string }

function decodePayload(encoded: string): SmartHealthLink {
  const padded = encoded.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - encoded.length % 4) % 4)
  const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0))
  const payload: unknown = JSON.parse(new TextDecoder().decode(bytes))
  if (!payload || typeof payload !== 'object' || !('url' in payload) || !('key' in payload) || typeof payload.url !== 'string' || typeof payload.key !== 'string') throw new Error('This QR code is not a valid Smart Health Link.')
  return { manifestUrl: payload.url, key: payload.key, label: 'label' in payload && typeof payload.label === 'string' ? payload.label : undefined }
}

export function parseSmartHealthLink(value: string): SmartHealthLink {
  const match = value.trim().match(/^shlink:\/([^/?#]+)$/i)
  if (!match) throw new Error('Please scan a QR code containing a shlink: URI.')
  return decodePayload(match[1])
}

export function manifestIdFromUrl(manifestUrl: string): string {
  const match = new URL(manifestUrl).pathname.match(/\/manifest\/([0-9a-f-]{36})$/i)
  if (!match) throw new Error('This Smart Health Link does not identify a valid shared record.')
  return match[1]
}
