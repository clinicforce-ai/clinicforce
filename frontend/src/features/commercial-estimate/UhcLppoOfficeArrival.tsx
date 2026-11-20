import { money } from './format'
import type { uhcLppoOfficeScenario } from './uhcLppoOfficeScenario'

export default function UhcLppoOfficeArrival({
  scenario,
}: {
  scenario: NonNullable<ReturnType<typeof uhcLppoOfficeScenario>>
}) {
  return (
    <>
      <span>Amount to collect at visit · provisional</span>
      <strong role="status">{money(scenario.balance)}</strong>
      <p>
        {scenario.percent}% × {money(scenario.fee)} clinic Medicare fee reference
        {scenario.paid ? ` less ${money(scenario.paid)} already paid` : ''}. Reconcile policy
        evidence, network, QMB protection and payer order before charging.
      </p>
    </>
  )
}
