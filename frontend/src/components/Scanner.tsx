import { Html5Qrcode, Html5QrcodeSupportedFormats } from 'html5-qrcode'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ArrowLeft, Camera, ScanLine, ShieldCheck, Upload } from 'lucide-react'
import { parseSmartHealthLink, type SmartHealthLink } from '../services/shl'
import { scanQrImage } from '../services/qr-image'

interface ScannerProps { onScanned: (link: SmartHealthLink) => void; onCancel: () => void }

const scannerConfig = {
  fps: 12,
}

const cameraMessage = (error: unknown) => {
  const name = typeof error === 'object' && error && 'name' in error ? String(error.name) : ''
  const detail = `${name} ${error instanceof Error ? error.message : String(error)}`.toLowerCase()
  if (/notfound|requested device not found/.test(detail)) return 'No camera is available on this device.'
  if (/notreadable|could not start video source/.test(detail)) return 'The camera is being used by another app.'
  if (/notallowed|permission denied|permission dismissed/.test(detail)) return 'Camera access is blocked by the browser or device.'
  if (/secure|mediadevices/.test(detail)) return 'Camera access requires HTTPS in a supported browser.'
  return 'Camera could not be started.'
}

export default function Scanner({ onScanned, onCancel }: ScannerProps) {
  const id = useId().replace(/:/g, '')
  const fileScannerId = useId().replace(/:/g, '')
  const fileInput = useRef<HTMLInputElement | null>(null)
  const onScannedRef = useRef(onScanned)
  const complete = useRef(false)
  const [status, setStatus] = useState('Requesting camera access…')
  const [error, setError] = useState('')
  const [readingFile, setReadingFile] = useState(false)

  useEffect(() => { onScannedRef.current = onScanned }, [onScanned])

  const handleDecoded = useCallback((decodedText: string) => {
    if (complete.current) return
    try {
      complete.current = true
      setStatus('Smart Health Link found.')
      onScannedRef.current(parseSmartHealthLink(decodedText))
    } catch (scanError) {
      complete.current = false
      setError(scanError instanceof Error ? scanError.message : 'The QR code could not be read.')
    }
  }, [])

  useEffect(() => {
    const reader = new Html5Qrcode(id, { formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE], verbose: false })
    let closed = false
    let running = false
    let start: Promise<void> | undefined
    const openingFrame = window.requestAnimationFrame(() => {
      start = reader.start({ facingMode: 'environment' }, scannerConfig, handleDecoded, () => undefined).then(() => {
        running = true
        if (!closed) setStatus('Keep the complete QR inside the frame and hold steady.')
      }).catch((caught) => {
        if (!closed) setError(`Camera unavailable: ${cameraMessage(caught)}`)
      })
    })

    return () => {
      closed = true
      window.cancelAnimationFrame(openingFrame)
      void start?.finally(async () => {
        if (running) await reader.stop().catch(() => undefined)
        try { reader.clear() } catch { /* scanner surface is already gone */ }
      })
    }
  }, [handleDecoded, id])

  const scanImage = async (file?: File) => {
    if (!file || complete.current) return
    setReadingFile(true); setError(''); setStatus('Reading the QR image locally…')
    const fileReader = new Html5Qrcode(fileScannerId, { formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE], verbose: false })
    try {
      handleDecoded(await scanQrImage(fileReader, file))
    } catch {
      setError('No readable Smart Health Link QR was found. Try the original screenshot or a sharper photo.')
      setStatus('Camera scanning is still active.')
    } finally {
      try { fileReader.clear() } catch { /* file scanner was already cleared */ }
      setReadingFile(false)
    }
  }

  return <section className="narrow-panel animate-enter" aria-labelledby="scan-heading">
    <button className="back-control" type="button" onClick={onCancel}><ArrowLeft aria-hidden="true" className="size-4" /> Back to home</button>
    <div className="panel scanner-panel">
      <p className="eyebrow"><ScanLine aria-hidden="true" className="size-4" /> Clinician access · Step 1 of 2</p>
      <h1 id="scan-heading">Scan the patient’s QR code</h1>
      <p className="lead-small">Use a ClinicForce, eClinicalWorks, or other compatible Smart Health Link QR code. External links are reviewed and decrypted in this browser.</p>
      <div className="scanner-frame"><div id={id} /></div>
      <p className="scanner-status" role="status" aria-live="polite"><Camera aria-hidden="true" className="size-4" /> {status}</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="scanner-actions"><button className="button button-secondary" type="button" disabled={readingFile} onClick={() => fileInput.current?.click()}><Upload aria-hidden="true" className="size-4" /> {readingFile ? 'Reading image…' : 'Upload QR image'}</button><input ref={fileInput} type="file" accept="image/*" tabIndex={-1} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; void scanImage(file) }} /><small>The image is decoded in this browser and is not uploaded.</small></div>
      <div id={fileScannerId} className="scanner-file-surface" aria-hidden="true" />
      <p className="scanner-privacy"><ShieldCheck aria-hidden="true" /> Camera access is used only to scan the QR code.</p>
    </div>
  </section>
}
