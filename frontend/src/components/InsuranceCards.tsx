import { useCallback, useEffect, useState } from 'react'
import './InsuranceCards.css'
import { request } from '../services/api/client'

type Cards = { cards: Array<{ id: string; title: string; content_type: string; url: string }>; message: string }
export default function InsuranceCards({ intakeId, background = false, inline = false }: { intakeId: string; background?: boolean; inline?: boolean }) {
  const [data, setData] = useState<Cards | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setBusy(true); setError('')
    try { setData(await request<Cards>(`/api/staff/intakes/${encodeURIComponent(intakeId)}/cards`)) }
    catch { setError('Card previews could not be loaded.') }
    finally { setBusy(false) }
  }, [intakeId])
  useEffect(() => {
    if (!background) return
    let active = true
    request<Cards>(`/api/staff/intakes/${encodeURIComponent(intakeId)}/cards`).then(value => { if (active) setData(value) }).catch(() => { if (active) setError('Card preview unavailable. The estimate uses the policy and payer response.') })
    return () => { active = false }
  }, [background, intakeId])
  const content = <>
    {busy && <p role="status">Reading saved cards and eCW card attachments…</p>}
    {error && <p role="alert">{error} <button onClick={() => void load()}>Retry</button></p>}
    {data && !data.cards.length && <p>{inline ? 'No card image was returned. The estimate uses the policy details.' : data.message || 'No saved card. Scan or upload the insurance card to add a preview.'}</p>}{data && !!data.cards.length && data.message && <p>{data.message}</p>}
    {data?.cards.map(card => <figure key={card.id}><figcaption>{card.title}</figcaption>{card.content_type === 'application/pdf' ? <iframe title={card.title} src={card.url} /> : <img src={card.url} alt={card.title} />}<a href={card.url} target="_blank" rel="noreferrer">Open card</a></figure>)}
  </>
  return inline ? <section className="insurance-card-preview"><h3>Insurance card</h3>{content}</section> : <details className="insurance-card-preview" onToggle={event => { if (event.currentTarget.open && !background && !data && !busy && !error) void load() }}><summary>Insurance card preview</summary>{content}</details>
}
