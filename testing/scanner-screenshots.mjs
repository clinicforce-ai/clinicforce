// Synthetic account only. Supply a private session file; never commit cookies.
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node testing/scanner-screenshots.mjs /path/to/session.json
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');

const session = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const origin = process.env.SCANNER_TEST_ORIGIN || 'https://app.clinicforce.ai';
const browser = await chromium.launch({ headless: true });
await fs.mkdir(new URL('./artifacts/', import.meta.url), { recursive: true });
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, deviceScaleFactor: 1 });
    await context.addCookies(session.cookies.map(c => ({ ...c, secure: true, httpOnly: true, sameSite: 'Lax' })));
    const page = await context.newPage();
    const failures = [];
    page.on('response', r => { if (r.status() >= 400) failures.push({ path: new URL(r.url()).pathname, status: r.status() }); });
    await page.goto(origin + '/?patient');
    await page.locator('#share-passcode').fill('4826');
    await page.getByRole('button', { name: 'Generate combined QR code' }).click();
    const qr = page.getByAltText('Smart Health Link QR code for the current health record');
    await qr.waitFor();
    await qr.evaluate(img => img.decode());
    const screenshot = await page.screenshot();
    const crop = await qr.screenshot();
    await fs.writeFile(new URL(`./artifacts/qr-screen-${width}.png`, import.meta.url), screenshot);
    await fs.writeFile(new URL(`./artifacts/qr-crop-${width}.png`, import.meta.url), crop);
    await page.getByRole('button', { name: 'Done', exact: true }).click();
    await page.goto(origin);
    await page.getByRole('button', { name: /^Clinician access/ }).click();
    for (const [kind, buffer] of [['full screenshot', screenshot], ['QR crop', crop]]) {
      await page.locator('input[type=file]').setInputFiles({ name: 'synthetic.png', mimeType: 'image/png', buffer });
      await page.waitForFunction(() => document.querySelector('#pin') || document.querySelector('[role=alert]'), { timeout: 15000 });
      const passed = await page.getByLabel('Four-digit passcode').isVisible();
      console.log(JSON.stringify({ width, kind, passed, failures, alerts: await page.getByRole('alert').allTextContents() }));
      assert.ok(passed, `The ${width}px ${kind} must reach the passcode screen`);
      if (passed) await page.getByRole('button', { name: 'Back to scan' }).click();
    }
    await context.close();
  }
} finally {
  await browser.close();
}
