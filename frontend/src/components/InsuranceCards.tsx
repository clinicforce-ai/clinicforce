import { useCallback, useEffect, useState } from 'react'
import './InsuranceCards.css'
import { request } from '../services/api/client'
import { getStaffIntake } from '../services/api/staff'

type Cards = { cards: Array<{ id: string; title: string; content_type: string; url: string }>; message: string }
export default function InsuranceCards({ intakeId, background = false, inline = false, savedOnly = false }: { intakeId: string; background?: boolean; inline?: boolean; savedOnly?: boolean }) {
  const [data, setData] = useState<Cards | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const read = useCallback(async (): Promise<Cards> => {
    if (!savedOnly) return request<Cards>(`/api/staff/intakes/${encodeURIComponent(intakeId)}/cards`)
    const intake = await getStaffIntake(intakeId)
    return { cards: intake.documents.map(doc => ({ id: doc.id, title: doc.filename, content_type: doc.content_type,
      url: `/api/staff/intakes/${encodeURIComponent(intakeId)}/documents/${encodeURIComponent(doc.id)}` })),
      message: 'Original card files saved with this intake. eCW card previews are not archived with the estimate.' }
  }, [intakeId, savedOnly])
  const load = useCallback(async () => {
    setBusy(true); setError('')
    try { setData(await read()) }
    catch { setError('Card previews could not be loaded.') }
    finally { setBusy(false) }
  }, [read])
  useEffect(() => {
    if (!background) return
    let active = true
    read().then(value => { if (active) setData(value) }).catch(() => { if (active) setError('Card preview unavailable. The estimate uses the policy and payer response.') })
    return () => { active = false }
  }, [background, read])
  const content = <>
    {busy && <p role="status">Reading saved cards and eCW card attachments…</p>}
    {error && <p role="alert">{error} {!savedOnly && <button onClick={() => void load()}>Retry</button>}</p>}
    {data && !data.cards.length && <p>{data.message || 'No card preview is available. Card previews are optional for estimates.'}</p>}{data && !!data.cards.length && data.message && <p>{data.message}</p>}
    {data?.cards.map(card => <figure key={card.id}><figcaption>{card.title}</figcaption>{card.content_type === 'application/pdf' ? <iframe title={card.title} src={card.url} /> : <img src={card.url} alt={card.title} />}<a href={card.url} target="_blank" rel="noreferrer">Open card</a></figure>)}
  </>
  return inline ? <section className="insurance-card-preview"><h3>Insurance card</h3>{content}</section> : <details className="insurance-card-preview" onToggle={event => { if (event.currentTarget.open && !background && !data && !busy && !error) void load() }}><summary>Insurance card preview</summary>{content}</details>
}
