export type ProjectStatus =
  | 'CREATED'
  | 'INPUT_READY'
  | 'REQUIREMENT_DRAFTED'
  | 'BLOCKED_FOR_CLARIFICATION'
  | 'EVIDENCE_READY'
  | 'OUTLINE_READY'
  | 'DRAFT_GENERATING'
  | 'DRAFT_READY'
  | 'REVIEW_REQUIRED'
  | 'EXPORTABLE'
  | 'EXPORTED'
  | 'FAILED';

export interface Project {
  id: string;
  name: string;
  product_line?: string;
  industry?: string;
  description?: string;
  status: ProjectStatus;
  current_requirement_card_id?: string;
  current_outline_id?: string;
  current_draft_version?: number;
  created_at?: string;
  updated_at?: string;
}

export interface Document {
  id: string;
  project_id: string;
  filename: string;
  file_type: string;
  file_size_bytes?: number;
  doc_type: string;
  parse_status: 'pending' | 'parsing' | 'done' | 'failed' | 'parse_insufficient';
  metadata?: Record<string, unknown>;
  created_at?: string;
}

export interface HistoryLibraryRefreshStatus {
  status: 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | string;
  requested_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  last_success_at?: string | null;
  pending: boolean;
  error?: string | null;
  stats?: Record<string, unknown>;
  pipelines?: Record<string, HistoryLibraryRefreshPipelineStatus>;
}

export interface HistoryLibraryRefreshPipelineStatus {
  status: 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | string;
  requested_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  last_success_at?: string | null;
  pending?: boolean;
  error?: string | null;
  duration_seconds?: number | null;
}

export interface Chunk {
  id: string;
  chunk_index: number;
  chunk_type: string;
  content: string;
  heading_path?: string;
}

export interface ClarificationItem {
  item_id: string;
  field_name: string;
  priority?: string;
  reason?: string;
  question?: string;
  blocking?: boolean;
  status?: string;
  resolution?: unknown;
}

