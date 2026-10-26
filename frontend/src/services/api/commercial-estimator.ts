import { json, request } from './client'
import type { CommercialAppointment } from './commercial-common'
import type { PrevisitEstimate } from './commercial-previsit'
import type { CommercialPastVisit, VisitReview, VisitFinancials } from './commercial-visit-types'
export type { CommercialAppointment, RequirementEvidence } from './commercial-common'
export type { PrevisitEstimate } from './commercial-previsit'
export type {
  CommercialPastVisit,
  VisitCode,
  VisitReview,
  VisitFinancials,
} from './commercial-visit-types'

const root = '/api/doctor/insurance/commercial'
const staffRequest = <T>(path: string, init: RequestInit, intakeId?: string) =>
  request<T>(path, init, { staffIntakeId: intakeId })

export const commercialAppointments = (intakeId?: string) =>
  staffRequest<{ appointments: CommercialAppointment[] }>(`${root}/appointments`, {}, intakeId)

export const commercialPrevisit = (
  appointment_id: string,
  refresh_benefits = false,
  intakeId?: string,
) =>
  staffRequest<PrevisitEstimate>(
    `${root}/appointments/previsit`,
    json({ appointment_id, refresh_benefits }),
    intakeId,
  )

export const commercialSavedPrevisit = (id: string, intakeId?: string) =>
  staffRequest<PrevisitEstimate>(`${root}/cases/${encodeURIComponent(id)}/previsit`, {}, intakeId)

export const commercialSelectCoverageCandidate = (
  result: PrevisitEstimate,
  candidate_id: string,
  intakeId?: string,
) =>
  staffRequest<PrevisitEstimate>(
    `${root}/cases/${encodeURIComponent(result.check_id!)}/coverage-candidate`,
    json({ candidate_id, revision: result.revision, context_token: result.context_token }),
    intakeId,
  )

export const commercialSelectPayerRoute = (
  result: PrevisitEstimate,
  route_id: string,
  intakeId?: string,
) =>
  staffRequest<PrevisitEstimate>(
    `${root}/cases/${encodeURIComponent(result.check_id!)}/payer-route`,
    json({ route_id, revision: result.revision, context_token: result.context_token }),
    intakeId,
  )

export const commercialHistory = (intakeId?: string) =>
  staffRequest<{ visits: CommercialPastVisit[] }>(`${root}/history`, {}, intakeId)

export const commercialVisitReview = (
  result: PrevisitEstimate,
  review: VisitReview,
  intakeId?: string,
) =>
  staffRequest<PrevisitEstimate>(
    `${root}/cases/${encodeURIComponent(result.check_id!)}/visit-review`,
    json({ ...review, revision: result.revision, context_token: result.context_token }),
    intakeId,
  )

export const commercialFinancials = (id: string) =>
  request<VisitFinancials>(`${root}/cases/${encodeURIComponent(id)}/financials`)
