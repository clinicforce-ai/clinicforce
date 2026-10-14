import CommercialEstimateLoading from '../commercial-estimate/CommercialEstimateLoading'
import StaffInboxScreen from './StaffInboxScreen'
import StaffEcwLookupScreen from './StaffEcwLookupScreen'
import StaffCardIntakeScreen from './StaffCardIntakeScreen'
import StaffPatientReviewScreen from './StaffPatientReviewScreen'
import StaffPatientMatchScreen from './StaffPatientMatchScreen'
import StaffPatientRecordScreen from './StaffPatientRecordScreen'
import StaffMembersScreen from './StaffMembersScreen'
import {
  ArrowLeft,
  ChevronRight,
  FolderOpen,
  Inbox,
  LogOut,
  QrCode,
  Upload,
  Users,
} from 'lucide-react'
import ClinicLibrary from '../staff-library/ClinicLibrary'
import InsuranceCardScanner from '../insurance-card/InsuranceCardScanner'
import StaffUploadLinks from './StaffUploadLinks'
import ClinicalPatientDashboard from './ClinicalPatientDashboard'
import { loginStaff, logoutStaff } from '../../services/api/staff-session'
import type { StaffWorkspaceModel } from './useStaffWorkspaceModel'
import { emptyFields } from './staff-workspace-utils'

