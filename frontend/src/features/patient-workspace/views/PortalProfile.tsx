import type { PortalState } from '../portal-state'
import type { createPortalAccountActions } from '../portal-account-actions'
import { CheckCircle2, LoaderCircle, ShieldCheck, UserRound } from 'lucide-react'
import { profileIsComplete } from '../profile-form'
import { type ProfileFormState } from '../profile-form'

export function PortalProfile({
  state,
  account,
}: {
  state: PortalState
  account: ReturnType<typeof createPortalAccountActions>
}) {
  const { profile, profileForm, setProfileForm, workspaceTab, busy } = state
  const { saveProfile } = account
  const demographicProfileComplete = profileIsComplete(profile)
  return (
    <section
      className="section-card demographics-card"
      data-workspace-panel
      aria-labelledby="demographics-heading"
      hidden={workspaceTab !== 'overview'}
    >
      <div className="section-heading">
        <div>
          <p className="section-kicker">Required for EHR QR sharing</p>
          <h2 id="demographics-heading">Confirm your demographics</h2>
        </div>
        <UserRound aria-hidden="true" className="section-icon" />
      </div>
      <p className="demographics-intro">
        Receiving EHRs use these details to match your QR to the right chart. Google profile names,
        when available, are only suggestions—confirm your legal name before sharing.
      </p>
      <form className="demographics-form" onSubmit={saveProfile} noValidate>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="given-name">
              Legal first name <em>Required</em>
            </label>
            <input
              id="given-name"
              autoComplete="given-name"
              value={profileForm.given_name}
              onChange={(event) =>
                setProfileForm((current) => ({ ...current, given_name: event.target.value }))
              }
            />
          </div>
          <div className="field">
            <label htmlFor="family-name">
              Legal last name <em>Required</em>
            </label>
            <input
              id="family-name"
              autoComplete="family-name"
              value={profileForm.family_name}
              onChange={(event) =>
                setProfileForm((current) => ({ ...current, family_name: event.target.value }))
              }
            />
          </div>
          <div className="field">
            <label htmlFor="birth-date">
              Date of birth <em>Required</em>
            </label>
            <input
              id="birth-date"
              type="date"
              autoComplete="bday"
              value={profileForm.birth_date}
              onChange={(event) =>
                setProfileForm((current) => ({ ...current, birth_date: event.target.value }))
              }
            />
          </div>
          <div className="field">
            <label htmlFor="administrative-gender">
              Administrative sex/gender <em>Required</em>
            </label>
            <select
              id="administrative-gender"
              autoComplete="sex"
              value={profileForm.administrative_gender}
              onChange={(event) =>
                setProfileForm((current) => ({
                  ...current,
                  administrative_gender: event.target
                    .value as ProfileFormState['administrative_gender'],
                }))
              }
            >
              <option value="female">Female</option>
              <option value="male">Male</option>
              <option value="other">Other</option>
              <option value="unknown">Unknown</option>
            </select>
          </div>
        </div>
        <div className="demographics-actions">
          <button className="button button-secondary" type="submit" disabled={busy === 'profile'}>
            {busy === 'profile' ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <ShieldCheck className="size-4" aria-hidden="true" />
            )}
            {demographicProfileComplete ? 'Update demographic profile' : 'Save demographic profile'}
          </button>
          {demographicProfileComplete && (
            <span className="demographics-complete">
              <CheckCircle2 aria-hidden="true" /> Ready for EHR QR sharing
            </span>
          )}
        </div>
      </form>
    </section>
  )
}
