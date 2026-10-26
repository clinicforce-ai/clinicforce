import type { PatientAppointmentHistory } from '../../services/api/staff-patients'

export function localVisitDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

export function ecwVisitDay(history: PatientAppointmentHistory, today: string): string | null {
  if (history.ecw_status !== 'complete') return null
  const dates = history.appointments
    .filter((visit) => visit.from_ecw)
    .filter((visit) => {
      const day = visit.date || visit.start?.slice(0, 10) || ''
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return false
      const date = new Date(`${day}T00:00:00Z`)
      if (Number.isNaN(date.valueOf()) || date.toISOString().slice(0, 10) !== day) return false
      return !['cancelled', 'entered-in-error'].includes(visit.status.toLowerCase())
    })
    .map((visit) => visit.date || visit.start?.slice(0, 10) || '')
  if (dates.includes(today)) return today
  return (
    dates.filter((day) => day > today).sort()[0] ||
    dates
      .filter((day) => day < today)
      .sort()
      .at(-1) ||
    null
  )
}
