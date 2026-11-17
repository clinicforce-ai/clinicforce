import { useCallback, useEffect, useState } from 'react'
import { commercialFinancials, type VisitFinancials } from '../../services/api/commercial-estimator'
import { getStaffCheckFinancials } from '../../services/api/staff-history'
import type { PrevisitEstimate } from '../../services/api/commercial-previsit'

export type PaidClaimFinancialState = {
  data: VisitFinancials | null
  busy: boolean
  error: string
  reload: () => Promise<void>
}

export function usePaidClaimFinancials(
  result: PrevisitEstimate | null,
  intakeId?: string,
  active = true,
): PaidClaimFinancialState | undefined {
  const checkId = result?.check_id
  const enabled =
    active &&
    !!checkId &&
    (/aetna/i.test(result?.payer || '') ||
      ['60054', 'HPQRS'].includes(result?.stedi_payer?.id || '')) &&
    !!result?.service_date &&
    result.service_date < new Date().toISOString().slice(0, 10)
  const key = `${intakeId || ''}:${checkId || ''}:${result?.checked_at || ''}`
  const [data, setData] = useState<VisitFinancials | null>(null)
  const [loadedKey, setLoadedKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const read = useCallback(
    () => (intakeId ? getStaffCheckFinancials(intakeId, checkId!) : commercialFinancials(checkId!)),
    [checkId, intakeId],
  )
  const reload = useCallback(async () => {
    if (!enabled) return
    setBusy(true)
    setError('')
    try {
      setData(await read())
      setLoadedKey(key)
    } catch {
      setError('The processed claim check could not be read. Retry.')
      setLoadedKey(key)
    } finally {
      setBusy(false)
    }
  }, [enabled, key, read])
  useEffect(() => {
    if (!enabled) return
    let active = true
    read()
      .then((value) => {
        if (active) {
          setData(value)
          setLoadedKey(key)
          setError('')
        }
      })
      .catch(() => {
        if (active) {
          setError('The processed claim check could not be read. Retry.')
          setLoadedKey(key)
        }
      })
    return () => {
      active = false
    }
  }, [enabled, key, read])
  return enabled
    ? {
        data: loadedKey === key ? data : null,
        busy: loadedKey !== key || busy,
        error: loadedKey === key ? error : '',
        reload,
      }
    : undefined
}
