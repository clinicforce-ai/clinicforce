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
    <section className="cost-breakdown" aria-label={title}>
      <h3>{title}</h3>
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
    </section>
  )
}
