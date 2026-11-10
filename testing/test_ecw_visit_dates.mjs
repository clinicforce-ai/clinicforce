import { test } from 'node:test'
import { deepStrictEqual, strictEqual } from 'node:assert'
import { ecwVisitDays, localVisitDay } from '../frontend/src/features/staff-workspace/ecwVisitDates.ts'

const visit = (status, date, start = null, from_ecw = true) => ({ status, date, start, from_ecw })
const history = (appointments, ecw_status = 'complete') => ({ appointments, ecw_status })

test('keeps a single patient on every valid eCW visit date', () => {
  deepStrictEqual(ecwVisitDays(history([
    visit('finished', '2026-11-07', '2026-11-07T09:00:00-05:00'),
    visit('booked', '2026-11-12', '2026-11-12T10:00:00-05:00'),
  ])), [
    { day: '2026-11-07', time: Date.parse('2026-11-07T09:00:00-05:00') },
    { day: '2026-11-12', time: Date.parse('2026-11-12T10:00:00-05:00') },
  ])
  strictEqual(localVisitDay(new Date(2026, 10, 10)), '2026-11-10')
})

test('keeps the earliest time for a date and ignores cancelled, invalid, and incomplete visits', () => {
  deepStrictEqual(ecwVisitDays(history([
    visit('booked', '2026-11-07', '2026-11-07T15:00:00-05:00'),
    visit('finished', '2026-11-07', '2026-11-07T09:00:00-05:00'),
    visit('cancelled', '2026-11-07', '2026-11-07T08:00:00-05:00'),
    visit('entered-in-error', '2026-11-08'),
    visit('finished', '2026-02-30'),
    visit('booked', '2026-11-31'),
    visit('finished', '2026-11-06', null, false),
  ])), [{ day: '2026-11-07', time: Date.parse('2026-11-07T09:00:00-05:00') }])
  deepStrictEqual(ecwVisitDays(history([visit('booked', '2026-11-07')], 'incomplete')), [])
  deepStrictEqual(ecwVisitDays(history([visit('booked', '2026-11-07')])), [
    { day: '2026-11-07', time: null },
  ])
})
