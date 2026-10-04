/* Guide search stays local: no query requests, analytics or browser storage. */
(() => {
  const input = document.getElementById('guide-search');
  const results = document.getElementById('search-results');
  const status = document.getElementById('search-status');
  const clear = document.getElementById('search-clear');
  const stop = new Set('a an the to for of in on and or is are do does how what where can i my it this with from we our you your when why would like please'.split(' '));
  const related = [
    'database databases db postgres postgresql rds sql tables table',
    'file files document documents pdf storage stored save saved storing s3 bucket buckets',
    'login signin sign authentication auth session sessions log',
    'deploy deployment deploying release releases ship publish promotion promote',
    'setup install installation start running run locally local localhost computer',
    'error errors failed failure broken debug debugging troubleshoot troubleshooting',
    'timeout timeouts slow stalled deadline',
    'estimate estimates price pricing cost costs calculation amount',
    'eligibility benefits coverage insurance payer insurer',
    'ecw eclinicalworks healow',
    'frontend browser screen react ui website',
    'backend server fastapi api',
    'credentials secrets secret password token tokens',
    'logs logging cloudwatch diagnostics',
    'beginner beginners intern interns introduction orientation onboarding basics',
    'code source implementation edit function functions',
  ];
  const synonyms = new Map(related.flatMap((group, i) => group.split(' ').map(word => [word, `group${i}`])));
  const normalize = value => value.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
  const words = value => [...new Set(normalize(value).split(' ').filter(word => word && !stop.has(word)))];
  const text = element => element.textContent.replace(/\s+/g, ' ').trim();
  const records = [];
  for (const section of document.querySelectorAll('.article > section')) {
    const title = text(section.querySelector('h2'));
    const overview = section.cloneNode(true);
    overview.querySelectorAll('details').forEach(detail => detail.remove());
    records.push({id: section.id, title, section: title, body: text(overview), aliases: section.dataset.keywords || ''});
    for (const detail of section.querySelectorAll('details[id]')) {
      records.push({id: detail.id, title: text(detail.querySelector('summary')), section: title, body: text(detail), aliases: ''});
    }
  }
  records.push({id: 'reference', title: 'API reference · Swagger', section: 'API tools', body: 'Browse endpoints, request parameters and response models. Use the API filter for an exact operation.', aliases: 'swagger openapi endpoints http reference'});
  for (const record of records) {
    record.titleWords = words(record.title);
    record.aliasWords = words(record.aliases);
    record.bodyWords = words(record.body);
  }
  // ponytail: curated synonyms and single-edit typos cover this guide; revisit if the corpus becomes multilingual or much larger.
  function near(a, b) {
    if (a.length < 4 || b.length < 4 || Math.abs(a.length - b.length) > 1) return false;
    let i = 0, j = 0, edits = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { i++; j++; continue; }
      if (++edits > 1) return false;
      if (a[i] === b[j + 1] && a[i + 1] === b[j] && a.length === b.length) { i += 2; j += 2; }
      else if (a.length > b.length) i++;
      else if (b.length > a.length) j++;
      else { i++; j++; }
    }
    return edits + (a.length - i) + (b.length - j) <= 1;
  }
  function match(query, vocabulary) {
    if (vocabulary.includes(query)) return 1;
    if (vocabulary.some(word => synonyms.has(query) && synonyms.get(query) === synonyms.get(word))) return .7;
    if (vocabulary.some(word => query.length >= 4 && word.startsWith(query))) return .6;
    return vocabulary.some(word => near(query, word)) ? .5 : 0;
  }
  function search() {
    const query = input.value.trim().slice(0, 180);
    results.replaceChildren();
    clear.hidden = !query;
    if (query.length < 2) {
      results.hidden = true;
      status.textContent = query ? 'Type at least two characters to search.' : '';
      return;
    }
    const terms = words(query);
    const ranked = records.map(record => {
      let score = 0, found = 0;
      for (const term of terms) {
        const weight = Math.max(match(term, record.titleWords) * 8, match(term, record.aliasWords) * 5, match(term, record.bodyWords));
        if (weight) found++;
        score += weight;
      }
      if (!terms.length || found < Math.ceil(terms.length * .6)) return {...record, score: 0};
      score += found / terms.length * 5;
      if (normalize(record.title).includes(normalize(query))) score += 12;
      return {...record, score};
    }).filter(record => record.score > 0).sort((a, b) => b.score - a.score).slice(0, 6);
    results.hidden = !ranked.length;
    status.textContent = ranked.length ? `${ranked.length} relevant sections. Choose one to jump there.` : 'No matching section. Try fewer words, a service name, or browse the guide below.';
    for (const record of ranked) {
      const item = document.createElement('li');
      const link = document.createElement('a');
      link.href = `#${record.id}`;
      const section = document.createElement('span');
      section.className = 'result-section';
      section.textContent = record.section;
      const title = document.createElement('strong');
      title.textContent = record.title;
      const excerpt = document.createElement('span');
      excerpt.textContent = record.body.replace(record.title, '').trim().slice(0, 155) + (record.body.length > 155 ? '…' : '');
      link.append(section, title, excerpt);
      item.append(link);
      results.append(item);
    }
  }
  function revealHash(focus = false) {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    const target = document.getElementById(id);
    if (!target) return; // Swagger owns its own operation fragments.
    for (let node = target; node; node = node.parentElement) if (node.tagName === 'DETAILS') node.open = true;
    if (focus) {
      const destination = target.tagName === 'DETAILS' ? target.querySelector('summary') : target;
      if (!destination.matches('input,button,a,summary,[tabindex]')) destination.setAttribute('tabindex', '-1');
      destination.focus({preventScroll: true});
    }
    target.scrollIntoView({block: 'start'});
    document.querySelectorAll('.toc a').forEach(link => {
      if (link.hash === location.hash) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }
  let timer;
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 100); });
  clear.addEventListener('click', () => { clearTimeout(timer); input.value = ''; search(); input.focus(); });
  document.getElementById('guide-search-form').addEventListener('submit', event => {
    event.preventDefault(); clearTimeout(timer); search(); results.querySelector('a')?.click();
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown') { event.preventDefault(); clearTimeout(timer); search(); results.querySelector('a')?.focus(); }
    if (event.key === 'Escape') { clearTimeout(timer); results.hidden = true; status.textContent = ''; }
  });
  results.addEventListener('keydown', event => {
    const links = [...results.querySelectorAll('a')];
    const index = links.indexOf(document.activeElement);
    if (event.key === 'ArrowDown') { event.preventDefault(); links[(index + 1) % links.length]?.focus(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); if (index <= 0) input.focus(); else links[index - 1].focus(); }
    if (event.key === 'Escape') { results.hidden = true; status.textContent = ''; input.focus(); }
  });
  document.addEventListener('keydown', event => {
    if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.closest('input,textarea,select,[contenteditable="true"]')) {
      event.preventDefault(); input.focus(); input.scrollIntoView({block: 'center'});
    }
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const id = link.getAttribute('href').slice(1);
    if (!document.getElementById(id)) return;
    event.preventDefault();
    if (location.hash !== `#${id}`) history.pushState(null, '', `#${id}`);
    revealHash(true);
  });
  window.addEventListener('hashchange', () => revealHash());
  window.addEventListener('popstate', () => revealHash());
  // Native fragment navigation can occur before fonts and the API viewer settle.
  revealHash();
})();
