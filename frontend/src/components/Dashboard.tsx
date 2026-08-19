import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Download,
  FileHeart,
  HeartPulse,
  Hospital,
  Pill,
  ScanLine,
  ShieldCheck,
} from 'lucide-react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { CareEvent, ClinicalItem, ClinicalMeasure, DoctorSummary, Medication, SourceCitation } from '../services/api'

interface DashboardProps {
  data: DoctorSummary
  onScanAnother: () => void
  onHome: () => void
}

function SourceNote({ citation }: { citation?: SourceCitation | null }) {
  if (!citation) return <span className="source-note source-note-manual">Patient-entered</span>
  if (citation.source_type === 'epic') {
    return <span className="source-note"><FileHeart aria-hidden="true" /> Epic / MyChart{citation.organization_name ? ` · ${citation.organization_name}` : ''}</span>
  }
  const label = <><FileHeart aria-hidden="true" /> {citation.source_filename} · p. {citation.page_number}</>
  if (!citation.download_url) return <span className="source-note">{label}</span>
  return <a className="source-note source-link" href={citation.download_url} download aria-label={`Download source PDF ${citation.source_filename}, page ${citation.page_number}`}>{label}</a>
}

function MeasureTable({ rows, title, testLabel, icon: Icon }: { rows: ClinicalMeasure[]; title: string; testLabel: string; icon: typeof Activity }) {
  if (!rows.length) return null
  const hasSources = rows.some((row) => Boolean(row.citation))
  return <section className="summary-section">
    <div className="summary-section-title"><span><Icon aria-hidden="true" /></span><h2>{title}</h2></div>
    <div className="table-wrap"><table><thead><tr><th>{testLabel}</th><th>Value</th>{rows.some((row) => row.unit) && <th>Unit</th>}{rows.some((row) => row.date) && <th>Date</th>}{rows.some((row) => row.details) && <th>Notes</th>}{hasSources && <th>Original source</th>}</tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.name}-${index}`}><td>{row.name}</td><td>{row.value}</td>{rows.some((item) => item.unit) && <td>{row.unit ?? '—'}</td>}{rows.some((item) => item.date) && <td>{row.date ?? '—'}</td>}{rows.some((item) => item.details) && <td>{row.details ?? '—'}</td>}{hasSources && <td><SourceNote citation={row.citation} /></td>}</tr>)}</tbody></table></div>
  </section>
}

function MedicationTable({ rows }: { rows: Medication[] }) {
  if (!rows.length) return null
  const hasSources = rows.some((row) => Boolean(row.citation))
  const hasNotes = rows.some((row) => Boolean(row.details))
  return <section className="summary-section"><div className="summary-section-title"><span><Pill aria-hidden="true" /></span><h2>Medications</h2></div><div className="table-wrap"><table><thead><tr><th>Medication</th><th>Dosage</th>{hasNotes && <th>Notes</th>}{hasSources && <th>Original source</th>}</tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.name}-${index}`}><td>{row.name}</td><td>{row.dosage}</td>{hasNotes && <td>{row.details ?? '—'}</td>}{hasSources && <td><SourceNote citation={row.citation} /></td>}</tr>)}</tbody></table></div></section>
}

function ClinicalItemList({ rows, title, icon: Icon }: { rows: ClinicalItem[]; title: string; icon: typeof Activity }) {
  if (!rows.length) return null
  return <section className="summary-section"><div className="summary-section-title"><span><Icon aria-hidden="true" /></span><h2>{title}</h2></div><ul className="summary-list">{rows.map((row, index) => <li key={`${row.name}-${index}`}><strong>{row.name}</strong>{row.details && <p>{row.details}</p>}<SourceNote citation={row.citation} /></li>)}</ul></section>
}

