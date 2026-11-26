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
  is_todo?: boolean;
  gitee_linked?: boolean;
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
export type AllocationItem = {
  id: string;
  eligible: boolean;
  weight: number;
  estimated_minutes: number | null;
  reason: string;
  allocated_minutes: number;
};
export type AllocationSnapshot = {
  stale: boolean;
  pending?: boolean;
  applied_at?: string | null;
  estimated_at: string;
  fixed_minutes: number;
  preserved_minutes?: number;
  distributed_minutes: number;
  independent_estimate_minutes: number;
  warnings: string[];
  note: string;
  items?: AllocationItem[];
  manual_tasks?: {
    title: string;
    confirmed_minutes: number;
    category?: string;
  }[];
};
export type ReviewAlertItem = {
  key?: string;
  kind: "similarity" | "tool_overlap" | "time_overlap" | "analysis_warning";
  location: string;
  date: string;
  evidence_dates?: string[];
  project?: string;
  indices: number[];
  ids: string[];
  titles: string[];
  similarity?: number;
  detail?: string;
  reason: string;
};

export type MultiDayBinding = {
  id: string;
  start_date: string;
  end_date: string;
  anchor_date: string;
  target_minutes: number;
  active: boolean;
  created_at: string;
  unbound_at?: string | null;
};

export type Report = {
  date: string;
  canonical_date?: string;
  binding?: MultiDayBinding | null;
  tasks: Task[];
  total_minutes: number;
  target_minutes: number;
  remaining_minutes: number;
  confirmed: boolean;
  warnings: string[];
  review_items?: ReviewAlertItem[];
  projects: number;
  sources: number;
  allocation_running?: boolean;
  time_allocation: null | AllocationSnapshot;
  conversations: ConversationOverview;
  legacy_count: number;
  clearable_ai_count?: number;
  protected_ai_count?: number;
  merge_suggestions: {
    ids: string[];
    indices?: number[];
    titles?: string[];
    project?: string;
    date?: string;
    evidence_dates?: string[];
    location?: string;
    kind?: string;
    similarity?: number;
    reason: string;
  }[];
  similarity_status?: SimilarityStatus;
};

export type SimilarityStatus = {
  checked: boolean;
  tasks_changed: boolean;
  titles_modified?: boolean;
  handled: boolean;
  has_duplicates: boolean;
  pairs_count: number;
  tasks_signature: string;
};

export type DuplicatePair = {
  pair_id: string;
  manual_task: Task;
  ai_task: Task;
  similarity_score: number;
  reason: string;
  recommended_title: string;
  recommended_details: string;
  recommended_project: string;
  recommended_category: string;
  recommended_minutes: number;
  recommended_status: string;
};

export type SimilarityCheckResponse = {
  status: "clean" | "duplicates_found";
  tasks_changed: boolean;
  has_duplicates: boolean;
  pairs: DuplicatePair[];
  tasks_signature: string;
};

export type SimilarityResolution = {
  pair_id: string;
  action: "ai_merge" | "keep_manual" | "keep_ai" | "ignore";
  target_task_id?: string;
  title?: string;
  details?: string;
  project?: string;
  category?: string;
  confirmed_minutes?: number;
  status?: string;
};
export type SourceConfig = { enabled: boolean; paths: string[] };
export type GiteeProjectRule = {
  pattern: string;
  mode: "prefix" | "contains" | "exact";
  program_id: number;
};
export type GiteeDedupConfig = {
  enabled: boolean;
  dedup_on_pull: boolean;
  dedup_on_publish: boolean;
  threshold_high: number;
  threshold_low: number;
  date_weight: number;
};
export type GiteeCandidateMatch = {
  local_task_id: string;
  local_task: Task;
  confidence: number;
  explanation?: Record<string, unknown>;
};
export type GiteeAmbiguousItem = {
  id: string;
  issue_id?: string;
  title: string;
  description?: string;
  program?: string;
  project?: string;
  date?: string;
  url?: string;
  source?: string;
  confidence: number;
  candidates: GiteeCandidateMatch[];
};
export type GiteeSyncResult = {
  date: string;
  total_remote: number;
  auto_matched: {
    task_id: string;
    remote_issue_id: string;
    title: string;
    previous_title: string;
    confirmed_minutes: number;
    confidence: number;
  }[];
  ambiguous: GiteeAmbiguousItem[];
  discarded_count: number;
  unmatched_local_count: number;
};
export type GiteeMatchDecision = {
  remote_issue_id: string;
  action: "match" | "create" | "ignore";
  target_task_id?: string | null;
};
export type AutoGenerateConfig = {
  enabled: boolean;
  mode: "daily_times" | "interval";
  daily_times: string[];
  interval_minutes: number;
  weekdays_only: boolean;
  collect_before_analyze: boolean;
  skip_if_unchanged: boolean;
  skip_if_confirmed: boolean;
};

