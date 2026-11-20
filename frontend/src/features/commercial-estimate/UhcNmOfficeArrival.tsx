import { money } from './format'
import type { uhcNmOfficeReference } from './uhcNmOfficeReference'

export default function UhcNmOfficeArrival({
  reference,
}: {
  reference: NonNullable<ReturnType<typeof uhcNmOfficeReference>>
}) {
  return (
    <>
      <span>Amount to collect at visit · verification pending</span>
      <strong role="status">{money(reference.balance)} conditional</strong>
      <p>
        Published out-of-network specialist copay for this exact 2026 PPO plan
        {reference.paid ? `, less ${money(reference.paid)} already paid` : ''}. This is a plan
        reference, not an authorized charge. Confirm active coverage, provider status, QMB
        protection, other coverage and payments before collecting.{' '}
        <a href={reference.source} target="_blank" rel="noreferrer">
          UHC plan source
        </a>
        .
      </p>
    </>
  )
}
