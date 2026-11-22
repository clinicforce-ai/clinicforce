import { LoaderCircle } from 'lucide-react'
import { clinicFallbackReason } from './isClinicZeroArrival'
import './CommercialEstimator.css'

interface Props {
  practiceName: string
  patient: { given_name: string; family_name: string; birth_date: string }
  arrivalCollectionDefaultCents?: number | null
}

export default function CommercialEstimateLoading({
  practiceName,
  patient,
  arrivalCollectionDefaultCents,
}: Props) {
  return (
    <section className="visit-estimator" aria-labelledby="lookup-estimate-title">
      <header className="visit-page-header">
        <h1 id="lookup-estimate-title">Insurance estimator</h1>
        <span className="visit-practice">{practiceName}</span>
      </header>
      <div className="visit-patient-bar">
        <div>
          <div>
            <strong>
              {patient.given_name} {patient.family_name}
            </strong>
            <small>DOB {patient.birth_date}</small>
          </div>
        </div>
      </div>
      {arrivalCollectionDefaultCents === 0 && (
        <section className="visit-overview" aria-label="Arrival collection">
          <div className="arrival-total">
            <span>Amount to collect at visit</span>
            <strong role="status">$0.00</strong>
            <p>{clinicFallbackReason}</p>
          </div>
        </section>
      )}
      <div className="visit-empty" role="status" aria-busy="true">
        <LoaderCircle className="visit-spin" size={26} aria-hidden="true" />
        <h2>Finding the patient and scheduled appointment</h2>
        <p>
          ClinicForce will pull demographics, insurance and available cards, reconcile eligibility,
          and prepare the arrival estimate.
        </p>
      </div>
    </section>
  )
}
