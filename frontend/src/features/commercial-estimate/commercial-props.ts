import { type InsuranceCheckSnapshot } from '../../services/api/staff-history'

export interface CommercialEstimatorProps {
  onBack?: () => void
  onResolvePatient?: () => void
  intakeId?: string
  refreshBenefits?: boolean
  saved?: InsuranceCheckSnapshot
  currentAccountNumber?: string | null
}
