export const FRAME_WIDTH = 400
export const FRAME_HEIGHT = 300
export const CARD_GUIDE = { x: 32, y: 44, width: 336, height: 212 }
export interface CaptureRect { x: number; y: number; width: number; height: number }
export interface CardQuality {
  aligned: boolean; sharp: boolean; wellLit: boolean; still: boolean
  ready: boolean; crop: CaptureRect; gray: Uint8Array
}

/** Match the video's CSS object-fit: cover, including portrait phone cameras. */
export function videoCrop(width: number, height: number, crop: CaptureRect): CaptureRect {
  const scale = Math.max(FRAME_WIDTH / width, FRAME_HEIGHT / height)
  const offsetX = (FRAME_WIDTH - width * scale) / 2
  const offsetY = (FRAME_HEIGHT - height * scale) / 2
  return { x: (crop.x - offsetX) / scale, y: (crop.y - offsetY) / scale, width: crop.width / scale, height: crop.height / scale }
}

/** Detect a nearly horizontal rectangular border close to the on-screen guide.
 * ponytail: four border projections are deliberately conservative; use perspective
 * detection only if field testing justifies it. This does not recognize insurance cards.
 */
export function analyzeCardFrame(data: Uint8ClampedArray, previous?: Uint8Array): CardQuality {
  const gray = new Uint8Array(FRAME_WIDTH * FRAME_HEIGHT)
  for (let pixel = 0; pixel < gray.length; pixel++) {
    const offset = pixel * 4
    gray[pixel] = Math.round(data[offset] * .299 + data[offset + 1] * .587 + data[offset + 2] * .114)
  }
  const at = (x: number, y: number) => gray[y * FRAME_WIDTH + x]
  const edge = (vertical: boolean, center: number, start: number, end: number, tolerance: number) => {
    let best = { position: center, score: 0, coverage: 0 }
    for (let position = center - tolerance; position <= center + tolerance; position++) {
      let total = 0, found = 0, samples = 0
      for (let along = start; along < end; along += 2) {
        const difference = vertical ? Math.abs(at(position - 3, along) - at(position + 3, along)) : Math.abs(at(along, position - 3) - at(along, position + 3))
        total += difference
        if (difference > 24) found++
        samples++
      }
      const coverage = found / samples
      const score = total / samples * coverage
      if (score > best.score) best = { position, score, coverage }
    }
    return best
  }
  const left = edge(true, 32, 65, 235, 18), right = edge(true, 368, 65, 235, 18)
  const top = edge(false, 44, 65, 335, 16), bottom = edge(false, 256, 65, 335, 16)
  const width = right.position - left.position, height = bottom.position - top.position
  const aligned = [left, right, top, bottom].every(item => item.coverage >= .65 && item.score >= 24) && width / height > 1.4 && width / height < 1.8
  let brightness = 0, laplacian = 0, movement = 0, samples = 0
  // Exclude the outer border: a blank rectangle must not pass the text-focus check.
  for (let y = 78; y < 222; y += 2) for (let x = 72; x < 328; x += 2) {
    const center = at(x, y)
    const laplace = at(x - 1, y) + at(x + 1, y) + at(x, y - 1) + at(x, y + 1) - 4 * center
    brightness += center
    laplacian += laplace * laplace
    if (previous) movement += Math.abs(center - previous[y * FRAME_WIDTH + x])
    samples++
  }
  const sharp = laplacian / samples > 65
  const wellLit = brightness / samples > 45 && brightness / samples < 253
  const still = Boolean(previous && previous.length === gray.length && movement / samples < 5)
  const crop = aligned ? { x: left.position - 7, y: top.position - 7, width: width + 14, height: height + 14 } : CARD_GUIDE
  return { aligned, sharp, wellLit, still, ready: aligned && sharp && wellLit && still, crop, gray }
}

export async function validateCardFiles(files: File[]): Promise<void> {
  if (!files.length || files.length > 2) throw new Error('Choose one or two card files: PDF, JPG or PNG.')
  for (const file of files) {
    if (!file.size) throw new Error(`${file.name} is empty. Choose a readable card file.`)
    if (file.size > 10 * 1024 * 1024) throw new Error(`${file.name} is too large. Each card file must be 10 MB or less.`)
    const signature = new Uint8Array(await file.slice(0, 8).arrayBuffer())
    const pdf = String.fromCharCode(...signature.slice(0, 4)) === '%PDF'
    const jpeg = signature[0] === 255 && signature[1] === 216 && signature[2] === 255
    const png = [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => signature[index] === value)
    const extension = file.name.split('.').pop()?.toLowerCase()
    const matches = pdf ? extension === 'pdf' : jpeg ? extension === 'jpg' || extension === 'jpeg' : png && extension === 'png'
    const mime = pdf ? 'application/pdf' : jpeg ? 'image/jpeg' : png ? 'image/png' : ''
    if (!matches || (file.type && file.type !== mime && file.type !== 'application/octet-stream')) throw new Error(`${file.name} must be a PDF, JPG or PNG with the matching file extension.`)
  }
}
