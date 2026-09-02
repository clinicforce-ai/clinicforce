import { Html5Qrcode, Html5QrcodeSupportedFormats } from 'html5-qrcode'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ArrowLeft, Camera, ScanLine, ShieldCheck, Upload } from 'lucide-react'
import { parseSmartHealthLink, type SmartHealthLink } from '../services/shl'

interface ScannerProps { onScanned: (link: SmartHealthLink) => void; onCancel: () => void }

const cameraConstraints: MediaTrackConstraints = { facingMode: { ideal: 'environment' } }
type NativeBarcodeDetector = { detect: (video: HTMLVideoElement) => Promise<Array<{ rawValue: string }>> }
type NativeBarcodeDetectorConstructor = new (options: { formats: string[] }) => NativeBarcodeDetector

const cameraMessage = (error: unknown) => {
  if (error instanceof DOMException) {
    if (error.name === 'NotFoundError') return 'No camera is available on this device.'
    if (error.name === 'NotReadableError') return 'The camera is being used by another app.'
    if (error.name === 'NotAllowedError') return 'Camera access is blocked by the browser or device.'
  }
  if (error instanceof Error) return error.message
  return 'Camera could not be started.'
}

export default function Scanner({ onScanned, onCancel }: ScannerProps) {
  const id = useId().replace(/:/g, '')
  const fileScannerId = useId().replace(/:/g, '')
  const video = useRef<HTMLVideoElement | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const fallbackScanner = useRef<Html5Qrcode | null>(null)
  const frame = useRef(0)
  const onScannedRef = useRef(onScanned)
  const complete = useRef(false)
  const [status, setStatus] = useState('Start the camera to scan the QR code.')
  const [error, setError] = useState('')
  const [readingFile, setReadingFile] = useState(false)
  const [cameraStarted, setCameraStarted] = useState(false)

  useEffect(() => { onScannedRef.current = onScanned }, [onScanned])

  const stopCamera = useCallback(() => {
    window.cancelAnimationFrame(frame.current)
    stream.current?.getTracks().forEach((track) => track.stop())
    stream.current = null
    if (video.current) video.current.srcObject = null
    const reader = fallbackScanner.current
    fallbackScanner.current = null
    if (reader) {
      const fallbackVideo = document.getElementById(id)?.querySelector('video')
      if (fallbackVideo) fallbackVideo.onabort = null
      void reader.stop().catch(() => undefined).finally(() => { try { reader.clear() } catch { /* already cleared */ } })
    }
    setCameraStarted(false)
  }, [id])

  useEffect(() => () => stopCamera(), [stopCamera])

  const handleDecoded = useCallback((decodedText: string) => {
    if (complete.current) return
    try {
      const link = parseSmartHealthLink(decodedText)
      complete.current = true
      setStatus('Smart Health Link found.')
      stopCamera()
      onScannedRef.current(link)
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : 'The QR code could not be read.')
    }
  }, [stopCamera])

  const startCamera = async () => {
    stopCamera(); setError(''); setStatus('Requesting camera access…')
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        throw new Error('Camera access requires a secure browser connection.')
      }
      let liveStream: MediaStream | undefined
      let lastError: unknown
      for (const videoConstraint of [cameraConstraints, true] as const) {
        try {
          liveStream = await navigator.mediaDevices.getUserMedia({ audio: false, video: videoConstraint })
          break
        } catch (caught) { lastError = caught }
      }
      if (!liveStream) throw lastError
      const Detector = (window as unknown as { BarcodeDetector?: NativeBarcodeDetectorConstructor }).BarcodeDetector
      if (Detector && video.current) {
        stream.current = liveStream
        video.current.srcObject = liveStream
        await video.current.play()
        const detector = new Detector({ formats: ['qr_code'] })
        const scan = async () => {
          if (complete.current || !video.current) return
          try {
            const [result] = await detector.detect(video.current)
            if (result?.rawValue) handleDecoded(result.rawValue)
          } catch { /* the camera can yield a frame before it is ready to decode */ }
          if (!complete.current) frame.current = window.requestAnimationFrame(() => { void scan() })
        }
        setCameraStarted(true); setStatus('Keep the complete QR inside the frame and hold steady.')
        void scan()
        return
      }
      // The browser has already granted a user-initiated stream. Release it, then use the established fallback.
      liveStream.getTracks().forEach((track) => track.stop())
      const reader = new Html5Qrcode(id, { formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE], verbose: false })
      fallbackScanner.current = reader
      await reader.start(cameraConstraints, { fps: 12, qrbox: (width, height) => {
        const edge = Math.floor(Math.min(width, height) * .9)
        return { width: edge, height: edge }
      } }, handleDecoded, () => undefined)
      setCameraStarted(true); setStatus('Keep the complete QR inside the frame and hold steady.')
    } catch (cameraError) {
      stopCamera()
      setError(`Camera unavailable: ${cameraMessage(cameraError)}`)
    }
  }

  const scanImage = async (file?: File) => {
    if (!file || complete.current) return
    setReadingFile(true); setError(''); setStatus('Reading the QR image locally…')
    const fileReader = new Html5Qrcode(fileScannerId, { formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE], verbose: false })
    try {
      handleDecoded(await fileReader.scanFile(file, false))
    } catch {
      setError('No readable Smart Health Link QR was found. Try the original screenshot or a sharper photo.')
      setStatus(cameraStarted ? 'Camera scanning is still active.' : 'Start the camera or upload another QR image.')
    } finally {
      try { fileReader.clear() } catch { /* file scanner was already cleared */ }
      setReadingFile(false)
    }
  }

  const cancel = () => { stopCamera(); onCancel() }

  return <section className="narrow-panel animate-enter" aria-labelledby="scan-heading">
    <button className="back-control" type="button" onClick={cancel}><ArrowLeft aria-hidden="true" className="size-4" /> Back to home</button>
    <div className="panel scanner-panel">
      <p className="eyebrow"><ScanLine aria-hidden="true" className="size-4" /> Clinician access · Step 1 of 2</p>
      <h1 id="scan-heading">Scan the patient’s QR code</h1>
      <p className="lead-small">Use a ClinicForce, eClinicalWorks, or other compatible Smart Health Link QR code. External links are reviewed and decrypted in this browser.</p>
      <div className="scanner-frame"><video ref={video} muted playsInline /><div id={id} /></div>
      <p className="scanner-status" role="status" aria-live="polite"><Camera aria-hidden="true" className="size-4" /> {status}</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="scanner-actions"><span>{cameraStarted ? 'Camera is active.' : 'Ready to scan?'}</span><button className="button button-primary" type="button" disabled={readingFile} onClick={() => void startCamera()}><Camera aria-hidden="true" className="size-4" /> {cameraStarted ? 'Restart camera' : 'Start camera'}</button><button className="button button-secondary" type="button" disabled={readingFile} onClick={() => fileInput.current?.click()}><Upload aria-hidden="true" className="size-4" /> {readingFile ? 'Reading image…' : 'Upload QR image'}</button><input ref={fileInput} type="file" accept="image/*" tabIndex={-1} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; void scanImage(file) }} /><small>The image is decoded in this browser and is not uploaded.</small></div>
      <div id={fileScannerId} className="scanner-file-surface" aria-hidden="true" />
      <p className="scanner-privacy"><ShieldCheck aria-hidden="true" /> Camera access is used only to scan the QR code.</p>
    </div>
  </section>
}
