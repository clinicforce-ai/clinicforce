import type { PortalState } from '../portal-state'
import type { createPortalAccountActions } from '../portal-account-actions'
import { LoaderCircle, ShieldCheck } from 'lucide-react'

export function PortalIdme({
  state,
  account,
}: {
  state: PortalState
  account: ReturnType<typeof createPortalAccountActions>
}) {
  const { idmeStatus, idmeStatusLoading, workspaceTab, busy, idmeReady, idmeCanLink } = state
  const { continueWithIdme, unlinkIdme } = account
  return (
    <section
      className="section-card"
      data-workspace-panel
      aria-labelledby="idme-heading"
      hidden={workspaceTab !== 'connections'}
    >
      <div className="section-heading">
        <div>
          <p className="section-kicker">ClinicForce sign-in</p>
          <h2 id="idme-heading">ID.me</h2>
        </div>
        <ShieldCheck aria-hidden="true" className="section-icon" />
      </div>
      <p className="text-sm leading-6 text-[var(--muted-ink)]">
        ID.me is an optional secure sign-in method for this ClinicForce account. Epic, MyChart, and
        healow authorization remains separate; use the care connections below when required.
      </p>
      {idmeStatusLoading ? (
        <div className="status-panel" role="status">
          <LoaderCircle className="size-5 animate-spin" aria-hidden="true" />
          <p>Checking ID.me connection…</p>
        </div>
      ) : idmeStatus?.linked ? (
        <div className="storage-choice mt-4">
          <div>
            <strong>ID.me is linked</strong>
            <p id="idme-unlink-help">
              {idmeStatus.auth_recent
                ? 'You can use ID.me to sign in to this ClinicForce account. Disconnecting signs out every device.'
                : 'For your security, sign out and sign back in before disconnecting ID.me.'}
            </p>
          </div>
          <button
            className="button button-secondary"
            type="button"
            aria-describedby="idme-unlink-help"
            disabled={!idmeStatus.auth_recent || busy === 'idme-unlink' || busy === 'idme'}
            onClick={() => void unlinkIdme()}
          >
            {busy === 'idme-unlink' ? (
              <>
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> Disconnecting…
              </>
            ) : (
              'Disconnect ID.me'
            )}
          </button>
        </div>
      ) : idmeReady ? (
        <div className="storage-choice mt-4">
          <div>
            <strong>
              {idmeCanLink ? 'ID.me is not linked' : 'Sign in again before linking ID.me'}
            </strong>
            <p id="idme-link-help">
              {idmeCanLink
                ? 'Link ID.me to this account as another secure sign-in method.'
                : 'For your security, sign out and sign back in with your patient ID or Google, then return to Care connections.'}
            </p>
          </div>
          <button
            className="button button-primary"
            type="button"
            aria-describedby="idme-link-help"
            disabled={!idmeCanLink || busy === 'idme'}
            onClick={() => void continueWithIdme('link')}
          >
            {busy === 'idme' ? (
              <>
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> Opening ID.me…
              </>
            ) : (
              <>
                <ShieldCheck className="size-4" aria-hidden="true" /> Link ID.me
              </>
            )}
          </button>
        </div>
      ) : (
        <div className="empty-block compact mt-4" role="status">
          <ShieldCheck aria-hidden="true" />
          <p>
            <strong>ID.me is not available in this environment.</strong>
            <br />
            Your ClinicForce account and the Epic/healow connection options below still work.
          </p>
        </div>
      )}
    </section>
  )
}
