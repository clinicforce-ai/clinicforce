import type { InsuranceV1Model } from './useInsuranceV1Model'
import { UploadTile } from './InsuranceV1Details'
import { imageOrPdf } from './v1-display'
import { confirmInsuranceIntake } from '../../services/api/insurance-v1'

export default function InsuranceCardStage({ model }: { model: InsuranceV1Model }) {
  const {
    insuranceCase,
    staffIntakeId,
    busy,
    perform,
    uploadCard,
    reviewing,
    cardValues,
    cardReview,
  } = model
  return (
    <>
      <p className="section-kicker">1 · Insurance card</p>
      <h2>
        {insuranceCase
          ? 'Check what was read from the card'
          : 'Upload the patient’s insurance card'}
      </h2>
      <p>
        Use a clear image of the card or a PDF containing its front and back. Keep the member name,
        member ID and group number readable. We show only information extracted from this file.
      </p>
      {!reviewing && (
        <UploadTile
          title={insuranceCase ? 'Replace insurance card' : 'Insurance card'}
          hint="PDF (up to 2 pages), JPG or PNG · maximum 10 MB. Include both sides in one PDF when possible. A benefits statement and fee schedule come in the next steps."
          accept={imageOrPdf}
          busy={busy}
          source={insuranceCase?.card_source}
          onFile={uploadCard}
        />
      )}
      {cardReview}
      {insuranceCase && !reviewing && (
        <button
          className="button button-primary"
          type="button"
          disabled={busy || cardValues.length === 0}
          onClick={() =>
            void perform(
              () => confirmInsuranceIntake(insuranceCase.id, insuranceCase.ocr, staffIntakeId),
              'Card fields confirmed. Benefits and rates will come from your next uploads.',
            )
          }
        >
          Confirm extracted card fields
        </button>
      )}
    </>
  )
}
