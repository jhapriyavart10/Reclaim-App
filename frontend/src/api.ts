/* -----------------------------------------------------------------------
   RECLAIM API Client — REST + SSE
   ----------------------------------------------------------------------- */
import type {
  HealthResponse, AppCapability, MissionSnapshot, EvidenceItem,
  ActionReceipt, VerificationResult, ProposedAction, Scorecard,
  TelemetryEvent, MissionMode, DemoScenario,
} from './types';

const BASE = '/api';

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () => fetchJSON<HealthResponse>('/health'),

  capabilities: () => fetchJSON<Record<string, AppCapability>>('/capabilities'),

  startMission: (goal: string, mode: MissionMode, scenario: DemoScenario) =>
    fetchJSON<{ mission_id: string; mode: string; goal: string; status: string }>(
      '/missions', { method: 'POST', body: JSON.stringify({ goal, mode, scenario }) }
    ),

  getMission: (id: string) => fetchJSON<MissionSnapshot>(`/missions/${id}`),

  getEvidence: (id: string) =>
    fetchJSON<{ evidence: EvidenceItem[]; count: number }>(`/missions/${id}/evidence`),

  getActions: (id: string) =>
    fetchJSON<{ proposed_actions: ProposedAction[]; executed_receipts: ActionReceipt[]; verifications: VerificationResult[] }>(
      `/missions/${id}/actions`
    ),

  getScorecard: (id: string) => fetchJSON<Scorecard>(`/missions/${id}/scorecard`),

  submitApproval: (id: string, approvalToken: string, action: 'APPROVE' | 'REJECT') =>
    fetchJSON<{ status: string }>(`/missions/${id}/approval`, {
      method: 'POST',
      body: JSON.stringify({ approval_token: approvalToken, action }),
    }),
};

/* -----------------------------------------------------------------------
   SSE subscription with automatic de-duplication and reconnect
   ----------------------------------------------------------------------- */
export function subscribeToEvents(
  missionId: string,
  onEvent: (evt: TelemetryEvent) => void,
  onEnd: () => void,
): () => void {
  const seenIds = new Set<string>();
  let source: EventSource | null = null;
  let closed = false;

  function connect() {
    if (closed) return;
    source = new EventSource(`${BASE}/missions/${missionId}/events`);

    source.addEventListener('telemetry', (e: MessageEvent) => {
      try {
        const evt: TelemetryEvent = JSON.parse(e.data);
        if (!seenIds.has(evt.event_id)) {
          seenIds.add(evt.event_id);
          onEvent(evt);
        }
      } catch { /* ignore parse errors */ }
    });

    source.addEventListener('mission_end', () => {
      onEnd();
      source?.close();
    });

    source.onerror = () => {
      source?.close();
      if (!closed) {
        setTimeout(connect, 2000);
      }
    };
  }

  connect();

  return () => {
    closed = true;
    source?.close();
  };
}
