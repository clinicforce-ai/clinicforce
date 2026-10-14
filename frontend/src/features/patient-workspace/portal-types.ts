export interface PatientPortalProps {
  onBack: () => void
}

export type AuthMode = 'login' | 'register'

export type WorkspaceTab = 'overview' | 'records' | 'connections'
