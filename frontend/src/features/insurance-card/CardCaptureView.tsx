import { ArrowLeft, Camera, Check, FileText, RotateCw, ShieldCheck, Upload, X } from 'lucide-react'
import type { CardCaptureModel } from './useCardCapture'

const sideName = (side: number) => (side === 0 ? 'front' : 'back')

interface Props {
  model: CardCaptureModel
  onCaptured: (files: File[]) => void
  onCancel: () => void
}

export default function CardCaptureView({ model, onCaptured, onCancel }: Props) {
  const {
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
  } = model
  return (
    <section className="card-capture" aria-labelledby={headingId} aria-busy={busy}>
      <button
        className="card-capture-back"
        type="button"
        onClick={() => {
          stopCamera()
          onCancel()
        }}
      >
        <ArrowLeft aria-hidden="true" /> Back
      </button>
      <header>
        <p className="card-capture-kicker">Add insurance card</p>
        <h2 id={headingId} ref={heading} tabIndex={-1}>
          {cameraOn
            ? `Capture the ${sideName(side)} of the card`
            : readyFiles.length
              ? 'Review the card before continuing'
              : 'Scan a card or upload a file'}
        </h2>
        <p>
          Keep the member name, member ID and group number readable. A patient account is not
          required.
        </p>
      </header>
      <ol className="card-capture-steps" aria-label="Card capture progress">
        <li className={files[0] ? 'is-done' : 'is-current'}>
          {files[0] ? <Check aria-hidden="true" /> : <span>1</span>} Front or card PDF
        </li>
        <li className={files[1] ? 'is-done' : side === 1 && cameraOn ? 'is-current' : ''}>
          {files[1] ? <Check aria-hidden="true" /> : <span>2</span>} Back <small>optional</small>
        </li>
        <li>
          <span>3</span> Confirm details
        </li>
      </ol>
      {error && (
        <p className="card-capture-error" role="alert">
          {error}
        </p>
      )}
      {notice && !cameraOn && (
        <p className="card-capture-notice" role="status">
          {notice}
        </p>
      )}
      {cameraOn ? (
        <div className="card-capture-camera">
          <div className={`card-capture-viewfinder${quality?.ready ? ' is-ready' : ''}`}>
            <video
              ref={video}
              muted
              playsInline
              autoPlay
              aria-label={`Live camera for the ${sideName(side)} of the insurance card`}
            />
            <div className="card-capture-outline" aria-hidden="true">
              <span>{sideName(side)} of card</span>
            </div>
            <span className="card-capture-camera-label">
              {automatic ? 'Auto capture on' : 'Manual capture'}
            </span>
          </div>
          <p className="card-capture-guidance" role="status" aria-live="polite">
            {guidance}
          </p>
          <div className="card-capture-quality" aria-label="Image checks">
            <span className={quality?.aligned ? 'passed' : ''}>
              {quality?.aligned && <Check aria-hidden="true" />} Edges aligned
            </span>
            <span className={quality?.sharp ? 'passed' : ''}>
              {quality?.sharp && <Check aria-hidden="true" />} Text focus
            </span>
            <span className={quality?.still ? 'passed' : ''}>
              {quality?.still && <Check aria-hidden="true" />} Hold steady
            </span>
          </div>
          <div className="card-capture-progress" aria-hidden="true">
            <span style={{ transform: `scaleX(${automatic ? steady / 5 : 0})` }} />
          </div>
          <div className="card-capture-controls">
            <label className="card-capture-auto">
              <input
                type="checkbox"
                checked={automatic}
                onChange={(event) => setAutomatic(event.target.checked)}
              />{' '}
              Capture automatically when ready
            </label>
            <button
              className="card-capture-primary"
              type="button"
              disabled={!cameraReady || busy}
              onClick={() => void capture()}
            >
              <Camera aria-hidden="true" /> {busy ? 'Saving photo…' : 'Capture photo'}
            </button>
            <button type="button" disabled={busy} onClick={closeCamera}>
              Close camera
            </button>
          </div>
          <p className="card-capture-help">
            Automatic capture checks the outline and image quality. Always review the crop; it does
            not verify card details.
          </p>
        </div>
      ) : (
        <>
          {!readyFiles.length && (
            <div className="card-capture-start">
              <Camera aria-hidden="true" />
              <h3>Place the card on a flat surface</h3>
              <p>
                Use even light and a contrasting background. We will help line up the edges, then
                show you the photo.
              </p>
              <button
                className="card-capture-primary"
                type="button"
                disabled={busy}
                onClick={() => startCamera(0)}
              >
                <Camera aria-hidden="true" /> Use camera
              </button>
            </div>
          )}
          {!!readyFiles.length && (
            <div className="card-capture-previews">
              {([0, 1] as const).map((index) => {
                const entry = files[index]
                const pdf = entry?.file.name.toLowerCase().endsWith('.pdf')
                return (
                  <article className="card-capture-preview" key={index}>
                    <h3>{index === 0 ? 'Front / card PDF' : 'Back · optional'}</h3>
                    {entry ? (
                      <>
                        {pdf ? (
                          <div className="card-capture-pdf">
                            <FileText aria-hidden="true" />
                            <strong>{entry.file.name}</strong>
                            <a href={entry.url} target="_blank" rel="noopener noreferrer">
                              Open PDF preview <span className="sr-only">in a new tab</span>
                            </a>
                          </div>
                        ) : (
                          <img
                            src={entry.url}
                            alt={`Captured or uploaded ${sideName(index)} of the insurance card; check all edges and printed text`}
                          />
                        )}
                        <p className="card-capture-filename">
                          {entry.file.name} · {(entry.file.size / 1024 / 1024).toFixed(1)} MB
                        </p>
                        <div className="card-capture-preview-actions">
                          <button type="button" disabled={busy} onClick={() => startCamera(index)}>
                            <Camera aria-hidden="true" /> Retake
                          </button>
                          <button type="button" disabled={busy} onClick={() => chooseUpload(index)}>
                            <Upload aria-hidden="true" /> Replace
                          </button>
                          {!pdf && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void rotate(index)}
                            >
                              <RotateCw aria-hidden="true" /> Rotate
                            </button>
                          )}
                          {index === 1 && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => {
                                saveFiles([files[0]?.file])
                                setNotice('Back removed. You can continue with the front.')
                              }}
                            >
                              <X aria-hidden="true" /> Remove
                            </button>
                          )}
                        </div>
                      </>
                    ) : (
                      <div className="card-capture-empty">
                        <p>
                          {index === 1
                            ? 'Add the back if it has plan, routing or contact details.'
                            : 'Add the front of the card before continuing.'}
                        </p>
                        <button type="button" disabled={busy} onClick={() => startCamera(index)}>
                          <Camera aria-hidden="true" />{' '}
                          {index === 1 ? 'Capture back' : 'Capture front'}
                        </button>
                        <button type="button" disabled={busy} onClick={() => chooseUpload(index)}>
                          <Upload aria-hidden="true" /> Upload {sideName(index)}
                        </button>
                      </div>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </>
      )}
      <div className="card-capture-upload">
        <button type="button" disabled={busy} onClick={() => chooseUpload()}>
          <Upload aria-hidden="true" />{' '}
          {readyFiles.length ? 'Choose replacement files' : 'Upload PDF or photos'}
        </button>
        <p>
          Choose up to two PDF, JPG or PNG files, with two card pages in total. Each file: 10 MB
          maximum.
        </p>
        <input
          ref={input}
          hidden
          type="file"
          accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
          multiple
          onChange={(event) => {
            const selected = Array.from(event.currentTarget.files ?? [])
            event.currentTarget.value = ''
            void upload(selected)
          }}
        />
      </div>
      {!!readyFiles.length && !cameraOn && (
        <div className="card-capture-finish">
          <p>Can you read the name and member ID, with all card edges visible?</p>
          <button
            className="card-capture-primary"
            type="button"
            disabled={busy || !files[0]}
            onClick={() => {
              stopCamera()
              onCaptured(readyFiles.map((entry) => entry.file))
            }}
          >
            {busy ? 'Preparing files…' : files[1] ? 'Use these card files' : 'Use this card file'}{' '}
            <Check aria-hidden="true" />
          </button>
        </div>
      )}
      <p className="card-capture-privacy">
        <ShieldCheck aria-hidden="true" /> Camera and previews stay on this device until you confirm
        the files for upload.
      </p>
    </section>
  )
}
