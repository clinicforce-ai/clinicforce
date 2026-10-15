import { useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  analyzeCardFrame,
  CARD_GUIDE,
  FRAME_HEIGHT,
  FRAME_WIDTH,
  validateCardFiles,
  videoCrop,
  type CardQuality,
} from '../../services/card-capture'

interface CardFile {
  file: File
  url: string
}
type Side = 0 | 1
const sideName = (side: number) => (side === 0 ? 'front' : 'back')
const asJpeg = (canvas: HTMLCanvasElement) =>
  new Promise<Blob>((resolve, reject) =>
    canvas.toBlob(
      (blob) =>
        blob ? resolve(blob) : reject(new Error('The photo could not be saved. Please try again.')),
      'image/jpeg',
      0.94,
    ),
  )
const readImage = (url: string) =>
  new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image()
    image.onload = () =>
      image.naturalWidth * image.naturalHeight > 20_000_000
        ? reject(
            new Error(
              'This photo is too large to process. Choose a photo of 20 megapixels or less.',
            ),
          )
        : resolve(image)
    image.onerror = () =>
      reject(new Error('This photo could not be read. Choose another JPG or PNG.'))
    image.src = url
  })
const cameraError = (caught: unknown) => {
  const name = caught instanceof DOMException ? caught.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError')
    return 'Camera access is blocked. Allow it in your browser, or upload a card photo or PDF.'
  if (name === 'NotFoundError') return 'No camera was found. Upload a card photo or PDF.'
  if (name === 'NotReadableError')
    return 'The camera is in use by another app. Close that app, or upload a card photo or PDF.'
  return 'The camera could not start. Try again, or upload a card photo or PDF.'
}