function CareEvents({ rows }: { rows: CareEvent[] }) {
  if (!rows.length) return null
  const hasSources = rows.some((row) => Boolean(row.citation))
  const hasDate = rows.some((row) => Boolean(row.date))
  const hasDetails = rows.some((row) => Boolean(row.details))
  return <section className="summary-section"><div className="summary-section-title"><span><Hospital aria-hidden="true" /></span><h2>Care visits</h2></div><div className="table-wrap"><table><thead><tr><th>Facility</th>{hasDate && <th>Date</th>}{hasDetails && <th>Details</th>}{hasSources && <th>Original source</th>}</tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.facility}-${index}`}><td>{row.facility}</td>{hasDate && <td>{row.date ?? '—'}</td>}{hasDetails && <td>{row.details ?? '—'}</td>}{hasSources && <td><SourceNote citation={row.citation} /></td>}</tr>)}</tbody></table></div></section>
}

function TrendCharts({ rows }: { rows: ClinicalMeasure[] }) {
  const groups = new Map<string, { name: string; unit?: string | null; rows: ClinicalMeasure[] }>()
  rows.forEach((row) => {
    const numeric = typeof row.value === 'number' || (typeof row.value === 'string' && row.value.trim() !== '' && Number.isFinite(Number(row.value)))
    if (!numeric) return
    const key = `${row.name}__${row.unit ?? ''}`
    const group = groups.get(key) ?? { name: row.name, unit: row.unit, rows: [] }
    group.rows.push(row)
    groups.set(key, group)
  })
  const series = [...groups.values()]
    .filter((group) => group.rows.length >= 4)
    .sort((left, right) => right.rows.length - left.rows.length || left.name.localeCompare(right.name))
    .slice(0, 3)
  if (!series.length) return null

  return <section className="trend-card" aria-labelledby="trend-heading">
    <div className="trend-card-heading"><p className="section-kicker">Documented trends</p><h2 id="trend-heading">Longitudinal measurements</h2><p>Charts show only a metric with at least four directly numeric records. The cited tables below remain the accessible source of truth.</p></div>
    <div className="trend-chart-grid">{series.map((group) => {
      const metric = `${group.name}${group.unit ? ` (${group.unit})` : ''}`
      const points = [...group.rows]
        .sort((left, right) => (left.date ?? '').localeCompare(right.date ?? ''))
        .map((row, index) => ({ label: row.date ?? `Record ${index + 1}`, value: Number(row.value), source: row.citation?.source_type === 'epic' ? `Epic / MyChart · ${row.citation.organization_name ?? ''}` : row.citation ? `${row.citation.source_filename} · p. ${row.citation.page_number}` : 'Patient-entered' }))
      return <article className="trend-chart" key={`${group.name}-${group.unit ?? ''}`}>
        <h3>{metric}</h3>
        <div role="img" aria-label={`${metric} trend chart with ${points.length} documented values`}>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={points} margin={{ top: 12, right: 18, left: -18, bottom: 5 }}>
              <CartesianGrid stroke="#d9e8eb" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: '#547084', fontSize: 12 }} />
              <YAxis tick={{ fill: '#547084', fontSize: 12 }} />
              <Tooltip formatter={(value) => [value, metric]} labelFormatter={(label, payload) => `${label} · ${payload[0]?.payload?.source ?? ''}`} />
              <Line type="monotone" dataKey="value" stroke="#087ca7" strokeWidth={3} dot={{ r: 4, fill: '#178563' }} activeDot={{ r: 6 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </article>
    })}</div>
  </section>
}

export default function Dashboard({ data, onScanAnother, onHome }: DashboardProps) {
  const download = () => { const link = document.createElement('a'); link.href = data.pdf_url; link.download = 'clinicforce-clinical-summary.pdf'; link.click() }
  const { summary } = data
  const itemCount = summary.vitals.length + summary.labs.length + summary.medications.length + summary.conditions.length + summary.allergies.length + summary.care_events.length
  const sourcedCount = [summary.vitals, summary.labs, summary.medications, summary.conditions, summary.allergies, summary.care_events].flat().filter((item) => item.citation).length
  return <section className="summary-page animate-enter" aria-labelledby="summary-heading">
    <button className="back-control" type="button" onClick={onHome}><ArrowLeft aria-hidden="true" className="size-4" /> Back to home</button>
    <header className="summary-hero"><div><p className="eyebrow"><FileHeart aria-hidden="true" className="size-4" /> Clinician view · shared record</p><h1 id="summary-heading">Clinical summary</h1><p>{itemCount} shared health item{itemCount === 1 ? '' : 's'} · {sourcedCount} linked to original report pages.</p></div><div className="summary-actions"><button className="button button-secondary" type="button" onClick={onScanAnother}><ScanLine aria-hidden="true" className="size-4" /> Scan another</button><button className="button button-primary" type="button" onClick={download}><Download aria-hidden="true" className="size-4" /> Download cited PDF</button></div></header>
    <div className="source-assurance"><ShieldCheck aria-hidden="true" /><span>Each linked source label downloads the original private PDF for that fact. Patient-entered items are marked separately.</span></div>
    <article className="clinical-narrative"><div className="narrative-mark"><HeartPulse aria-hidden="true" /></div><div><p className="section-kicker">Clinical overview</p><h2>Patient history at a glance</h2><p>{summary.narrative}</p></div></article>
    <TrendCharts rows={[...summary.vitals, ...summary.labs]} />
    <div className="summary-grid"><CareEvents rows={summary.care_events} /><ClinicalItemList title="Conditions" rows={summary.conditions} icon={Activity} /><ClinicalItemList title="Allergies" rows={summary.allergies} icon={AlertTriangle} /><MeasureTable title="Vitals" testLabel="Measurement" rows={summary.vitals} icon={HeartPulse} /><MeasureTable title="Labs" testLabel="Test" rows={summary.labs} icon={FileHeart} /><MedicationTable rows={summary.medications} /></div>
  </section>
}
