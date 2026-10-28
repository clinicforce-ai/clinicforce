import { test } from 'node:test'
import { strictEqual } from 'node:assert'
import {
  ecwVisit,
  localVisitDay,
} from '../frontend/src/features/staff-workspace/ecwVisitDates.ts'

test('places a returning patient on today or their next future visit, then their latest past visit', () => {
  const today = '2026-10-25'
  const visit = (status, date, from_ecw = true) => ({ status, date, from_ecw })
  const history = (appointments, ecw_status = 'complete') => ({ appointments, ecw_status })

  strictEqual(ecwVisit(history([
    visit('finished', '2026-10-21'),
    visit('booked', '2026-10-28'),
    visit('planned', '2026-10-26'),
  ]), today).day, '2026-10-26')
  strictEqual(ecwVisit(history([
    visit('finished', '2026-10-21'),
    visit('finished', today),
    visit('booked', '2026-10-26'),
  ]), today).day, today)
  strictEqual(ecwVisit(history([
    visit('planned', today),
    visit('finished', '2026-10-21'),
  ]), today).day, today)
  strictEqual(ecwVisit(history([
    visit('finished', '2026-10-21'),
    visit('booked', '2026-11-09'),
  ]), today).day, '2026-11-09')
  strictEqual(ecwVisit(history([
    visit('planned', '2026-10-22'),
    visit('finished', '2026-10-21'),
    visit('cancelled', '2026-10-24'),
  ]), today).day, '2026-10-22')
  strictEqual(ecwVisit(history([
    visit('cancelled', '2026-10-26'),
    visit('entered-in-error', today),
    visit('finished', '2026-02-30'),
    visit('booked', '2026-11-31'),
    visit('finished', '2026-10-20', false),
  ]), today).day, null)
  strictEqual(ecwVisit(history([visit('booked', '2026-10-26')], 'incomplete'), today).day, null)
  strictEqual(localVisitDay(new Date(2026, 9, 25)), today)
})

test('uses the earliest valid appointment time on the selected day', () => {
  const appointments = [
    { from_ecw: true, status: 'booked', date: '2026-10-26', start: '2026-10-26T15:00:00-04:00' },
    { from_ecw: true, status: 'booked', date: '2026-10-26', start: '2026-10-26T09:00:00-04:00' },
    { from_ecw: true, status: 'cancelled', date: '2026-10-26', start: '2026-10-26T08:00:00-04:00' },
    { from_ecw: true, status: 'booked', date: '2026-10-27', start: '2026-10-27T07:00:00-04:00' },
  ]
  const result = ecwVisit({ ecw_status: 'complete', appointments }, '2026-10-25')
  strictEqual(result.day, '2026-10-26')
  strictEqual(result.time, Date.parse('2026-10-26T09:00:00-04:00'))
  strictEqual(ecwVisit({ ecw_status: 'complete', appointments: [{ from_ecw: true, status: 'booked', date: '2026-10-26', start: null }] }, '2026-10-25').time, null)
})
