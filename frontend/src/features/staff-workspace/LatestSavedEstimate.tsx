import { useEffect, useState } from 'react'
import { getPatientAppointments } from '../../services/api/staff-patients'
import {
  getStaffInsuranceCheck,
  type InsuranceCheckSnapshot,
} from '../../services/api/staff-history'
import { money, visitDate } from '../commercial-estimate/format'

export default function LatestSavedEstimate({ patientId }: { patientId: string }) {
  const [snapshot, setSnapshot] = useState<InsuranceCheckSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  function refresh() {
    setLoading(true)
    setError(false)
    setAttempt((value) => value + 1)
  }

  useEffect(() => {
    let active = true
    getPatientAppointments(patientId)
      .then((history) => {
        const latest = history.appointments
          .flatMap((visit) => visit.checks)
          .filter((check) => check.source_mode === 'commercial_previsit' && check.intake_id)
          .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
        return latest ? getStaffInsuranceCheck(latest.intake_id!, latest.id) : null
      })
      .then((result) => {
        if (active) setSnapshot(result?.snapshots[0] || null)
      })
      .catch(() => {
        if (active) setError(true)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [patientId, attempt])

  const result = snapshot?.result
  const guidance = result?.collection_guidance
  const amount =
    guidance?.amount_cents != null &&
    Number.isFinite(guidance.amount_cents) &&
    guidance.amount_cents >= 0 &&
    !['deferred', 'deferred_billing', 'action_required'].includes(guidance.status) &&
    !guidance.integrity_error &&
    !result?.encounter_summary?.integrity_error &&
    (result?.encounter_summary?.collect_today_cents === undefined ||
      result.encounter_summary.collect_today_cents === guidance.amount_cents)
      ? guidance.amount_cents
      : null

  return (
    <section
      className="staff-panel latest-saved-estimate"
      aria-label="Latest saved collection estimate"
    >
      <div className="staff-section-heading">
        <h2>Latest saved staff collection estimate</h2>
        <button type="button" disabled={loading} onClick={refresh}>
          Refresh
        </button>
      </div>
      {loading ? (
        <p role="status">Loading latest saved estimate…</p>
      ) : error ? (
        <p role="alert">The latest saved estimate could not be loaded. Select Refresh to retry.</p>
      ) : !result ? (
        <p>No saved estimate is available for this patient.</p>
      ) : (
        <>
          <strong className="latest-saved-estimate-amount">
            {amount == null ? 'Review before collecting' : money(amount)}
          </strong>
          <p>
            {amount == null
              ? 'No supported collection amount was retained.'
              : 'Recorded amount for staff to collect at this visit.'}{' '}
            {result.service_date && `Visit: ${visitDate(result.service_date)}.`}
          </p>
          <p className="staff-small staff-muted">
            Saved recommendation{snapshot?.saved_at ? ` ${visitDate(snapshot.saved_at)}` : ''}.
            Recheck the current visit before collecting today; final patient responsibility follows
            the processed claim.
          </p>
        </>
      )}
    </section>
  )
}
