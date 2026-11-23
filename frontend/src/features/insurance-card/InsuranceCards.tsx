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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [deletingId, setDeletingId] = useState('')
  const read = useCallback(async (): Promise<Cards> => {
    return request<Cards>(
      `/api/staff/intakes/${encodeURIComponent(intakeId)}/cards`,
      {},
      { timeoutMs: 65_000 },
    )
  }, [intakeId])
  const load = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      setData(await read())
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 401
          ? 'Your clinic session ended. Sign in again to load card previews.'
          : 'Card previews could not be loaded.',
      )
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
      .catch((caught) => {
        if (active)
          setError(
            caught instanceof ApiError && caught.status === 401
              ? 'Your clinic session ended. Sign in again to load card previews.'
              : 'Card preview unavailable. The estimate uses the policy and payer response.',
          )
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
      {data?.retryable && !error && (
        <button type="button" disabled={busy} onClick={() => void load()}>
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
            {card.source === 'ecw_current' ? 'Current eCW card' : 'ClinicForce upload'}
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
        if (event.currentTarget.open && !background && !data && !busy && !error) void load()
      }}
    >
      <summary>Insurance card preview</summary>
      {content}
    </details>
  )
}
