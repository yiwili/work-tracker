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

export type Report = {
  date: string;
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
export type GiteeEnterpriseConfig = {
  enterprise_id: number;
  rules: GiteeProjectRule[];
  include_details: boolean;
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
  llm_provider: "deepseek" | "glm" | "glm-coding" | "ccswitch" | "custom";
  llm_protocol: "openai" | "anthropic";
  ccswitch_config_path: string;
  auto_import_analysis: boolean;
  auto_apply_allocation: boolean;
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
};

export type DailyCollectionCount = {
  date: string;
  sessions: number;
  user_messages: number;
  assistant_messages: number;
  git_events: number;
  events: number;
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
  user_messages: number;
  assistant_messages: number;
  event_count: number;
  selected_sources: string[];
  stale: boolean;
  sources: {
    source: string;
    selected: boolean;
    detected?: boolean;
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
