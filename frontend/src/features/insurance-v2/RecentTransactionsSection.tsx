import type { InsuranceV2Model } from './useInsuranceV2Model'
import { readable } from './v2-display'

export default function RecentTransactionsSection({
  model,
}: {
  model: InsuranceV2Model
  insuranceCase: NonNullable<InsuranceV2Model['insuranceCase']>
}) {
  const { ecwPreview, ecwSource } = model
  return (
    <>
      {ecwPreview && (
        <section className="v2-workflow" aria-labelledby="recent-ecw-visits">
          <p className="section-kicker">eClinicalWorks history</p>
          <h2 id="recent-ecw-visits">Recent completed appointments</h2>
          <p>
            The most recent completed visit informs clinician-reviewable CPT suggestions for this
            appointment. Its services are never copied automatically.
          </p>
          {ecwSource?.recent_completed_appointments?.length ? (
            <div className="v2-service-list">
              {ecwSource.recent_completed_appointments.map((appointment, index) => (
                <div key={`${appointment.date}-${index}`}>
                  <strong>{appointment.date}</strong>
                  <span>
                    {readable(appointment.status)}
                    {appointment.visit_type === 'new'
                      ? ' · New patient'
                      : appointment.visit_type === 'established'
                        ? ' · Established patient'
                        : ''}
                  </span>
                  {(appointment.reason || appointment.coded_services.length > 0) && (
                    <small>
                      {appointment.reason}
                      {appointment.reason && appointment.coded_services.length ? ' · ' : ''}
                      {appointment.coded_services.length
                        ? `CPT/HCPCS: ${appointment.coded_services.join(', ')}`
                        : ''}
                    </small>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="v2-next-step">
              {ecwSource?.history_status === 'timed_out'
                ? 'Appointment history is taking longer than expected. Reload to retry.'
                : ecwSource?.history_status === 'unavailable'
                  ? 'Completed appointment history is not available for this review.'
                  : 'No completed appointments were returned from the last 12 months.'}
            </p>
          )}
        </section>
      )}
    </>
  )
}
