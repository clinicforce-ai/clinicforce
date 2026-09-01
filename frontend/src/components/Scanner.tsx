import { Html5Qrcode, Html5QrcodeSupportedFormats } from 'html5-qrcode'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ArrowLeft, Camera, ScanLine, ShieldCheck, Upload } from 'lucide-react'
import { parseSmartHealthLink, type SmartHealthLink } from '../services/shl'

interface ScannerProps { onScanned: (link: SmartHealthLink) => void; onCancel: () => void }

export default function Scanner({ onScanned, onCancel }: ScannerProps) {
  const id = useId().replace(/:/g, '')
  const fileScannerId = useId().replace(/:/g, '')
  const scanner = useRef<Html5Qrcode | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)
  const stopCamera = useRef<() => void>(() => undefined)
  const cleanupSequence = useRef<Promise<void>>(Promise.resolve())
  const isInitializing = useRef(false)
  const onScannedRef = useRef(onScanned)
  const complete = useRef(false)
  const [status, setStatus] = useState('Requesting camera access…')
  const [error, setError] = useState('')
  const [readingFile, setReadingFile] = useState(false)

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
    const reader = new Html5Qrcode(id, { formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE], verbose: false })
    scanner.current = reader
    let active = true
    let cleanupPromise: Promise<void> | undefined

    const videoElements = () => document.getElementById(id)?.querySelectorAll('video') ?? []
    const releaseMediaTracks = () => {
      // Stop the actual MediaStream as well as the library. This guarantees the
      // camera indicator turns off even if html5-qrcode's asynchronous stop hangs.
      videoElements().forEach((video) => {
        const stream = video.srcObject
        if (stream && typeof (stream as MediaStream).getTracks === 'function') {
          const mediaStream = stream as MediaStream
          mediaStream.getTracks().forEach((track) => track.stop())
          video.srcObject = null
        }
      })
    }

    const cleanup = (): Promise<void> => {
      if (cleanupPromise) return cleanupPromise
      cleanupPromise = (async () => {
        // Let a pending start() settle before stopping and clearing the instance.
        // This prevents React Strict Mode's effect replay from interrupting video.play().
        await startPromise?.catch(() => undefined)
        // html5-qrcode throws from its video onabort handler during expected cleanup.
        // Remove only that handler, then let the library stop before forcing tracks off.
        videoElements().forEach((video) => { video.onabort = null })
        try {
          await Promise.race([
            reader.stop().catch(() => undefined),
            new Promise<void>((resolve) => window.setTimeout(resolve, 750)),
          ])
        } catch { /* scanner was not running */ }
        releaseMediaTracks()
        try { reader.clear() } catch { /* scanner was already cleared */ }
      })()
      return cleanupPromise
    }
    stopCamera.current = () => { void cleanup() }

    const start = async () => {
      // A previous Strict Mode effect may still be releasing its scanner.
      await cleanupSequence.current
      if (!active || isInitializing.current) return

      isInitializing.current = true
      try {
        const scanConfig = {
          fps: 15,
          qrbox: (width: number, height: number) => {
            const edge = Math.floor(Math.min(width, height) * .9)
            return { width: edge, height: edge }
          },
        }
        const onSuccess = (decodedText: string) => { if (active) handleDecoded(decodedText) }
        const preferredCamera = {
          facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1440 },
          advanced: [{ focusMode: 'continuous' }],
        } as unknown as MediaTrackConstraints
        try {
          await reader.start(preferredCamera, scanConfig, onSuccess, () => undefined)
        } catch {
          // Some mobile browsers reject optional focus or resolution constraints.
          await reader.start({ facingMode: { ideal: 'environment' } }, scanConfig, onSuccess, () => undefined)
        }
        if (active) setStatus('Keep the complete QR inside the frame and hold steady.')
      } catch (cameraError) {
        if (active) setError(cameraError instanceof Error ? `Camera unavailable: ${cameraError.message}` : 'Camera unavailable. Allow camera access in your browser, then try again.')
      } finally {
        isInitializing.current = false
      }
    }

    const startPromise = start()
    return () => {
      active = false
      cleanupSequence.current = cleanup()
      scanner.current = null
      stopCamera.current = () => undefined
    }
  }, [handleDecoded, id])

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
      <div className="scanner-frame"><div id={id} /></div>
      <p className="scanner-status" role="status" aria-live="polite"><Camera aria-hidden="true" className="size-4" /> {status}</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="scanner-actions"><span>Camera having trouble?</span><button className="button button-secondary" type="button" disabled={readingFile} onClick={() => fileInput.current?.click()}><Upload aria-hidden="true" className="size-4" /> {readingFile ? 'Reading image…' : 'Upload QR image'}</button><input ref={fileInput} type="file" accept="image/*" tabIndex={-1} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; void scanImage(file) }} /><small>The image is decoded in this browser and is not uploaded.</small></div>
      <div id={fileScannerId} className="scanner-file-surface" aria-hidden="true" />
      <p className="scanner-privacy"><ShieldCheck aria-hidden="true" /> Camera access is used only to scan the QR code.</p>
    </div>
  </section>
}
