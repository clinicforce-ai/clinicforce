// Run: node testing/test-card-capture.mjs (Node 24's native TypeScript support).
import assert from 'node:assert/strict'
import { analyzeCardFrame, videoCrop, CARD_GUIDE, validateCardFiles } from '../frontend/src/services/card-capture.ts'

const frame = ({ blank = false, dark = false, shift = 0, smooth = false } = {}) => {
  const data = new Uint8ClampedArray(400 * 300 * 4)
  for (let y = 0; y < 300; y++) for (let x = 0; x < 400; x++) {
    let value = dark ? 12 : 30
    if (!blank && x >= 32 + shift && x <= 368 + shift && y >= 44 && y <= 256) {
      value = dark ? 28 : 224
      if (!smooth && x > 75 + shift && x < 290 + shift && y > 75 && y < 225 && (y % 14 < 3) && (x + shift) % 21 < 15) value = dark ? 4 : 52
    }
    const index = (y * 400 + x) * 4
    data[index] = data[index + 1] = data[index + 2] = value
    data[index + 3] = 255
  }
  return data
}
const first = analyzeCardFrame(frame())
const blurredFrame = () => {
  const source = frame(), blurred = new Uint8ClampedArray(source)
  for (let y = 5; y < 295; y++) for (let x = 5; x < 395; x++) {
    let sum = 0
    for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) sum += source[((y + dy) * 400 + x + dx) * 4]
    const index = (y * 400 + x) * 4
    blurred[index] = blurred[index + 1] = blurred[index + 2] = sum / 81
  }
  return blurred
}
assert.equal(first.aligned, true, 'rectangular border near guide should be found')
assert.equal(first.ready, false, 'first frame alone cannot auto-capture')
assert.equal(analyzeCardFrame(frame(), first.gray).ready, true, 'steady readable rectangle should allow capture')
assert.equal(analyzeCardFrame(frame({ blank: true }), first.gray).ready, false, 'no rectangle must not capture')
assert.equal(analyzeCardFrame(frame({ smooth: true }), first.gray).sharp, false, 'border alone does not establish focus')
assert.equal(analyzeCardFrame(blurredFrame(), first.gray).sharp, false, 'blurred printed text must fail focus check')
assert.equal(analyzeCardFrame(frame({ dark: true }), first.gray).wellLit, false, 'dark frames must not auto-capture')
assert.equal(analyzeCardFrame(frame({ shift: 7 }), first.gray).still, false, 'moving card must reset sustained capture')
assert.equal(analyzeCardFrame(frame({ shift: 30 }), first.gray).aligned, false, 'off-guide card must not auto-capture')
for (const [width, height] of [[1920, 1080], [1080, 1920], [640, 480]]) {
  const crop = videoCrop(width, height, CARD_GUIDE)
  assert(crop.x >= 0 && crop.y >= 0 && crop.x + crop.width <= width && crop.y + crop.height <= height)
  assert(Math.abs(crop.width / crop.height - CARD_GUIDE.width / CARD_GUIDE.height) < .001)
}
await validateCardFiles([new File(['%PDF-1.7\ncard'], 'card.pdf', { type: 'application/pdf' })])
await validateCardFiles([new File([Uint8Array.of(255, 216, 255, 224)], 'front.jpg', { type: 'image/jpeg' })])
await assert.rejects(validateCardFiles([]), /one or two/)
await assert.rejects(validateCardFiles([new File([], 'card.pdf')]), /empty/)
await assert.rejects(validateCardFiles([new File(['html'], 'card.pdf')]), /matching file extension/)
await assert.rejects(validateCardFiles([new File(['%PDF-1.7'], 'card.png')]), /matching file extension/)
await assert.rejects(validateCardFiles([new File(['%PDF-1.7'], 'card.pdf', { type: 'text/html' })]), /matching file extension/)
await assert.rejects(validateCardFiles([new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'card.png')]), /too large/)
console.log('Card capture: steady/blank/blur/dark/moving/off-guide frames, portrait crop, and file guards passed.')