export function useCardCapture() {
  const headingId = useId()
  const video = useRef<HTMLVideoElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const stream = useRef<MediaStream | null>(null)
  const mounted = useRef(false)
  const entries = useRef<Array<CardFile | undefined>>([])
  const uploadSide = useRef<Side | null>(null)
  const latestQuality = useRef<CardQuality | null>(null)
  const captureLock = useRef(false)
  const autoEnabled = useRef(true)
  const captureRef = useRef<() => void>(() => undefined)
  const [files, setFiles] = useState<Array<CardFile | undefined>>([])
  const [side, setSide] = useState<Side>(0)
  const [cameraOn, setCameraOn] = useState(false)
  const [cameraReady, setCameraReady] = useState(false)
  const [automatic, setAutomatic] = useState(true)
  const [quality, setQuality] = useState<CardQuality | null>(null)
  const [steady, setSteady] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const stopCamera = useCallback(() => {
    stream.current?.getTracks().forEach((track) => track.stop())
    stream.current = null
    if (video.current) video.current.srcObject = null
  }, [])
  useEffect(() => {
    mounted.current = true
    heading.current?.focus()
    return () => {
      mounted.current = false
      stopCamera()
      entries.current.forEach((entry) => {
        if (entry) URL.revokeObjectURL(entry.url)
      })
    }
  }, [stopCamera])
  useEffect(() => {
    autoEnabled.current = automatic
  }, [automatic])

  const saveFiles = useCallback((next: Array<File | undefined>) => {
    const previous = entries.current
    const saved = next.map((file) => (file ? { file, url: URL.createObjectURL(file) } : undefined))
    entries.current = saved
    setFiles(saved)
    previous.forEach((entry) => {
      if (entry) URL.revokeObjectURL(entry.url)
    })
  }, [])
  const closeCamera = () => {
    stopCamera()
    setCameraOn(false)
    setCameraReady(false)
  }
  const startCamera = (nextSide: Side) => {
    setSide(nextSide)
    setError('')
    setNotice('')
    setQuality(null)
    setSteady(0)
    setCameraReady(false)
    latestQuality.current = null
    setCameraOn(true)
  }

  const capture = useCallback(async () => {
    const source = video.current
    if (!source?.videoWidth || !source.videoHeight || captureLock.current) return
    captureLock.current = true
    setBusy(true)
    setError('')
    try {
      const rectangle = videoCrop(
        source.videoWidth,
        source.videoHeight,
        latestQuality.current?.crop ?? CARD_GUIDE,
      )
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(rectangle.width)
      canvas.height = Math.round(rectangle.height)
      const context = canvas.getContext('2d')
      if (!context)
        throw new Error('Photo capture is unavailable in this browser. Upload a photo or PDF.')
      context.drawImage(
        source,
        rectangle.x,
        rectangle.y,
        rectangle.width,
        rectangle.height,
        0,
        0,
        canvas.width,
        canvas.height,
      )
      const blob = await asJpeg(canvas)
      if (!mounted.current) return
      const next = entries.current.map((entry) => entry?.file)
      next[side] = new File([blob], `${sideName(side)}.jpg`, { type: 'image/jpeg' })
      await validateCardFiles(next.filter((file): file is File => Boolean(file)))
      if (!mounted.current) return
      saveFiles(next)
      stopCamera()
      setCameraOn(false)
      setCameraReady(false)
      setNotice(
        `${side === 0 ? 'Front' : 'Back'} captured. Check the crop and make sure the text is readable.`,
      )
      heading.current?.focus()
    } catch (caught) {
      if (mounted.current)
        setError(
          caught instanceof Error
            ? caught.message
            : 'The photo could not be captured. Please try again.',
        )
    } finally {
      captureLock.current = false
      if (mounted.current) setBusy(false)
    }
  }, [saveFiles, side, stopCamera])
  useEffect(() => {
    captureRef.current = () => {
      void capture()
    }
  }, [capture])

  useEffect(() => {
    if (!cameraOn) return
    let closed = false,
      timer: number | undefined,
      previous: Uint8Array | undefined,
      goodFrames = 0
    const pause = () => {
      if (!document.hidden) return
      stopCamera()
      setCameraOn(false)
      setCameraReady(false)
      setNotice('Camera paused. Start the camera again when you are ready to continue.')
    }
    const open = async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera unavailable')
        const media = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1920 },
            height: { ideal: 1080 },
          },
        })
        if (closed) {
          media.getTracks().forEach((track) => track.stop())
          return
        }
        stream.current = media
        const source = video.current
        if (!source) {
          stopCamera()
          return
        }
        source.srcObject = media
        await source.play()
        if (closed) return
        setCameraReady(true)
        const canvas = document.createElement('canvas')
        canvas.width = FRAME_WIDTH
        canvas.height = FRAME_HEIGHT
        const context = canvas.getContext('2d', { willReadFrequently: true })
        if (!context) return // Manual capture remains available if frame analysis is unsupported.
        timer = window.setInterval(() => {
          if (closed || captureLock.current || source.readyState < 2 || !source.videoWidth) return
          try {
            const whole = videoCrop(source.videoWidth, source.videoHeight, {
              x: 0,
              y: 0,
              width: FRAME_WIDTH,
              height: FRAME_HEIGHT,
            })
            context.drawImage(
              source,
              whole.x,
              whole.y,
              whole.width,
              whole.height,
              0,
              0,
              FRAME_WIDTH,
              FRAME_HEIGHT,
            )
            const result = analyzeCardFrame(
              context.getImageData(0, 0, FRAME_WIDTH, FRAME_HEIGHT).data,
              previous,
            )
            previous = result.gray
            latestQuality.current = result
            setQuality(result)
            goodFrames = result.ready ? goodFrames + 1 : 0
            setSteady(Math.min(goodFrames, 5))
            if (autoEnabled.current && goodFrames >= 5) {
              goodFrames = 0
              captureRef.current()
            }
          } catch {
            previous = undefined
            goodFrames = 0
            latestQuality.current = null
            setQuality(null)
            setSteady(0)
          }
        }, 250)
      } catch (caught) {
        if (!closed) {
          stopCamera()
          setCameraOn(false)
          setCameraReady(false)
          setError(cameraError(caught))
        }
      }
    }
    void open()
    document.addEventListener('visibilitychange', pause)
    window.addEventListener('pagehide', stopCamera)
    return () => {
      closed = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', pause)
      window.removeEventListener('pagehide', stopCamera)
      stopCamera()
    }
  }, [cameraOn, stopCamera])

  const chooseUpload = (replace: Side | null = null) => {
    closeCamera()
    uploadSide.current = replace
    if (input.current) {
      input.current.multiple = replace === null
      input.current.click()
    }
  }
  const upload = async (selected: File[]) => {
    if (!selected.length) return
    setBusy(true)
    setError('')
    try {
      await validateCardFiles(selected)
      for (const file of selected) {
        if (file.name.toLowerCase().endsWith('.pdf')) continue
        const url = URL.createObjectURL(file)
        try {
          await readImage(url)
        } finally {
          URL.revokeObjectURL(url)
        }
      }
      if (!mounted.current) return
      const next =
        uploadSide.current === null ? selected : entries.current.map((entry) => entry?.file)
      if (uploadSide.current !== null) next[uploadSide.current] = selected[0]
      saveFiles(next)
      setNotice(
        'Card files added. Review them before continuing. PDF readability and page limits are checked when uploaded.',
      )
      heading.current?.focus()
    } catch (caught) {
      if (mounted.current)
        setError(caught instanceof Error ? caught.message : 'The card files could not be read.')
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const rotate = async (index: Side) => {
    const entry = entries.current[index]
    if (!entry) return
    setBusy(true)
    setError('')
    try {
      const image = await readImage(entry.url)
      const canvas = document.createElement('canvas')
      canvas.width = image.naturalHeight
      canvas.height = image.naturalWidth
      const context = canvas.getContext('2d')
      if (!context)
        throw new Error(
          'Image rotation is unavailable. Replace the photo with a correctly oriented image.',
        )
      context.translate(canvas.width, 0)
      context.rotate(Math.PI / 2)
      context.drawImage(image, 0, 0)
      const blob = await asJpeg(canvas)
      if (!mounted.current) return
      const next = entries.current.map((item) => item?.file)
      next[index] = new File([blob], `${sideName(index)}.jpg`, { type: 'image/jpeg' })
      await validateCardFiles(next.filter((file): file is File => Boolean(file)))
      if (mounted.current) {
        saveFiles(next)
        setNotice('Photo rotated. Check that the card text is upright.')
      }
    } catch (caught) {
      if (mounted.current)
        setError(caught instanceof Error ? caught.message : 'The image could not be rotated.')
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const guidance = !cameraReady
    ? 'Requesting camera access…'
    : !quality
      ? 'Place the card inside the outline. Manual capture is available.'
      : !quality.wellLit
        ? 'Move to even lighting; avoid shadows and reflections.'
        : !quality.aligned
          ? 'Fit all four edges to the outline, against a contrasting background.'
          : !quality.sharp
            ? 'Let the camera focus until the printed text is sharp.'
            : !quality.still
              ? 'Hold the card and camera steady.'
              : automatic
                ? 'Hold steady — capturing automatically.'
                : 'The image looks ready. Choose Capture photo.'
  const readyFiles = files.filter((entry): entry is CardFile => Boolean(entry))
  return {
    headingId,
    heading,
    video,
    input,
    files,
    side,
    cameraOn,
    cameraReady,
    automatic,
    setAutomatic,
    quality,
    steady,
    busy,
    error,
    notice,
    setNotice,
    stopCamera,
    closeCamera,
    startCamera,
    capture,
    chooseUpload,
    upload,
    rotate,
    saveFiles,
    guidance,
    readyFiles,
  }
}

export type CardCaptureModel = ReturnType<typeof useCardCapture>
