// Read-only browser checks against a running app. No external provider calls.
// PLAYWRIGHT_MODULE may point to an existing Playwright install; screenshots are opt-in.
import assert from 'node:assert/strict';
import path from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async()=>{
 const base=process.argv[2]||'http://127.0.0.1:18765',label=process.argv[3]||'local';
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_PATH});
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const requests=[];page.on('request',r=>requests.push(r.url()));
 await page.goto(base+'/api/docs',{waitUntil:'networkidle'});
 await page.locator('#swagger-ui .opblock-tag').first().waitFor({timeout:30000});
 assert.equal(await page.locator('redoc,script[src*="redoc"]').count(),0);
 if(process.env.GUIDE_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.GUIDE_SCREENSHOT_DIR,`visual-guide-${label}-desktop.png`)});
 const input=page.locator('#guide-search');
 const cases=[
  ['where are files stored', /data|data-inspection|architecture-decisions-detail-6/],
  ['databse',/data|data-inspection|local-setup/],
  ['eCW timeout',/pipelines|debugging|architecture-decisions/],
  ['production deployment',/release-runbook|infrastructure/],
  ['sign in',/swagger-access|local-setup/],
  ['coinsurance',/worked-example/],
  ['pgAdmin',/data-inspection/],
  ['frontend libraries',/code-atlas/],
 ];
 const outcome=[];
 const before=requests.length;
 for(const [query,expected] of cases){
  await input.fill(query);
  await page.waitForTimeout(250);
  const links=await page.locator('#search-results a').evaluateAll(els=>els.map(e=>({href:e.getAttribute('href'),title:e.querySelector('strong').textContent})));
  console.log(query,links.slice(0,3));
  assert(links.length>0,query+' returned nothing');
  assert(expected.test(links[0].href),query+' top result: '+links[0].href);
  outcome.push({query,top_result:links[0]});
 }
 assert.equal(requests.length,before,'Search must not make network requests');
 await input.fill('qzxwvbnm');await page.waitForTimeout(250);
 assert.match(await page.locator('#search-status').textContent(),/No matching/);
 await input.fill('<img src=x onerror=alert(1)>');await page.waitForTimeout(250);
 assert.equal(await page.locator('#search-results img').count(),0);
 await input.fill('pgAdmin');await page.waitForTimeout(250);
 const target=await page.locator('#search-results a').first().getAttribute('href');
 await input.press('ArrowDown');assert.equal(await page.evaluate(()=>document.activeElement.tagName),'A');
 await page.keyboard.press('Enter');await page.waitForURL('**'+target);
 assert(await page.locator(target).evaluate(el=>el.open===true),'Search result must open collapsed instructions');
 assert.equal(await page.evaluate(()=>document.activeElement.tagName),'SUMMARY');
 assert.equal(await page.evaluate(()=>document.activeElement.tabIndex),0,'Expanded summary stays keyboard accessible');
 await page.reload({waitUntil:'networkidle'});
 assert(await page.locator(target).evaluate(el=>el.open===true),'Direct URL must reopen instructions');
 await page.goBack({waitUntil:'networkidle'});
 await page.keyboard.press('/');assert(await input.evaluate(el=>document.activeElement===el));
 assert.equal(await input.evaluate(el=>el.tabIndex),0);
 await input.fill('files');await page.waitForTimeout(250);await input.press('Escape');
 assert(await page.locator('#search-results').isHidden());
 await page.locator('#search-clear').click();assert.equal(await input.inputValue(),'');
 // A legacy bookmark preserves the section fragment through the HTTP redirect.
 const legacy=await page.request.get(base+'/api/redoc',{maxRedirects:0});
 assert.equal(legacy.status(),308);assert.equal(legacy.headers().location,'/api/docs');
 await page.goto(base+'/api/redoc#data',{waitUntil:'networkidle'});
 assert.equal(new URL(page.url()).pathname,'/api/docs');assert.equal(new URL(page.url()).hash,'#data');
 // Signed-out readers can browse operations, but cannot execute them.
 const tag=page.locator('#swagger-ui .opblock-tag').first();await tag.waitFor();
 if(await tag.getAttribute('data-is-open')!=='true')await tag.click();
 await page.locator('.opblock-summary-control').first().click();
 assert.equal(await page.getByRole('button',{name:'Try it out',exact:true}).count(),0);
 await page.goto(base+'/api/docs',{waitUntil:'networkidle'});
 if(process.env.GUIDE_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.GUIDE_SCREENSHOT_DIR,`visual-guide-${label}-desktop.png`)});
 for(const width of [375,768,1024,1440]){
  await page.setViewportSize({width,height:900});
  await page.locator('#getting-started').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`Overflow at ${width}`);
 }
 await page.setViewportSize({width:375,height:850});await page.evaluate(()=>scrollTo(0,0));
 if(process.env.GUIDE_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.GUIDE_SCREENSHOT_DIR,`visual-guide-${label}-mobile.png`)});
 await page.locator('#architecture').scrollIntoViewIfNeeded();
 if(process.env.GUIDE_SCREENSHOT_DIR) await page.locator('#architecture').screenshot({path:path.join(process.env.GUIDE_SCREENSHOT_DIR,`visual-guide-${label}-diagram.png`)});
 await page.locator('.mobile-toc summary').click();await page.locator('.mobile-toc a[href="#data-inspection"]').click();
 assert.equal(new URL(page.url()).hash,'#data-inspection');
 const noJS=await browser.newPage({javaScriptEnabled:false});await noJS.goto(base+'/api/docs');
 await noJS.locator('#data-inspection-detail-1 summary').click();
 assert(await noJS.locator('#data-inspection-detail-1').evaluate(el=>el.open));
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({site:base,cases:outcome,keyboard:'passed',deep_links:'passed',legacy_redirect:'passed',mobile:'passed',no_js_reading:'passed',anonymous_execution:'locked',javascript_errors:errors},null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
