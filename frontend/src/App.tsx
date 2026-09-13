/* ===================================================================
   RECLAIM Mission Control — Main Application
   
   Single-screen autonomous operational agent dashboard.
   All data flows from backend APIs — nothing is hardcoded.
   =================================================================== */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { api, subscribeToEvents } from './api';
import type {
  AppCapability, TelemetryEvent, MissionSnapshot, EvidenceItem,
  ProposedAction, ActionReceipt, VerificationResult, Scorecard,
  MissionMode, DemoScenario, Hypothesis,
} from './types';

// ===================================================================
// State Pipeline Steps
// ===================================================================
const PIPELINE_STEPS = [
  'GOAL_RECEIVED', 'PLANNING', 'INVESTIGATING', 'EVIDENCE_SYNTHESIS',
  'DECISION', 'EXECUTING', 'VERIFYING', 'COMPLETED',
];
const RECOVERY_STATES = ['RECOVERING', 'WAITING_FOR_APPROVAL'];

function getStepStatus(step: string, currentState: string, history: [string, string][]): 'completed' | 'current' | 'waiting' | 'failed' {
  if (currentState === 'FAILED' && step === currentState) return 'failed';
  if (step === currentState) return 'current';
  const visited = new Set(history.map(([s]) => s));
  if (visited.has(step)) return 'completed';
  return 'waiting';
}

// ===================================================================
// Event type classification
// ===================================================================
function eventBadgeClass(type: string): string {
  if (type.includes('COMPLETED') || type.includes('PASSED') || type.includes('GRANTED')) return 'success';
  if (type.includes('FAILED') || type.includes('REJECTED')) return 'error';
  if (type.includes('APPROVAL') || type.includes('RECOVERY') || type.includes('WAITING')) return 'warning';
  return 'info';
}

function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch { return ts; }
}

function riskClass(level: string): string {
  const l = level.toLowerCase();
  if (l.includes('high')) return 'high';
  if (l.includes('low')) return 'low';
  return 'read';
}

