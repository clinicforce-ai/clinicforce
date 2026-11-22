import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { writeFile, unlink } from 'node:fs/promises'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
async function load(path) {
  const bundle = await rolldown({ input: new URL(path, import.meta.url).pathname,
    plugins: [{ name: 'ignore-css', load(id) { if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' } } }] })
  const compiled = await bundle.generate({ format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
}
const { deriveCommercialEstimateView } = await load('../frontend/src/features/commercial-estimate/deriveCommercialEstimateView.ts')
const { default: ArrivalAmountSummary } = await load('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx')
const { default: CommercialCostBreakdown } = await load('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx')
const { AvailityArrivalAmount } = await load('../frontend/src/features/commercial-estimate/AvailityVisitPlanning.tsx')

const result = {
  payer: 'Aetna', payment_mode: 'insurance', service_date: '2026-12-15',
  identity: { status: 'matched', binding: 'fictional-binding', match_version: 2 },
  benefits: { active: true, service_date_matches: false, network_status: 'unknown',
    network_source: null, source_errors: [] },
  price_range: { complete: false, mode: 'services', lines: [{ id: 'office', code: '99213',
    units: 1, allowed_cents: 12000, rate_resolution: { status: 'RESOLVED' },
    benefits: { active: true, copay_cents: 3000, source_errors: [] } }] },
  aetna_office_planning: { status: 'planning_only', collectable: false,
    allowed_cents: 12000, deductible_applies_cents: 12000, patient_share_cents: 12000,
    deductible_remaining_cents: 20000, oop_remaining_cents: 50000 },
  collection_guidance: { amount_cents: 0, status: 'zero_advance', basis: 'claim_after_service',
    collection_rule: { mode: 'clinic_zero_arrival_default' },
    reason: 'Collect $0 at arrival under the clinic default; final patient share is unknown.',
    services: [] },
  encounter_summary: { collect_today_cents: 0, complete: false,
    estimated_total_patient_min_cents: null },
}
const model = deriveCommercialEstimateView({ result, appointments: [], selectedVisit: '',
  busy: null, error: null, readOnly: true, recovering: false })
assert.equal(model.supportedAmount, true)
assert.equal(model.amount, '$0.00')
const headline = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
  { result, model, partialCopay: null, visibleAmount: true }))
assert.match(headline, /Amount to collect at visit.*\$0\.00/s)
assert.doesNotMatch(headline, /Verify network|\$120\.00/)
const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { result, model }))
assert.match(table, /Amount to collect at visit.*\$0\.00/s)

const returnedDate = { ...result,
  network_evidence: { source: 'clinic_product_roster', match_kind: 'shared_product_word',
    matched_words: ['gold'] },
  benefits: { ...result.benefits, network_status: 'in_network',
    network_source: 'clinic_product_roster' },
  price_range: { ...result.price_range, lines: [{ ...result.price_range.lines[0],
    benefits: { ...result.price_range.lines[0].benefits, copay_cents: 3000 } }] },
  collection_guidance: { amount_cents: 2500, status: 'estimated', basis: 'office_copay',
    payer_response_date: '2026-12-13',
    collection_rule: { mode: 'nmct_stedi_provisional_office_copay' },
    reason: 'Provisional office copay from 2026-12-13; refund any excess.', services: [] },
  encounter_summary: { collect_today_cents: 2500, complete: false,
    estimated_total_patient_min_cents: null },
}
const returnedDateModel = deriveCommercialEstimateView({ result: returnedDate,
  appointments: [], selectedVisit: '', busy: null, error: null, readOnly: true,
  recovering: false })
assert.equal(returnedDateModel.supportedAmount, true)
assert.equal(returnedDateModel.amount, '$25.00')
assert.match(returnedDateModel.networkSource, /shared plan word: gold/)
const returnedDateHeadline = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
  { result: returnedDate, model: returnedDateModel, partialCopay: null, visibleAmount: true }))
assert.match(returnedDateHeadline, /Amount to collect at visit.*\$25\.00/s)
assert.match(returnedDateHeadline, /2026-12-13/)
const unmatchedRoster = { ...returnedDate,
  network_evidence: { decision: 'lacking_applicable_network_evidence' },
  benefits: { ...returnedDate.benefits, network_status: 'unknown', network_source: null },
}
const unmatchedRosterModel = deriveCommercialEstimateView({ result: unmatchedRoster,
  appointments: [], selectedVisit: '', busy: null, error: null, readOnly: true,
  recovering: false })
assert.equal(unmatchedRosterModel.supportedAmount, true)
assert.equal(unmatchedRosterModel.amount, '$25.00')

const cashResult = { ...result, payment_mode: 'cash',
  price_range: { ...result.price_range, cash_complete: false, cash_min_cents: null,
    cash_max_cents: null },
  collection_guidance: { ...result.collection_guidance, basis: 'cash_price_pending',
    reason: 'Collect $0 at arrival under the clinic default; the cash quote is pending.' },
}
const cashModel = deriveCommercialEstimateView({ result: cashResult, appointments: [],
  selectedVisit: '', busy: null, error: null, readOnly: true, recovering: false })
assert.equal(cashModel.supportedAmount, true)
assert.equal(cashModel.amount, '$0.00')
const cashHeadline = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
  { result: cashResult, model: cashModel, partialCopay: null, visibleAmount: true }))
assert.match(cashHeadline, /Amount to collect at visit.*\$0\.00/s)
assert.doesNotMatch(cashHeadline, /Confirm clinic price/)
const cashTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { result: cashResult, model: cashModel }))
assert.match(cashTable, /Amount to collect at visit.*\$0\.00/s)

