import ExternalImportComplete from './ExternalImportComplete'
import ExternalRecordReview from './ExternalRecordReview'
import ExternalIssuerConfirm from './ExternalIssuerConfirm'
import { useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../../services/api/client'
import { getCurrentPatientSession } from '../../services/api/patient-session'
import { importExternalShl } from '../../services/api/external'
import { type PatientSession } from '../../services/api/patient-session-types'
import { receiveExternalShl, type ExternalShlReview } from '../../services/external-shl'
import type { SmartHealthLink } from '../../services/shl'

interface ExternalShlReceiverProps {
  link: SmartHealthLink
  onCancel: () => void
  onImported: () => void
}

function messageFrom(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export default function ExternalShlReceiver({
  link,
  onCancel,
  onImported,
}: ExternalShlReceiverProps) {
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [review, setReview] = useState<ExternalShlReview | null>(null)
  const [session, setSession] = useState<PatientSession | null>(null)
  const [error, setError] = useState('')
  const [imported, setImported] = useState(false)
  const host = new URL(link.manifestUrl).host

  useEffect(() => {
    let active = true
    void getCurrentPatientSession()
      .then((current) => {
        if (active) setSession(current)
      })
      .catch(() => {
        if (active) setSession(null)
      })
    return () => {
      active = false
    }
  }, [])

  const retrieve = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (link.requiresPasscode && !pin.trim()) {
      setError('Ask the sharing patient for this link’s passcode.')
      return
    }
    setBusy(true)
    setError('')
    try {
      setReview(await receiveExternalShl(link, link.requiresPasscode ? pin : undefined, session))
    } catch (caught) {
      setError(messageFrom(caught, 'ClinicForce could not open this external Smart Health Link.'))
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!review || !review.canImport) return
    setBusy(true)
    setError('')
    try {
      await importExternalShl(
        {
          resourceType: 'Bundle',
          type: 'collection',
          entry: review.resources.map((resource) => ({ resource })),
        },
        review.sourceHost,
      )
      setImported(true)
    } catch (caught) {
      const fallback =
        caught instanceof ApiError && caught.status === 401
          ? 'Sign in to the ClinicForce patient workspace that should receive this data, then scan again.'
          : 'ClinicForce could not save the confirmed external records.'
      setError(messageFrom(caught, fallback))
    } finally {
      setBusy(false)
    }
  }

  if (imported) return <ExternalImportComplete onImported={onImported} />

  if (review)
    return (
      <ExternalRecordReview
        review={review}
        session={session}
        error={error}
        busy={busy}
        onCancel={onCancel}
        onSave={() => void save()}
      />
    )

  return (
    <ExternalIssuerConfirm
      link={link}
      host={host}
      pin={pin}
      onPinChange={setPin}
      busy={busy}
      error={error}
      onCancel={onCancel}
      onRetrieve={retrieve}
    />
  )
}