// ===================================================================
// Main App
// ===================================================================
export default function App() {
  // System state
  const [capabilities, setCapabilities] = useState<Record<string, AppCapability>>({});
  const [systemOnline, setSystemOnline] = useState(false);

  // Mission state
  const [missionId, setMissionId] = useState<string | null>(null);
  const [mission, setMission] = useState<MissionSnapshot | null>(null);
  const [events, setEvents] = useState<TelemetryEvent[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [proposedActions, setProposedActions] = useState<ProposedAction[]>([]);
  const [receipts, setReceipts] = useState<ActionReceipt[]>([]);
  const [verifications, setVerifications] = useState<VerificationResult[]>([]);
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);

  // UI state
  const [goal, setGoal] = useState("Resolve Acme's production crisis before it causes business impact.");
  const [scenario, setScenario] = useState<DemoScenario>('golden_path');
  const [loading, setLoading] = useState(false);
  const [evidenceFilter, setEvidenceFilter] = useState<string>('ALL');
  const [showAudit, setShowAudit] = useState(false);
  const [judgeMode, setJudgeMode] = useState(false);
  const [approvalPending, setApprovalPending] = useState<{ token: string; event: TelemetryEvent } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const unsubRef = useRef<(() => void) | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Boot: check capabilities
  useEffect(() => {
    api.capabilities().then(caps => {
      setCapabilities(caps);
      setSystemOnline(true);
    }).catch(() => setSystemOnline(false));
  }, []);

  // Poll mission state while running
  const pollMission = useCallback((mid: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const snap = await api.getMission(mid);
        setMission(snap);

        const evRes = await api.getEvidence(mid);
        setEvidence(evRes.evidence);

        const actRes = await api.getActions(mid);
        setProposedActions(actRes.proposed_actions);
        setReceipts(actRes.executed_receipts);
        setVerifications(actRes.verifications);

        if (snap.status === 'completed' || snap.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current);
          try {
            const sc = await api.getScorecard(mid);
            setScorecard(sc);
          } catch { /* scorecard may not be available */ }
        }
      } catch { /* ignore transient errors */ }
    }, 1000);
  }, []);

  // Start mission
  const startMission = useCallback(async (mode: MissionMode) => {
    setLoading(true);
    setError(null);
    setEvents([]);
    setEvidence([]);
    setProposedActions([]);
    setReceipts([]);
    setVerifications([]);
    setScorecard(null);
    setApprovalPending(null);
    setMission(null);

    // Cleanup previous
    if (unsubRef.current) unsubRef.current();
    if (pollRef.current) clearInterval(pollRef.current);

    try {
      const res = await api.startMission(goal, mode, scenario);
      setMissionId(res.mission_id);

      // Initial snapshot
      const snap = await api.getMission(res.mission_id);
      setMission(snap);

      // Subscribe to SSE
      unsubRef.current = subscribeToEvents(
        res.mission_id,
        (evt) => {
          setEvents(prev => [...prev, evt]);
          // Detect approval request
          if (evt.event_type === 'APPROVAL_REQUESTED' && evt.details?.token) {
            setApprovalPending({ token: evt.details.token as string, event: evt });
          }
        },
        async () => {
          // Mission ended — fetch final state
          try {
            const finalSnap = await api.getMission(res.mission_id);
            setMission(finalSnap);
            const ev = await api.getEvidence(res.mission_id);
            setEvidence(ev.evidence);
            const act = await api.getActions(res.mission_id);
            setProposedActions(act.proposed_actions);
            setReceipts(act.executed_receipts);
            setVerifications(act.verifications);
            const sc = await api.getScorecard(res.mission_id);
            setScorecard(sc);
          } catch { /* best effort */ }
          setLoading(false);
        },
      );

      // Also poll for state updates
      pollMission(res.mission_id);

    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setLoading(false);
    }
  }, [goal, scenario, pollMission]);

  // Handle approval
  const handleApproval = useCallback(async (action: 'APPROVE' | 'REJECT') => {
    if (!missionId || !approvalPending) return;
    try {
      await api.submitApproval(missionId, approvalPending.token, action);
      setApprovalPending(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [missionId, approvalPending]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (unsubRef.current) unsubRef.current();
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Derived
  const missionMode = mission?.mode || 'simulation';
  const isLive = missionMode === 'live';
  const isRunning = mission?.status === 'running';
  const isComplete = mission?.status === 'completed';
  const isFailed = mission?.status === 'failed';
  const currentState = mission?.current_state || 'GOAL_RECEIVED';
  const stateHistory = mission?.state_history || [];

  // Filtered evidence
  const filteredEvidence = useMemo(() => {
    if (evidenceFilter === 'ALL') return evidence;
    return evidence.filter(e => e.app.toLowerCase() === evidenceFilter.toLowerCase());
  }, [evidence, evidenceFilter]);

  // Build causal graph data from evidence entities
  const graphNodes = useMemo(() => {
    const appEvidence: Record<string, string[]> = {};
    for (const ev of evidence) {
      if (!appEvidence[ev.app]) appEvidence[ev.app] = [];
      appEvidence[ev.app].push(ev.content.slice(0, 60));
    }
    return appEvidence;
  }, [evidence]);

  // Count live apps
  const liveAppCount = Object.values(capabilities).filter(
    c => c.source_type === 'LIVE' && c.availability === 'AVAILABLE'
  ).length;

  return (
    <div className="app-layout">
      {/* ===== HEADER ===== */}
      <header className="header" role="banner">
        <div className="header-brand">
          <span className="header-logo">RECLAIM</span>
          <span className="header-tagline">Autonomous Operational Agent</span>
        </div>
        <div className="header-status">
          {mission && (
            <>
              <span className={`badge ${isLive ? 'badge-live' : 'badge-simulated'}`}>
                <span className="badge-dot" /> {isLive ? 'LIVE' : 'SIMULATION'}
              </span>
              <span className="header-badge" style={{ color: 'var(--text-muted)' }}>
                {liveAppCount}/{Object.keys(capabilities).length} Connected
              </span>
              <span className="header-mission-id">{mission.mission_id}</span>
            </>
          )}
          <label className="judge-toggle" title="Emphasize integration proof and verification">
            <input type="checkbox" checked={judgeMode} onChange={e => setJudgeMode(e.target.checked)} />
            Judge
          </label>
        </div>
      </header>

      {/* ===== MISSION HERO (before mission starts) ===== */}
      {!mission && (
        <section className="mission-hero" aria-label="Mission Input">
          <h1>Resolve an operational crisis.</h1>
          <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--space-md)' }}>
            Give an AI agent an outcome. Watch it investigate, act, verify, and recover.
          </p>
          <div className="hero-features">
            <div className="hero-feature">
              <span className="hero-feature-icon">◆</span>
              {liveAppCount} Live Applications
            </div>
            <div className="hero-feature">
              <span className="hero-feature-icon">◆</span>
              Autonomous Reasoning
            </div>
            <div className="hero-feature">
              <span className="hero-feature-icon">◆</span>
              Verified Actions
            </div>
          </div>
          <div className="mission-input-container">
            <textarea
              className="mission-input"
              rows={2}
              value={goal}
              onChange={e => setGoal(e.target.value)}
              placeholder="Describe the operational objective..."
              aria-label="Mission goal"
            />
            <div className="mission-actions">
              <button
                className="btn btn-primary"
                onClick={() => startMission('live')}
                disabled={loading || !systemOnline}
                aria-label="Run live mission"
              >
                ● RUN LIVE MISSION
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => startMission('simulation')}
                disabled={loading || !systemOnline}
                aria-label="Run simulation"
              >
                ○ RUN SIMULATION
              </button>
              <select
                className="scenario-select"
                value={scenario}
                onChange={e => setScenario(e.target.value as DemoScenario)}
                aria-label="Demo scenario"
              >
                <option value="golden_path">Golden Path</option>
                <option value="verification_failure">Verification Failure</option>
                <option value="approval_rejected">Approval Rejected</option>
                <option value="slack_outage">Slack Outage</option>
                <option value="counterfactual">Counterfactual</option>
              </select>
            </div>
          </div>
          {error && <p style={{ color: 'var(--accent-danger)', marginTop: 'var(--space-md)' }}>{error}</p>}
          {!systemOnline && <p style={{ color: 'var(--accent-sim)', marginTop: 'var(--space-md)' }}>Backend unavailable. Start the API server.</p>}
        </section>
      )}

      {/* ===== STATE PIPELINE ===== */}
      {mission && (
        <div style={{ padding: '0 var(--space-lg)', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-subtle)' }}>
          <div className="pipeline" role="progressbar" aria-label="Mission state pipeline">
            {PIPELINE_STEPS.map((step, i) => {
              const status = getStepStatus(step, currentState, stateHistory);
              return (
                <div key={step} style={{ display: 'flex', alignItems: 'center' }}>
                  {i > 0 && <span className="pipeline-arrow">→</span>}
                  <div className={`pipeline-step ${status}`}>
                    {status === 'completed' && '✓ '}
                    {status === 'failed' && '✕ '}
                    {step.replace(/_/g, ' ')}
                  </div>
                </div>
              );
            })}
            {/* Show recovery states if visited */}
            {RECOVERY_STATES.map(rs => {
              const visited = stateHistory.some(([s]) => s === rs);
              if (!visited && currentState !== rs) return null;
              return (
                <div key={rs} style={{ display: 'flex', alignItems: 'center' }}>
                  <span className="pipeline-arrow">↻</span>
                  <div className={`pipeline-step ${currentState === rs ? 'current' : 'completed'}`}>
                    {rs.replace(/_/g, ' ')}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ===== DASHBOARD ===== */}
      {mission && (
        <div className="main-content">
          {/* Mission goal bar */}
          <div className="card" style={{ marginBottom: 'var(--space-lg)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 'var(--space-md)' }}>
            <div>
              <div className="card-title" style={{ marginBottom: '4px' }}>Mission Objective</div>
              <div style={{ fontSize: '0.95rem' }}>{mission.goal}</div>
            </div>
            <div style={{ display: 'flex', gap: 'var(--space-md)', alignItems: 'center' }}>
              <span className={`badge ${isLive ? 'badge-live' : 'badge-simulated'}`}>
                <span className="badge-dot" /> {isLive ? 'LIVE' : 'SIMULATION'}
              </span>
              {!isComplete && !isFailed && (
                <button className="btn btn-secondary" style={{ padding: '6px 14px', fontSize: '0.75rem' }}
                  onClick={() => { setMission(null); setMissionId(null); if (unsubRef.current) unsubRef.current(); if (pollRef.current) clearInterval(pollRef.current); setLoading(false); }}>
                  New Mission
                </button>
              )}
              {(isComplete || isFailed) && (
                <button className="btn btn-primary" style={{ padding: '6px 14px', fontSize: '0.75rem' }}
                  onClick={() => { setMission(null); setMissionId(null); setEvents([]); setEvidence([]); setProposedActions([]); setReceipts([]); setVerifications([]); setScorecard(null); setLoading(false); }}>
                  New Mission
                </button>
              )}
            </div>
          </div>

          <div className="dashboard">
            {/* ===== SIDEBAR ===== */}
            <div className="dashboard-sidebar">
              {/* App Status */}
              <div className="card">
                <div className="card-title">Connections</div>
                <div className="app-status-list" style={{ marginTop: 'var(--space-sm)' }}>
                  {['github', 'slack', 'jira', 'groq'].map(app => {
                    const cap = capabilities[app];
                    const isAvailable = cap?.availability === 'AVAILABLE';
                    const srcType = cap?.source_type || 'UNAVAILABLE';
                    return (
                      <div className="app-status-item" key={app}>
                        <span className="app-status-name">
                          <span className={`app-icon ${app}`}>{app[0].toUpperCase()}</span>
                          {app.charAt(0).toUpperCase() + app.slice(1)}
                        </span>
                        {isAvailable ? (
                          <span className={`badge ${srcType === 'LIVE' ? 'badge-live' : 'badge-simulated'}`}>
                            <span className="badge-dot" />
                            {srcType}
                          </span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>OFFLINE</span>
                        )}
                      </div>
                    );
                  })}
                </div>
                {capabilities.groq?.model && (
                  <div style={{ marginTop: 'var(--space-sm)', fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    LLM: {capabilities.groq.model}
                  </div>
                )}
              </div>

              {/* Hypothesis */}
              {mission.hypotheses.length > 0 && (
                <div className="card" style={{ borderColor: 'var(--accent-purple)' }}>
                  <div className="card-title" style={{ color: 'var(--accent-purple)' }}>Root Cause Hypothesis</div>
                  {mission.hypotheses.map(h => (
                    <div key={h.hypothesis_id} style={{ marginTop: 'var(--space-sm)' }}>
                      <div style={{ fontSize: '0.9rem', fontWeight: 600 }}>{h.title}</div>
                      {h.description && <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '4px' }}>{h.description}</div>}
                      <div style={{ marginTop: 'var(--space-sm)', fontSize: '0.8rem' }}>
                        <span style={{ color: 'var(--accent-purple)' }}>Confidence: {Math.round(h.confidence * 100)}%</span>
                      </div>
                      {h.corroborating_claim_ids.length > 0 && (
                        <div style={{ marginTop: '4px', fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                          Evidence: {h.corroborating_claim_ids.join(', ')}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Scorecard */}
              {scorecard && (
                <div className="card">
                  <div className="card-header">
                    <span className="card-title">Reliability Scorecard</span>
                    <span style={{
                      fontSize: '0.7rem', fontWeight: 700, padding: '2px 8px', borderRadius: '3px',
                      background: scorecard.verdict === 'GENUINE_AGENT' ? 'var(--accent-live-bg)' : 'var(--accent-danger-bg)',
                      color: scorecard.verdict === 'GENUINE_AGENT' ? 'var(--accent-live)' : 'var(--accent-danger)',
                    }}>
                      {scorecard.verdict}
                    </span>
                  </div>
                  <div className="scorecard-grid">
                    {[
                      ['Mission Completion', scorecard.metrics.live_mission_success ?? (isComplete ? 1 : 0)],
                      ['Evidence Grounding', scorecard.metrics.evidence_grounding],
                      ['Cross-App Correlation', scorecard.metrics.cross_app_correlation],
                      ['Verification', scorecard.metrics.verification_rate],
                      ['Recovery', scorecard.metrics.recovery_compliance],
                      ['Safety', scorecard.metrics.safety_compliance],
                      ['Live Coverage', scorecard.metrics.live_integration_coverage],
                    ].map(([label, val]) => (
                      <div className="scorecard-row" key={label as string}>
                        <span className="scorecard-label">{label as string}</span>
                        <div className="scorecard-bar-track">
                          <div className="scorecard-bar-fill" style={{ width: `${((val as number) ?? 0) * 100}%` }} />
                        </div>
                        <span className="scorecard-value">
                          {val != null ? `${Math.round((val as number) * 100)}%` : 'N/A'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* ===== MAIN AREA ===== */}
            <div className="dashboard-main">
              {/* Mission Result */}
              {(isComplete || isFailed) && (
                <div className="card mission-result">
                  <div className={`mission-result-status ${isComplete ? 'success' : 'failure'}`}>
                    {isComplete ? '✓ MISSION COMPLETE' : '✕ MISSION FAILED'}
                  </div>
                  {mission.hypotheses.length > 0 && (
                    <div style={{ fontSize: '0.95rem', color: 'var(--text-secondary)', marginBottom: 'var(--space-lg)' }}>
                      Root Cause: {mission.hypotheses[0].title}
                    </div>
                  )}
                  <div className="result-metrics">
                    <div className="result-metric">
                      <div className="result-metric-value">{mission.evidence_counts.total}</div>
                      <div className="result-metric-label">Evidence</div>
                    </div>
                    <div className="result-metric">
                      <div className="result-metric-value">{mission.action_counts.verified}/{mission.action_counts.required}</div>
                      <div className="result-metric-label">Verified</div>
                    </div>
                    <div className="result-metric">
                      <div className="result-metric-value">{isLive ? mission.evidence_counts.live : mission.evidence_counts.simulated}</div>
                      <div className="result-metric-label">{isLive ? 'Live' : 'Sim'} Evidence</div>
                    </div>
                    <div className="result-metric">
                      <div className="result-metric-value">{mission.llm_call_count}</div>
                      <div className="result-metric-label">LLM Calls</div>
                    </div>
                    <div className="result-metric">
                      <div className="result-metric-value">{mission.action_counts.failed}</div>
                      <div className="result-metric-label">Safety Violations</div>
                    </div>
                    <div className="result-metric">
                      <div className="result-metric-value">{Object.values(capabilities).filter(c => c.availability === 'AVAILABLE' && c.source_type === 'LIVE').length}</div>
                      <div className="result-metric-label">Live Apps</div>
                    </div>
                  </div>
                </div>
              )}

              {/* Evidence Panel */}
              <div className="card">
                <div className="card-header">
                  <span className="card-title">Cross-App Evidence ({evidence.length})</span>
                  <div className="evidence-filters">
                    {['ALL', 'GITHUB', 'SLACK', 'JIRA'].map(f => (
                      <button
                        key={f}
                        className={`evidence-filter ${evidenceFilter === f ? 'active' : ''}`}
                        onClick={() => setEvidenceFilter(f)}
                        aria-label={`Filter evidence by ${f}`}
                      >{f}</button>
                    ))}
                  </div>
                </div>
                {filteredEvidence.length > 0 ? (
                  <div className="evidence-grid">
                    {filteredEvidence.map(ev => (
                      <div className="evidence-card" key={ev.evidence_id}>
                        <div className="evidence-card-header">
                          <span className="evidence-card-app" style={{
                            color: ev.app === 'github' ? 'var(--github-color)' :
                              ev.app === 'slack' ? 'var(--slack-color)' :
                              ev.app === 'jira' ? 'var(--jira-color)' : 'var(--text-primary)'
                          }}>
                            {ev.app.toUpperCase()}
                          </span>
                          <span className={`badge ${ev.source_type === 'LIVE' ? 'badge-live' : 'badge-simulated'}`}>
                            <span className="badge-dot" />
                            {ev.source_type}
                          </span>
                        </div>
                        <div className="evidence-card-content">{ev.content}</div>
                        <div className="evidence-card-meta">
                          <span>{ev.resource_reference}</span>
                          <span>{ev.evidence_id}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: 'var(--space-lg)', textAlign: 'center' }}>
                    {isRunning ? 'Collecting evidence…' : 'No evidence collected'}
                  </div>
                )}
              </div>

              {/* Causal Graph */}
              {Object.keys(graphNodes).length > 1 && (
                <div className="card">
                  <div className="card-title">Evidence Correlation Graph</div>
                  <div className="causal-graph">
                    <CausalGraphSVG nodes={graphNodes} hypotheses={mission.hypotheses} />
                  </div>
                </div>
              )}

              {/* Actions & Verification */}
              {(proposedActions.length > 0 || receipts.length > 0) && (
                <div className="card">
                  <div className="card-title">Actions & Verification</div>
                  <div className="action-list" style={{ marginTop: 'var(--space-sm)' }}>
                    {proposedActions.map(action => {
                      const receipt = receipts.find(r => r.action_id === action.action_id);
                      const verification = verifications.find(v => v.action_id === action.action_id);
                      return (
                        <div className="action-card" key={action.action_id}>
                          <div className="action-card-header">
                            <div>
                              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>{action.app}</span>
                              <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>{action.action.replace(/_/g, ' ')}</div>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
                              <span className={`risk-badge ${riskClass(action.risk_level)}`}>{action.risk_level.replace(/_/g, ' ')}</span>
                              {receipt && (
                                <span className={`action-status ${receipt.verification_status === 'VERIFIED' ? 'verified' : receipt.verification_status === 'FAILED_VERIFICATION' ? 'failed' : 'executed'}`}>
                                  {receipt.verification_status === 'VERIFIED' ? '✓ VERIFIED' :
                                   receipt.verification_status === 'FAILED_VERIFICATION' ? '✕ FAILED' :
                                   receipt.execution_status === 'EXECUTED' ? '◉ EXECUTED' : receipt.execution_status}
                                </span>
                              )}
                              {!receipt && <span className="action-status pending">◯ PENDING</span>}
                            </div>
                          </div>
                          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '4px' }}>
                            {action.rationale}
                          </div>
                          {/* Verification Display */}
                          {receipt && (verification || receipt.verification_status !== 'PENDING') && (
                            <div className="verification-row">
                              <div className="verification-cell">
                                <div className="verification-label">Previous</div>
                                <div className="verification-value" style={{ color: 'var(--text-secondary)' }}>
                                  {Object.entries(receipt.previous_state).map(([k, v]) => `${k}: ${v}`).join(', ') || '—'}
                                </div>
                              </div>
                              <div className="verification-cell">
                                <div className="verification-label">Requested</div>
                                <div className="verification-value" style={{ color: 'var(--accent-blue)' }}>
                                  {Object.entries(receipt.requested_state).map(([k, v]) => `${k}: ${v}`).join(', ') || '—'}
                                </div>
                              </div>
                              <div className="verification-cell">
                                <div className="verification-label">Observed</div>
                                <div className={`verification-value ${receipt.verification_status === 'VERIFIED' ? 'match' : 'mismatch'}`}>
                                  {verification
                                    ? Object.entries(verification.observed_state)
                                        .filter(([k]) => k !== 'verified')
                                        .map(([k, v]) => `${k}: ${v}`).join(', ') || '✓ Confirmed'
                                    : Object.entries(receipt.resulting_state).map(([k, v]) => `${k}: ${v}`).join(', ') || '—'}
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Live Event Timeline */}
              <div className="card">
                <div className="card-title">Live Event Timeline ({events.length})</div>
                <div className="timeline" style={{ marginTop: 'var(--space-sm)' }}>
                  {events.length === 0 && isRunning && (
                    <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: 'var(--space-lg)' }}>Waiting for events…</div>
                  )}
                  {[...events].reverse().map(evt => (
                    <div className="timeline-event" key={evt.event_id}>
                      <span className="timeline-time">{formatTime(evt.timestamp)}</span>
                      <span className="timeline-summary">
                        {evt.app && <span style={{ color: evt.app === 'github' ? 'var(--github-color)' : evt.app === 'slack' ? 'var(--slack-color)' : evt.app === 'jira' ? 'var(--jira-color)' : 'var(--text-primary)', fontWeight: 600, marginRight: '6px', textTransform: 'uppercase', fontSize: '0.7rem' }}>{evt.app}</span>}
                        {evt.summary}
                      </span>
                      <span className={`timeline-badge ${eventBadgeClass(evt.event_type)}`}>
                        {evt.source_type && <>{evt.source_type} · </>}
                        {evt.event_type.replace(/_/g, ' ')}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Integration Proof — Judge Mode emphasis */}
              {(judgeMode || isComplete) && (
                <div className="card">
                  <div className="card-title">Integration Proof</div>
                  <div className="proof-grid" style={{ marginTop: 'var(--space-sm)' }}>
                    {['github', 'slack', 'jira'].map(app => {
                      const cap = capabilities[app];
                      const hasRead = evidence.some(e => e.app === app);
                      const hasWrite = receipts.some(r => r.app === app && r.execution_status === 'EXECUTED');
                      const hasVerify = verifications.some(v => {
                        const r = receipts.find(rc => rc.action_id === v.action_id);
                        return r?.app === app && v.verified;
                      });
                      const isAppLive = cap?.source_type === 'LIVE' && cap?.availability === 'AVAILABLE';
                      return (
                        <div className="proof-card" key={app}>
                          <div className="proof-card-title">
                            <span className={`app-icon ${app}`}>{app[0].toUpperCase()}</span>
                            {app.charAt(0).toUpperCase() + app.slice(1)}
                            {isAppLive && <span className="badge badge-live" style={{ marginLeft: '8px' }}><span className="badge-dot" />LIVE VERIFIED</span>}
                          </div>
                          <ul className="proof-list">
                            <li>{isAppLive ? <span className="proof-check">✓</span> : <span className="proof-cross">✕</span>} Real API</li>
                            <li>{hasRead ? <span className="proof-check">✓</span> : <span className="proof-cross">✕</span>} Read</li>
                            <li>{hasWrite ? <span className="proof-check">✓</span> : <span className="proof-cross">—</span>} Write</li>
                            <li>{hasVerify ? <span className="proof-check">✓</span> : <span className="proof-cross">—</span>} Read-back verification</li>
                          </ul>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Audit Trail */}
              <div className="card">
                <div className="audit-toggle" onClick={() => setShowAudit(!showAudit)} role="button" tabIndex={0} aria-expanded={showAudit}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setShowAudit(!showAudit); }}>
                  {showAudit ? '▼' : '▶'} Audit Trail ({events.length} events)
                </div>
                {showAudit && (
                  <div style={{ overflowX: 'auto' }}>
                    <table className="audit-table">
                      <thead>
                        <tr>
                          <th>Time</th>
                          <th>State</th>
                          <th>Event</th>
                          <th>App</th>
                          <th>Source</th>
                          <th>Summary</th>
                        </tr>
                      </thead>
                      <tbody>
                        {events.map(evt => (
                          <tr key={evt.event_id}>
                            <td>{formatTime(evt.timestamp)}</td>
                            <td>{evt.state}</td>
                            <td>{evt.event_type}</td>
                            <td>{evt.app || '—'}</td>
                            <td>{evt.source_type || '—'}</td>
                            <td style={{ maxWidth: '400px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{evt.summary}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ===== APPROVAL MODAL ===== */}
      {approvalPending && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Action approval required">
          <div className="modal">
            <div className="modal-title">
              <span className="risk-indicator">⚠</span> HIGH-RISK ACTION — APPROVAL REQUIRED
            </div>
            <div className="modal-section">
              <div className="modal-section-label">Why</div>
              <div className="modal-section-value">{approvalPending.event.summary}</div>
            </div>
            <div className="modal-section">
              <div className="modal-section-label">Application</div>
              <div className="modal-section-value">{approvalPending.event.app?.toUpperCase() || 'UNKNOWN'}</div>
            </div>
            <div className="modal-section">
              <div className="modal-section-label">Risk Level</div>
              <div className="modal-section-value" style={{ color: 'var(--accent-danger)' }}>HIGH RISK WRITE</div>
            </div>
            <div className="modal-section">
              <div className="modal-section-label">Verification</div>
              <div className="modal-section-value">Independent read-back verification after execution</div>
            </div>
            <div className="modal-actions">
              <button className="btn btn-reject" onClick={() => handleApproval('REJECT')} aria-label="Reject action">
                REJECT
              </button>
              <button className="btn btn-approve" onClick={() => handleApproval('APPROVE')} aria-label="Approve action">
                APPROVE
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


/* ===================================================================
   Causal Graph SVG — Derived from backend evidence
   =================================================================== */
function CausalGraphSVG({ nodes, hypotheses }: { nodes: Record<string, string[]>; hypotheses: Hypothesis[] }) {
  const apps = Object.keys(nodes);
  if (apps.length === 0) return null;

  const nodeW = 140;
  const nodeH = 36;
  const gapX = 40;
  const gapY = 60;

  // Layout: apps in a row, hypothesis node below center
  const totalW = apps.length * (nodeW + gapX) - gapX;
  const hasHyp = hypotheses.length > 0;
  const svgW = Math.max(totalW + 40, 400);
  const svgH = hasHyp ? nodeH + gapY + nodeH + 40 : nodeH + 40;

  const appPositions = apps.map((_, i) => ({
    x: 20 + i * (nodeW + gapX),
    y: 20,
  }));

  const hypX = (totalW - nodeW) / 2 + 20;
  const hypY = 20 + nodeH + gapY;

  return (
    <svg width="100%" viewBox={`0 0 ${svgW} ${svgH}`} style={{ maxWidth: `${svgW}px` }}>
      <defs>
        <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <polygon points="0 0, 8 3, 0 6" fill="var(--text-muted)" />
        </marker>
      </defs>

      {/* App nodes */}
      {apps.map((app, i) => {
        const pos = appPositions[i];
        const count = nodes[app].length;
        const color = app === 'github' ? 'var(--github-color)' : app === 'slack' ? 'var(--slack-color)' : app === 'jira' ? 'var(--jira-color)' : 'var(--text-primary)';
        return (
          <g key={app}>
            <rect className="node-rect" x={pos.x} y={pos.y} width={nodeW} height={nodeH} />
            <text x={pos.x + nodeW / 2} y={pos.y + 15} textAnchor="middle" style={{ fill: color, fontWeight: 600, fontSize: '12px' }}>
              {app.toUpperCase()}
            </text>
            <text x={pos.x + nodeW / 2} y={pos.y + 28} textAnchor="middle" style={{ fill: 'var(--text-muted)', fontSize: '10px' }}>
              {count} items
            </text>
            {/* Edge to hypothesis */}
            {hasHyp && (
              <line
                className="edge-line"
                x1={pos.x + nodeW / 2} y1={pos.y + nodeH}
                x2={hypX + nodeW / 2} y2={hypY}
              />
            )}
          </g>
        );
      })}

      {/* Hypothesis node */}
      {hasHyp && (
        <g>
          <rect className="node-rect" x={hypX} y={hypY} width={nodeW} height={nodeH}
            style={{ stroke: 'var(--accent-purple)', strokeWidth: 1.5 }} />
          <text x={hypX + nodeW / 2} y={hypY + 15} textAnchor="middle" style={{ fill: 'var(--accent-purple)', fontWeight: 600, fontSize: '11px' }}>
            ROOT CAUSE
          </text>
          <text x={hypX + nodeW / 2} y={hypY + 28} textAnchor="middle" style={{ fill: 'var(--text-muted)', fontSize: '9px' }}>
            {Math.round(hypotheses[0].confidence * 100)}% confidence
          </text>
        </g>
      )}
    </svg>
  );
}
