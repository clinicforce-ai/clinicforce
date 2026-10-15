import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { type V2Transaction } from '../../services/api/insurance-v2-types'
import { money, readable, dateTime } from './v2-display'

export function TransactionStatus({ transaction }: { transaction: V2Transaction }) {
  const complete = transaction.outcome === 'complete'
  return (
    <div className={`v2-response ${complete ? 'is-complete' : ''}`}>
      <div className="v2-response-heading">
        {complete ? (
          <CheckCircle2 size={18} aria-hidden="true" />
        ) : (
          <AlertTriangle size={18} aria-hidden="true" />
        )}
        <strong>{readable(transaction.outcome)}</strong>
        {transaction.mock_response && <span className="v2-tag">Synthetic</span>}
      </div>
      {transaction.message && <p>{transaction.message}</p>}
      <dl className="v2-facts">
        <div>
          <dt>{transaction.source === 'stedi' ? 'Stedi check ID' : 'Availity result ID'}</dt>
          <dd>{transaction.resource_id || 'Not returned'}</dd>
        </div>
        <div>
          <dt>Transaction ID</dt>
          <dd>{transaction.transaction_id || 'Not returned'}</dd>
        </div>
        <div>
          <dt>Last checked</dt>
          <dd>{dateTime(transaction.checked_at)}</dd>
        </div>
      </dl>
    </div>
  )
}

export function PriceExplanation({ transaction }: { transaction?: V2Transaction }) {
  const lines = transaction?.result?.lines
  if (!lines?.length) return null
  return (
    <details className="v2-price-explanation">
      <summary>
        {transaction?.source === 'stedi_local'
          ? 'How the Stedi estimate was calculated'
          : 'Why Availity returned this amount'}
      </summary>
      {lines.map((line, index) => (
        <div key={`${line.code}-${index}`}>
          <strong>
            {line.code || 'Service'} · patient {money(line.patient_cents)}
          </strong>
          <small>
            Allowed {money(line.allowed_cents)} · deductible {money(line.deductible_cents)} · copay{' '}
            {money(line.copay_cents)} · coinsurance {money(line.coinsurance_cents)}
          </small>
        </div>
      ))}
    </details>
  )
}