export type AutoGenerateStatus = AutoGenerateConfig & {
  llm_enabled: boolean;
  auto_import_analysis: boolean;
  timezone: string;
  running: boolean;
  target_date: string;
  binding?: MultiDayBinding | null;
  next_run_at: string | null;
  last_attempt_at: string | null;
  last_run_at: string | null;
  last_status: "idle" | "running" | "success" | "skipped" | "error" | "interrupted" | string;
  last_reason: string;
  last_error: string;
  last_date: string;
  last_slot: string;
  last_summary?: {
    created?: number;
    archived?: number;
    pending?: number;
    auto_imported?: boolean;
    new_events?: number;
  } | null;
};

export type GiteeMember = {
  id: number;
  user_id?: number;
  name: string;
  remark?: string;
  username?: string;
  email?: string;
  role?: string;
};
export type GiteeEnterpriseConfig = {
  enterprise_id: number;
  rules: GiteeProjectRule[];
  include_details: boolean;
  assignee_id?: number | null;
  assignee_name?: string;
  assignee_username?: string;
  assignee_email?: string;
};
export type GiteeProgram = { id: number; name: string; ident: string };
export type GitAuthor = { name: string; email: string };
export type GitIdentity = {
  root: string;
  override_key: string | null;
  project: string;
  detected: GitAuthor & { source: string };
  selected: GitAuthor & { source: string };
  authors: (GitAuthor & { commits: number })[];
};
export type LlmProvider = "deepseek" | "glm" | "glm-coding" | "ccswitch" | "local" | "custom";
export type LlmProtocol = "openai" | "anthropic";

export type ModelConnection = {
  id: string;
  label: string;
  provider: LlmProvider;
  endpoint: string;
  protocol: LlmProtocol;
  key_env: string;
  ccswitch_config_path: string;
  model: string;
  available_models?: string[];
};

export type ModelCheck = {
  ok: boolean;
  message: string;
  at: string;
};

export type ModelConnectionState = {
  state: "ok" | "error" | "unknown";
  message: string;
  last_test: ModelCheck | null;
  last_use: ModelCheck | null;
};

export type ModelConnectionStatus = {
  id: string;
  label: string;
  provider: LlmProvider;
  provider_label: string;
  endpoint: string;
  protocol: LlmProtocol;
  key_env: string;
  ccswitch_config_path: string;
  key_configured: boolean;
  model: string;
  model_label: string;
  available_models?: string[];
  status: ModelConnectionState;
};

export type ModelStatus = {
  configured: boolean;
  provider: string;
  endpoint: string;
  model: string;
  protocol?: string;
  error: string;
  connections?: ModelConnectionStatus[];
  active?: { connection_id: string; model: string };
};

export type MemoryCompressionConfig = {
  enabled: boolean;
  mode: "connection" | "custom";
  connection_id: string;
  connection_model: string;
  fallback_connection_ids: string[];
  provider: LlmProvider;
  endpoint: string;
  protocol: LlmProtocol;
  key_env: string;
  ccswitch_config_path: string;
  model: string;
  sources: string[];
};

export type MemoryCompressionStorageStats = {
  total_records: number;
  total_raw_chars: number;
  total_compressed_chars: number;
  saved_chars: number;
  saved_ratio: number;
  recent: {
    id: string;
    source: string;
    session_id: string;
    date_key: string;
    model: string;
    raw_chars: number;
    compressed_chars: number;
    raw_events_count: number;
    summary_text: string;
    updated_at: string;
  }[];
};

export type MemoryCompressionStatus = {
  enabled: boolean;
  mode: "connection" | "custom";
  connection_id: string;
  connection_model: string;
  configured: boolean;
  provider: string;
  endpoint: string;
  model: string;
  protocol: string;
  key_configured: boolean;
  error: string;
  status: ModelConnectionState;
  storage?: MemoryCompressionStorageStats;
};

