import type { View } from './types'

export function parseEntryRoute(location: Pick<Location, 'pathname' | 'search'>) {
  const query = new URLSearchParams(location.search)
  const parts = location.pathname.split('/').filter(Boolean)
  const invitation = query.get('join') || (parts[0] === 'join' ? parts[1] : '') || ''
  const clinic =
    query.get('clinic') ||
    (/^[a-z][a-z0-9-]{2,39}$/.test(parts[0] || '') &&
    !['api', 'assets', 'join', 'upload', 'staff', 'patient', 'manifest'].includes(parts[0]) &&
    parts.length <= 2
      ? parts[0]
      : '') ||
    ''
  const upload = query.get('upload') || (clinic && parts.length === 2 ? parts[1] : '') || ''
  const view: View =
    query.get('staff') === 'create'
      ? 'staff-create'
      : query.has('join') || invitation || parts[0] === 'join'
        ? 'staff-join'
        : query.has('staff') || parts[0] === 'staff'
          ? 'staff'
          : query.has('patient') || parts[0] === 'patient'
            ? 'patient'
            : clinic || parts[0] === 'upload' || query.has('upload')
              ? 'patient-upload'
              : 'landing'
  return { view, clinic, upload, invitation }
}
