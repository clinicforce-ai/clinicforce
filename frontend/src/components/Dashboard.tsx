import type {
  ClinicalItem,
  ClinicalMeasure,
  CareEvent,
  DoctorSummary,
  Medication,
} from "../services/api";
interface DashboardProps {
  data: DoctorSummary;
  onScanAnother: () => void;
}
function MeasureTable({
  rows,
  title,
  testLabel,
}: {
  rows: ClinicalMeasure[];
  title: string;
  testLabel: string;
}) {
  return (
    <section className="data-section">
      <h2>{title}</h2>
      {rows.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{testLabel}</th>
                <th>Value</th>
                <th>Unit</th>
                <th>Date</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.name}-${index}`}>
                  <td>{row.name}</td>
                  <td>{row.value}</td>
                  <td>{row.unit ?? "—"}</td>
                  <td>{row.date ?? "—"}</td>
                  <td>{row.details ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty-state">
          No {title.toLowerCase()} are available in this record.
        </p>
      )}
    </section>
  );
}
function MedicationTable({ rows }: { rows: Medication[] }) {
  return (
    <section className="data-section">
      <h2>Medications</h2>
      {rows.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Medication</th>
                <th>Dosage</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.name}-${index}`}>
                  <td>{row.name}</td>
                  <td>{row.dosage}</td>
                  <td>{row.details ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty-state">
          No medications are available in this record.
        </p>
      )}
    </section>
  );
}
function ClinicalItemList({
  rows,
  title,
}: {
  rows: ClinicalItem[];
  title: string;
}) {
  return (
    <section className="data-section">
      <h2>{title}</h2>
      {rows.length ? (
        <ul className="clinical-list">
          {rows.map((row, index) => (
            <li key={`${row.name}-${index}`}>{row.name}{row.details && <><br /><span className="muted">{row.details}</span></>}</li>
          ))}
        </ul>
      ) : (
        <p className="empty-state">
          No {title.toLowerCase()} are available in this record.
        </p>
      )}
    </section>
  );
}
function CareEvents({ rows }: { rows: CareEvent[] }) {
  return <section className="data-section"><h2>Hospitals &amp; care visits</h2>{rows.length ? <div className="table-wrap"><table><thead><tr><th>Facility</th><th>Date</th><th>Details</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.facility}-${index}`}><td>{row.facility}</td><td>{row.date ?? '—'}</td><td>{row.details ?? '—'}</td></tr>)}</tbody></table></div> : <p className="empty-state">No hospital or clinic visits are available in this record.</p>}</section>
}
export default function Dashboard({ data, onScanAnother }: DashboardProps) {
  const download = () => {
    const link = document.createElement("a");
    link.href = data.pdf_url;
    link.download = "clinicforce-clinical-summary.pdf";
    link.click();
  };
  return (
    <section className="dashboard">
      <div className="dashboard-top">
        <div>
          <p className="step-label">Patient file opened</p>
          <h1>Clinical summary</h1>
        </div>
        <div className="dashboard-actions">
          <button className="secondary-button" onClick={onScanAnother}>
            Scan another
          </button>
          <button className="primary-button" onClick={download}>
            Download Official PDF
          </button>
        </div>
      </div>
      <article className="narrative-card">
        <h2>AI clinical narrative</h2>
        <p>{data.summary.narrative}</p>
      </article>
      <div className="data-grid">
        <CareEvents rows={data.summary.care_events} />
        <ClinicalItemList title="Conditions" rows={data.summary.conditions} />
        <ClinicalItemList title="Allergies" rows={data.summary.allergies} />
        <MeasureTable
          title="Vitals"
          testLabel="Measurement"
          rows={data.summary.vitals}
        />
        <MeasureTable title="Labs" testLabel="Test" rows={data.summary.labs} />
        <MedicationTable rows={data.summary.medications} />
      </div>
    </section>
  );
}
