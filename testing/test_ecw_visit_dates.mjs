import { test } from 'node:test'
import { deepStrictEqual } from 'node:assert'
import {
  ecwVisitDates,
  localVisitDay,
} from '../frontend/src/features/staff-workspace/ecwVisitDates.ts'

test('groups upcoming and completed eCW encounters by valid visit day', () => {
  const today = '2026-10-25'
  const appointments = [
    { from_ecw: true, status: 'finished', date: '2026-10-21' },
    { from_ecw: true, status: 'finished', date: '2026-10-21' },
    { from_ecw: true, status: 'completed', date: '2026-10-19' },
    { from_ecw: true, status: 'finished', start: '2026-10-17T09:00:00-04:00' },
    { from_ecw: true, status: 'planned', date: today },
    { from_ecw: true, status: 'booked', date: '2026-10-26' },
    { from_ecw: true, status: 'planned', start: '2026-11-02T09:00:00-05:00' },
    { from_ecw: true, status: 'planned', date: '2026-10-22' },
    { from_ecw: true, status: 'cancelled', date: '2026-10-18' },
    { from_ecw: true, status: 'cancelled', date: '2026-10-27' },
    { from_ecw: true, status: 'entered-in-error', date: '2026-10-28' },
    { from_ecw: false, status: 'finished', date: '2026-10-16' },
    { from_ecw: false, status: 'booked', date: '2026-10-29' },
    { from_ecw: true, status: 'finished', date: '2026-02-30' },
    { from_ecw: true, status: 'planned', date: '2026-11-31' },
  ]
  deepStrictEqual(ecwVisitDates({ ecw_status: 'complete', appointments }, today), [
    today,
    '2026-10-26',
    '2026-11-02',
    '2026-10-21',
    '2026-10-19',
    '2026-10-17',
  ])
  deepStrictEqual(ecwVisitDates({ ecw_status: 'incomplete', appointments }, today), [])
  deepStrictEqual(localVisitDay(new Date(2026, 9, 25)), today)
})
