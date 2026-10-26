import type { PatientAppointmentHistory } from '../../services/api/staff-patients'

export function localVisitDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

export function compareVisitDays(first: string, second: string, today: string): number {
  const firstCurrentOrFuture = first >= today
  const secondCurrentOrFuture = second >= today
  if (firstCurrentOrFuture !== secondCurrentOrFuture) return firstCurrentOrFuture ? -1 : 1
  return firstCurrentOrFuture ? first.localeCompare(second) : second.localeCompare(first)
}

export function ecwVisitDates(history: PatientAppointmentHistory, today: string): string[] {
  if (history.ecw_status !== 'complete') return []
  const dates = history.appointments
    .filter((visit) => visit.from_ecw)
    .filter((visit) => {
      const day = visit.date || visit.start?.slice(0, 10) || ''
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return false
      const date = new Date(`${day}T00:00:00Z`)
      if (Number.isNaN(date.valueOf()) || date.toISOString().slice(0, 10) !== day) return false
      const status = visit.status.toLowerCase()
      return (
        status === 'finished' ||
        status === 'completed' ||
        (day >= today && !['cancelled', 'entered-in-error'].includes(status))
      )
    })
    .map((visit) => visit.date || visit.start?.slice(0, 10) || '')
  return [...new Set(dates)].sort((first, second) => compareVisitDays(first, second, today))
}
