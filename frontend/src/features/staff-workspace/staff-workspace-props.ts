export interface StaffWorkspaceProps {
  onBack: () => void
  onOpenInsurance: (
    intakeId: string,
    version: 'v1' | 'v2' | 'commercial',
    refreshBenefits?: boolean,
  ) => void
  onScanQr: () => void
  onCreateClinic: () => void
  onJoinClinic: () => void
  onOpenPatientQr: () => void
  initialIntakeId?: string
  initialEcwLookup?: boolean
}
