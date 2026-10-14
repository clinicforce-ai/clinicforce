import type { InsuranceV2Model } from './useInsuranceV2Model'
import { Check } from 'lucide-react'

export default function ManualReviewSection({
  model,
  insuranceCase,
}: {
  model: InsuranceV2Model
  insuranceCase: NonNullable<InsuranceV2Model['insuranceCase']>
}) {
  const { staffMode, busy, coverageDone, stediCoverage, recordManualReview } = model
  return (
    <>
      {stediCoverage && coverageDone && (
        <section className="v2-manual-review" aria-labelledby="manual-review-heading">
          <div>
            <p className="section-kicker">Stedi benefits-only path</p>
            <h2 id="manual-review-heading">Record payer verification before scheduling</h2>
            <p>
              Stedi did not return a patient-price estimate. Verify the payer portal or call the
              payer, record its confirmation reference, then choose either a pending insurance
              estimate or the clinic cash-price path.
            </p>
          </div>
          {!staffMode ? (
            <p className="v2-next-step">
              Clinic staff must record the payer verification. No insured amount will be shown for
              this case.
            </p>
          ) : (
            <form className="v2-manual-review-form" onSubmit={recordManualReview}>
              <div className="v2-manual-review-grid">
                <label>
                  <span>Verification source</span>
                  <select
                    name="source"
                    defaultValue={insuranceCase.manual_review?.source || 'payer_portal'}
                  >
                    <option value="payer_portal">Payer portal</option>
                    <option value="payer_call">Payer phone call</option>
                  </select>
                </label>
                <label>
                  <span>Network status</span>
                  <select
                    name="network_status"
                    defaultValue={insuranceCase.manual_review?.network_status || 'unconfirmed'}
                  >
                    <option value="unconfirmed">Not verified</option>
                    <option value="in_network">In network</option>
                    <option value="out_of_network">Out of network</option>
                  </select>
                </label>
                <label>
                  <span>Network confirmation reference</span>
                  <input
                    name="network_reference"
                    defaultValue={insuranceCase.manual_review?.network_reference || ''}
                    maxLength={200}
                  />
                </label>
                <label>
                  <span>Referral status</span>
                  <select
                    name="referral_status"
                    defaultValue={insuranceCase.manual_review?.referral_status || 'unconfirmed'}
                  >
                    <option value="unconfirmed">Not verified</option>
                    <option value="not_required">Not required</option>
                    <option value="approved">Approved</option>
                    <option value="required">Required / pending</option>
                  </select>
                </label>
                <label>
                  <span>Referral confirmation reference</span>
                  <input
                    name="referral_reference"
                    defaultValue={insuranceCase.manual_review?.referral_reference || ''}
                    maxLength={200}
                  />
                </label>
                <label>
                  <span>Prior-authorization status</span>
                  <select
                    name="authorization_status"
                    defaultValue={
                      insuranceCase.manual_review?.authorization_status || 'unconfirmed'
                    }
                  >
                    <option value="unconfirmed">Not verified</option>
                    <option value="not_required">Not required</option>
                    <option value="approved">Approved</option>
                    <option value="required">Required / pending</option>
                  </select>
                </label>
                <label>
                  <span>Authorization confirmation reference</span>
                  <input
                    name="authorization_reference"
                    defaultValue={insuranceCase.manual_review?.authorization_reference || ''}
                    maxLength={200}
                  />
                </label>
                <label>
                  <span>Price path</span>
                  <select
                    name="price_path"
                    defaultValue={insuranceCase.manual_review?.price_path || 'pending'}
                  >
                    <option value="pending">Insurance estimate pending</option>
                    <option value="cash">Offer clinic cash price only</option>
                  </select>
                </label>
              </div>
              <p className="v2-next-step">
                A reference is required for every status other than “Not verified.” This record
                documents staff verification; it never creates an insured price estimate.
              </p>
              <button className="button button-primary" type="submit" disabled={!!busy}>
                <Check size={16} aria-hidden="true" /> Save payer verification
              </button>
            </form>
          )}
        </section>
      )}
    </>
  )
}
