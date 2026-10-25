export type WorkEvent = {
  id: string;
  source: string;
  session_id: string;
  occurred_at: string;
  kind: string;
  summary: string;
  cwd: string;
  project: string;
  raw_ref: string;
  metadata: Record<string, unknown>;
};
export type Task = {
  id: string;
  date: string;
  title: string;
  details: string;
  project: string;
  category: string;
  status: string;
  origin: string;
  confidence: string;
  evidence_level: string;
  confirmed_minutes: number;
  suggested_minutes: number | null;
  started_at: string | null;
  ended_at: string | null;
  evidence: WorkEvent[];
  time_locked: boolean;
  time_source: string;
  time_reason: string;
  timing: Timing;
};
export type Timing = {
  session_span_minutes: number;
  observable_active_minutes: number;
  tool_runtime_minutes: number | null;
  gap_minutes: number;
  confidence: string;
  method: string;
};
export type Report = {
  date: string;
  tasks: Task[];
  total_minutes: number;
  target_minutes: number;
  remaining_minutes: number;
  confirmed: boolean;
  warnings: string[];
  projects: number;
  sources: number;
  time_allocation: null | {
    stale: boolean;
    estimated_at: string;
    fixed_minutes: number;
    distributed_minutes: number;
    independent_estimate_minutes: number;
    warnings: string[];
    note: string;
  };
  conversations: ConversationOverview;
  legacy_count: number;
  merge_suggestions: { ids: string[]; reason: string }[];
};
export type SourceConfig = { enabled: boolean; paths: string[] };
export type GiteeProjectRule = {
  pattern: string;
  mode: "prefix" | "contains" | "exact";
  program_id: number;
};
export type GiteeEnterpriseConfig = {
  enterprise_id: number;
  rules: GiteeProjectRule[];
  include_details: boolean;
};
export type GiteeProgram = { id: number; name: string; ident: string };
export type Settings = {
  timezone: string;
  target_minutes: number;
  step_minutes: number;
  claude_code: SourceConfig;
  codex: SourceConfig;
  git_roots: string[];
  project_mappings: Record<string, string>;
  excluded_paths: string[];
  redact_patterns: string[];
  llm_enabled: boolean;
  llm_endpoint: string;
  llm_model: string;
  llm_key_env: string;
  llm_provider: "deepseek" | "glm" | "ccswitch" | "custom";
  llm_protocol: "openai" | "anthropic";
  ccswitch_config_path: string;
  analysis_sources: string[];
  agent_prompts: Record<string, string>;
  title_examples: string[];
  history_reference: {
    file?: string;
    since?: string;
    selected_records?: number;
    examples?: number;
    git_examples?: number;
  };
  activity_gap_minutes: number;
  style_template: string;
  gitee: {
    enabled: boolean;
    interval_hours: number;
    filter: string;
    repository: string;
    since: string;
  };
  gitee_enterprise: GiteeEnterpriseConfig;
};

export type ConversationOverview = {
  running?: boolean;
  timing: Timing;
  work_timing: Timing | null;
  date: string;
  session_count: number;
  user_messages: number;
  assistant_messages: number;
  event_count: number;
  selected_sources: string[];
  stale: boolean;
  sources: {
    source: string;
    selected: boolean;
    sessions: number;
    user_messages: number;
    assistant_messages: number;
    events: number;
  }[];
  sessions: {
    id: string;
    source: string;
    projects: string[];
    first_at: string;
    last_at: string;
    user_messages: number;
    assistant_messages: number;
    events: number;
    preview: string;
    timing: Timing;
  }[];
  analysis: null | {
    summary: string;
    analyzed_at: string;
    model: string;
    provider: string;
    tasks: number;
    model_requests: number;
    scope?: {
      work_user_messages: number;
      excluded_user_messages: number;
      uncertain_user_messages: number;
    };
  };
};
