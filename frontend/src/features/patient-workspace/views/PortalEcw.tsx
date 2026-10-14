import type { PortalState } from '../portal-state'
import type { createPortalProviderActions } from '../portal-provider-actions'
import { LoaderCircle } from 'lucide-react'
import { ProviderLogo } from '../ProviderLogo'
import { HealowRecordReview } from '../HealowRecordReview'

export function PortalEcw({
  state,
  provider,
}: {
  state: PortalState
  provider: ReturnType<typeof createPortalProviderActions>
}) {
  const {
    ecwPracticeCode,
    setEcwPracticeCode,
    ecwPracticeCodeError,
    setEcwPracticeCodeError,
    ecwRecord,
    workspaceTab,
    busy,
  } = state
  const {
    validateEcwPracticeCode,
    connectEcw,
    reloadEcwRecords,
    importConfirmedEcw,
    discardEcwConnection,
  } = provider
  return (
    <section
      className="section-card"
      data-workspace-panel
      aria-labelledby="ecw-heading"
      hidden={workspaceTab !== 'connections'}
    >
      <div className="section-heading">
        <div>
          <p className="section-kicker">Live patient-authorized connection</p>
          <h2 id="ecw-heading">eClinicalWorks / healow</h2>
        </div>
        <div className="provider-lockup provider-lockup-dual">
          <ProviderLogo provider="ecw" />
          <ProviderLogo provider="healow" />
        </div>
      </div>
      {!ecwRecord ? (
        <>
          <p className="text-sm leading-6 text-[var(--muted-ink)]">
            Enter the six-character OEM code for the eClinicalWorks practice where you receive care.
            ClinicForce derives that practice’s FHIR endpoint and opens its secure healow sign-in;
            it never asks you for an eCW password.
          </p>
          <div className="storage-choice mt-4">
            <div className="flex-1">
              <label htmlFor="ecw-practice-code">
                <strong>eClinicalWorks practice OEM code</strong>
              </label>
              <input
                id="ecw-practice-code"
                value={ecwPracticeCode}
                onChange={(event) => {
                  setEcwPracticeCode(event.target.value.toUpperCase())
                  if (ecwPracticeCodeError) setEcwPracticeCodeError('')
                }}
                onBlur={(event) =>
                  setEcwPracticeCodeError(
                    validateEcwPracticeCode(event.target.value.trim().toUpperCase()) ?? '',
                  )
                }
                placeholder="e.g. BEDBAA"
                autoComplete="off"
                spellCheck="false"
                maxLength={6}
                aria-describedby="ecw-practice-code-help"
                aria-invalid={Boolean(ecwPracticeCodeError)}
              />
              {ecwPracticeCodeError ? (
                <p id="ecw-practice-code-help" className="field-help mt-2" role="alert">
                  {ecwPracticeCodeError}
                </p>
              ) : (
                <p id="ecw-practice-code-help" className="field-help mt-2">
                  This is supplied by the practice. It may contain letters, such as BEDBAA.
                </p>
              )}
            </div>
            <button
              className="button button-primary"
              type="button"
              disabled={busy === 'ecw-connect' || busy === 'ecw-load'}
              onClick={() => void connectEcw()}
            >
              {busy === 'ecw-connect' || busy === 'ecw-load' ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                'Continue to healow'
              )}
            </button>
          </div>
          {(busy === 'ecw-connect' || busy === 'ecw-load') && (
            <div className="feedback feedback-success mt-3" role="status">
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              <span>
                {busy === 'ecw-connect'
                  ? 'healow opened in a secure window. Complete sign-in there, then return here.'
                  : 'Loading the records you agreed to share…'}
              </span>
            </div>
          )}
        </>
      ) : (
        <>
          <HealowRecordReview
            key={ecwRecord.source.retrieved_at}
            record={ecwRecord}
            busy={busy !== null}
            onRetry={() => void reloadEcwRecords()}
            onDisconnect={() => void discardEcwConnection()}
            onImport={() => void importConfirmedEcw()}
          />
        </>
      )}
    </section>
  )
}
