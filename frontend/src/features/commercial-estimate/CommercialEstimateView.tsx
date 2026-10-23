import CommercialSourcesDetail from './CommercialSourcesDetail'
import CommercialCalculationDetail from './CommercialCalculationDetail'
import CommercialCoverageDetail from './CommercialCoverageDetail'
import CommercialServicesDetail from './CommercialServicesDetail'
import CommercialOverview from './CommercialOverview'
import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Check,
  Clock3,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { range, memberSource, visitDate, visitTime } from './format'

export default function CommercialEstimateView({ model }: { model: CommercialEstimateModel }) {
  const {
    onBack,
    onResolvePatient,
    saved,
    currentAccountNumber,
    readOnly,
    session,
    patient,
    appointments,
    result,
    error,
    patientResolutionNeeded,
    selectedVisit,
    loaded,
    busy,
    deleteError,
    cleanupPending,
    notice,
    recovering,
    load,
    estimate,
    saveReview,
    chooseVisit,
    selected,
    planName,
    planSource,
    planType,
    blueCross,
    blueCard,
    payerSource,
    tooEarly,
    policyStatus,
    cash,
    coverageTone,
    calculating,
    previousEstimate,
    emg,
    amountText,
    amountLabel,
    explanation,
    provider,
  } = model
  return (
    <section className="visit-estimator visit-visual" aria-labelledby="visit-estimate-title">
      {!readOnly && (
        <header className="visit-page-header">
          <div>
            <button className="visit-back" onClick={onBack}>
              <ArrowLeft size={17} /> Back to patient
            </button>
            <h1 id="visit-estimate-title">
              {cash ? 'Self-pay collection' : 'Insurance estimator'}
            </h1>
          </div>
          <span className="visit-practice">{session?.practice_name}</span>
        </header>
      )}
      {readOnly && (
        <header className="visit-page-header">
          <div>
            <h2 id="visit-estimate-title">
              {cash ? 'Saved self-pay estimate' : 'Saved insurance estimate'}
            </h2>
            <p>
              Recorded recommendation for {visitDate(result!.service_date)}.
              {!cash && ' Final responsibility follows the processed claim.'}
            </p>
            {saved?.saved_at && (
              <small>
                Saved {visitDate(saved.saved_at)} · {visitTime(saved.saved_at)} · Revision{' '}
                {result?.revision}
              </small>
            )}
            {saved?.legacy && (
              <p className="visit-muted">
                This older check contains the last retained result. Earlier versions and the exact
                save time were not recorded.
              </p>
            )}
          </div>
        </header>
      )}
      <div className="visit-patient-bar">
        <div>
          <span className="patient-monogram" aria-hidden="true">
            {patient?.given_name?.[0]}
            {patient?.family_name?.[0]}
          </span>
          <div>
            <strong>
              {patient ? `${patient.given_name} ${patient.family_name}` : 'Patient record'}
            </strong>
            {patient?.birth_date && <small>DOB {visitDate(patient.birth_date)}</small>}
            {(result?.patient_details?.ecw_account_number ||
              (!readOnly && patient?.ecw_account_number) ||
              currentAccountNumber) && (
              <small>
                eCW ACCT
                {readOnly && !result?.patient_details?.ecw_account_number
                  ? ' (current record)'
                  : ''}
                :{' '}
                {result?.patient_details?.ecw_account_number ||
                  (!readOnly && patient?.ecw_account_number) ||
                  currentAccountNumber}
              </small>
            )}
          </div>
        </div>
        <span className={`coverage-chip ${coverageTone}`}>
          <ShieldCheck size={18} aria-hidden="true" />
          {policyStatus}
        </span>
      </div>
      {!readOnly && (
        <div className="visit-appointment-strip">
          <CalendarDays size={22} aria-hidden="true" />
          <label>
            Scheduled visit
            <select
              value={selectedVisit}
              disabled={!!busy || !appointments.length}
              onChange={(event) => chooseVisit(event.target.value)}
            >
              {appointments.length ? (
                appointments.map((visit) => (
                  <option key={visit.id} value={visit.id}>
                    {visitDate(visit.start)} ·{' '}
                    {visitTime(visit.start, result?.appointment_context?.timezone)} ·{' '}
                    {visit.reason || 'Scheduled care'}
                  </option>
                ))
              ) : (
                <option value="">
                  {loaded ? 'No scheduled visit returned by eCW' : 'Loading appointments…'}
                </option>
              )}
            </select>
          </label>
          <button
            className="visit-icon-button"
            aria-label="Refresh visits"
            disabled={!!busy}
            onClick={() => void load()}
          >
            <RefreshCw size={18} className={busy === 'visits' ? 'visit-spin' : ''} />
          </button>
        </div>
      )}
      {result && (
        <dl className="visit-context-facts">
          <div>
            <dt>Visit type</dt>
            <dd>
              {result.visit_type === 'new'
                ? 'New patient'
                : result.visit_type === 'established'
                  ? 'Established patient'
                  : result.appointment_context?.appointment_type || 'Not returned'}
            </dd>
          </div>
          <div>
            <dt>Reason</dt>
            <dd>{result.reason || selected?.reason || 'Not returned'}</dd>
          </div>
          <div>
            <dt>Provider</dt>
            <dd>{provider || 'Name not returned by eCW'}</dd>
          </div>
          <div>
            <dt>Insurance</dt>
            <dd>
              {result.payer ||
                (result.recovery?.candidates?.length === 1
                  ? `${result.recovery.candidates[0].payer || 'Unknown payer'} · returned policy`
                  : result.recovery?.candidates?.length
                    ? `${result.recovery.candidates.length} policy suggestions`
                    : 'Not returned')}
              {result.payer && <small>Insurer source: {memberSource(payerSource)}</small>}
              <small>{planName}</small>
              {planSource && <small>Plan source: {planSource}</small>}
              {planType && (
                <small>Plan type: {planType} · inferred from the reported plan description</small>
              )}
              {blueCross && (
                <small>
                  Member home plan:{' '}
                  {blueCard
                    ? 'BlueCard indicated. Confirm the issuing state'
                    : 'In-state or out-of-state not established by eCW or Stedi'}
                </small>
              )}
            </dd>
          </div>
        </dl>
      )}
      <div className="visit-content" aria-busy={!!busy || recovering}>
        {notice && (
          <p className="visit-notice" role="status">
            {notice}
          </p>
        )}
        {deleteError && (
          <p className="visit-error" role="alert">
            {deleteError}
          </p>
        )}
        {patientResolutionNeeded && onResolvePatient && (
          <section className="visit-notice" aria-label="Confirm eCW patient">
            <h2>Confirm the eCW patient</h2>
            <p>
              The saved details need review. Search for a matching patient and confirm the name,
              date of birth and account before checking benefits.
            </p>
            <button onClick={onResolvePatient}>Find matching eCW patients</button>
          </section>
        )}
        {error && !(patientResolutionNeeded && onResolvePatient) && (
          <p className="visit-notice" role="alert">
            The estimate could not be updated: {error}{' '}
            {error.includes('Refresh saved visit details')
              ? 'Review the updated CPTs before collecting.'
              : previousEstimate
                ? 'The saved visit details and estimate are retained below. Reconfirm before collecting.'
                : 'Check the eCW visit and billing details, then retry the benefits check.'}
            {error.includes('Refresh saved visit details') && result && !readOnly ? (
              <button disabled={!!busy} onClick={() => void saveReview({ refresh_context: true })}>
                Refresh visit details <ArrowRight size={15} />
              </button>
            ) : (
              <button disabled={!!busy} onClick={() => void (selected ? estimate() : load())}>
                Try again <ArrowRight size={15} />
              </button>
            )}
          </p>
        )}
        {(!result || busy === 'estimate' || busy === 'visits') && (
          <section className="visit-overview" aria-label="Arrival collection">
            <div className="arrival-total">
              <span>{amountLabel}</span>
              <strong className="amount-pending" role="status">
                {amountText}
              </strong>
              <p>{explanation}</p>
            </div>
          </section>
        )}
        {busy === 'estimate' || busy === 'visits' ? (
          <div className="visit-empty" role="status">
            <RefreshCw className="visit-spin" size={26} aria-hidden="true" />
            <h2>{busy === 'visits' ? 'Finding your visit' : 'Putting the estimate together'}</h2>
            <p>
              Reading the appointment, patient and insurance from eCW. Checking saved eligibility
              and any current benefits needed.
            </p>
          </div>
        ) : result ? (
          <>
            <CommercialOverview model={model} result={result} />
            {emg && !cash && (
              <section
                className={`visit-overview emg-option${emg.total_min_cents == null ? ' is-unpriced' : ''}`}
                aria-label="Additional testing"
              >
                <label>
                  {!readOnly && (
                    <input
                      type="checkbox"
                      checked={emg.requested || emg.included}
                      disabled={!!busy || recovering || emg.included}
                      onChange={(event) => void saveReview({ emg_required: event.target.checked })}
                    />
                  )}{' '}
                  EMG / nerve testing{' '}
                  {readOnly
                    ? emg.requested || emg.included
                      ? 'included in estimate'
                      : 'not selected'
                    : 'required'}
                </label>
                <p>{calculating ? 'Calculating additional testing cost…' : emg.message}</p>
                {(emg.requested || emg.included) && !calculating && !error && (
                  <dl className="collection-components">
                    <div>
                      <dt>Additional estimated patient cost</dt>
                      <dd>
                        {emg.additional_min_cents != null && emg.additional_max_cents != null
                          ? range(emg.additional_min_cents, emg.additional_max_cents)
                          : 'Not yet calculated'}
                      </dd>
                    </div>
                    <div>
                      <dt>Planning total with testing</dt>
                      <dd>
                        {emg.total_min_cents != null && emg.total_max_cents != null
                          ? range(emg.total_min_cents, emg.total_max_cents)
                          : 'Not yet calculated'}
                      </dd>
                    </div>
                  </dl>
                )}
                {!readOnly && (emg.requested || emg.included) && (
                  <button
                    onClick={() => {
                      const detail = document.getElementById(
                        'visit-detail-services',
                      ) as HTMLDetailsElement
                      detail.open = true
                      detail.scrollIntoView({ block: 'start' })
                    }}
                  >
                    Specify testing scope
                  </button>
                )}
              </section>
            )}
            {result.identity?.status === 'warning' && (
              <p className="visit-identity-notice">
                <Check size={16} aria-hidden="true" />
                {result.identity.message}
              </p>
            )}
            {recovering && (
              <p role="status" className="visit-muted">
                Checking additional coverage in the background…
              </p>
            )}
            {readOnly && result.recovery?.state === 'pending' && (
              <p className="visit-muted">
                An additional coverage search was pending when this version was saved.
              </p>
            )}
            <div className="visit-detail-sections">
              <CommercialServicesDetail model={model} result={result} />
              <CommercialCoverageDetail model={model} result={result} />
              <CommercialCalculationDetail model={model} result={result} />
              <CommercialSourcesDetail model={model} result={result} />
            </div>
            <footer className="visit-result-footer">
              <span>
                <Clock3 size={14} aria-hidden="true" />
                {tooEarly
                  ? `Benefits check opens ${visitDate(result.eligibility_timing!.check_from!)}`
                  : result.checked_at
                    ? `Benefits checked ${visitDate(result.checked_at)} · ${visitTime(result.checked_at)}`
                    : 'Benefits not yet returned'}
              </span>
              {!readOnly && (
                <button
                  disabled={!!busy || cleanupPending || recovering}
                  onClick={() => void estimate(true)}
                >
                  <RefreshCw size={16} />
                  Check again
                </button>
              )}
            </footer>
          </>
        ) : (
          !error &&
          !busy && (
            <div className="visit-empty">
              <CalendarDays size={28} />
              <h2>{selected ? 'Preparing this visit' : 'No upcoming visit returned'}</h2>
              <p>
                {selected
                  ? 'Reading the appointment and benefits.'
                  : 'Clinicforce needs a scheduled eCW visit to match the date, provider and insurance.'}
              </p>
              <button onClick={() => void load()}>
                <RefreshCw size={16} />
                Refresh visits
              </button>
            </div>
          )
        )}
      </div>
    </section>
  )
}
