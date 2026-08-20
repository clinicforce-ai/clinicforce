import { Html5Qrcode } from 'html5-qrcode'
import { useEffect, useId, useRef, useState } from 'react'
import { ArrowLeft, Camera, ScanLine, ShieldCheck } from 'lucide-react'
import { parseSmartHealthLink, type SmartHealthLink } from '../services/shl'

interface ScannerProps { onScanned: (link: SmartHealthLink) => void; onCancel: () => void }

export default function Scanner({ onScanned, onCancel }: ScannerProps) {
  const id = useId().replace(/:/g, '')
  const scanner = useRef<Html5Qrcode | null>(null)
  const stopCamera = useRef<() => void>(() => undefined)
  const cleanupSequence = useRef<Promise<void>>(Promise.resolve())
  const isInitializing = useRef(false)
  const onScannedRef = useRef(onScanned)
  const complete = useRef(false)
  const [status, setStatus] = useState('Requesting camera access…')
  const [error, setError] = useState('')

  useEffect(() => {
    onScannedRef.current = onScanned
  }, [onScanned])

  useEffect(() => {
    const reader = new Html5Qrcode(id)
    scanner.current = reader
    let active = true
    let cleanupPromise: Promise<void> | undefined

    const releaseMediaTracks = () => {
      // Stop the actual MediaStream as well as the library. This guarantees the
      // camera indicator turns off even if html5-qrcode's asynchronous stop hangs.
      const videos = document.getElementById(id)?.querySelectorAll('video') ?? []
      videos.forEach((video) => {
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
        releaseMediaTracks()
        // Let a pending start() settle before stopping and clearing the instance.
        // This prevents React Strict Mode's effect replay from interrupting video.play().
        await startPromise?.catch(() => undefined)
        try { await reader.stop() } catch { /* scanner was not running */ }
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
        await reader.start(
          { facingMode: 'environment' },
          { fps: 10, qrbox: { width: 250, height: 250 } },
          (decodedText) => {
            if (complete.current) return
            try {
              const link = parseSmartHealthLink(decodedText)
              complete.current = true
              setStatus('Smart Health Link found.')
              // Navigation must not wait for a camera-driver stop promise, which can
              // hang after a successful decode in some browsers.
              void cleanup()
              if (active) onScannedRef.current(link)
            } catch (scanError) {
              setError(scanError instanceof Error ? scanError.message : 'The QR code could not be read.')
            }
          },
          () => undefined,
        )
        if (active) setStatus('Point the camera at a Smart Health Link QR code.')
      } catch (cameraError) {
        if (active) setError(cameraError instanceof Error ? `Camera unavailable: ${cameraError.message}` : 'Camera access was unavailable.')
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
  }, [id])

  const cancel = () => { stopCamera.current(); onCancel() }

  return <section className="narrow-panel animate-enter" aria-labelledby="scan-heading">
    <button className="back-control" type="button" onClick={cancel}><ArrowLeft aria-hidden="true" className="size-4" /> Back to home</button>
    <div className="panel scanner-panel">
      <p className="eyebrow"><ScanLine aria-hidden="true" className="size-4" /> Clinician access · Step 1 of 2</p>
      <h1 id="scan-heading">Scan the patient’s QR code</h1>
      <p className="lead-small">Use a ClinicForce, eClinicalWorks, or other compatible Smart Health Link QR code. External links are reviewed and decrypted in this browser.</p>
      <div className="scanner-frame"><div id={id} /></div>
      <p className="scanner-status"><Camera aria-hidden="true" className="size-4" /> {status}</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <p className="scanner-privacy"><ShieldCheck aria-hidden="true" /> Camera access is used only to scan the QR code.</p>
    </div>
  </section>
}
