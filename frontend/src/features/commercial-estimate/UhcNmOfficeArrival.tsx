import { money } from './format'
import type { uhcNmOfficeReference } from './uhcNmOfficeReference'

export default function UhcNmOfficeArrival({
  reference,
}: {
  reference: NonNullable<ReturnType<typeof uhcNmOfficeReference>>
}) {
  return (
    <>
      <span>Amount to collect at visit</span>
      <strong role="status">Not determined</strong>
      <p>
        Network tier not determined. This 2026 UnitedHealthcare PPO plan lists{' '}
        {money(reference.inNetworkCopay)} in-network and {money(reference.outOfNetworkCopay)}{' '}
        out-of-network specialist copays. Confirm active coverage, provider network, QMB protection,
        other coverage and payments before collecting.{' '}
        <a href={reference.source} target="_blank" rel="noreferrer">
          UHC plan source
        </a>
        .
      </p>
    </>
  )
}
