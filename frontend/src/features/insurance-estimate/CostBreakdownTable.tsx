import './cost-breakdown-table.css'

export type CostBreakdownRow = {
  label: string
  value: string
  detail?: string
  emphasis?: boolean
}

export default function CostBreakdownTable({
  title = 'Visit cost and collection',
  rows,
}: {
  title?: string
  rows: CostBreakdownRow[]
}) {
  return (
    <details className="cost-breakdown" aria-label={title}>
      <summary>{title}</summary>
      <div className="cost-breakdown-scroll">
        <table>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.label}-${index}`} className={row.emphasis ? 'is-total' : undefined}>
                <th scope="row">{row.label}</th>
                <td>
                  <span>{row.value}</span>
                  {row.detail && <small>{row.detail}</small>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
