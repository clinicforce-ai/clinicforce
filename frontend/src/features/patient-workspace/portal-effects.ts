import { useEffect } from 'react'
import { animate, stagger } from 'animejs'
import { ApiError } from '../../services/api/client'
import { getCurrentPatientSession } from '../../services/api/patient-session'
import { getEcwRecord } from '../../services/api/ecw'
import { getEpicRecord, searchEpicEndpoints } from '../../services/api/epic'
import { getIdmeStatus } from '../../services/api/idme'
import { getPatientProfile } from '../../services/api/patient-profile'
import { messageFrom } from './error-text'
import { idmeResultFromLocation, type PortalState } from './portal-state'

export function usePortalEffects(state: PortalState) {
  const {
    session,
    setSession,
    setIdmeStatus,
    setIdmeStatusLoading,
    setEpicRecord,
    setEpicEndpoints,
    epicQuery,
    setSelectedEpicEndpointId,
    setEpicSearching,
    setEpicDirectoryError,
    setEcwRecord,
    workspaceTab,
    setBusy,
    setRestoringSession,
    setError,
    setNotice,
    workspacePanel,
    epicPopup,
    epicPopupWatch,
    epicConnectionHandled,
    ecwPopup,
    ecwPopupWatch,
    ecwConnectionHandled,
    applyProfile,
  } = state
  useEffect(() => {
    let active = true
    const query = new URLSearchParams(window.location.search)
    const idmeResult = idmeResultFromLocation()
    if (idmeResult) {
      query.delete('idme')
      if (!query.has('patient')) query.set('patient', '')
      window.history.replaceState({}, '', `${window.location.pathname}?${query.toString()}`)
    }
    void getIdmeStatus()
      .then((status) => {
        if (active) setIdmeStatus(status)
      })
      .catch(() => {
        if (active) setIdmeStatus(null)
      })
      .finally(() => {
        if (active) setIdmeStatusLoading(false)
      })
    void getCurrentPatientSession()
      .then((workspace) => {
        if (active) {
          setSession(workspace)
          void getPatientProfile()
            .then((currentProfile) => {
              if (active) applyProfile(currentProfile)
            })
            .catch((caught) => {
              if (active) setError(messageFrom(caught, 'Unable to load your demographic profile.'))
            })
          setNotice(
            idmeResult === 'connected'
              ? 'Signed in to ClinicForce with ID.me.'
              : idmeResult === 'linked'
                ? 'ID.me is now linked to your ClinicForce account.'
                : idmeResult === 'verified'
                  ? 'Your fresh ID.me identity verification was validated. No health-data query was performed.'
                  : (workspace.drive_sync_message ?? 'Your Google Drive workspace is ready.'),
          )
          if (new URLSearchParams(window.location.search).get('epic') === 'connected') {
            void getEpicRecord()
              .then((record) => {
                if (!active) return
                setEpicRecord(record)
                setNotice(`Live Epic record connected from ${record.source.organization_name}.`)
              })
              .catch((caught) => {
                if (active)
                  setError(
                    messageFrom(caught, 'Epic connected, but the live record could not be loaded.'),
                  )
              })
            window.history.replaceState({}, '', window.location.pathname)
          }
        }
      })
      .catch((caught) => {
        if (!active) return
        if (caught instanceof ApiError && caught.status === 401) {
          if (idmeResult === 'connected' || idmeResult === 'linked')
            setError(
              'ID.me completed, but ClinicForce could not open your session. Please try again.',
            )
          return
        }
        setError(messageFrom(caught, 'Unable to restore your workspace.'))
      })
      .finally(() => {
        if (active) setRestoringSession(false)
      })
    return () => {
      active = false
    }
  }, [
    applyProfile,
    setEpicRecord,
    setError,
    setIdmeStatus,
    setIdmeStatusLoading,
    setNotice,
    setRestoringSession,
    setSession,
  ])

  useEffect(() => {
    const panel = workspacePanel.current
    if (!panel || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const animation = animate(
      Array.from(panel.querySelectorAll<HTMLElement>('[data-workspace-panel]:not([hidden])')),
      {
        opacity: [0, 1],
        translateY: [10, 0],
        duration: 240,
        delay: stagger(45),
        ease: 'outQuad',
      },
    )
    return () => {
      animation.cancel()
    }
  }, [workspaceTab, session?.patient_id, workspacePanel])

  useEffect(() => {
    const stopWatchingPopup = () => {
      if (ecwPopupWatch.current !== null) window.clearInterval(ecwPopupWatch.current)
      ecwPopupWatch.current = null
      ecwPopup.current = null
    }
    const onMessage = (event: MessageEvent<unknown>) => {
      if (
        event.origin !== window.location.origin ||
        !event.data ||
        typeof event.data !== 'object' ||
        !('type' in event.data)
      )
        return
      const type = (event.data as { type?: unknown }).type
      if (type !== 'clinicforce:ecw-connected' && type !== 'clinicforce:ecw-not-connected') return
      stopWatchingPopup()
      setBusy(null)
      ecwConnectionHandled.current = true
      if (type === 'clinicforce:ecw-not-connected') {
        setError(
          'eClinicalWorks did not complete authorization. Check the six-character practice code and try again.',
        )
        return
      }
      setBusy('ecw-load')
      void getEcwRecord()
        .then((record) => {
          setEcwRecord(record)
          setError('')
          setNotice(
            `eClinicalWorks data is ready to review from ${record.source.organization_name}. Nothing has been imported yet.`,
          )
        })
        .catch((caught) => {
          setEcwRecord(null)
          setError(
            messageFrom(
              caught,
              'eClinicalWorks connected, but the authorized data could not be loaded. Reconnect healow and try again.',
            ),
          )
        })
        .finally(() => setBusy(null))
    }
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('message', onMessage)
      stopWatchingPopup()
    }
  }, [ecwConnectionHandled, ecwPopup, ecwPopupWatch, setBusy, setEcwRecord, setError, setNotice])

  useEffect(() => {
    const query = epicQuery.trim()
    if (!session || query.length < 2) return

    let active = true
    const timer = window.setTimeout(() => {
      setEpicSearching(true)
      setEpicDirectoryError('')
      void searchEpicEndpoints(query)
        .then((endpoints) => {
          if (!active) return
          setEpicEndpoints(endpoints)
          setSelectedEpicEndpointId((current) =>
            endpoints.some((endpoint) => endpoint.id === current) ? current : '',
          )
        })
        .catch((caught) => {
          if (!active) return
          setEpicEndpoints([])
          setSelectedEpicEndpointId('')
          setEpicDirectoryError(messageFrom(caught, 'Unable to load Epic hospital suggestions.'))
        })
        .finally(() => {
          if (active) setEpicSearching(false)
        })
    }, 250)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [
    epicQuery,
    session,
    setEpicDirectoryError,
    setEpicEndpoints,
    setEpicSearching,
    setSelectedEpicEndpointId,
  ])

  useEffect(() => {
    const stopWatchingPopup = () => {
      if (epicPopupWatch.current !== null) window.clearInterval(epicPopupWatch.current)
      epicPopupWatch.current = null
      epicPopup.current = null
    }
    const onMessage = (event: MessageEvent<unknown>) => {
      if (
        event.origin !== window.location.origin ||
        !event.data ||
        typeof event.data !== 'object' ||
        !('type' in event.data)
      )
        return
      const type = (event.data as { type?: unknown }).type
      if (type !== 'clinicforce:epic-connected' && type !== 'clinicforce:epic-not-connected') return
      stopWatchingPopup()
      setBusy(null)
      if (type === 'clinicforce:epic-not-connected') {
        epicConnectionHandled.current = true
        setError(
          'Epic did not complete authorization. If Epic showed an OAuth2 error before its sign-in page, the hospital has not activated ClinicForce yet—try again after activation completes. Otherwise, complete or retry MyChart sign-in. No Epic data was imported.',
        )
        return
      }
      epicConnectionHandled.current = true
      void getEpicRecord()
        .then((record) => {
          setEpicRecord(record)
          setError('')
          setNotice(`Live Epic record connected from ${record.source.organization_name}.`)
        })
        .catch((caught) =>
          setError(messageFrom(caught, 'Epic connected, but the live record could not be loaded.')),
        )
    }
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('message', onMessage)
      stopWatchingPopup()
    }
  }, [
    epicConnectionHandled,
    epicPopup,
    epicPopupWatch,
    setBusy,
    setEpicRecord,
    setError,
    setNotice,
  ])
}
