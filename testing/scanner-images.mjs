// Start the frontend dev server, then run this from the repository root.
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node testing/scanner-images.mjs
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixtures = JSON.parse(execFileSync(process.env.TEST_PYTHON || 'backend/venv/bin/python', ['-c', `
import sys, json, base64, random
sys.path.insert(0, 'backend')
from app.core.shl import create_shl_uri, generate_qr_code
rng = random.Random(42)
def token():
    return base64.urlsafe_b64encode(rng.randbytes(32)).decode().rstrip('=')
fixtures = []
for i in range(8):
    text = create_shl_uri('https://app.clinicforce.ai/manifest/' + token(), token(), 'ClinicForce records', 4102444800, 'https://app.clinicforce.ai/')
    fixtures.append({'text': text, 'image': generate_qr_code(text)})
print(json.dumps(fixtures))
`], { encoding: 'utf8' }));

const browser = await chromium.launch();
let recovered = 0;
try {
  const page = await browser.newPage();
  await page.goto(process.env.SCANNER_TEST_ORIGIN || 'http://127.0.0.1:5177');
  const display = await browser.newPage({ viewport: { width: 390, height: 900 }, deviceScaleFactor: 1 });
  for (const [index, fixture] of fixtures.entries()) {
    await display.setContent(`<body style="margin:0;background:#809098;display:grid;place-items:center;height:900px"><div style="box-sizing:border-box;padding:20px;width:92vw;background:white;border-radius:18px"><h2>Synthetic QR regression test</h2><img style="box-sizing:border-box;width:100%;padding:12px;border:1px solid #eee;image-rendering:pixelated" src="data:image/png;base64,${fixture.image}"><p>Not a real patient or active share.</p></div></body>`);
    await display.locator('img').evaluate(img => img.decode());
    const images = [Buffer.from(fixture.image, 'base64'), await display.locator('img').screenshot(), await display.screenshot()];
    for (const [variant, image] of images.entries()) {
      const result = await page.evaluate(async ({ base64, expected }) => {
        const { Html5Qrcode, Html5QrcodeSupportedFormats } = await import('/node_modules/.vite/deps/html5-qrcode.js');
        const { scanQrImage } = await import('/src/services/qr-image.ts');
        const { parseSmartHealthLink } = await import('/src/services/shl.ts');
        const element = document.createElement('div'); element.id = 'image-test'; document.body.append(element);
        const reader = new Html5Qrcode(element.id, { formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE], verbose: false });
        const file = new File([Uint8Array.from(atob(base64), c => c.charCodeAt(0))], 'synthetic.png', { type: 'image/png' });
        let originalWorked = false;
        try { originalWorked = await reader.scanFile(file, false) === expected; } catch { /* Regression evidence. */ }
        try {
          const actual = await scanQrImage(reader, file);
          return { originalWorked, matches: actual === expected, protected: parseSmartHealthLink(actual).requiresPasscode };
        } catch (error) { return { originalWorked, matches: false, error: String(error) }; }
        finally { reader.clear(); element.remove(); }
      }, { base64: image.toString('base64'), expected: fixture.text });
      if (!result.matches) await fs.writeFile(new URL(`./artifacts/failed-${index}-${variant}.png`, import.meta.url), image);
      assert.ok(result.matches && result.protected, `Fixture ${index}, variant ${variant}`);
      if (!result.originalWorked) recovered++;
    }
  }
  assert.ok(recovered > 0, 'Fixtures must reproduce a failure of the original decoder');
  console.log(JSON.stringify({ scansPassed: fixtures.length * 3, previouslyFailingImagesRecovered: recovered }));
} finally { await browser.close(); }
