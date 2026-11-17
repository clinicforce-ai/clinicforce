import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

export default function AetnaOfficeZeroArrival({
  office,
}: {
  office: NonNullable<PrevisitEstimate['aetna_office_planning']>
}) {
  return (
    <>
      <span>Amount to collect at visit</span>
      <strong role="status">{money(0)}</strong>
      <p>
        {money(office.allowed_cents)} clinic Aetna fee reference for CPT {office.code};{' '}
        {office.coinsurance_percent}% × {money(office.allowed_cents)} ={' '}
        {money(office.gross_coinsurance_cents || 0)} gross coinsurance. The reported individual
        in-network medical out-of-pocket remainder is {money(0)}, so the covered visit’s check-in
        amount is {money(0)}. The final Aetna allowance follows the claim.
      </p>
    </>
  )
}
