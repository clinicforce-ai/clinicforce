import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { text as readStreamText } from 'node:stream/consumers'
import { existsSync } from 'node:fs'

const root = fileURLToPath(new URL('..', import.meta.url))
let result
if (process.argv.includes('--stdin')) {
  result = JSON.parse(await readStreamText(process.stdin))
} else {
  const pythonCommand = join(root, '.venv/bin/python')
  const python = spawnSync(existsSync(pythonCommand) ? pythonCommand : 'python3', ['-c',
    'import json; from test_fep_pshb_recovery import standard_evidence, finish; print(json.dumps(finish(*standard_evidence())))',
  ], { cwd: root, encoding: 'utf8', env: {
    ...process.env, PYTHONPATH: [join(root, 'backend'), join(root, 'testing')].join(':'),
  } })
  assert.equal(python.status, 0, python.stderr)
  result = JSON.parse(python.stdout)
}
const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
async function bundle(input) {
  const built = await rolldown({ input: join(root, 'frontend/src/features/commercial-estimate', input),
    plugins: [{ name: 'ignore-styles',
      resolveId(source) { if (source.endsWith('.css')) return '\0empty-style' },
      load(id) { if (id === '\0empty-style') return '' },
    }],
  })
  const compiled = await built.generate({ format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
}
const { deriveCommercialEstimateView } = await bundle('deriveCommercialEstimateView.ts')
const { default: CommercialCostBreakdown } = await bundle('CommercialCostBreakdown.tsx')
const { default: PublishedPlanBenefit } = await bundle('PublishedPlanBenefit.tsx')
const model = deriveCommercialEstimateView({ result, appointments: [], selectedVisit: null,
  busy: null, error: null, readOnly: false, recovering: false })
assert.equal(model.amountText, '$40.00')
const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { result, model }))
const published = renderToStaticMarkup(React.createElement(PublishedPlanBenefit, { result, show: true }))
for (const fragment of ['Estimated patient share', '$40.00 office copay advance',
  'Amount to collect at visit', 'FEP Blue Standard FEHB', '$264.24']) {
  assert.ok((table + published).includes(fragment), `Missing office result: ${fragment}`)
}
assert.match(published, /office copay has no deductible/)
assert.doesNotMatch(table, /Agreement mismatch/)
console.log('FEP Standard office UI: $40 copay and collection, no deductible on office visit')
