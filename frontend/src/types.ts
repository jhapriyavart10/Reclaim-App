/* -----------------------------------------------------------------------
   RECLAIM Types — mirrors backend Pydantic models
   ----------------------------------------------------------------------- */

export interface HealthResponse {
  status: string;
  llm: { provider: string; model: string; configured: boolean };
  adapters: Record<string, boolean>;
}

export interface AppCapability {
  app: string;
  source_type: 'LIVE' | 'SIMULATED' | 'UNAVAILABLE';
  availability: 'AVAILABLE' | 'UNAVAILABLE' | 'DEGRADED';
  authentication_status: string;
  read_operations?: string[];
  write_operations?: string[];
  verification_operations?: string[];
  connector_status?: string;
  model?: string;
}

export interface TelemetryEvent {
  event_id: string;
  event_type: string;
  mission_id: string;
  timestamp: string;
  state: string;
  step_id?: string;
  app?: string;
  source_type?: string;
  summary: string;
  details: Record<string, unknown>;
}

export interface EvidenceItem {
  evidence_id: string;
  app: string;
  source_type: 'LIVE' | 'SIMULATED';
  resource_reference: string;
  timestamp: string;
  author: string;
  content: string;
  entities: string[];
  relevance: number;
  retrieved_at: string;
  connector_status?: string;
}

export interface ActionReceipt {
  action_id: string;
  app: string;
  operation: string;
  resource: string;
  previous_state: Record<string, unknown>;
  requested_state: Record<string, unknown>;
  resulting_state: Record<string, unknown>;
  source_type: string;
  execution_status: string;
  verification_status: string;
  timestamp: string;
  idempotency_key: string;
  error?: string;
}

export interface VerificationResult {
  action_id: string;
  verified: boolean;
  verification_method: string;
  observed_state: Record<string, unknown>;
  details: string;
}

export interface ProposedAction {
  action_id: string;
  app: string;
  action: string;
  arguments: Record<string, unknown>;
  risk_level: string;
  requires_approval: boolean;
  rationale: string;
  expected_effect: Record<string, unknown>;
}

export interface Hypothesis {
  hypothesis_id: string;
  title: string;
  description: string;
  confidence: number;
  culprit_app: string;
  corroborating_claim_ids: string[];
}

export interface MissionSnapshot {
  mission_id: string;
  mode: 'live' | 'simulation';
  goal: string;
  scenario: string;
  status: 'running' | 'completed' | 'failed';
  started_at: string;
  completed_at?: string;
  error?: string;
  current_state: string;
  state_history: [string, string][];
  policy: string;
  evidence_counts: { total: number; live: number; simulated: number };
  action_counts: { required: number; verified: number; failed: number; pending: number };
  llm_call_count: number;
  hypotheses: Hypothesis[];
}

export interface Scorecard {
  mission_id: string;
  target_entity: string;
  final_state: string;
  policy: string;
  metrics: Record<string, number>;
  counts: Record<string, number>;
  verdict: string;
}

export type MissionMode = 'live' | 'simulation';
export type DemoScenario = 'golden_path' | 'verification_failure' | 'approval_rejected' | 'slack_outage' | 'counterfactual';
