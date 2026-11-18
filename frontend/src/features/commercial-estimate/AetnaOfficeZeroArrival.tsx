import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

export default function AetnaOfficeZeroArrival({
  office,
}: {
  office: NonNullable<PrevisitEstimate['aetna_office_planning']>
}) {
  const telemedicineReference = office.coinsurance_scope === 'telemedicine_reference'
  return (
    <>
      <span>Amount to collect at visit</span>
      <strong role="status">{money(0)}</strong>
      <p>
        {money(office.allowed_cents)} clinic Aetna fee reference for CPT {office.code};{' '}
        {office.coinsurance_percent}% × {money(office.allowed_cents)} ={' '}
        {money(office.gross_coinsurance_cents || 0)}{' '}
        {telemedicineReference
          ? 'illustrative telemedicine coinsurance'
          : 'gross office coinsurance'}
        . The reported individual in-network medical out-of-pocket remainder is {money(0)}, so the
        covered visit’s check-in amount is {money(0)}.{' '}
        {telemedicineReference && 'Aetna did not return an in-person office coinsurance rate. '}
        The final Aetna allowance follows the claim.
      </p>
    </>
  )
}
