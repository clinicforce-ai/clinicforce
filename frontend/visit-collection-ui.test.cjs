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
      service_groups: [{ service_group: 'specialist_office', cpts: [{ code: '99204', units: 1 }],
        interaction_rule: 'COPAY_ONLY', estimated_patient_share_cents: 4000 }],
    } } },
    partialCopay: null, visibleAmount: true,
  }))
}

assert.match(rendered(17669, 17669), /Amount to Collect Now<\/span><strong[^>]*>\$176\.69/)
assert.match(rendered(null, 17669), /Collection policy or evidence required/)
assert.match(rendered(null, 17669), /Modeled patient responsibility: \$176\.69/)
assert.doesNotMatch(rendered(null, 17669), /role="status"[^>]*>\$176\.69/)
console.log('Visit collection UI: 4 assertions passed')
