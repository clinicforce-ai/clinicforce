import { useEffect, type Dispatch, type SetStateAction } from 'react'
import {
  getClinicalPatients,
  type ClinicalDashboard,
  type ClinicalPatient,
} from '../../services/api/staff-patients'

export function useClinicalCardRefresh(
  dashboard: ClinicalDashboard | null,
  setDashboard: Dispatch<SetStateAction<ClinicalDashboard | null>>,
  setSelected: Dispatch<SetStateAction<ClinicalPatient | null>>,
  visible: boolean,
) {
  useEffect(() => {
    if (!dashboard || !visible) return
    let active = true
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      void getClinicalPatients()
        .then((updated) => {
          if (!active) return
          const previous = new Map(dashboard.patients.map((patient) => [patient.id, patient]))
          if (
            !updated.patients.some(
              (patient) =>
                previous.get(patient.id)?.card_status !== patient.card_status ||
                previous.get(patient.id)?.latest_intake_id !== patient.latest_intake_id,
            )
          )
            return
          setDashboard(updated)
          setSelected((current) =>
            current
              ? updated.patients.find((patient) => patient.id === current.id) || current
              : null,
          )
        })
        .catch(() => {})
    }, 15_000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [dashboard, setDashboard, setSelected, visible])
}
