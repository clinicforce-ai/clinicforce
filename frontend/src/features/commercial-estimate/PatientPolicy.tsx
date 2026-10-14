import { ArrowRight } from 'lucide-react'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import InsuranceCards from '../insurance-card/InsuranceCards'
import { ApplicableBenefits, AppointmentNotes } from './EstimatePanels'
import { memberSource, visitDate } from './format'

export default function PatientPolicy({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const { intakeId, readOnly, patient, payerSubscriber, payerPatient, policyDates } = model
  return (
    <>
      <section className="patient-policy">
        <div className="policy-relationship">
          <div>
            <span>Patient</span>
            <strong>
              {payerPatient?.lastName
                ? [payerPatient.firstName, payerPatient.lastName].filter(Boolean).join(' ')
                : patient
                  ? `${patient.given_name} ${patient.family_name}`
                  : 'Not returned'}
            </strong>
            <small>
              eCW: {patient?.given_name} {patient?.family_name}
            </small>
          </div>
          <ArrowRight size={22} aria-hidden="true" />
          <div>
            <span>
              Policyholder ·{' '}
              {result.insurance_details?.payer_relationship?.toLowerCase() ||
                (
                  {
                    '18': 'self',
                    '01': 'spouse',
                    '19': 'child',
                    G8: 'other',
                  } as Record<string, string>
                )[result.insurance_details?.relationship || ''] ||
                'relationship not returned'}
            </span>
            <strong>
              {payerSubscriber?.lastName
                ? [payerSubscriber.firstName, payerSubscriber.lastName].filter(Boolean).join(' ')
                : [
                    result.insurance_details?.subscriber_first,
                    result.insurance_details?.subscriber_last,
                  ]
                    .filter(Boolean)
                    .join(' ') || 'Not returned'}
            </strong>
          </div>
        </div>
        <dl>
          <div>
            <dt>Member ID</dt>
            <dd>
              {result.insurance_details?.member_id || 'Not returned'}
              {result.insurance_details?.member_id && (
                <small>Source: {memberSource(result.insurance_details.member_source)}</small>
              )}
            </dd>
          </div>
          {(result.insurance_details?.group_number || result.plan_details?.stedi.group_number) && (
            <div>
              <dt>Group</dt>
              <dd>
                {result.insurance_details?.group_number || result.plan_details?.stedi.group_number}
                <small>
                  Source:{' '}
                  {result.insurance_details?.group_number
                    ? memberSource(result.insurance_details.group_source)
                    : result.benefit_source || 'Payer response'}
                </small>
              </dd>
            </div>
          )}
          {policyDates?.start && (
            <div>
              <dt>Coverage dates</dt>
              <dd>
                {policyDates.start}
                {policyDates.end ? ` – ${policyDates.end}` : ' onward'}
              </dd>
            </div>
          )}
          <div>
            <dt>Contact</dt>
            <dd>
              {result.patient_details?.phone || 'Phone not returned'}
              <small>{result.patient_details?.email}</small>
            </dd>
          </div>
          <div>
            <dt>Address</dt>
            <dd>
              {Object.values(result.patient_details?.address || {})
                .filter(Boolean)
                .join(', ') || 'Not returned'}
            </dd>
          </div>
          <div>
            <dt>Payer patient DOB</dt>
            <dd>
              {payerPatient?.dateOfBirth ? visitDate(payerPatient.dateOfBirth) : 'Not returned'}
            </dd>
          </div>
        </dl>
        {!!result.identity?.differences.length && (
          <p className="visit-muted">
            Both names are retained with their source.{' '}
            {result.identity.status === 'reconciled'
              ? 'A verified relationship is on file.'
              : result.identity.status === 'warning'
                ? result.identity.message
                : 'The patient details need reconciliation before collection.'}
          </p>
        )}
        {intakeId && (
          <InsuranceCards
            key={intakeId}
            intakeId={intakeId}
            background
            inline
            savedOnly={readOnly}
          />
        )}
        {result.card_check && (
          <p className="visit-muted" role="status">
            {
              (
                {
                  filled_missing: `Card OCR filled missing ${result.card_check.fields_added.map((field) => field.replaceAll('_', ' ')).join(', ')} before the payer check.`,
                  matched:
                    'Card OCR matched the available eCW policy details before the payer check.',
                  conflict:
                    result.recovery?.outcome === 'recovered'
                      ? 'The payer confirmed the card-based check; eCW was not changed.'
                      : result.recovery?.state === 'pending'
                        ? 'The card and eCW differ. A single card-based payer check is in progress.'
                        : result.card_check.alternative_supported
                          ? 'The card and eCW differ. The card-based check did not confirm this policy; review both sources.'
                          : 'The card and eCW differ. There was not enough matching evidence to send a card-based check.',
                  unverified:
                    'The card text could not be matched to the eCW patient or policy. No card fields were added.',
                  not_found:
                    'No insurance card was available to compare with eCW before the payer check.',
                  multiple_cards: 'Multiple card sets need review. No new Stedi check was sent.',
                  unavailable: 'The insurance card could not be read before the payer check.',
                  read: 'The insurance card was read before the payer check.',
                } as Record<string, string>
              )[result.card_check.status]
            }
          </p>
        )}
        {result.card_check?.payer_comparison && (
          <p className="visit-muted">
            Insurer comparison: eCW says {result.card_check.payer_comparison.ecw}; card says{' '}
            {result.card_check.payer_comparison.card}. Confirm which route covers this visit.
          </p>
        )}
      </section>
      <ApplicableBenefits result={result} readOnly={readOnly} />
      <AppointmentNotes result={result} />
    </>
  )
}
