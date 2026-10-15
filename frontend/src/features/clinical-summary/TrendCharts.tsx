import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { type ClinicalMeasure } from '../../services/api/clinical-models'

export function TrendCharts({ rows }: { rows: ClinicalMeasure[] }) {
  const groups = new Map<string, { name: string; unit?: string | null; rows: ClinicalMeasure[] }>()
  rows.forEach((row) => {
    const numeric =
      typeof row.value === 'number' ||
      (typeof row.value === 'string' &&
        row.value.trim() !== '' &&
        Number.isFinite(Number(row.value)))
    if (!numeric) return
    const key = `${row.name}__${row.unit ?? ''}`
    const group = groups.get(key) ?? { name: row.name, unit: row.unit, rows: [] }
    group.rows.push(row)
    groups.set(key, group)
  })
  const series = [...groups.values()]
    .filter((group) => group.rows.length >= 4)
    .sort(
      (left, right) => right.rows.length - left.rows.length || left.name.localeCompare(right.name),
    )
    .slice(0, 3)
  if (!series.length) return null
  return (
    <section className="trend-card" aria-labelledby="trend-heading">
      <div className="trend-card-heading">
        <p className="section-kicker">Documented trends</p>
        <h2 id="trend-heading">Longitudinal measurements</h2>
        <p>
          Charts show only a metric with at least four directly numeric records. The cited tables
          below remain the accessible source of truth.
        </p>
      </div>
      <div className="trend-chart-grid">
        {series.map((group) => {
          const metric = `${group.name}${group.unit ? ` (${group.unit})` : ''}`
          const points = [...group.rows]
            .sort((left, right) => (left.date ?? '').localeCompare(right.date ?? ''))
            .map((row, index) => ({
              label: row.date ?? `Record ${index + 1}`,
              value: Number(row.value),
              source:
                row.citation?.source_type === 'epic'
                  ? `Epic / MyChart · ${row.citation.organization_name ?? ''}`
                  : row.citation
                    ? `${row.citation.source_filename} · p. ${row.citation.page_number}`
                    : 'Patient-entered',
            }))
          return (
            <article className="trend-chart" key={`${group.name}-${group.unit ?? ''}`}>
              <h3>{metric}</h3>
              <div
                role="img"
                aria-label={`${metric} trend chart with ${points.length} documented values`}
              >
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={points} margin={{ top: 12, right: 18, left: -18, bottom: 5 }}>
                    <CartesianGrid stroke="#d9e8eb" strokeDasharray="3 3" />
                    <XAxis dataKey="label" tick={{ fill: '#547084', fontSize: 12 }} />
                    <YAxis tick={{ fill: '#547084', fontSize: 12 }} />
                    <Tooltip
                      formatter={(value) => [value, metric]}
                      labelFormatter={(label, payload) =>
                        `${label} · ${payload[0]?.payload?.source ?? ''}`
                      }
                    />
                    <Line
                      type="monotone"
                      dataKey="value"
                      stroke="#087ca7"
                      strokeWidth={3}
                      dot={{ r: 4, fill: '#178563' }}
                      activeDot={{ r: 6 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