export interface RequirementCard {
  id: string;
  project_id: string;
  version: number;
  schema_version: string;
  content: Record<string, unknown>;
  missing_items: ClarificationItem[];
  blocking_items: ClarificationItem[];
  source_refs: Array<Record<string, unknown>>;
  confirmed_by_user: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface EvidenceCard {
  evidence_id: string;
  source_title: string;
  source_doc_id: string;
  heading_path: string[];
  summary: string;
  raw_content?: string;
  relevance_score: number;
  retrieval_reason?: string;
  reason_trace?: string[];
  retrieval_score_breakdown?: Record<string, number>;
  recommended_use?: string;
  risk_note?: string | null;
  metadata?: Record<string, unknown>;
}

export interface CaseCandidate {
  sample_id: string;
  file_name: string;
  score: number;
  reason?: string;
  reason_trace?: string[];
  profile?: string;
  library_track?: string;
  score_breakdown?: Record<string, number>;
  top_level_titles?: string[];
}

export interface EvidenceBundle {
  id: string;
  project_id: string;
  requirement_card_id: string;
  retrieval_version: number;
  content: {
    query?: string;
    filters?: Record<string, unknown>;
    retrieval_strategy?: string;
    case_candidates?: CaseCandidate[];
    results: EvidenceCard[];
    source_requirement_card_id?: string;
  };
  quality_score: number;
  created_at?: string;
}

export interface OutlineNode {
  section_id: string;
  title: string;
  purpose: string;
  mandatory: boolean;
  expected_evidence_types: string[];
  needs_human_review: boolean;
  children: OutlineNode[];
  section_class?: string;
  reuse_level?: string;
  generation_mode?: 'baseline' | 'reuse_first' | 'manual_only' | string;
  asset_required?: boolean;
  parameter_sensitive?: boolean;
  customer_specificity?: string;
}

export interface Outline {
  id: string;
  version: number;
  title: string;
  sections: OutlineNode[];
  validator_status: string;
  outline_status?: 'candidate' | 'approved';
  generation_strategy?: string;
  approval_required?: boolean;
  approved_by_user?: boolean;
  approved_at?: string;
  reviewer_notes?: string;
}

export interface Citation {
  evidence_id?: string;
  source_doc_id?: string;
  source_title?: string;
  heading_path?: string[];
  relevance_score?: number;
  type?: string;
  excerpt?: string;
  source_content?: string;
}

export interface RecommendedAsset {
  asset_id?: string;
  asset_type: string;
  title: string;
  display_title?: string | null;
  document_name: string;
  page_no?: number | null;
  heading_path?: string;
  caption?: string | null;
  reason?: string;
  preview_text: string;
  review_required: boolean;
  visual_role?: string | null;
  asset_uri?: string;
  score?: number;
  reason_trace?: string[];
  score_breakdown?: Record<string, number | string>;
  metadata?: Record<string, unknown>;
}

export interface RetrievalSectionTrace {
  sample_id?: string;
  file_name?: string;
  section_id?: string;
  section_path?: string;
  source_heading?: string;
  level?: number;
  score?: number;
  reason?: string;
  reason_trace?: string[];
  score_breakdown?: Record<string, number>;
}

export interface RetrievalBlockTrace {
  block_id?: string;
  source_title?: string;
  source_section_id?: string;
  section_path?: string;
  heading_path?: string[];
  selection_score?: number;
  selection_reasons?: string[];
  retrieval_reason?: string;
  retrieval_reason_trace?: string[];
  retrieval_score_breakdown?: Record<string, number>;
  selection_score_breakdown?: Record<string, number>;
}

export interface KnowledgeWikiPriorSummary {
  selected_block_count?: number;
  prior_hit_block_count?: number;
  prior_hit_ratio?: number;
  total_prior_boost?: number;
  max_prior_boost?: number;
  reason_hits?: Record<string, number>;
}

export interface RetrievalTokenBudget {
  section_material_tokens?: number;
  asset_tokens?: number;
  within_budget?: boolean;
}

export interface RetrievalSelectionReason {
  mode?: string;
  top_section_id?: string;
  top_section_score?: number;
  runner_up_score?: number;
  lead_score?: number;
  full_section_block_count?: number;
  full_section_within_budget?: boolean;
  reasons?: string[];
}

export interface RetrievalQueryIntents {
  title_text?: string;
  detail_text?: string;
  context_text?: string;
  title_terms?: string[];
  detail_terms?: string[];
  context_terms?: string[];
}

export interface ReuseRetrievalTrace {
  query?: string;
  query_intents?: RetrievalQueryIntents;
  knowledge_wiki_terms?: string[];
  knowledge_wiki_product_cards?: string[];
  knowledge_wiki_module_cards?: string[];
  section_candidates?: RetrievalSectionTrace[];
  scoped_sections?: RetrievalSectionTrace[];
}

export interface ReuseBlockLike {
  block_id?: string;
  block_type?: string;
  chunk_index?: number;
  source_title?: string;
  source_section_id?: string;
  section_path?: string;
  heading_path?: string[] | string;
  content_md?: string;
  retrieval_reason?: string;
  retrieval_reason_trace?: string[];
  retrieval_score_breakdown?: Record<string, number>;
}

export interface ReusePack {
  generation_mode?: string;
  reuse_level?: string;
  reusable_blocks?: ReuseBlockLike[];
  recommended_assets?: RecommendedAsset[];
  asset_candidates?: RecommendedAsset[];
  must_replace_fields?: string[];
  banned_terms?: string[];
  replacement_hints?: Record<string, unknown>;
  required_asset_placeholders?: Array<Record<string, unknown>>;
  parameter_candidates?: Record<string, unknown>;
  risk_flags?: string[];
  retrieval_trace?: ReuseRetrievalTrace;
}

export interface SectionGenerationDetails {
  effective_path?: string;
  retrieval_mode?: 'baseline_fallback' | 'section_pack' | 'full_section' | string;
  selected_sections?: RetrievalSectionTrace[];
  selected_blocks?: RetrievalBlockTrace[];
  knowledge_wiki_prior_summary?: KnowledgeWikiPriorSummary;
  selection_reason?: RetrievalSelectionReason;
  token_budget?: RetrievalTokenBudget;
  refinement_status?: string;
  refinement_fallback_reason?: string | null;
  refinement_error?: string | null;
  assembled_block_count?: number;
  selected_citation_ids?: string[];
}

export interface SectionValidatorResult {
  recommended_assets?: RecommendedAsset[];
  asset_candidates?: RecommendedAsset[];
  generation_mode?: string;
  reuse_pack?: ReusePack;
  generation_details?: SectionGenerationDetails;
  [key: string]: unknown;
}

export interface SectionDraft {
  id: string;
  project_id: string;
  draft_version: number;
  section_id: string;
  title: string;
  content_md: string;
  citation_refs: Citation[];
  assumptions: unknown[];
  global_param_snapshot: Record<string, unknown>;
  status: 'generated' | 'edited' | 'review_required' | 'approved' | 'rejected' | string;
  generation_mode?: string;
  reuse_level?: string;
  asset_required?: boolean;
  recommended_assets?: RecommendedAsset[];
  asset_candidates?: RecommendedAsset[];
  validator_result?: SectionValidatorResult;
  created_at?: string;
  updated_at?: string;
}

export interface JobProgress {
  stage?: string;
  completed_sections?: number;
  total_sections?: number;
  current_section_index?: number;
  current_section_id?: string;
  current_section_title?: string;
  elapsed_ms?: number;
  completed_materials?: number;
  total_materials?: number;
  current_sample_id?: string;
  current_file_name?: string;
}

export interface JobRead {
  id: string;
  project_id?: string | null;
  job_type: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | string;
  input_ref: Record<string, unknown>;
  output_ref: {
    progress?: JobProgress;
    error?: string;
    [key: string]: unknown;
  };
  retry_count: number;
  error_code?: string | null;
  trace_id: string;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface JobAccepted {
  job_id: string;
  status: string;
  resource_id?: string | null;
  next_poll: string;
}

export type MaterialRoute =
  | 'main_indexed'
  | 'review_pending'
  | 'holdout_eval'
  | 'conversion_required'
  | 'conversion_failed'
  | 'excluded';

export interface LibraryMaterial {
  sample_id: string;
  file_name: string;
  file_format: string;
  file_size_bytes: number;
  source_path: string;
  source_exists: boolean;
  phase_b_track?: string | null;
  suggested_track?: string | null;
  route: MaterialRoute;
  route_reason?: string | null;
  route_updated_at?: string | null;
  detected_profile?: string | null;
  ingestion_recommendation?: string | null;
  manifest_metrics: Record<string, unknown>;
  high_risk_content_flags: string[];
  document_id?: string | null;
  raw_document_id?: string | null;
  doc_type?: string | null;
  parse_status: string;
  parser_backend?: string | null;
  parse_gate_status?: string | null;
  parse_gate_reason?: string | null;
  chunk_count: number;
  indexed_chunk_count: number;
  skipped_chunk_count: number;
  figure_asset_count: number;
  storage_fallback_asset_count: number;
  image_count: number;
  table_count: number;
  quality_flags: string[];
}

export interface LibraryMaterialAsset {
  id: string;
  asset_type: string;
  title?: string | null;
  caption?: string | null;
  page_no?: number | null;
  asset_uri: string;
  reuse_mode: string;
  parse_confidence?: string | null;
  created_at?: string | null;
  visual_role?: string | null;
  width?: number | string | null;
  height?: number | string | null;
  source_ref?: string | null;
  source_section_id?: string | null;
  heading_path?: string | null;
  section_path?: string | null;
  storage_fallback: boolean;
  preserve_in_vector_db?: boolean;
  review_required: boolean;
  asset_audit_status?: string | null;
  asset_quality_score?: number | string | null;
  asset_audit_reasons?: string[];
  quality_flags: string[];
  raw_table_markdown?: string | null;
  table_profile?: Record<string, unknown> | null;
  semantic_summary?: Record<string, unknown> | null;
  context_before?: string | null;
  context_after?: string | null;
  metadata: Record<string, unknown>;
}

export interface LibraryMaterialChunk {
  id: string;
  chunk_index: number;
  chunk_type: string;
  heading_path?: string | null;
  token_count?: number | null;
  indexed: boolean;
  qdrant_point_id?: string | null;
  content_preview: string;
  metadata: Record<string, unknown>;
}

export interface LibraryMaterialDetail extends LibraryMaterial {
  assets: LibraryMaterialAsset[];
  chunks: LibraryMaterialChunk[];
}

export interface LibraryMaterialsResponse {
  items: LibraryMaterial[];
  summary: {
    total: number;
    route_counts: Record<string, number>;
    ingested_count: number;
    main_indexed_count: number;
    figure_asset_count: number;
    indexed_chunk_count: number;
  };
}

export interface ReviewTask {
  id: string;
  task_type: 'param_conflict' | 'figure_confirm' | 'content_review' | 'final_review';
  blocking_level: 'P0' | 'P1';
  payload: Record<string, unknown>;
  assignee_user_id?: string | null;
  status: 'open' | 'in_progress' | 'resolved' | 'rejected';
  created_at?: string;
  resolved_at?: string | null;
}

export interface ValidationReport {
  id: string;
  project_id: string;
  draft_version: number;
  outline_id?: string | null;
  requirement_card_id?: string | null;
  evidence_bundle_id?: string | null;
  status: string;
  errors: Array<{ code: string; message: string }>;
  warnings: Array<{ code: string; message: string }>;
  review_tasks_created: string[];
  created_at?: string;
}

export interface ExportData {
  id: string;
  project_id: string;
  draft_version: number;
  outline_id?: string | null;
  requirement_card_id?: string | null;
  evidence_bundle_id?: string | null;
  validation_report_id?: string | null;
  file_name: string;
  file_type: string;
  content_md: string;
  snapshot: Record<string, unknown>;
  status: string;
  created_at?: string;
}

// ApiResponse wrapper type to match standard layout
export interface ApiResponse<T = unknown> {
  code: number;
  message: string;
  data: T;
}
