import type { RequirementEvidence } from '../../services/api/commercial-estimator'

function sourceName(source: RequirementEvidence['sources'][number]): string {
  if (/Stedi/i.test(source.label || '')) return 'Stedi benefit check'
  if (/BCBS PPO manual/i.test(source.label || '')) return 'BCBS PPO manual'
  if (/Payer Plan Matrix/i.test(source.label || '')) return 'Payer plan matrix'
  if (/Portal & Contact/i.test(source.label || '')) return 'Payer contact sheet'
  if (/eCW referral/i.test(source.label || '')) return 'eCW referral record'
  if (source.document_id && /\.pdf$/i.test(source.filename || '')) return 'Clinic contract'
  return source.label || (source.document_id ? 'Clinic Library source' : 'Source not retained')
}

export default function RequirementSourceLink({
  source,
}: {
  source: RequirementEvidence['sources'][number]
}) {
  const href = source.document_id
    ? `/api/staff/library/${encodeURIComponent(source.document_id)}/document?preview=true${source.page ? `#page=${source.page}` : ''}`
    : source.url
  const label = sourceName(source)
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {label}
      {source.page ? ` · p. ${source.page}` : ''}
    </a>
  ) : (
    <small>{label}</small>
  )
}
