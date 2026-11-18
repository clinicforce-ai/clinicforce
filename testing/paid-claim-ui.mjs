import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const bundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/PaidClaimCollection.tsx', import.meta.url).pathname,
  plugins: [{ name: 'ignore-css', load(id) { if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' } } }],
})
const compiled = await bundle.generate({ format: 'esm' })
const { PaidClaimAmount, PaidClaimTable } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`
)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const claim = {
  status: 'estimated', code: '99204', units: 1, submitted_cents: 27100,
  allowed_cents: 13895, insurer_paid_cents: 11116, patient_share_cents: 2779,
  amount_cents: 2779, copay_cents: 0, deductible_remaining_cents: 0,
  oop_remaining_cents: 0, coinsurance_percent: '20.0',
  reason: 'Aetna paid $111.16. Its 20% office coinsurance implies $27.79; confirm the EOB.',
}
const result = { service_date: '2026-10-07', reason: 'Synthetic office consultation',
  price_range: { mode: 'services', lines: [{ code: '99203' }] } }
const headline = renderToStaticMarkup(React.createElement(PaidClaimAmount, { claim }))
const table = renderToStaticMarkup(React.createElement(PaidClaimTable, { claim, result }))
assert.match(headline, /\$27\.79/)
assert.match(headline, /confirm the EOB/)
assert.match(table, /CPT 99204 × 1 · actual paid claim/)
assert.match(table, /provisional CPT 99203/)
assert.match(table, /Implied allowed amount/)
assert.match(table, /Amount to collect for this visit/)
assert.match(table, /\$27\.79 estimated/)
assert.match(table, /confirm the EOB/)
