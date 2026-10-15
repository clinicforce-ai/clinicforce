import {
  beginEcwAuthorization,
  disconnectEcw,
  getEcwRecord,
  importEcwRecord,
} from '../../services/api/ecw'
import {
  beginEpicAuthorization,
  disconnectEpic,
  downloadEpicDocument,
  getEpicRecord,
} from '../../services/api/epic'
import { type EpicEndpoint } from '../../services/api/patient-provider-types'
import { messageFrom } from './error-text'
import type { PortalState } from './portal-state'

export function createPortalProviderActions(state: PortalState) {
  const {
    password,
    session,
    setSession,
    setEpicRecord,
    epicEndpoints,
    setEpicEndpoints,
    setEpicQuery,
    setSelectedEpicEndpointId,
    setEpicSearching,
    setEpicDirectoryError,
    ecwPracticeCode,
    setEcwPracticeCode,
    setEcwPracticeCodeError,
    setEcwRecord,
    setBusy,
    setError,
    setNotice,
    epicPopup,
    epicPopupWatch,
    epicConnectionHandled,
    ecwPopup,
    ecwPopupWatch,
    ecwConnectionHandled,
    clearFeedback,
  } = state
  const chooseEpicHospital = (value: string) => {
    const normalized = value.trim().toLocaleLowerCase()
    const endpoint = epicEndpoints.find(
      (candidate) => candidate.name.toLocaleLowerCase() === normalized,
    )
    setEpicQuery(value)
    setSelectedEpicEndpointId(endpoint?.id ?? '')
    setEpicDirectoryError('')
    if (normalized.length < 2) {
      setEpicEndpoints([])
      setEpicSearching(false)
    }
  }

  const selectEpicHospital = (endpoint: EpicEndpoint) => {
    setEpicQuery(endpoint.name)
    setSelectedEpicEndpointId(endpoint.id)
    setEpicEndpoints([])
    setEpicDirectoryError('')
  }

  const connectEpic = async (endpoint: EpicEndpoint) => {
    if (!session) return
    clearFeedback()
    const popup = window.open('', 'clinicforce-epic-authorize', 'popup,width=540,height=760')
    if (!popup) {
      setError(
        'Your browser blocked the secure MyChart window. Allow pop-ups for ClinicForce and try again.',
      )
      return
    }
    epicPopup.current = popup
    epicConnectionHandled.current = false
    setBusy('epic-connect')
    try {
      popup.location.replace(
        await beginEpicAuthorization(session.patient_id, password, endpoint.id, true),
      )
      epicPopupWatch.current = window.setInterval(() => {
        if (!popup.closed) return
        if (epicPopupWatch.current !== null) window.clearInterval(epicPopupWatch.current)
        epicPopupWatch.current = null
        epicPopup.current = null
        if (epicConnectionHandled.current) return
        // A popup may close before its postMessage reaches the opener. Verify
        // the server-side, HttpOnly Epic session before treating that close as
        // a failed authorization; this prevents a successful sign-in from
        // leaving a false red error banner behind.
        epicConnectionHandled.current = true
        setBusy('epic-load')
        void getEpicRecord()
          .then((record) => {
            setEpicRecord(record)
            setError('')
            setNotice(`Live Epic record connected from ${record.source.organization_name}.`)
          })
          .catch(() =>
            setError(
              'Epic authorization was not completed. Close the MyChart window only after sign-in finishes, then try again.',
            ),
          )
          .finally(() => setBusy(null))
      }, 500)
    } catch (caught) {
      popup.close()
      epicPopup.current = null
      setBusy(null)
      setError(messageFrom(caught, 'Unable to start the Epic connection.'))
    }
  }

  const validateEcwPracticeCode = (value: string): string | null => {
    const normalized = value.trim().toUpperCase()
    return /^[A-Z0-9]{6}$/.test(normalized)
      ? null
      : 'Enter the six-character eClinicalWorks practice code (letters and numbers only).'
  }

  const connectEcw = async () => {
    if (!session) return
    const normalizedCode = ecwPracticeCode.trim().toUpperCase()
    const validationError = validateEcwPracticeCode(normalizedCode)
    setEcwPracticeCode(normalizedCode)
    setEcwPracticeCodeError(validationError ?? '')
    if (validationError) return
    clearFeedback()
    const popup = window.open('', 'clinicforce-ecw-authorize', 'popup,width=540,height=760')
    if (!popup) {
      setError(
        'Your browser blocked the secure eClinicalWorks window. Allow pop-ups for ClinicForce and try again.',
      )
      return
    }
    ecwPopup.current = popup
    ecwConnectionHandled.current = false
    setBusy('ecw-connect')
    try {
      popup.location.replace(
        await beginEcwAuthorization(session.patient_id, password, normalizedCode, true),
      )
      ecwPopupWatch.current = window.setInterval(() => {
        if (!popup.closed) return
        if (ecwPopupWatch.current !== null) window.clearInterval(ecwPopupWatch.current)
        ecwPopupWatch.current = null
        ecwPopup.current = null
        if (ecwConnectionHandled.current) return
        ecwConnectionHandled.current = true
        // A secure callback can close before its postMessage reaches this
        // window. Ask the HttpOnly session endpoint once before calling the
        // authorization unsuccessful, as we do for Epic.
        setBusy('ecw-load')
        void getEcwRecord()
          .then((record) => {
            setEcwRecord(record)
            setError('')
            setNotice(
              `eClinicalWorks data is ready to review from ${record.source.organization_name}. Nothing has been imported yet.`,
            )
          })
          .catch(() =>
            setError(
              'eClinicalWorks authorization was not completed. Close the healow window only after sign-in finishes, then try again.',
            ),
          )
          .finally(() => setBusy(null))
      }, 500)
    } catch (caught) {
      popup.close()
      ecwPopup.current = null
      setBusy(null)
      setError(messageFrom(caught, 'Unable to start the eClinicalWorks connection.'))
    }
  }

  const cancelEpicConnection = () => {
    epicPopup.current?.close()
    if (epicPopupWatch.current !== null) window.clearInterval(epicPopupWatch.current)
    epicPopup.current = null
    epicPopupWatch.current = null
    epicConnectionHandled.current = true
    setBusy(null)
    setNotice('Epic connection cancelled. No Epic data was imported.')
  }

  const reloadEcwRecords = async () => {
    clearFeedback()
    setBusy('ecw-load')
    try {
      setEcwRecord(await getEcwRecord())
    } catch (caught) {
      setError(
        messageFrom(
          caught,
          'Unable to reload records. Reconnect Healow if your session has expired.',
        ),
      )
    } finally {
      setBusy(null)
    }
  }

  const importConfirmedEcw = async () => {
    clearFeedback()
    setBusy('ecw-import')
    try {
      const workspace = await importEcwRecord()
      setSession(workspace)
      setEcwRecord(null)
      setNotice(
        'Your confirmed eClinicalWorks data was imported with its eCW/healow source label. The live connection has been closed.',
      )
    } catch (caught) {
      setError(
        messageFrom(
          caught,
          'Unable to import the reviewed eClinicalWorks data. Reconnect healow and try again.',
        ),
      )
    } finally {
      setBusy(null)
    }
  }

  const discardEcwConnection = async () => {
    clearFeedback()
    setBusy('ecw-disconnect')
    try {
      await disconnectEcw()
      setEcwRecord(null)
      setNotice('eClinicalWorks disconnected. No reviewed data was imported.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to discard the eClinicalWorks connection.'))
    } finally {
      setBusy(null)
    }
  }

  const refreshEpic = async () => {
    clearFeedback()
    setBusy('epic-load')
    try {
      const record = await getEpicRecord()
      setEpicRecord(record)
      setNotice(`Live Epic data refreshed from ${record.source.organization_name}.`)
    } catch (caught) {
      setEpicRecord(null)
      setError(messageFrom(caught, 'Your Epic session needs to be reconnected.'))
    } finally {
      setBusy(null)
    }
  }

  const endEpicSession = async () => {
    clearFeedback()
    try {
      await disconnectEpic()
      setEpicRecord(null)
      setEpicEndpoints([])
      setEpicQuery('')
      setSelectedEpicEndpointId('')
      setEpicDirectoryError('')
      setNotice('Epic disconnected. ClinicForce no longer has live access to this record.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to end the Epic connection.'))
    }
  }

  const openEpicDocument = async (documentId: string) => {
    clearFeedback()
    setBusy('epic-document')
    try {
      const url = URL.createObjectURL(await downloadEpicDocument(documentId))
      window.open(url, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to open this live Epic PDF.'))
    } finally {
      setBusy(null)
    }
  }

  return {
    chooseEpicHospital,
    selectEpicHospital,
    connectEpic,
    validateEcwPracticeCode,
    connectEcw,
    cancelEpicConnection,
    reloadEcwRecords,
    importConfirmedEcw,
    discardEcwConnection,
    refreshEpic,
    endEpicSession,
    openEpicDocument,
  }
}
