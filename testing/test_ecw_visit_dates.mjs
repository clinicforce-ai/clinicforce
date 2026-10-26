import { test } from 'node:test'
import { strictEqual } from 'node:assert'
import {
  ecwVisitDay,
  localVisitDay,
} from '../frontend/src/features/staff-workspace/ecwVisitDates.ts'

test('places a returning patient on today or their next future visit, then their latest past visit', () => {
  const today = '2026-10-25'
  const visit = (status, date, from_ecw = true) => ({ status, date, from_ecw })
  const history = (appointments, ecw_status = 'complete') => ({ appointments, ecw_status })

  strictEqual(ecwVisitDay(history([
    visit('finished', '2026-10-21'),
    visit('booked', '2026-10-28'),
    visit('planned', '2026-10-26'),
  ]), today), '2026-10-26')
  strictEqual(ecwVisitDay(history([
    visit('finished', '2026-10-21'),
    visit('finished', today),
    visit('booked', '2026-10-26'),
  ]), today), today)
  strictEqual(ecwVisitDay(history([
    visit('planned', today),
    visit('finished', '2026-10-21'),
  ]), today), today)
  strictEqual(ecwVisitDay(history([
    visit('finished', '2026-10-21'),
    visit('booked', '2026-11-09'),
  ]), today), '2026-11-09')
  strictEqual(ecwVisitDay(history([
    visit('planned', '2026-10-22'),
    visit('finished', '2026-10-21'),
    visit('cancelled', '2026-10-24'),
  ]), today), '2026-10-22')
  strictEqual(ecwVisitDay(history([
    visit('cancelled', '2026-10-26'),
    visit('entered-in-error', today),
    visit('finished', '2026-02-30'),
    visit('booked', '2026-11-31'),
    visit('finished', '2026-10-20', false),
  ]), today), null)
  strictEqual(ecwVisitDay(history([visit('booked', '2026-10-26')], 'incomplete'), today), null)
  strictEqual(localVisitDay(new Date(2026, 9, 25)), today)
})
