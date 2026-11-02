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
const RequirementContact = require('./src/features/commercial-estimate/RequirementContact.tsx').default

function rendered(collect, modeled, synthetic = false) {
  return renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model: { cash: false, previousEstimate: false },
    result: { staging_synthetic_fixture: synthetic, collection_guidance: { visit_model: {
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
assert.match(rendered(17669, 17669, true), /Synthetic staging test visit/)
const call = renderToStaticMarkup(React.createElement(RequirementContact, { evidence: {
  status: 'Verification required', reason: 'Unknown for exact product', sources: [],
  contact_department: 'Precertification', contact_phone: '800-882-4462',
  contact_source: { label: 'Official payer provider contact', url: 'https://www.cigna.com/health-care-providers/coverage-and-claims/precertification', retrieved_at: '2026-11-02' },
  call_script: 'Verify CPT 95908 for this member and location.',
} }))
assert.match(call, /tel:8008824462/)
assert.match(call, /Official contact source/)
assert.match(call, /Copyable provider call script/)
assert.match(call, /Verify CPT 95908/)
console.log('Visit collection UI: 15 assertions passed')
