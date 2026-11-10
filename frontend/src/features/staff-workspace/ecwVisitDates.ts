import type { PatientAppointmentHistory } from '../../services/api/staff-patients'

export function localVisitDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

export function ecwVisitDays(history: {
  ecw_status: PatientAppointmentHistory['ecw_status']
  appointments: Array<{ from_ecw: boolean; status: string; date?: string; start?: string | null }>
}) {
  if (history.ecw_status !== 'complete') return []
  const days = new Map<string, number | null>()
  for (const visit of history.appointments) {
    if (!visit.from_ecw || ['cancelled', 'entered-in-error'].includes(visit.status.toLowerCase()))
      continue
    const day = visit.date || visit.start?.slice(0, 10) || ''
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) continue
    const date = new Date(`${day}T00:00:00Z`)
    if (Number.isNaN(date.valueOf()) || date.toISOString().slice(0, 10) !== day) continue
    const parsedTime = visit.start?.includes('T') ? Date.parse(visit.start) : NaN
    const time = Number.isFinite(parsedTime) ? parsedTime : null
    const prior = days.get(day)
    if (prior === undefined || (time !== null && (prior === null || time < prior)))
      days.set(day, time)
  }
  return [...days]
    .sort(([first], [second]) => first.localeCompare(second))
    .map(([day, time]) => ({ day, time }))
}
