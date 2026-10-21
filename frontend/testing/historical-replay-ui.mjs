import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const python = existsSync('../.venv/bin/python') ? '../.venv/bin/python' : 'python3'
const results = JSON.parse(
  execFileSync(python, ['../testing/emit_historical_replay_ui.py'], { encoding: 'utf8' }),
)
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const { default: CommercialOverview } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialOverview.tsx',
  )
  for (const [name, result] of Object.entries(results)) {
    const model = deriveCommercialEstimateView({
      result,
      appointments: [],
      selectedVisit: '',
      busy: null,
      error: '',
      readOnly: false,
      recovering: false,
    })
    const html = renderToStaticMarkup(React.createElement(CommercialOverview, { model, result }))
    assert(html.includes('RPPO-UHC Complete Care Support TX-1A'), name)
    assert(html.includes('excluded from NMCT'), name)
    if (name === 'fixed_copay') assert(html.includes('$40.00'), name)
    if (name === 'deductible_coinsurance') assert(html.includes('$75.33'), name)
    if (name === 'multi_service') {
      assert(html.includes('$114.07'), name)
      for (const code of ['99204', '95907', '95886']) assert(html.includes(code), name)
    }
    if (name === 'incomplete') assert(!html.includes('Amount to collect now: $0'), name)
    if (['deductible_coinsurance', 'multi_service'].includes(name))
      assert(html.includes('CMS'), name)
  }
  console.log('Historical replay UI scenarios: 4 passed')
} finally {
  await server.close()
}
