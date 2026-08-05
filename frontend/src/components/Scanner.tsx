import { Html5Qrcode } from 'html5-qrcode'
import { useEffect, useId, useRef, useState } from 'react'
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
        if (active) setStatus('Point the camera at the patient’s ClinicForce QR code.')
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

  return <><button className="back-link" type="button" onClick={cancel}>← Back to home</button><section className="card scanner-card" aria-labelledby="scan-heading"><p className="step-label">Step 1 of 2</p><h1 id="scan-heading">Scan patient QR code</h1><p className="muted">Use the patient’s Smart Health Link to securely retrieve their clinical record.</p><div id={id} className="scanner-frame" /><p className="scanner-status">{status}</p>{error && <p className="scanner-error" role="alert">{error}</p>}</section></>
}
