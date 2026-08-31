import { FileHeart } from 'lucide-react'
import type { SourceCitation } from '../../services/api'

export function SourceNote({ citation }: { citation?: SourceCitation | null }) {
  if (!citation) return <span className="source-note source-note-manual">Patient-entered</span>
  if (citation.source_type === 'epic') return <span className="source-note"><FileHeart aria-hidden="true" /> Epic / MyChart{citation.organization_name ? ` · ${citation.organization_name}` : ''}</span>
  const label = <><FileHeart aria-hidden="true" /> {citation.source_filename} · p. {citation.page_number}</>
  return citation.download_url ? <a className="source-note source-link" href={citation.download_url} download aria-label={`Download source PDF ${citation.source_filename}, page ${citation.page_number}`}>{label}</a> : <span className="source-note">{label}</span>
}
