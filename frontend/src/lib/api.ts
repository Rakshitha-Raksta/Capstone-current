// Typed client for the ClearClaim API (/api/v1). Every screen goes through here.
// No mock data: if the backend is unreachable the UI shows an error state.

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

const TOKEN_KEY = 'clearclaim.writeToken';
export const tokenStore = {
  get: (): string => {
    try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
  },
  set: (v: string) => {
    try { if (v) localStorage.setItem(TOKEN_KEY, v); else localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
  },
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (init.body) headers['Content-Type'] = 'application/json';
  const tok = tokenStore.get();
  if (tok && init.method && init.method !== 'GET') headers.Authorization = `Bearer ${tok}`;
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as Record<string, string> || {}) } });
  } catch (e) {
    throw new ApiError(`Cannot reach the ClearClaim API (${(e as Error).message}). Is the backend running on port 5001?`, 0);
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try { body = JSON.parse(text); } catch { body = text; }
  }
  if (!res.ok) {
    const detail = (body && typeof body === 'object' && 'detail' in (body as object))
      ? (body as { detail: unknown }).detail : `HTTP ${res.status}`;
    let msg = typeof detail === 'string' ? detail : humanizeValidation(detail);
    if (res.status === 401) msg = 'This action needs the ClearClaim write token. Add it under Settings → Access, then try again.';
    if (res.status === 413) msg = 'The request was too large for the server limit.';
    if (res.status >= 500 && res.status !== 503) msg = `The server hit an internal error (HTTP ${res.status}). Nothing was changed; try again or check the backend log.`;
    throw new ApiError(msg, res.status);
  }
  return body as T;
}

/** FastAPI validation errors arrive as a list of {loc, msg}; turn them into one readable line. */
function humanizeValidation(detail: unknown): string {
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      const loc = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== 'body').join('.') : '';
      return `${loc ? `${loc}: ` : ''}${d?.msg ?? 'invalid value'}`;
    }).join('; ');
  }
  return JSON.stringify(detail);
}

const get = <T,>(p: string) => request<T>(p, { method: 'GET' });
const send = <T,>(method: string, p: string, body?: unknown) =>
  request<T>(p, { method, body: body === undefined ? undefined : JSON.stringify(body) });

function qs(params: Record<string, unknown>) {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') u.set(k, String(v));
  });
  const s = u.toString();
  return s ? `?${s}` : '';
}

/* eslint-disable @typescript-eslint/no-explicit-any */
export const api = {
  health: () => get<any>('/api/v1/health'),
  meta: () => get<any>('/api/v1/meta'),
  overview: () => get<any>('/api/v1/overview'),
  cases: (p: Record<string, unknown>) => get<any>(`/api/v1/cases${qs(p)}`),
  caseDetail: (id: number | string) => get<any>(`/api/v1/cases/${id}`),
  caseRuns: (id: number | string) => get<any[]>(`/api/v1/cases/${id}/runs`),
  caseEvents: (id: number | string) => get<any[]>(`/api/v1/cases/${id}/events`),
  casePacket: (id: number | string) => get<any>(`/api/v1/cases/${id}/packet`),
  stageHistory: (id: number | string, stage: string) => get<any[]>(`/api/v1/cases/${id}/stage-history/${stage}`),
  processCase: (id: number | string, body: object = {}) => send<any>('POST', `/api/v1/cases/${id}/process`, body),
  rerunCase: (id: number | string, body: object) => send<any>('POST', `/api/v1/cases/${id}/rerun`, body),
  processPending: (body: object = {}) => send<any>('POST', '/api/v1/cases/process-pending', body),
  correct: (id: number | string, body: object) => send<any>('POST', `/api/v1/cases/${id}/corrections`, body),
  explain: (id: number | string, question: string) => send<any>('POST', `/api/v1/cases/${id}/explain`, { question }),
  tasks: (p: Record<string, unknown>) => get<any>(`/api/v1/review-tasks${qs(p)}`),
  decide: (taskId: number, body: object) => send<any>('POST', `/api/v1/review-tasks/${taskId}/decision`, body),
  graph: () => get<any>('/api/v1/workflow/graph'),
  audit: (p: Record<string, unknown>) => get<any>(`/api/v1/audit-events${qs(p)}`),
  auditVerify: () => get<any>('/api/v1/audit-events/verify'),
  dataStatus: () => get<any>('/api/v1/data/status'),
  settings: () => get<any>('/api/v1/settings'),
  saveSettings: (values: object) => send<any>('PUT', '/api/v1/settings', { values }),
  experiments: () => get<any[]>('/api/v1/experiments'),
  experiment: (id: number | string) => get<any>(`/api/v1/experiments/${id}`),
  startExperiment: (body: object) => send<any>('POST', '/api/v1/experiments', body),
  importSummary: (path?: string) => send<any>('POST', `/api/v1/experiments/import-summary${qs({ path })}`),
  // Agent Execution Studio: real runs observed through the orchestrator's event hook
  studioStages: () => get<any>('/api/v1/studio/stages'),
  studioEncounter: (hadm: string) => get<any>(`/api/v1/studio/encounters/${encodeURIComponent(hadm)}`),
  studioStart: (body: object) => send<any>('POST', '/api/v1/studio/runs', body),
  studioRun: (token: string, after: number) => get<any>(`/api/v1/studio/runs/${token}${qs({ after })}`),
  studioPipelineRun: (runId: number) => get<any>(`/api/v1/studio/pipeline-runs/${runId}`),
  // Agent Control Room: real sequential / parallel runs, isolated comparisons, run history
  crStages: () => get<any>('/api/v1/control-room/stages'),
  crRun: (body: object) => send<any>('POST', '/api/v1/control-room/runs', body),
  crCompare: (body: object) => send<any>('POST', '/api/v1/control-room/comparisons', body),
  crCorrection: (body: object) => send<any>('POST', '/api/v1/control-room/correction-comparisons', body),
  crCancel: (token: string) => send<any>('POST', `/api/v1/control-room/jobs/${token}/cancel`),
  crJob: (token: string, after: number) => get<any>(`/api/v1/control-room/jobs/${token}${qs({ after })}`),
  crHistory: () => get<any[]>('/api/v1/control-room/history'),
  crRecord: (id: number) => get<any>(`/api/v1/control-room/history/${id}`),
  crExportUrl: (id: number, format: 'csv' | 'json') => `/api/v1/control-room/history/${id}/export?format=${format}`,
  // ClearClaim One workspace: read-only correction preview from the orchestrator's dependency model
  workspacePreview: (op: string) => get<any>(`/api/v1/workspace/correction-preview${qs({ op })}`),
  // policy, historical and research intelligence (read-only)
  intelRules: (caseId?: number) => get<any>(`/api/v1/intel/rules${qs({ case_id: caseId })}`),
  intelHistorical: (caseId: number, sample = 40) => get<any>(`/api/v1/intel/cases/${caseId}/historical${qs({ sample })}`),
  intelResearch: () => get<any>('/api/v1/intel/research'),
  // existing (legacy) policy endpoints, reused by the Policy Library
  policies: () => get<any[]>('/policies'),
};
