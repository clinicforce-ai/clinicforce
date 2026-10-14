import type { PortalState } from '../portal-state'
import type { createPortalAccountActions } from '../portal-account-actions'
import {
  KeyRound,
  LoaderCircle,
  QrCode,
  ShieldAlert,
  ShieldCheck,
  UserRound,
  X,
} from 'lucide-react'
import { profileIsComplete } from '../profile-form'
import * as Dialog from '@radix-ui/react-dialog'

export function PortalShare({
  state,
  account,
}: {
  state: PortalState
  account: ReturnType<typeof createPortalAccountActions>
}) {
  const {
    session,
    profile,
    sharePasscode,
    setSharePasscode,
    link,
    setLink,
    ecwCompatibilityConfirmOpen,
    setEcwCompatibilityConfirmOpen,
    epicRecord,
    workspaceTab,
    busy,
  } = state
  const { generateLink } = account
  if (!session) return null
  const recordsAvailable = session.documents.length + session.records.length
  const epicResources = (epicRecord?.bundle.entry ?? [])
    .map((entry) => entry.resource)
    .filter(Boolean)
  const canCreateShare = recordsAvailable > 0 || epicResources.length > 0
  const demographicProfileComplete = profileIsComplete(profile)
  return (
    <>
      <aside
        className="share-card"
        data-workspace-panel
        aria-labelledby="share-heading"
        hidden={workspaceTab !== 'overview'}
      >
        <div className="share-icon">
          <QrCode aria-hidden="true" />
        </div>
        <p className="eyebrow">Share with your clinician</p>
        <h2 id="share-heading">Create a secure QR code</h2>
        <p>
          {epicRecord
            ? `This one QR combines ${recordsAvailable} workspace item${recordsAvailable === 1 ? '' : 's'} with ${epicResources.length} current MyChart resource${epicResources.length === 1 ? '' : 's'}.`
            : `It includes the ${recordsAvailable} item${recordsAvailable === 1 ? '' : 's'} in this workspace. You do not need to connect MyChart to share with eClinicalWorks.`}{' '}
          Give the clinician your passcode separately.
        </p>
        {!demographicProfileComplete && (
          <p className="share-requirement">
            <UserRound aria-hidden="true" /> Save the demographic profile first. It is required so
            the receiving EHR can match the patient safely.
          </p>
        )}
        <label htmlFor="share-passcode">Four-digit passcode</label>
        <div className="passcode-field">
          <KeyRound aria-hidden="true" />
          <input
            id="share-passcode"
            type="password"
            inputMode="numeric"
            autoComplete="off"
            maxLength={4}
            value={sharePasscode}
            onChange={(event) => setSharePasscode(event.target.value.replace(/\D/g, ''))}
            placeholder="0000"
          />
        </div>
        <button
          className="button button-primary w-full"
          type="button"
          onClick={() => void generateLink()}
          disabled={busy === 'share' || !canCreateShare || !demographicProfileComplete}
        >
          {busy === 'share' ? (
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <QrCode aria-hidden="true" className="size-4" />
          )}{' '}
          Generate combined QR code
        </button>
        <p className="share-footnote">
          <ShieldCheck aria-hidden="true" /> The clinician needs both this QR code and your
          passcode.
        </p>
        <div className="ecw-test-warning" role="note">
          <ShieldAlert aria-hidden="true" />
          <p>
            <strong>eClinicalMobile compatibility test</strong> creates a bare SHL with no passcode.
            Anyone who photographs it can retrieve this snapshot until it expires in 3 hours.
          </p>
        </div>
        <button
          className="button button-secondary ecw-test-button w-full"
          type="button"
          onClick={() => setEcwCompatibilityConfirmOpen(true)}
          disabled={busy === 'share' || !canCreateShare || !demographicProfileComplete}
        >
          {busy === 'share' ? (
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <QrCode aria-hidden="true" className="size-4" />
          )}{' '}
          Generate eClinicalMobile test QR
        </button>
      </aside>

      <Dialog.Root
        open={Boolean(link)}
        onOpenChange={(open) => {
          if (!open) setLink(null)
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content" aria-describedby="qr-description">
            <Dialog.Close className="dialog-close" aria-label="Close QR code">
              <X aria-hidden="true" />
            </Dialog.Close>
            <p className="eyebrow">Secure link created</p>
            <Dialog.Title>
              {link?.share_mode === 'ecw_compat'
                ? 'eClinicalMobile compatibility QR'
                : 'Show this QR code to your clinician'}
            </Dialog.Title>
            <Dialog.Description id="qr-description">
              {link?.share_mode === 'ecw_compat'
                ? 'This is a bare SMART Health Link with no passcode, created only to test eClinicalMobile compatibility. It expires in 3 hours.'
                : 'This is your one combined record. Your clinician can scan it with ClinicForce, then enter the four-digit passcode you selected.'}
            </Dialog.Description>
            {link && (
              <img
                className="qr-image"
                src={`data:image/png;base64,${link.qr_code_base64}`}
                alt="Smart Health Link QR code for the current health record"
              />
            )}
            {link?.share_mode === 'ecw_compat' ? (
              <div className="dialog-note dialog-note-warning">
                <ShieldAlert aria-hidden="true" />
                <span>No passcode: use only for the live eClinicalMobile compatibility test.</span>
              </div>
            ) : (
              <div className="dialog-note">
                <KeyRound aria-hidden="true" />
                <span>
                  Passcode: <strong>{sharePasscode}</strong>
                </span>
              </div>
            )}
            <Dialog.Close className="button button-secondary w-full">Done</Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={ecwCompatibilityConfirmOpen} onOpenChange={setEcwCompatibilityConfirmOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content
            className="dialog-content"
            aria-describedby="ecw-compatibility-description"
          >
            <Dialog.Close
              className="dialog-close"
              aria-label="Close eClinicalMobile compatibility confirmation"
            >
              <X aria-hidden="true" />
            </Dialog.Close>
            <p className="eyebrow">Compatibility test</p>
            <Dialog.Title>Generate a no-passcode health QR?</Dialog.Title>
            <Dialog.Description id="ecw-compatibility-description">
              This QR uses the bare SMART Health Link format required for this eClinicalMobile scan
              test. It expires in 3 hours, but anyone with a copy can retrieve the encrypted record
              during that time.
            </Dialog.Description>
            <div className="dialog-note dialog-note-warning">
              <ShieldAlert aria-hidden="true" />
              <span>Use this only while testing with a clinician you trust.</span>
            </div>
            <div className="dialog-actions">
              <Dialog.Close className="button button-secondary">Cancel</Dialog.Close>
              <button
                className="button button-primary"
                type="button"
                onClick={() => {
                  setEcwCompatibilityConfirmOpen(false)
                  void generateLink('ecw_compat')
                }}
              >
                Generate test QR
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  )
}