export default function StaffWorkspaceView({ model }: { model: StaffWorkspaceModel }) {
  const {
    onBack,
    onCreateClinic,
    onJoinClinic,
    onOpenPatientQr,
    historyPatientId,
    linkPatient,
    setLinkPatient,
    linkBack,
    setLinkBack,
    setInvitations,
    setInvitation,
    session,
    setSession,
    restoring,
    screen,
    setScreen,
    setIntakes,
    intake,
    setIntake,
    setFields,
    setCandidates,
    setQuery,
    setStatus,
    setFiles,
    lookupDetails,
    setEcwSuggestions,
    setEcwSelection,
    setEcwConfirmed,
    camera,
    setCamera,
    library,
    setLibrary,
    busy,
    setInboxLoaded,
    setNextOffset,
    setError,
    setNotice,
    heading,
    perform,
    navigate,
    refreshInbox,
    requestPatientCard,
    openLibrary,
    openIntake,
    saveCard,
    alerts,
  } = model
  if (restoring)
    return (
      <section className="staff-workspace">
        <div className="staff-login">
          <p role="status">Restoring your staff session…</p>
        </div>
      </section>
    )
  if (!session)
    return (
      <section className="staff-workspace">
        <div className="staff-login">
          <button type="button" className="staff-text-button" onClick={onBack}>
            <ArrowLeft size={18} /> Back
          </button>
          <div className="staff-eyebrow">ClinicForce · Staff workspace</div>
          <h1 tabIndex={-1} ref={heading}>
            Sign in to your clinic
          </h1>
          <p className="staff-muted">
            Review submitted insurance cards and manage patient insurance. Patients can be added
            without a ClinicForce account.
          </p>
          {alerts}
          <form
            onSubmit={(event) => {
              event.preventDefault()
              const form = event.currentTarget
              const data = new FormData(form)
              void perform('login', async () => {
                const result = await loginStaff(
                  String(data.get('username')).trim(),
                  String(data.get('password')),
                )
                form.reset()
                setSession(result.staff)
                setScreen('patients')
              })
            }}
          >
            <label className="staff-field">
              <span>Email or staff username</span>
              <input name="username" autoComplete="username" required disabled={Boolean(busy)} />
            </label>
            <label className="staff-field">
              <span>Password</span>
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                disabled={Boolean(busy)}
              />
            </label>
            <button className="staff-primary staff-full" disabled={Boolean(busy)}>
              Sign in
            </button>
          </form>
          <div className="staff-login-links">
            <button type="button" onClick={onCreateClinic} disabled={Boolean(busy)}>
              Create a clinic account <ChevronRight size={17} />
            </button>
            <button type="button" onClick={onJoinClinic} disabled={Boolean(busy)}>
              Join your clinic <ChevronRight size={17} />
            </button>
            <button
              type="button"
              className="staff-text-button"
              onClick={onOpenPatientQr}
              disabled={Boolean(busy)}
            >
              <QrCode size={18} /> Open a patient QR
            </button>
          </div>
          <p className="staff-small staff-muted">
            Clinic administrators can invite colleagues or create staff accounts. A patient QR opens
            only the records shared with its passcode.
          </p>
        </div>
      </section>
    )

  if (busy === 'lookup')
    return (
      <CommercialEstimateLoading practiceName={session.practice_name} patient={lookupDetails} />
    )

  if (screen === 'add' && camera)
    return (
      <section className="staff-workspace staff-scan-focus">
        <div className="staff-scan-content">
          <h1 className="staff-sr-only" tabIndex={-1} ref={heading}>
            Scan insurance card
          </h1>
          {alerts}
          <InsuranceCardScanner onCaptured={saveCard} onCancel={() => setCamera(false)} />
        </div>
      </section>
    )

  return (
    <section className="staff-workspace">
      <aside className="staff-sidebar">
        <div>
          <div className="staff-brand">
            clinic<span>force</span>
          </div>
          <p className="staff-small staff-muted">{session.practice_name}</p>
        </div>
        <nav aria-label="Staff navigation">
          <button
            className={screen === 'patients' ? 'staff-current' : ''}
            onClick={() => navigate('patients')}
            disabled={Boolean(busy)}
          >
            <Users size={18} /> Clinical patients
          </button>
          <button
            className={screen === 'inbox' ? 'staff-current' : ''}
            onClick={() => {
              navigate('inbox')
              void perform('refresh', refreshInbox)
            }}
            disabled={Boolean(busy)}
          >
            <Inbox size={18} /> Insurance inbox
          </button>
          <button
            className={screen === 'links' ? 'staff-current' : ''}
            onClick={() => {
              setLinkBack('inbox')
              setLinkPatient(null)
              navigate('links')
            }}
            disabled={Boolean(busy)}
          >
            <Upload size={18} /> Patient upload links
          </button>
          <button
            className={screen === 'library' ? 'staff-current' : ''}
            onClick={openLibrary}
            disabled={Boolean(busy)}
          >
            <FolderOpen size={18} /> Clinic library
          </button>
          {session.role === 'admin' && (
            <button
              className={screen === 'members' ? 'staff-current' : ''}
              onClick={() => navigate('members')}
              disabled={Boolean(busy)}
            >
              <Users size={18} /> Staff access
            </button>
          )}
          <button onClick={onBack} disabled={Boolean(busy)}>
            <ArrowLeft size={18} /> ClinicForce home
          </button>
        </nav>
        <div className="staff-user">
          <strong>{session.display_name}</strong>
          <span>{session.role === 'admin' ? 'Clinic administrator' : 'Clinic staff'}</span>
          <button
            className="staff-text-button"
            disabled={Boolean(busy)}
            onClick={() =>
              void perform('logout', async () => {
                await logoutStaff()
                setSession(null)
                setInvitation(null)
                setInvitations(null)
                setLinkPatient(null)
                setIntakes([])
                setIntake(null)
                setFields(emptyFields)
                setFiles([])
                setCandidates([])
                setEcwSuggestions(null)
                setEcwSelection('')
                setEcwConfirmed(false)
                setQuery('')
                setStatus('')
                setLibrary(null)
                setInboxLoaded(false)
                setNextOffset(null)
              })
            }
          >
            <LogOut size={17} /> Sign out
          </button>
        </div>
      </aside>
      <div className="staff-main" role="region" aria-label="Staff insurance workspace">
        {alerts}
        {screen === 'patients' && (
          <ClinicalPatientDashboard
            initialPatientId={historyPatientId}
            onPullEcw={() => navigate('lookup')}
            onOpenIntake={openIntake}
            onRequestCard={requestPatientCard}
            onAddCard={() => {
              setFiles([])
              navigate('add')
            }}
          />
        )}
        {screen === 'inbox' && <StaffInboxScreen model={model} session={session} />}
        {screen === 'links' && (
          <StaffUploadLinks
            key={linkPatient?.id || linkPatient?.intakeId || 'new'}
            patientId={linkPatient?.id}
            intakeId={linkPatient?.intakeId}
            patientName={linkPatient?.name}
            initialEmail={linkPatient?.email}
            backLabel={
              linkBack === 'record'
                ? 'Back to patient'
                : linkBack === 'inbox'
                  ? 'Back to inbox'
                  : 'Back to clinical patients'
            }
            onBack={() => navigate(linkBack)}
            onOpenIntake={openIntake}
          />
        )}
        {screen === 'lookup' && <StaffEcwLookupScreen model={model} session={session} />}
        {screen === 'add' && <StaffCardIntakeScreen model={model} session={session} />}
        {screen === 'review' && intake && (
          <StaffPatientReviewScreen model={model} session={session} intake={intake} />
        )}
        {screen === 'match' && intake && (
          <StaffPatientMatchScreen model={model} session={session} intake={intake} />
        )}
        {screen === 'record' && intake && (
          <StaffPatientRecordScreen model={model} session={session} intake={intake} />
        )}
        {screen === 'library' && (
          <ClinicLibrary
            session={session}
            library={library}
            setLibrary={setLibrary}
            busy={busy}
            perform={perform}
            refresh={openLibrary}
            heading={heading}
            setError={setError}
            setNotice={setNotice}
          />
        )}
        {screen === 'members' && session.role === 'admin' && (
          <StaffMembersScreen model={model} session={session} />
        )}
      </div>
    </section>
  )
}