export type Settings = {
  timezone: string;
  target_minutes: number;
  step_minutes: number;
  antigravity: SourceConfig;
  gemini: SourceConfig;
  doubao: SourceConfig;
  claude_code: SourceConfig;
  codex: SourceConfig;
  workbuddy_cn: SourceConfig;
  workbuddy_intl: SourceConfig;
  deepseek_harness: SourceConfig;
  qoder_cn: SourceConfig;
  qoder_intl: SourceConfig;
  trae_cn: SourceConfig;
  trae_intl: SourceConfig;
  cursor: SourceConfig;
  git_roots: string[];
  git_author: GitAuthor | null;
  git_author_overrides: Record<string, GitAuthor>;
  project_mappings: Record<string, string>;
  excluded_paths: string[];
  redact_patterns: string[];
  llm_enabled: boolean;
  llm_endpoint: string;
  llm_model: string;
  llm_key_env: string;
  llm_provider: LlmProvider;
  llm_protocol: LlmProtocol;
  llm_connections?: ModelConnection[];
  llm_active_connection?: string;
  llm_active_model?: string;
  output_token_budgets?: Partial<Record<"scope_filter" | "task_writer" | "time_estimator" | "manual_writer" | "task_merger" | "style_template" | "memory_compressor", number | "unlimited" | null>>;
  ccswitch_config_path: string;
  auto_import_analysis: boolean;
  auto_apply_allocation: boolean;
  auto_generate?: AutoGenerateConfig;
  memory_compression?: MemoryCompressionConfig;
  task_details_mode?: "standard" | "concise" | "none";
  theme_id?: string;
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
  gitee_dedup?: GiteeDedupConfig;
};

export type PendingMatchedTask = {
  id: string;
  title: string;
  project: string;
  confirmed_minutes: number;
  suggested_minutes?: number | null;
  time_source: string;
  edited: boolean;
  status: string;
  gitee_issue_id: string;
  gitee_url: string;
  similarity: number;
  evidence_overlap: number;
  match_reason: string;
};

export type PendingTask = {
  fingerprint: string;
  title: string;
  details: string;
  project: string;
  category: string;
  status: string;
  confidence: string;
  evidence_level: string;
  evidence_ids: string[];
  previously_deleted?: boolean;
  matched_task?: PendingMatchedTask | null;
};

export type DailyCollectionCount = {
  date: string;
  sessions: number;
  user_messages: number;
  assistant_messages: number;
  git_events: number;
  events: number;
};

export type CompressionSourcePrediction = {
  source: string;
  sessions: number;
  compression_selected: boolean;
  raw_tokens: number;
  predicted_tokens: number;
  simulated_tokens: number;
  cached_sessions: number;
};

export type CompressionPrediction = {
  date_key: string;
  enabled: boolean;
  raw_input_tokens: number;
  predicted_input_tokens: number;
  simulated_compressed_tokens: number;
  saved_tokens: number;
  saved_ratio: number;
  simulated_saved_tokens: number;
  simulated_saved_ratio: number;
  raw_chars: number;
  predicted_chars: number;
  session_count: number;
  will_compress_sessions: number;
  cached_sessions: number;
  per_source: CompressionSourcePrediction[];
  note: string;
};

export type ConversationOverview = {
  running?: boolean;
  timing: Timing;
  work_timing: Timing | null;
  date: string;
  start_date?: string;
  end_date?: string;
  daily_counts?: DailyCollectionCount[];
  session_count: number;
  total_session_count?: number;
  excluded_session_count?: number;
  partial_excluded_session_count?: number;
  excluded_event_count?: number;
  user_messages: number;
  total_user_messages?: number;
  assistant_messages: number;
  event_count: number;
  selected_sources: string[];
  stale: boolean;
  compression_preview?: CompressionPrediction;
  sources: {
    source: string;
    selected: boolean;
    detected?: boolean;
    sessions: number;
    total_sessions?: number;
    excluded_sessions?: number;
    user_messages: number;
    assistant_messages: number;
    events: number;
    total_events?: number;
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
    active_user_messages?: number;
    active_assistant_messages?: number;
    active_events?: number;
    excluded_user_messages?: number;
    excluded_events?: number;
    session_excluded?: boolean;
    exclusion_state?: "none" | "partial" | "full";
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
    pending_tasks?: PendingTask[];
    auto_imported?: boolean;
    imported_at?: string | null;
    usage?: null | {
      calls: number;
      prompt_tokens: number;
      completion_tokens: number;
      total_tokens: number;
      estimated: boolean;
      stages: {
        stage: string;
        label: string;
        calls: number;
        prompt_tokens: number;
        completion_tokens: number;
        estimated: boolean;
      }[];
    };
    scope?: {
      work_user_messages: number;
      excluded_user_messages: number;
      uncertain_user_messages: number;
    };
  };
};

export type GiteeRemoteIssue = {
  issue_id: number | string;
  title: string;
  description?: string;
  state: string;
  program_id?: number | null;
  program_name?: string;
  duration?: number;
  hours?: number;
  issue_date?: string;
  created_at?: string;
  updated_at?: string;
  url?: string;
  linked_task_id?: string;
};

export type GiteeVerifyItem = {
  task_id: string;
  title?: string;
  issue_id: string;
  status: string;
  verified: boolean;
  remote_title?: string;
  remote_state?: string;
  remote_duration?: number;
  url?: string;
  verify_error?: string;
};
