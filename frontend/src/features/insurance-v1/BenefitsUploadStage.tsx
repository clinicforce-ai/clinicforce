import type { InsuranceV1Model } from './useInsuranceV1Model'
import { UploadTile, SourcePages, FactList } from './InsuranceV1Details'
import { imageOrPdf } from './v1-display'

export default function BenefitsUploadStage({ model }: { model: InsuranceV1Model }) {
  const { busy, uploadEvidence, advance, reviewing, disabled, facts, documents } = model
  return (
    <>
      <p className="section-kicker">3 · Benefits & rates</p>
      <h2>Upload the documents behind the estimate</h2>
      <p>
        These files serve different purposes. The benefits document supplies the member’s coverage
        and remaining balances; the fee schedule supplies the allowed price for each CPT/HCPCS code.
      </p>
      <div className="evidence-upload-grid">
        <UploadTile
          title="Member benefits · required"
          hint="Upload the dated eligibility/benefits response for this member, such as a payer-portal export. Include the pages showing individual deductible remaining, out-of-pocket remaining and patient cost-sharing. PDF, JPG or PNG · up to 15 MB."
          accept={imageOrPdf}
          source={documents.coverage}
          busy={disabled}
          onFile={(file) => uploadEvidence('coverage', file)}
        />
        <UploadTile
          title="Fee schedule · required"
          hint="Upload the applicable rate CSV, with CPT/HCPCS or Procedure Code, Description, and Amount/Allowed Amount/Rate columns. Use the relevant plan and network schedule, with one rate per code. Up to 15 MB."
          accept="text/csv,.csv"
          source={documents.fee_schedule}
          busy={disabled}
          onFile={(file) => uploadEvidence('fee_schedule', file)}
        />
        <UploadTile
          title="Provider agreement · optional reference"
          hint="Upload the clinic–payer agreement if you want to review its participation terms alongside the benefits. An agreement alone is not evidence of a numeric CPT rate. PDF, JPG or PNG · up to 15 MB."
          accept={imageOrPdf}
          source={documents.contract}
          busy={disabled}
          onFile={(file) => uploadEvidence('contract', file)}
        />
        <UploadTile
          title="Self-pay rates · optional"
          hint="Upload the clinic’s cash-rate CSV to compare prices for the selected codes, or a PDF/image pricing guide for reference. Insurance rates are never used as cash prices. Up to 15 MB."
          accept={`${imageOrPdf},.csv,text/csv`}
          source={documents.self_pay}
          busy={disabled}
          onFile={(file) => uploadEvidence('self_pay', file)}
        />
      </div>
      <FactList facts={facts.filter((fact) => fact.stage === 'network')} />
      {documents.coverage && <SourcePages source={documents.coverage} />}
      {documents.contract && <SourcePages source={documents.contract} />}
      {documents.coverage && (
        <p className="missing-source">
          If a field cannot be read or matched unambiguously, it stays unavailable. No sample
          benefits or saved payer defaults are used.
        </p>
      )}
      {!reviewing && (
        <>
          <button
            className="button button-primary"
            type="button"
            disabled={busy || !documents.coverage || !documents.fee_schedule}
            onClick={() => advance('network')}
          >
            Continue to CPT suggestions
          </button>
          {(!documents.coverage || !documents.fee_schedule) && (
            <p className="missing-source">Upload both required documents to continue.</p>
          )}
        </>
      )}
    </>
  )
}
