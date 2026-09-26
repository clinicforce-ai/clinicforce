import { useState } from 'react'
import { request } from '../services/api/client'

type Cards = { cards: Array<{ id: string; title: string; content_type: string; url: string }>; message: string }
export default function InsuranceCards({ intakeId }: { intakeId: string }) {
  const [data, setData] = useState<Cards | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function load() {
    setBusy(true); setError('')
    try { setData(await request<Cards>(`/api/staff/intakes/${encodeURIComponent(intakeId)}/cards`)) }
    catch { setError('Card previews could not be loaded.') }
    finally { setBusy(false) }
  }
  return <details className="insurance-card-preview" onToggle={event => { if (event.currentTarget.open && !data && !busy && !error) void load() }}><summary>Insurance card preview</summary>
    {busy && <p role="status">Reading saved cards and eCW card attachments…</p>}
    {error && <p role="alert">{error} <button onClick={() => void load()}>Retry</button></p>}
    {data?.message && <p>{data.message}</p>}{data && !data.cards.length && <p>No card preview is available from the connected sources.</p>}
    {data?.cards.map(card => <figure key={card.id}><figcaption>{card.title}</figcaption>{card.content_type === 'application/pdf' ? <iframe title={card.title} src={card.url} /> : <img src={card.url} alt={card.title} />}<a href={card.url} target="_blank" rel="noreferrer">Open card</a></figure>)}
  </details>
}
