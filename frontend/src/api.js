// Thin client for the backend API (backend/src/app.js). Errors carry the HTTP status.
async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `${res.status} ${res.statusText}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export const api = {
  meta: () => request('GET', '/api/meta'),
  wards: () => request('GET', '/api/wards'),
  quote: (body) => request('POST', '/api/quote', body),
  submitLot: (body) => request('POST', '/api/lots', body),
  credits: () => request('GET', '/api/credits'),
  creditEvents: (id) => request('GET', `/api/credits/${id}/events`),
  latestAudit: () => request('GET', '/api/audit/latest').catch((err) => {
    if (err.status === 404) return null;
    throw err;
  }),
  runAudit: () => request('POST', '/api/audit/run', {}),
  escrowCycle: (now) => request('POST', '/api/escrow/cycle', { now }),
  inspectFlag: (id) => request('POST', `/api/flags/${id}/inspect`),
  confirmFlag: (id) => request('POST', `/api/flags/${id}/confirm`),
  clearFlag: (id) => request('POST', `/api/flags/${id}/clear`),
  inject: (body) => request('POST', '/api/demo/inject', body),
  reset: () => request('POST', '/api/demo/reset'),
};
