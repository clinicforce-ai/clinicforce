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
  return 'Camera could not be started on this attempt.'
}

export default function Scanner({ onScanned, onCancel }: ScannerProps) {
  const id = useId().replace(/:/g, '')
  const fileScannerId = useId().replace(/:/g, '')
  const video = useRef<HTMLVideoElement | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)
  const stopCamera = useRef<() => void>(() => undefined)
  const onScannedRef = useRef(onScanned)
  const complete = useRef(false)
  const [status, setStatus] = useState('Requesting camera access…')
  const [error, setError] = useState('')
  const [readingFile, setReadingFile] = useState(false)
  const [retry, setRetry] = useState(0)

  const handleDecoded = useCallback((decodedText: string) => {
    if (complete.current) return
    try {
      const link = parseSmartHealthLink(decodedText)
      complete.current = true
      setStatus('Smart Health Link found.')
      stopCamera.current()
      onScannedRef.current(link)
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : 'The QR code could not be read.')
    }
  }, [])

  useEffect(() => {
    onScannedRef.current = onScanned
  }, [onScanned])

  useEffect(() => {
    let active = true
    let stream: MediaStream | null = null
    let reader: Html5Qrcode | null = null
    let animationFrame = 0
    const stop = () => {
      window.cancelAnimationFrame(animationFrame)
      stream?.getTracks().forEach((track) => track.stop())
      stream = null
      if (video.current) video.current.srcObject = null
      if (reader) {
        const fallbackVideo = document.getElementById(id)?.querySelector('video')
        if (fallbackVideo) fallbackVideo.onabort = null
        void reader.stop().catch(() => undefined).finally(() => { try { reader?.clear() } catch { /* already cleared */ } })
        reader = null
      }
    }
    stopCamera.current = stop

    const start = async () => {
      try {
        if (!window.isSecureContext) {
          throw new Error('Camera access requires a secure (HTTPS) connection.')
        }
        const scanConfig = {
          fps: 15,
          qrbox: (width: number, height: number) => {
            const edge = Math.floor(Math.min(width, height) * .9)
            return { width: edge, height: edge }
          },
        }
        const onSuccess = (decodedText: string) => { if (active) handleDecoded(decodedText) }
        const Detector = (window as unknown as { BarcodeDetector?: NativeBarcodeDetectorConstructor }).BarcodeDetector
        if (Detector && video.current) {
          // Chrome's native stream and detector avoid html5-qrcode's fragile video setup.
          stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: cameraConstraints })
          video.current.srcObject = stream
          await video.current.play()
          const detector = new Detector({ formats: ['qr_code'] })
          const scan = async () => {
            if (!active || complete.current || !video.current) return
            try {
              const [result] = await detector.detect(video.current)
              if (result?.rawValue) onSuccess(result.rawValue)
            } catch { /* a frame can be unavailable while the camera warms up */ }
            if (active) animationFrame = window.requestAnimationFrame(() => { void scan() })
          }
          void scan()
        } else {
          reader = new Html5Qrcode(id, { formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE], verbose: false })
          await reader.start(cameraConstraints, scanConfig, onSuccess, () => undefined)
        }
        if (active) setStatus('Keep the complete QR inside the frame and hold steady.')
      } catch (cameraError) {
        if (active) setError(`Camera unavailable: ${cameraMessage(cameraError)}`)
      }
    }

    void start()
    return () => {
      active = false
      stopCamera.current = () => undefined
      stop()
    }
  }, [handleDecoded, id, retry])

  const retryCamera = async () => {
    setError(''); setStatus('Requesting camera access…')
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera API unavailable')
      const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: cameraConstraints })
      stream.getTracks().forEach((track) => track.stop())
      setRetry((value) => value + 1)
    } catch (cameraError) {
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
      setStatus('Camera scanning is still active.')
    } finally {
      try { fileReader.clear() } catch { /* file scanner was already cleared */ }
      setReadingFile(false)
    }
  }

  const cancel = () => { stopCamera.current(); onCancel() }

  return <section className="narrow-panel animate-enter" aria-labelledby="scan-heading">
    <button className="back-control" type="button" onClick={cancel}><ArrowLeft aria-hidden="true" className="size-4" /> Back to home</button>
    <div className="panel scanner-panel">
      <p className="eyebrow"><ScanLine aria-hidden="true" className="size-4" /> Clinician access · Step 1 of 2</p>
      <h1 id="scan-heading">Scan the patient’s QR code</h1>
      <p className="lead-small">Use a ClinicForce, eClinicalWorks, or other compatible Smart Health Link QR code. External links are reviewed and decrypted in this browser.</p>
      <div className="scanner-frame"><video ref={video} muted playsInline /><div id={id} /></div>
      <p className="scanner-status" role="status" aria-live="polite"><Camera aria-hidden="true" className="size-4" /> {status}</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="scanner-actions"><span>Camera having trouble?</span>{error && <button className="button button-secondary" type="button" disabled={readingFile} onClick={() => void retryCamera()}><Camera aria-hidden="true" className="size-4" /> Try camera again</button>}<button className="button button-secondary" type="button" disabled={readingFile} onClick={() => fileInput.current?.click()}><Upload aria-hidden="true" className="size-4" /> {readingFile ? 'Reading image…' : 'Upload QR image'}</button><input ref={fileInput} type="file" accept="image/*" tabIndex={-1} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; void scanImage(file) }} /><small>The image is decoded in this browser and is not uploaded.</small></div>
      <div id={fileScannerId} className="scanner-file-surface" aria-hidden="true" />
      <p className="scanner-privacy"><ShieldCheck aria-hidden="true" /> Camera access is used only to scan the QR code.</p>
    </div>
  </section>
}
