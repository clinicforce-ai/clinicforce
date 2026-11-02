const assert = require('node:assert/strict')
const fs = require('node:fs')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

for (const ext of ['.ts', '.tsx']) {
  require.extensions[ext] = (module, filename) => module._compile(
    ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
        target: ts.ScriptTarget.ES2022 },
    }).outputText, filename)
}

const ArrivalAmountSummary = require('./src/features/commercial-estimate/ArrivalAmountSummary.tsx').default

function rendered(collect, modeled) {
  return renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model: { cash: false, previousEstimate: false },
    result: { collection_guidance: { visit_model: {
      collect_now_cents: collect, estimated_patient_responsibility_cents: modeled,
      pricing_basis: 'BCBSTX_CROSS_PAYER_REFERENCE', remaining_actions: [],
      selected_price_total_cents: 28347, reference_schedule_versions: ['reference-v1'],
      collection_disclosure: collect == null ? null : 'Synthetic check-in estimate.',
      service_groups: [{ service_group: 'specialist_office', cpts: [{ code: '99204', units: 1 }],
        interaction_rule: 'COPAY_ONLY', estimated_patient_share_cents: 4000,
        copay_cents: 4000, copay_frequency: 'VISIT', deductible_applies: false,
        benefit_evidence: ['synthetic-specialist'], price_lines: [{ code: '99204', units: 1,
          extended_cents: 20000, pricing_basis: 'BCBSTX_CROSS_PAYER_REFERENCE',
          rate_version: 'reference-v1' }] }],
    } } },
    partialCopay: null, visibleAmount: true,
  }))
}

assert.match(rendered(17669, 17669), /Amount to Collect Now<\/span><strong[^>]*>\$176\.69/)
assert.match(rendered(null, 17669), /Collection policy or evidence required/)
assert.match(rendered(null, 17669), /Modeled patient responsibility: \$176\.69/)
assert.doesNotMatch(rendered(null, 17669), /role="status"[^>]*>\$176\.69/)
assert.match(rendered(17669, 17669), /Synthetic check-in estimate/)
assert.match(rendered(17669, 17669), /Applied copay: \$40\.00/)
assert.match(rendered(17669, 17669), /CPT 99204 × 1: \$200\.00/)
assert.match(rendered(17669, 17669), /Selected benefit evidence: synthetic-specialist/)
assert.match(rendered(17669, 17669), /Copay frequency: VISIT/)
assert.match(rendered(17669, 17669), /Deductible applies: no/)
console.log('Visit collection UI: 10 assertions passed')
