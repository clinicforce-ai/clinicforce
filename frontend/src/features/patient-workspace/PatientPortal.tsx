import {
  CheckCircle2,
  ClipboardPlus,
  FileText,
  LogOut,
  ShieldCheck,
  UserRound,
  Building2,
  X,
} from 'lucide-react'
import { patientInitials } from './display-text'
import type { PatientPortalProps } from './portal-types'
import { usePortalState } from './portal-state'
import { usePortalEffects } from './portal-effects'
import { createPortalAccountActions } from './portal-account-actions'
import { createPortalProviderActions } from './portal-provider-actions'
import { createPortalDocumentActions } from './portal-document-actions'
import { PortalSignIn } from './views/PortalSignIn'
import { PortalProfile } from './views/PortalProfile'
import { PortalIdme } from './views/PortalIdme'
import { PortalEpic } from './views/PortalEpic'
import { PortalEcw } from './views/PortalEcw'
import { PortalDocuments } from './views/PortalDocuments'
import { PortalRecords } from './views/PortalRecords'
import { PortalShare } from './views/PortalShare'

export default function PatientPortal({ onBack }: PatientPortalProps) {
  const state = usePortalState()
  usePortalEffects(state)
  const {
    session,
    workspaceTab,
    setWorkspaceTab,
    error,
    notice,
    workspacePanel,
    clearFeedback,
    signOut,
  } = state
  const account = createPortalAccountActions(state)
  const provider = createPortalProviderActions(state)
  const document = createPortalDocumentActions(state)

  if (!session) return <PortalSignIn state={state} account={account} onBack={onBack} />

  const recordsAvailable = session.documents.length + session.records.length

  return (
    <section className="workspace animate-enter" aria-labelledby="workspace-heading">
      <div className="workspace-banner">
        <div className="avatar" aria-hidden="true">
          {patientInitials(session.patient_id)}
        </div>
        <div>
          <p className="eyebrow">Patient workspace</p>
          <h1 id="workspace-heading">Your health record</h1>
          <p>
            Signed in as <strong>{session.patient_id}</strong>
          </p>
        </div>
        <button
          className="button button-ghost signout-button"
          type="button"
          onClick={() => signOut()}
        >
          <LogOut aria-hidden="true" className="size-4" /> Sign out
        </button>
      </div>

      {(error || notice) && (
        <div
          className={error ? 'feedback feedback-error' : 'feedback feedback-success'}
          role={error ? 'alert' : 'status'}
        >
          {error ? <X aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
          <span>{error || notice}</span>
          <button type="button" onClick={clearFeedback} aria-label="Dismiss message">
            <X aria-hidden="true" className="size-4" />
          </button>
        </div>
      )}

      <div className="workspace-stats" aria-label="Workspace summary">
        <div>
          <FileText aria-hidden="true" />
          <span>
            <strong>{session.documents.length}</strong> report
            {session.documents.length === 1 ? '' : 's'} stored
          </span>
        </div>
        <div>
          <ClipboardPlus aria-hidden="true" />
          <span>
            <strong>{session.records.length}</strong> health detail
            {session.records.length === 1 ? '' : 's'} added
          </span>
        </div>
        <div>
          <ShieldCheck aria-hidden="true" />
          <span>{recordsAvailable ? 'Ready to share' : 'Add your first item'}</span>
        </div>
      </div>

      <div className="workspace-grid" ref={workspacePanel}>
        <nav className="workspace-tabs" aria-label="Health record sections">
          <button
            className={workspaceTab === 'overview' ? 'active' : ''}
            aria-pressed={workspaceTab === 'overview'}
            type="button"
            onClick={() => setWorkspaceTab('overview')}
          >
            <UserRound aria-hidden="true" />
            <span>Profile & sharing</span>
          </button>
          <button
            className={workspaceTab === 'records' ? 'active' : ''}
            aria-pressed={workspaceTab === 'records'}
            type="button"
            onClick={() => setWorkspaceTab('records')}
          >
            <FileText aria-hidden="true" />
            <span>Reports & details</span>
          </button>
          <button
            className={workspaceTab === 'connections' ? 'active' : ''}
            aria-pressed={workspaceTab === 'connections'}
            type="button"
            onClick={() => setWorkspaceTab('connections')}
          >
            <Building2 aria-hidden="true" />
            <span>Care connections</span>
          </button>
        </nav>
        <p className="workspace-tabs-helper" role="status">
          {workspaceTab === 'overview' && (
            <>
              <strong>Start here.</strong> Confirm your profile, then share.
            </>
          )}
          {workspaceTab === 'records' && (
            <>
              <strong>Add information.</strong> Upload a report or add a detail.
            </>
          )}
          {workspaceTab === 'connections' && (
            <>
              <strong>Connect care.</strong> Authorize in a secure window.
            </>
          )}
        </p>
        <div className="workspace-main">
          <PortalProfile state={state} account={account} />

          <PortalIdme state={state} account={account} />

          <PortalEpic state={state} provider={provider} />

          <PortalEcw state={state} provider={provider} />

          <PortalDocuments state={state} account={account} document={document} />

          <PortalRecords state={state} account={account} />
        </div>
        <PortalShare state={state} account={account} />
      </div>
    </section>
  )
}
