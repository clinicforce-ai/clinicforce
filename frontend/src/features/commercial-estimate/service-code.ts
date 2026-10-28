import type { VisitCode } from '../../services/api/commercial-estimator'

export const money = (value?: number | null) =>
  value == null
    ? '—'
    : new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value / 100)
export const groupFor = (code: string) =>
  /^992(0[2-5]|1[2-5])$/.test(code)
    ? 'visit'
    : /^959(0[7-9]|1[0-3])$/.test(code)
      ? 'nerve'
      : /^9588[5-7]$/.test(code)
        ? 'muscle'
        : 'other'
export const nerveCodeForStudyCount = (count: number) =>
  Number.isInteger(count) && count >= 1 && count <= 13
    ? String(95907 + Math.floor((count - 1) / 2))
    : ''
export const serviceGroups = (
  office: VisitCode[],
  nerve: VisitCode[],
  muscle: VisitCode[],
  other: VisitCode[],
  botoxVisit: boolean,
) =>
  [
    { id: 'visit', label: 'Office visit', rows: office, single: true },
    { id: 'nerve', label: 'Nerve study', rows: nerve, single: true },
    { id: 'muscle', label: 'Muscle EMG', rows: muscle, single: false },
    {
      id: 'other',
      label: botoxVisit ? 'Injection and medication' : 'Other care',
      rows: other,
      single: false,
    },
  ].filter((group) => group.rows.length)
export const clinicalName = (row: VisitCode) => {
  if (groupFor(row.code) === 'nerve')
    return (
      'Nerve conduction · ' +
      [
        '1–2 studies',
        '3–4 studies',
        '5–6 studies',
        '7–8 studies',
        '9–10 studies',
        '11–12 studies',
        '13 or more studies',
      ][Number(row.code) - 95907]
    )
  if (row.code === '95885') return 'Limited muscle examination'
  if (row.code === '95886') return 'Complete muscle examination'
  if (row.code === '95887') return 'Non-limb muscle examination'
  if (groupFor(row.code) === 'visit')
    return `${Number(row.code) < 99210 ? 'New patient' : 'Follow-up'} · ${['Straightforward', 'Low complexity', 'Moderate complexity', 'High complexity'][(Number(row.code) % 10) - 2]}`
  const injectionNames: Record<string, string> = {
    '64613': 'Retired neck injection code · remove from plan',
    '64614': 'Retired limb injection code · remove from plan',
    '64611': 'Salivary gland injections',
    '64612': 'Facial muscle injections',
    '64615': 'Chronic migraine injections',
    '64616': 'Neck muscle injections',
    '64642': 'First limb · 1–4 muscles',
    '64643': 'Additional limb · 1–4 muscles',
    '64644': 'First limb · 5 or more muscles',
    '64645': 'Additional limb · 5 or more muscles',
    J0585: 'Botox medication · per unit',
  }
  return injectionNames[row.code] || row.description || `Procedure ${row.code}`
}
