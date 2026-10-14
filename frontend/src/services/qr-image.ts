import type { Html5Qrcode } from 'html5-qrcode'

export async function scanQrImage(
  reader: Pick<Html5Qrcode, 'scanFile'>,
  file: File,
): Promise<string> {
  try {
    return await reader.scanFile(file, false)
  } catch (originalError) {
    // The camera decoder misses some valid dense QRs and resized screenshots.
    // Load the image-only fallback on failure; SHL validation still runs afterward.
    const { default: jsQR } = await import('jsqr')
    const image = await createImageBitmap(file)
    try {
      const scales = new Set(
        [1, 1.5].map((scale) => Math.min(scale, 2000 / Math.max(image.width, image.height))),
      )
      for (const scale of scales) {
        const canvas = document.createElement('canvas')
        canvas.width = Math.ceil(image.width * scale)
        canvas.height = Math.ceil(image.height * scale)
        const context = canvas.getContext('2d')
        if (!context) throw originalError
        context.fillStyle = '#fff'
        context.fillRect(0, 0, canvas.width, canvas.height)
        // Smooth fractional module edges left by a resized screenshot.
        context.imageSmoothingEnabled = true
        context.drawImage(image, 0, 0, canvas.width, canvas.height)
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height)
        const result = jsQR(pixels.data, pixels.width, pixels.height)
        if (result) return result.data
        if (scale !== 1) {
          const blob = await new Promise<Blob | null>((resolve) =>
            canvas.toBlob(resolve, 'image/png'),
          )
          if (blob) {
            try {
              return await reader.scanFile(
                new File([blob], 'qr-screenshot.png', { type: 'image/png' }),
                false,
              )
            } catch {
              /* Neither decoder could read this bounded image variant. */
            }
          }
        }
      }
      throw originalError
    } finally {
      image.close()
    }
  }
}
