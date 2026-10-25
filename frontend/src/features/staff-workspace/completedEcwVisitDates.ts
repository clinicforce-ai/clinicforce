import type { PatientAppointmentHistory } from '../../services/api/staff-patients'

export function completedEcwVisitDates(history: PatientAppointmentHistory): string[] {
  if (history.ecw_status !== 'complete') return []
  const dates = history.appointments
    .filter(
      (visit) => visit.from_ecw && ['finished', 'completed'].includes(visit.status.toLowerCase()),
    )
    .map((visit) => visit.date || visit.start?.slice(0, 10) || '')
    .filter((day) => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return false
      const date = new Date(`${day}T00:00:00Z`)
      return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === day
    })
  return [...new Set(dates)].sort().reverse()
}
