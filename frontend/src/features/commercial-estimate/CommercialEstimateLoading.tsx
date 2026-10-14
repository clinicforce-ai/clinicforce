import { LoaderCircle } from 'lucide-react'
import './CommercialEstimator.css'

interface Props {
  practiceName: string
  patient: { given_name: string; family_name: string; birth_date: string }
}

export default function CommercialEstimateLoading({ practiceName, patient }: Props) {
  return (
    <section className="visit-estimator" aria-labelledby="lookup-estimate-title" aria-busy="true">
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
      <div className="visit-empty" role="status">
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
