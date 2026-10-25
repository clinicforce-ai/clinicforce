import { test } from 'node:test'
import { deepStrictEqual } from 'node:assert'
import { completedEcwVisitDates } from '../frontend/src/features/staff-workspace/completedEcwVisitDates.ts'

test('groups only completed eCW encounters by valid visit day', () => {
  const appointments = [
    { from_ecw: true, status: 'finished', date: '2026-10-21' },
    { from_ecw: true, status: 'finished', date: '2026-10-21' },
    { from_ecw: true, status: 'completed', date: '2026-10-19' },
    { from_ecw: true, status: 'finished', start: '2026-10-17T09:00:00-04:00' },
    { from_ecw: true, status: 'planned', date: '2026-10-22' },
    { from_ecw: true, status: 'cancelled', date: '2026-10-18' },
    { from_ecw: false, status: 'finished', date: '2026-10-16' },
    { from_ecw: true, status: 'finished', date: '2026-02-30' },
  ]
  deepStrictEqual(completedEcwVisitDates({ ecw_status: 'complete', appointments }), [
    '2026-10-21',
    '2026-10-19',
    '2026-10-17',
  ])
  deepStrictEqual(completedEcwVisitDates({ ecw_status: 'incomplete', appointments }), [])
})
