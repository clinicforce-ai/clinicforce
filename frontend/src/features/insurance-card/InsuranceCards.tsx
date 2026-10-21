import { useCallback, useEffect, useState } from 'react'
import './InsuranceCards.css'
import { request } from '../../services/api/client'

type Cards = {
  cards: Array<{ id: string; title: string; content_type: string; url: string; source?: string }>
  message: string
}
export default function InsuranceCards({
  intakeId,
  background = false,
  inline = false,
  historicalEstimate = false,
}: {
  intakeId: string
  background?: boolean
  inline?: boolean
  historicalEstimate?: boolean
}) {
  const [data, setData] = useState<Cards | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const read = useCallback(async (): Promise<Cards> => {
    return request<Cards>(`/api/staff/intakes/${encodeURIComponent(intakeId)}/cards`)
  }, [intakeId])
  const load = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      setData(await read())
    } catch {
      setError('Card previews could not be loaded.')
    } finally {
      setBusy(false)
    }
  }, [read])
  useEffect(() => {
    if (!background) return
    let active = true
    read()
      .then((value) => {
        if (active) setData(value)
      })
      .catch(() => {
        if (active)
          setError('Card preview unavailable. The estimate uses the policy and payer response.')
      })
    return () => {
      active = false
    }
  }, [background, read])
  const content = (
    <>
      {busy && <p role="status">Reading saved cards and eCW card attachments…</p>}
      {error && (
        <p role="alert">
          {error} <button onClick={() => void load()}>Retry</button>
        </p>
      )}
      {data && !data.cards.length && (
        <p>
          {data.message ||
            'No card preview is available. Card previews are optional for estimates.'}
        </p>
      )}
      {data && !!data.cards.length && data.message && <p>{data.message}</p>}
      {historicalEstimate && data?.cards.some((card) => card.source === 'ecw_current') && (
        <p>
          Current eCW card preview; it may have changed since this estimate and was not used to
          recalculate it.
        </p>
      )}
      {data?.cards.map((card) => (
        <figure key={`${card.source || 'card'}:${card.id}`}>
          <figcaption>
            {card.title} ·{' '}
            {card.source === 'ecw_current' ? 'Current eCW card' : 'ClinicForce upload'}
          </figcaption>
          {card.content_type === 'application/pdf' ? (
            <iframe title={card.title} src={card.url} />
          ) : (
            <img src={card.url} alt={card.title} />
          )}
          <a href={card.url} target="_blank" rel="noreferrer">
            Open card
          </a>
        </figure>
      ))}
    </>
  )
  return inline ? (
    <section className="insurance-card-preview">
      <h3>Insurance card</h3>
      {content}
    </section>
  ) : (
    <details
      className="insurance-card-preview"
      onToggle={(event) => {
        if (event.currentTarget.open && !background && !data && !busy && !error) void load()
      }}
    >
      <summary>Insurance card preview</summary>
      {content}
    </details>
  )
}
