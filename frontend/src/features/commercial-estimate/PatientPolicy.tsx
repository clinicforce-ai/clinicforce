import { ArrowRight } from 'lucide-react'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import InsuranceCards from '../insurance-card/InsuranceCards'
import { ApplicableBenefits, AppointmentNotes } from './EstimatePanels'
import { memberSource, visitDate } from './format'
import BillingProtectionEntry from './BillingProtectionEntry'

const identityFieldLabels: Record<string, string> = {
  first_name: 'patient first name',
  last_name: 'patient surname',
  birth_date: 'patient date of birth',
  member_id: 'member ID',
  subscriber_first: 'subscriber first name',
  subscriber_last: 'subscriber surname',
  relationship: 'subscriber relationship',
}

export default function PatientPolicy({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const {
    intakeId,
    readOnly,
    patient,
    payerSubscriber,
    payerPatient,
    policyDates,
    session,
    busy,
    saveReview,
  } = model
  const mismatchFields = [
    ...(result.identity?.differences || []),
    ...(result.identity?.policy_differences || []),
  ].map((row) => identityFieldLabels[row.field] || row.field.replaceAll('_', ' '))
  const uhcCard = /\b(?:UHC|United\s*Health\s*Care)\b/i.test(
    result.stedi_payer?.name || result.payer || '',
  )
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
          {result.stedi_payer?.name && (
            <div>
              <dt>Local eligibility route</dt>
              <dd>{result.stedi_payer.name}</dd>
            </div>
          )}
          {result.plan_details?.stedi.verification_payer && (
            <div>
              <dt>
                {/blue cross|bcbs/i.test(result.stedi_payer?.name || result.payer || '')
                  ? 'BlueCard home plan verification party'
                  : 'Payer verification party'}
              </dt>
              <dd>
                {result.plan_details.stedi.verification_payer.name}
                <small>Source: Stedi medical coverage status</small>
              </dd>
            </div>
          )}
          {result.plan_details?.stedi.group_name && (
            <div>
              <dt>Reported group</dt>
              <dd>{result.plan_details.stedi.group_name}</dd>
            </div>
          )}
          {!!result.plan_details?.stedi.names?.length && (
            <div>
              <dt>Medical product</dt>
              <dd>{result.plan_details.stedi.names.join(' · ')}</dd>
            </div>
          )}
          {!!result.plan_details?.stedi.supplementary_names?.length && (
            <div>
              <dt>Separate supplementary coverage</dt>
              <dd>{result.plan_details.stedi.supplementary_names.join(' · ')}</dd>
            </div>
          )}
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
            <dt>Payer patient DOB</dt>
            <dd>
              {payerPatient?.dateOfBirth ? visitDate(payerPatient.dateOfBirth) : 'Not returned'}
            </dd>
          </div>
        </dl>
        {result.benefits?.active != null && result.identity?.status === 'review' && (
          <p className="visit-muted" role="status">
            eCW and payer differ on{' '}
            {mismatchFields.length
              ? [...new Set(mismatchFields)].join(', ')
              : 'patient or subscriber identity'}
            .{' '}
            {result.identity.policy_differences?.length
              ? 'The policyholder shown above comes from the payer response. Reconcile the eCW policy record with the payer before using member balances.'
              : 'Reconcile these fields with the payer before using member balances.'}
          </p>
        )}
        {result.identity?.status !== 'review' && !!result.identity?.differences.length && (
          <p className="visit-muted">
            Both names are retained with their source.{' '}
            {result.identity.status === 'reconciled'
              ? 'A verified relationship is on file.'
              : result.identity.status === 'warning'
                ? result.identity.message
                : ''}
          </p>
        )}
        {intakeId && (
          <InsuranceCards
            key={intakeId}
            intakeId={intakeId}
            background
            inline
            historicalEstimate={readOnly}
          />
        )}
        {result.card_check && (
          <p className="visit-muted" role="status">
            {
              (
                {
                  filled_missing: result.card_check.fields_added?.length
                    ? `Card OCR filled missing ${result.card_check.fields_added.map((field) => field.replaceAll('_', ' ')).join(', ')} before the payer check.`
                    : 'Card OCR supplied policy details before the payer check.',
                  matched:
                    'Card OCR matched the available eCW policy details before the payer check.',
                  conflict:
                    result.recovery?.outcome === 'recovered'
                      ? 'The payer confirmed the card-based check. eCW was not changed.'
                      : result.recovery?.state === 'pending'
                        ? 'The card and eCW differ. A single card-based payer check is in progress.'
                        : result.card_check.alternative_supported
                          ? 'The card and eCW differ. The card-based check did not confirm this policy. Review both sources.'
                          : 'The card and eCW differ. There was not enough matching evidence to send a card-based check.',
                  unverified: result.card_check.reference_only
                    ? 'The patient-specific card upload awaits staff confirmation. Its plan details are a conditional reference only; no card fields were used for the payer check.'
                    : 'The card text could not be matched to the eCW patient or policy. No card fields were added.',
                  not_found:
                    result.card_check.failure_reason === 'ecw_no_matching_card_document'
                      ? uhcCard
                        ? 'eCW returned no insurance-card image. If your clinic has UHC Provider Portal access, open this member’s Eligibility record for the digital ID card, upload it here, and ask eCW to expose type 64290-0.'
                        : 'At this estimate check, eCW returned no insurance-card document or image. Check the card in eCW and ask eCW to expose type 64290-0, or upload the card here and rerun the estimate.'
                      : result.card_check.failure_reason === 'ecw_patient_unlinked'
                        ? 'No eCW patient was linked for card lookup. Link the patient or upload the card here.'
                        : 'No insurance card was available to compare with eCW before the payer check.',
                  multiple_cards: 'Multiple card sets need review. No new Stedi check was sent.',
                  unavailable:
                    result.card_check.failure_reason === 'card_read_timeout'
                      ? 'The insurance-card read timed out. Retry the card preview or upload the card here.'
                      : 'The insurance card could not be read. Retry the card preview or upload the card here.',
                  read: 'The insurance card was read before the payer check.',
                } as Record<string, string>
              )[result.card_check.status]
            }
          </p>
        )}
        {result.card_check?.payer_comparison && (
          <p className="visit-muted">
            Insurer comparison: eCW says {result.card_check.payer_comparison.ecw}. Card says{' '}
            {result.card_check.payer_comparison.card}. Confirm which route covers this visit.
          </p>
        )}
      </section>
      {!readOnly &&
        session?.role === 'admin' &&
        result.plan_details?.stedi.names?.length === 1 &&
        /\b(?:rppo|lppo|regional ppo|local ppo)\b/i.test(result.plan_details.stedi.names[0]) && (
          <BillingProtectionEntry result={result} busy={!!busy} save={saveReview} />
        )}
      <ApplicableBenefits result={result} readOnly={readOnly} />
      <AppointmentNotes result={result} />
    </>
  )
}
