import { useCallback, useEffect, useState } from 'react'
import './InsuranceCards.css'
import { ApiError, request } from '../../services/api/client'
import { deleteStaffCardDocument } from '../../services/api/staff-intake'

type Cards = {
  cards: Array<{
    id: string
    intake_id?: string
    title: string
    content_type: string
    url: string
    source?: string
  }>
  message: string
  retryable?: boolean
  checked_at?: string | null
}
type EcwStatus = Cards & {
  status: 'queued' | 'running' | 'ready' | 'failed' | 'unavailable'
  checked_at: string | null
}
export default function InsuranceCards({
  intakeId,
  background = false,
  inline = false,
  historicalEstimate = false,
  onCardDeleted,
}: {
  intakeId: string
  background?: boolean
  inline?: boolean
  historicalEstimate?: boolean
  onCardDeleted?: (cleanupPending: boolean) => Promise<void>
}) {
  const [data, setData] = useState<Cards | null>(null)
  const [busy, setBusy] = useState(background)
  const [error, setError] = useState('')
  const [deletingId, setDeletingId] = useState('')
  const [enabled, setEnabled] = useState(background)
  const [generation, setGeneration] = useState(0)
  const read = useCallback(async (): Promise<Cards> => {
    return request<Cards>(
      `/api/staff/intakes/${encodeURIComponent(intakeId)}/cards?saved_only=true`,
      {},
      { timeoutMs: 10_000 },
    )
  }, [intakeId])
  const statusUrl = `/api/staff/intakes/${encodeURIComponent(intakeId)}/ecw-cards/status`
  const refreshUrl = `/api/staff/intakes/${encodeURIComponent(intakeId)}/ecw-cards/refresh`
  const load = useCallback(() => {
    setBusy(true)
    setError('')
    setEnabled(true)
    setGeneration((value) => value + 1)
  }, [])
  useEffect(() => {
    if (!enabled) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    read()
      .then((value) => {
        if (active)
          setData((current) => ({
            ...value,
            cards: [
              ...value.cards,
              ...(current?.cards.filter((card) => card.source === 'ecw_current') || []),
            ],
            message: current?.message || value.message,
            retryable: current?.retryable || value.retryable,
            checked_at: current?.checked_at,
          }))
      })
      .catch((caught) => {
        if (active)
          setError(
            caught instanceof ApiError && caught.status === 401
              ? 'Your clinic session ended. Sign in again to load card previews.'
              : 'Card preview unavailable. The estimate uses the policy and payer response.',
          )
      })
      .finally(() => {
        if (active) setBusy(false)
      })
    const poll = async () => {
      try {
        const value = await request<EcwStatus>(statusUrl, {}, { timeoutMs: 10_000 })
        if (!active) return
        setData((current) => ({
          cards: [
            ...(current?.cards.filter((card) => card.source !== 'ecw_current') || []),
            ...value.cards,
          ],
          message: value.message,
          retryable: value.retryable,
          checked_at: value.checked_at,
        }))
        if (value.status === 'queued' || value.status === 'running')
          timer = setTimeout(() => void poll(), 2_000)
      } catch (caught) {
        if (active)
          setError(
            caught instanceof ApiError && caught.status === 401
              ? 'Your clinic session ended. Sign in again to load card previews.'
              : 'The eCW card search status could not be read.',
          )
      }
    }
    void poll()
    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [enabled, generation, read, statusUrl])
  const content = (
    <>
      {busy && <p role="status">Reading saved cards…</p>}
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
      {data?.retryable && !error && (
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            try {
              await request<EcwStatus>(refreshUrl, { method: 'POST' }, { timeoutMs: 10_000 })
              load()
            } catch (caught) {
              setError(
                caught instanceof ApiError && caught.status === 401
                  ? 'Your clinic session ended. Sign in again to load card previews.'
                  : 'The eCW card refresh could not be started.',
              )
            }
          }}
        >
          Retry eCW card preview
        </button>
      )}
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
            {card.source === 'ecw_current'
              ? `eCW card${data.checked_at ? ` · checked ${new Date(data.checked_at).toLocaleString()}` : ''}`
              : 'ClinicForce upload'}
          </figcaption>
          <div className="insurance-card-actions">
            <a className="insurance-card-open" href={card.url} target="_blank" rel="noreferrer">
              Open card
            </a>
            {onCardDeleted && card.source === 'clinicforce_upload' && card.intake_id && (
              <button
                type="button"
                className="insurance-card-delete"
                disabled={!!deletingId}
                onClick={async () => {
                  if (
                    !window.confirm(
                      `Delete ${card.title} from this clinic patient record? The saved file will be removed from storage. This cannot be undone.`,
                    )
                  )
                    return
                  setDeletingId(card.id)
                  setError('')
                  try {
                    const result = await deleteStaffCardDocument(card.intake_id!, card.id)
                    setData(
                      (current) =>
                        current && {
                          ...current,
                          cards: current.cards.filter((item) => item.id !== card.id),
                        },
                    )
                    try {
                      await onCardDeleted(result.cleanup_pending)
                    } catch {
                      setError(
                        'Card removed, but the patient profile could not refresh. Reload the page.',
                      )
                    }
                  } catch (caught) {
                    setError(
                      caught instanceof Error ? caught.message : 'The card could not be deleted.',
                    )
                  } finally {
                    setDeletingId('')
                  }
                }}
              >
                {deletingId === card.id ? 'Deleting card…' : 'Delete insurance card'}
              </button>
            )}
          </div>
          {card.content_type === 'application/pdf' ? (
            <iframe title={card.title} src={card.url} />
          ) : (
            <img src={card.url} alt={card.title} />
          )}
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
        if (event.currentTarget.open && !background && !enabled) load()
      }}
    >
      <summary>Insurance card preview</summary>
      {content}
    </details>
  )
}
