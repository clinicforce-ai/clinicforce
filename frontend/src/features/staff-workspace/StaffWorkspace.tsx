import { useStaffWorkspaceModel } from './useStaffWorkspaceModel'
import StaffWorkspaceView from './StaffWorkspaceView'
import type { StaffWorkspaceProps } from './staff-workspace-props'
import './StaffWorkspace.css'

export default function StaffWorkspace(props: StaffWorkspaceProps) {
  const model = useStaffWorkspaceModel(props)
  return <StaffWorkspaceView model={model} />
}
