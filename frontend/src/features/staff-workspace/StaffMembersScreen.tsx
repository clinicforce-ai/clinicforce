import type { StaffWorkspaceModel } from './useStaffWorkspaceModel'
import { Plus, RefreshCw } from 'lucide-react'
import StaffShareLink from './StaffShareLink'
import {
  createStaffInvitation,
  getStaffInvitations,
  revokeStaffInvitation,
  addStaffMember,
} from '../../services/api/staff-clinic'
import { dateLabel } from './staff-workspace-utils'

export default function StaffMembersScreen({
  model,
  session,
}: {
  model: StaffWorkspaceModel
  session: NonNullable<StaffWorkspaceModel['session']>
}) {
  const {
    invitations,
    setInvitations,
    invitation,
    setInvitation,
    busy,
    setNotice,
    heading,
    perform,
  } = model
  return (
    <div className="staff-narrow">
      <header className="staff-header">
        <div>
          <div className="staff-eyebrow">Clinic administration</div>
          <h1 tabIndex={-1} ref={heading}>
            Staff accounts and invitations
          </h1>
          <p className="staff-muted">
            Grant colleagues access to the insurance inbox for {session.practice_name}. Invite them
            to choose their own password, or create an account below.
          </p>
        </div>
      </header>
      <form
        className="staff-panel"
        onSubmit={(event) => {
          event.preventDefault()
          const role = new FormData(event.currentTarget).get('role') === 'admin' ? 'admin' : 'staff'
          void perform('invitation', async () => {
            setInvitation(await createStaffInvitation(role))
          })
        }}
      >
        <h2>Invite a colleague</h2>
        <label className="staff-field">
          <span>Invitation access level</span>
          <select name="role" disabled={Boolean(busy)}>
            <option value="staff">Staff · Patient insurance</option>
            <option value="admin">Administrator · Patient insurance and staff accounts</option>
          </select>
        </label>
        <button className="staff-primary" disabled={Boolean(busy)}>
          Create staff invitation
        </button>
      </form>
      {invitation && (
        <section className="staff-panel">
          <StaffShareLink
            key={invitation.id}
            kind="invitations"
            id={invitation.id}
            url={invitation.url}
            expiresAt={invitation.expires_at}
            clinicName={session.practice_name}
          />
        </section>
      )}
      <form
        className="staff-panel"
        onSubmit={(event) => {
          event.preventDefault()
          const form = event.currentTarget
          const data = new FormData(form)
          void perform('member', async () => {
            await addStaffMember({
              username: String(data.get('username')).trim(),
              display_name: String(data.get('display_name')).trim(),
              password: String(data.get('password')),
              role: data.get('role') === 'admin' ? 'admin' : 'staff',
            })
            form.reset()
            setNotice(
              'Staff account created. Share the credentials through your clinic’s approved channel.',
            )
          })
        }}
      >
        <h2>Create a staff account directly</h2>
        <label className="staff-field">
          <span>Display name</span>
          <input name="display_name" required disabled={Boolean(busy)} />
        </label>
        <label className="staff-field">
          <span>Username</span>
          <input name="username" autoComplete="off" required disabled={Boolean(busy)} />
        </label>
        <label className="staff-field">
          <span>Password</span>
          <input
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
            disabled={Boolean(busy)}
          />
          <small>Use at least 12 characters.</small>
        </label>
        <label className="staff-field">
          <span>Access level</span>
          <select name="role" disabled={Boolean(busy)}>
            <option value="staff">Staff · Patient insurance</option>
            <option value="admin">Administrator · Patient insurance and staff accounts</option>
          </select>
        </label>
        <button className="staff-primary" disabled={Boolean(busy)}>
          <Plus size={18} /> Create staff account
        </button>
      </form>
      <section className="staff-panel">
        <div className="staff-section-heading">
          <h2>Staff invitations</h2>
          <button
            disabled={Boolean(busy)}
            onClick={() =>
              void perform('refresh-invitations', async () => {
                setInvitations((await getStaffInvitations()).invitations)
              })
            }
          >
            <RefreshCw size={17} /> Refresh invitations
          </button>
        </div>
        {invitations === null ? (
          <p className="staff-muted">Invitations have not loaded.</p>
        ) : invitations.length === 0 ? (
          <p className="staff-muted">No invitations created yet.</p>
        ) : (
          invitations.map((item) => (
            <div className="staff-document" key={item.id}>
              <span>
                <span>
                  <strong>
                    {item.role === 'admin' ? 'Administrator' : 'Staff'} invitation · {item.status}
                  </strong>
                  <small>
                    Created {dateLabel(item.created_at)} · Expires {dateLabel(item.expires_at)}
                  </small>
                </span>
              </span>
              {item.status === 'open' && (
                <button
                  disabled={Boolean(busy)}
                  onClick={() =>
                    void perform('revoke-invitation', async () => {
                      await revokeStaffInvitation(item.id)
                      if (invitation?.id === item.id) setInvitation(null)
                      setInvitations((await getStaffInvitations()).invitations)
                      setNotice('Staff invitation revoked.')
                    })
                  }
                >
                  Revoke invitation
                </button>
              )}
            </div>
          ))
        )}
      </section>
    </div>
  )
}
