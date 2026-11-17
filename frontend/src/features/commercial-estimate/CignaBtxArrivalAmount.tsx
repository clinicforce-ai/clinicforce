import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'

export default function CignaBtxArrivalAmount({ row }: { row: CostBreakdownRow }) {
  return (
    <>
      <span>{row.label}</span>
      <strong role="status">{row.value}</strong>
      <p>{row.detail}</p>
    </>
  )
}
