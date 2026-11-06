import { useEffect, useState } from 'react'
import { getPatientAppointments } from '../../services/api/staff-patients'
import { commercialSavedPrevisit } from '../../services/api/commercial-estimator'
import {
  getStaffInsuranceCheck,
  type InsuranceCheckSnapshot,
} from '../../services/api/staff-history'
import CommercialEstimator from '../commercial-estimate/CommercialEstimator'
import { visitDate, visitTime } from '../commercial-estimate/format'

type FeaturedEstimate = {
  snapshot: InsuranceCheckSnapshot
  intakeId: string
  accountNumber?: string | null
  reprojected?: boolean
}

export default function LatestSavedEstimate({ patientId }: { patientId: string }) {
  const [featured, setFeatured] = useState<FeaturedEstimate | null>(null)
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
        return latest
          ? getStaffInsuranceCheck(latest.intake_id!, latest.id).then(async (result) => {
              const snapshot = result.snapshots[0]
              if (!snapshot) return null
              const featured: FeaturedEstimate = {
                snapshot,
                intakeId: latest.intake_id!,
                accountNumber: history.ecw_account_number,
              }
              // A saved snapshot predating the published GEHA rule retains its original
              // calculation. Read the current projection for this exact missing-input case.
              const plan = snapshot.result.plan_details?.stedi
              const officeLine = snapshot.result.price_range?.lines?.[0]
              const cignaOfficeCopayPending =
                snapshot.result.collection_guidance?.amount_cents == null &&
                /\bcigna\b/i.test(snapshot.result.payer || '') &&
                plan?.names?.length === 1 &&
                /\b(?:open access plus|oap)\b/i.test(plan.names[0]) &&
                snapshot.result.price_range?.mode === 'services' &&
                snapshot.result.price_range.lines?.length === 1 &&
                officeLine?.units === 1 &&
                /^992(?:0[2-5]|1[2-5])$/.test(officeLine.code)
              if (
                cignaOfficeCopayPending ||
                (snapshot.result.collection_guidance?.basis === 'missing_collection_inputs' &&
                  snapshot.result.service_date?.startsWith('2026-') &&
                  plan?.group_number === '16615' &&
                  plan.names?.length === 1 &&
                  plan.names[0]
                    ?.toUpperCase()
                    .startsWith('LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE'))
              ) {
                const projection = await commercialSavedPrevisit(latest.id, latest.intake_id!)
                if (
                  projection.collection_guidance?.basis === 'published_geha_pshb_zero_office' ||
                  (cignaOfficeCopayPending &&
                    projection.collection_guidance?.basis === 'office_copay' &&
                    projection.collection_guidance.amount_cents != null &&
                    projection.collection_guidance.collection_rule?.mode ===
                      'cigna_oap_televisit_office_copay')
                )
                  return {
                    ...featured,
                    snapshot: { ...snapshot, result: projection },
                    reprojected: true,
                  }
              }
              return featured
            })
          : null
      })
      .then((result) => {
        if (active) setFeatured(result)
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

  return (
    <section
      className="staff-panel latest-saved-estimate"
      aria-label="Latest saved collection estimate"
    >
      <div className="staff-section-heading">
        <h2 id="latest-saved-estimate-title">Latest saved visit estimate</h2>
        <button type="button" disabled={loading} onClick={refresh}>
          Refresh
        </button>
      </div>
      {loading ? (
        <p role="status">Loading latest saved estimate…</p>
      ) : error ? (
        <p role="alert">The latest saved estimate could not be loaded. Select Refresh to retry.</p>
      ) : !featured ? (
        <p>No saved estimate is available for this patient.</p>
      ) : (
        <>
          <p className="staff-small staff-muted">
            Recorded for {visitDate(featured.snapshot.result.service_date)}
            {featured.snapshot.saved_at &&
              ` · Saved ${visitDate(featured.snapshot.saved_at)} at ${visitTime(featured.snapshot.saved_at)}`}
            {featured.reprojected && ' · Collection guidance refreshed from saved payer evidence'}.
            Recheck the current visit before collecting today; final responsibility follows the
            processed claim.
          </p>
          <CommercialEstimator
            saved={featured.snapshot}
            intakeId={featured.intakeId}
            currentAccountNumber={featured.accountNumber}
            featured
          />
        </>
      )}
    </section>
  )
}