const waitingActions = { result: null, appointments: [],
  selectedVisit: '', busy: 'visits', error: '',
  readOnly: false, recovering: false,
  session: { practice_id: 'synthetic-nmct', arrival_collection_default_cents: 0 },
  patient: null, loaded: false, onBack() {}, load() {}, estimate() {} }
const waiting = deriveCommercialEstimateView(waitingActions)
const viewPath = new URL('../frontend/src/features/commercial-estimate/CommercialEstimateView.tsx', import.meta.url).pathname
const staffViewPath = new URL('../frontend/src/features/staff-workspace/StaffWorkspaceView.tsx', import.meta.url).pathname
const prebookingPath = new URL('../frontend/src/features/staff-workspace/StaffPrebookingScreen.tsx', import.meta.url).pathname
const entry = 'virtual:waiting-visit-render'
const viewBundle = await rolldown({ input: entry, external: [/^react(?:$|\/)/, /^react-dom(?:$|\/)/], plugins: [{ name: 'waiting-view',
  resolveId(id) { if (id === entry) return entry },
  load(id) { if (id === entry) return `import React from 'react'; import { renderToStaticMarkup } from 'react-dom/server'; import View from '${viewPath}'; import StaffView from '${staffViewPath}'; import Prebooking from '${prebookingPath}'; export const render = model => renderToStaticMarkup(React.createElement(View, { model })); export const renderStaff = model => renderToStaticMarkup(React.createElement(StaffView, { model })); export const renderPrebooking = practiceId => renderToStaticMarkup(React.createElement(Prebooking, { practiceId, onBack() {} }));` },
}, { name: 'ignore-css', load(id) { if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' } } }] })
const viewModule = await viewBundle.generate({ format: 'esm' })
const renderFile = new URL('../frontend/.clinic-zero-arrival-ssr.mjs', import.meta.url)
await writeFile(renderFile, viewModule.output[0].code)
let renderView, renderStaff, renderPrebooking
try { ({ render: renderView, renderStaff, renderPrebooking } = await import(renderFile.href)) }
finally { await unlink(renderFile) }
const waitingScreen = renderView({ ...waitingActions, ...waiting })
assert.match(waitingScreen, /Amount to collect at visit.*\$0\.00/s)
assert.match(waitingScreen, /No payment is requested at arrival/)
const failedVisitScreen = renderView({ ...waitingActions, busy: null, error: 'eCW unavailable',
  ...deriveCommercialEstimateView({ ...waitingActions, busy: null, error: 'eCW unavailable' }) })
assert.match(failedVisitScreen, /Amount to collect at visit.*\$0\.00/s)
const otherPracticeScreen = renderView({ ...waitingActions,
  session: { arrival_collection_default_cents: null }, ...waiting })
assert.doesNotMatch(otherPracticeScreen, /Amount to collect at visit.*\$0\.00/s)

const stale = { ...model, session: { arrival_collection_default_cents: 0 },
  previousEstimate: true, supportedAmount: false }
const staleResult = { ...result, collection_guidance: { amount_cents: null,
  basis: 'missing_collection_inputs', reason: 'Previous result is stale.', services: [] } }
const staleHeadline = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
  { result: staleResult, model: stale, partialCopay: null, visibleAmount: false }))
assert.match(staleHeadline, /Amount to collect at visit.*\$0\.00/s)
const staleTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { result: staleResult, model: stale }))
assert.match(staleTable, /Amount to collect at visit.*\$0\.00/s)

const availity = renderToStaticMarkup(React.createElement(AvailityArrivalAmount,
  { result: { ...result, availity_visit_reference: { copay_cents: 3000,
    oop_remaining_cents: 10000 } } }))
assert.match(availity, /Amount to collect at visit.*\$0\.00/s)
assert.doesNotMatch(availity, /pending verification/)
const lookupScreen = renderStaff({
  restoring: false, busy: 'lookup', lookupDetails: { given_name: 'Pat', family_name: 'Example',
    birth_date: '1980-01-15' }, session: { practice_name: 'NMCT',
    arrival_collection_default_cents: 0 },
})
assert.match(lookupScreen, /Amount to collect at visit.*\$0\.00/s)
const otherLookupScreen = renderStaff({ restoring: false, busy: 'lookup',
  lookupDetails: { given_name: 'Pat', family_name: 'Example', birth_date: '1980-01-15' },
  session: { practice_name: 'Other clinic', arrival_collection_default_cents: null } })
assert.doesNotMatch(otherLookupScreen, /Amount to collect at visit.*\$0\.00/s)
const unresolvedPatientModel = { restoring: false, busy: null, screen: 'lookup',
  session: { practice_id: 'primary', practice_name: 'NMCT', arrival_collection_default_cents: 0,
    role: 'staff' },
  lookupDetails: { given_name: '', middle_initial: '', family_name: '', birth_date: '',
    ecw_account_number: '' }, ecwSuggestions: [], ecwSelection: '', ecwConfirmed: false,
  selectedEcwPatient: null, navigate() {}, changeEcwSearch() {}, startEcwLookup() {},
  lookupEcwPatient() {} }
const unresolvedPatientScreen = renderStaff(unresolvedPatientModel)
assert.match(unresolvedPatientScreen, /Amount to collect at visit.*\$0\.00/s)
assert.doesNotMatch(renderStaff({ ...unresolvedPatientModel,
  session: { practice_id: 'other', practice_name: 'Other clinic',
    arrival_collection_default_cents: null, role: 'staff' } }),
  /Amount to collect at visit.*\$0\.00/s)
assert.match(renderPrebooking('primary'), /Collect on arrival.*\$0\.00/s)
assert.doesNotMatch(renderPrebooking('synthetic-practice'), /Collect on arrival.*\$0\.00/s)
console.log('Clinic zero arrival: insured, cash, lookup, waiting, stale and Availity views')
