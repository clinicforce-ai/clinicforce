import type { ClinicalPatient, DateAppointments } from '../../services/api/staff-patients'
import { name } from './clinical-patient-labels'
import { ecwVisitDays } from './ecwVisitDates'

export type VisitResult = { day: string; time: number | null }

export function filterClinicalPatients(
  patients: ClinicalPatient[],
  query: string,
  cardStatus: string,
) {
  const search = query.trim().toLowerCase()
  const terms = query
    .trim()
    .toLowerCase()
    .split(/[\s,.]+/)
    .filter(Boolean)
  // eCW displays "last, first middle-initial"; the clinic record may omit that initial.
  const requiredTerms =
    terms.length > 2 && terms[terms.length - 1].length === 1 ? terms.slice(0, -1) : terms
  return patients.filter(
    (patient) =>
      (!cardStatus || patient.card_status === cardStatus) &&
      (requiredTerms.every((term) => name(patient).toLowerCase().includes(term)) ||
        [patient.email, patient.birth_date, patient.ecw_account_number].some((value) =>
          (value || '').toLowerCase().includes(search),
        )),
  )
}

export function partitionPatientDates(
  groups: Map<string, ClinicalPatient[]>,
  today: string,
  selectedLookupDay: string | null,
) {
  const upcoming = [...groups]
    .filter(([day]) => day > today && day !== 'undated' && day !== 'pending')
    .sort(([first], [second]) => first.localeCompare(second))
  const past = [...groups]
    .filter(([day]) => day < today)
    .sort(([first], [second]) => second.localeCompare(first))
  if (groups.has('undated')) past.push(['undated', groups.get('undated')!])
  if (selectedLookupDay) {
    const sortSelected = (
      first: [string, ClinicalPatient[]],
      second: [string, ClinicalPatient[]],
    ) => Number(second[0] === selectedLookupDay) - Number(first[0] === selectedLookupDay)
    past.sort(sortSelected)
    upcoming.sort(sortSelected)
  }
  return { today: groups.get(today) || [], upcoming, past, pending: groups.get('pending') || [] }
}

export function groupPatientDates(
  patients: ClinicalPatient[],
  visitResults: Record<string, VisitResult[]>,
  exactDates: Record<string, DateAppointments>,
) {
  const grouped = new Map<string, ClinicalPatient[]>()
  const groupTimes = new Map<string, number | null>()
  const checkedDays = new Set(Object.keys(exactDates))
  const exactByPatient = new Map<string, Map<string, VisitResult>>()
  for (const snapshot of Object.values(exactDates)) {
    for (const visit of snapshot.visits) {
      const result = ecwVisitDays({
        ecw_status: 'complete',
        appointments: [{ ...visit, from_ecw: true }],
      })[0]
      if (!result) continue
      const days = exactByPatient.get(visit.clinic_patient_id) || new Map<string, VisitResult>()
      const prior = days.get(result.day)
      if (!prior || (result.time !== null && (prior.time === null || result.time < prior.time)))
        days.set(result.day, result)
      exactByPatient.set(visit.clinic_patient_id, days)
    }
  }
  for (const patient of patients) {
    const known =
      visitResults[patient.id] === undefined && patient.ecw_linked
        ? [{ day: 'pending', time: null }]
        : visitResults[patient.id]?.length
          ? visitResults[patient.id]
          : [{ day: 'undated', time: null }]
    const exact = exactByPatient.get(patient.clinic_patient_id || '')
    const visits = [
      ...known.filter(
        (visit) =>
          !checkedDays.has(visit.day) &&
          (!exact?.size || (visit.day !== 'pending' && visit.day !== 'undated')),
      ),
      ...Array.from(exact?.values() || []),
    ]
    if (!visits.length) visits.push({ day: 'undated', time: null })
    for (const visit of visits) {
      const group = grouped.get(visit.day)
      if (group) group.push(patient)
      else grouped.set(visit.day, [patient])
      groupTimes.set(`${patient.id}:${visit.day}`, visit.time)
    }
  }
  for (const [day, group] of grouped)
    group.sort(
      (first, second) =>
        (groupTimes.get(`${first.id}:${day}`) ?? Infinity) -
          (groupTimes.get(`${second.id}:${day}`) ?? Infinity) ||
        name(first).localeCompare(name(second)),
    )
  return grouped
}

export function formatVisitDay(day: string) {
  return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  })
}
