/* Swagger shares the main application's HttpOnly cookies; it never reads them. */
(async () => {
  const status = document.getElementById('swagger-session-status');
  let sessions = {patient: false, clinician: false};
  async function refreshSession() {
    try {
      const response = await fetch('/api/docs/session', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('Session check unavailable');
      const value = await response.json();
      sessions = {patient: value.patient === true, clinician: value.clinician === true};
      const names = Object.keys(sessions).filter(key => sessions[key]);
      status.textContent = names.length ? `Signed in as ${names.join(' and ')}. Refresh after switching accounts.` : 'Sign in in the main app, then refresh this page to enable the matching APIs.';
    } catch {
      sessions = {patient: false, clinician: false};
      status.textContent = 'Session could not be verified. Execution is locked; refresh to retry.';
    }
    return sessions;
  }
  function allowed(audience) {
    return audience === 'either' ? sessions.patient || sessions.clinician : sessions[audience] === true;
  }
  const guideHash = document.getElementById(location.hash.slice(1)) ? location.hash : null;
  await refreshSession();
  SwaggerUIBundle({
    url: '/api/openapi.json', dom_id: '#swagger-ui', deepLinking: true,
    onComplete: () => {
      // Swagger normalizes fragments to #/tag/operation; preserve guide bookmarks.
      if (guideHash && (location.hash === guideHash || location.hash === `#/${guideHash.slice(1)}`)) {
        history.replaceState(null, '', guideHash);
        window.dispatchEvent(new HashChangeEvent('hashchange'));
      }
    },
    filter: true, docExpansion: 'none', validatorUrl: null,
    supportedSubmitMethods: ['get', 'post', 'put', 'patch', 'delete', 'head', 'options'],
    presets: [SwaggerUIBundle.presets.apis], layout: 'BaseLayout',
    plugins: [() => ({statePlugins: {spec: {wrapSelectors: {
      allowTryItOutFor: (original, system) => (state, path, method) =>
        original(state, path, method) && allowed(system.specSelectors.specJson().getIn(['paths', path, method, 'x-clinicforce-session']))
    }}}})],
    requestInterceptor: async request => {
      const url = new URL(request.url, location.href);
      if (url.origin !== location.origin) throw new Error('Swagger can call only this ClinicForce environment');
      request.credentials = 'same-origin';
      if (request.loadSpec && url.pathname === '/api/openapi.json') return request;
      await refreshSession();
      const audience = url.pathname.startsWith('/api/staff/') || url.pathname.startsWith('/api/doctor/') ? 'clinician'
        : url.pathname.startsWith('/api/patient/') || url.pathname === '/api/insurance-upload/account' ? 'patient' : 'either';
      if (!allowed(audience)) throw new Error('Sign in to the matching account in the main app and refresh Swagger');
      url.searchParams.set('__clinicforce_docs', '1');
      request.url = url.href;
      request.headers = {...request.headers};
      const intake = document.getElementById('swagger-intake').value.trim();
      if (audience === 'clinician' && url.pathname.startsWith('/api/doctor/insurance') && intake) {
        request.headers['X-ClinicForce-Intake'] = intake;
      }
      return request;
    }
  });
})();
