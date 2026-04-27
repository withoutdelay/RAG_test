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
  parse_status: 'pending' | 'parsing' | 'done' | 'failed';
  metadata?: Record<string, unknown>;
  created_at?: string;
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
  recommended_use?: string;
  risk_note?: string | null;
  metadata?: Record<string, unknown>;
}

export interface CaseCandidate {
  sample_id: string;
  file_name: string;
  score: number;
  reason?: string;
  profile?: string;
  library_track?: string;
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

export interface SolutionSelectedProduct {
  role: string;
  series_code: string;
  name: string;
  family?: string;
  model_number?: string;
  topology?: string;
  rated_voltage?: string;
  rated_power_kw?: number | null;
  quantity: number;
  config?: string;
  vendor?: string;
  rationale?: string;
  source_material_key?: string;
}

export interface SolutionCatalogModelMatch {
  series_code: string;
  series_name?: string;
  model_number: string;
  rated_voltage?: string | null;
  rated_power_kw?: number | null;
  rated_current?: string | null;
  source_material_key?: string | null;
}

export interface SolutionCatalogInterfaceEntry {
  series_code: string;
  series_name?: string;
  interface_type: string;
  protocol?: string | null;
  signal_summary?: string[];
  source_material_key?: string | null;
}

export interface SolutionInterfacePlan {
  dcs_protocol?: string;
  io_allocation?: Record<string, number>;
  notes?: string;
  catalog_interface_entries?: SolutionCatalogInterfaceEntry[];
  catalog_source_material_keys?: string[];
}

export interface SolutionCompatibilityAction {
  source_family_code: string;
  target_family_code: string;
  relation_type: string;
  condition?: string | null;
  applies?: boolean;
  preferred_series_codes?: string[];
  optional_series_codes?: string[];
  covered_series_codes?: string[];
  added_series_codes?: string[];
  missing_series_codes?: string[];
}

export interface SolutionSelectionReason {
  matching_signals?: string[];
  why_selected?: string[];
  risk_flags?: string[];
  source_mode?: string;
  catalog_version?: string;
  catalog_model_matches?: SolutionCatalogModelMatch[];
  catalog_source_material_keys?: string[];
  compatibility_actions?: SolutionCompatibilityAction[];
  candidate_scores?: CatalogCandidateScore[];
}

export interface SolutionSnapshot {
  id: string;
  project_id: string;
  requirement_card_id?: string | null;
  version: number;
  status: string;
  solution_summary: string;
  selected_products: SolutionSelectedProduct[];
  interface_plan: SolutionInterfacePlan;
  key_constraints: string[];
  open_questions: string[];
  suggested_chapters: string[];
  selection_reason: SolutionSelectionReason;
  source_catalog_version?: string | null;
  confirmation_notes?: string | null;
  confirmed_by_user: boolean;
  confirmed_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface CatalogCandidateScore {
  series_code: string;
  series_name: string;
  score: number;
  reasons: string[];
}

export interface ProductCatalogConstraint {
  id?: string | null;
  constraint_type: string;
  condition: string;
  action: string;
  severity: string;
}

export interface ProductCatalogStandardConfig {
  id?: string | null;
  config_name: string;
  components: Array<Record<string, unknown>>;
  applicable_scenarios: string[];
  description?: string | null;
}

export interface ProductCatalogSeries {
  id?: string | null;
  catalog_version: string;
  is_published: boolean;
  role_type: string;
  family: string;
  family_code?: string | null;
  series_name: string;
  code: string;
  vendor?: string | null;
  description?: string | null;
  voltage_levels: string[];
  min_power_kw?: number | null;
  max_power_kw?: number | null;
  topology?: string | null;
  applicable_motors: string[];
  applicable_loads: string[];
  communication_protocols: string[];
  io_allocation: Record<string, number>;
  protection_features: string[];
  preferred_scenarios: string[];
  default_chapters: string[];
  standard_configs: ProductCatalogStandardConfig[];
  constraints: ProductCatalogConstraint[];
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProductCatalogFamilyAlias {
  id?: string | null;
  alias: string;
  alias_type: string;
  source?: string | null;
  sort_order: number;
}

export interface ProductCatalogCompatibility {
  id?: string | null;
  catalog_version: string;
  is_published: boolean;
  source_family_code: string;
  target_family_code: string;
  relation_type: string;
  condition?: string | null;
  description?: string | null;
  preferred_series_codes: string[];
  optional_series_codes: string[];
  sort_order: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProductCatalogFamily {
  id?: string | null;
  catalog_version: string;
  is_published: boolean;
  code: string;
  name: string;
  display_name?: string | null;
  description?: string | null;
  status: string;
  sort_order: number;
  parent_family_code?: string | null;
  aliases: ProductCatalogFamilyAlias[];
  compatibilities: ProductCatalogCompatibility[];
  series_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProductCatalogMaterial {
  id?: string | null;
  material_key: string;
  family_code?: string | null;
  material_type: string;
  document_name: string;
  source_path?: string | null;
  source_kind: string;
  availability_status: string;
  file_format?: string | null;
  file_size_bytes?: number | null;
  assigned_track?: string | null;
  suggested_track?: string | null;
  priority_tier?: string | null;
  tags: string[];
  notes?: string | null;
  details: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProductCatalogModel {
  id?: string | null;
  catalog_version: string;
  is_published: boolean;
  series_id?: string | null;
  series_code: string;
  model_number: string;
  rated_voltage?: string | null;
  rated_power_kw?: number | null;
  rated_current?: string | null;
  specs: Record<string, unknown>;
  source_material_key?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProductCatalogInterface {
  id?: string | null;
  catalog_version: string;
  is_published: boolean;
  series_id?: string | null;
  series_code: string;
  interface_type: string;
  protocol?: string | null;
  signal_spec: Record<string, unknown>;
  notes?: string | null;
  source_material_key?: string | null;
  sort_order: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface CatalogVersion {
  catalog_version: string;
  is_published: boolean;
  series_count: number;
}

export interface CatalogMaterialReadinessCheck {
  check_key: string;
  label: string;
  passed: boolean;
  required_count: number;
  actual_count: number;
  matched_family_codes: string[];
  matched_material_keys: string[];
  matched_document_names: string[];
  missing_detail?: string | null;
}

export interface CatalogMaterialReadinessPhaseAllowance {
  phase: string;
  label: string;
  allowed: boolean;
  reason: string;
}

export interface CatalogMaterialReadiness {
  catalog_version?: string | null;
  target_family_codes: string[];
  gate_passed: boolean;
  available_material_count: number;
  required_core_manual_family_count: number;
  checklist: CatalogMaterialReadinessCheck[];
  missing_items: string[];
  phase_allowances: CatalogMaterialReadinessPhaseAllowance[];
  material_type_counts: Record<string, number>;
  family_material_counts: Record<string, Record<string, number>>;
}

export interface CatalogMaterialManifestPreviewIssue {
  issue_type: string;
  severity: string;
  message: string;
  material_key?: string | null;
  document_name?: string | null;
}

export interface CatalogMaterialManifestPreviewEntry {
  material_key: string;
  document_name: string;
  family_code?: string | null;
  material_type: string;
  availability_status: string;
  source_kind: string;
  source_path?: string | null;
  source_path_exists?: boolean | null;
  explicit_family_code: boolean;
  explicit_material_type: boolean;
  explicit_availability_status: boolean;
  existing_material: boolean;
  duplicate_material_key: boolean;
  non_synthetic_source: boolean;
  counted_toward_gate: boolean;
  issues: string[];
}

export interface CatalogMaterialManifestPreview {
  manifest_path: string;
  source_kind: string;
  replace_existing: boolean;
  import_blocked: boolean;
  total_entry_count: number;
  unique_material_key_count: number;
  duplicate_material_key_count: number;
  existing_material_count: number;
  new_material_count: number;
  would_import_count: number;
  would_skip_existing_count: number;
  would_replace_existing_count: number;
  gate_ready_material_count: number;
  non_synthetic_material_count: number;
  inferred_family_count: number;
  inferred_material_type_count: number;
  inferred_status_count: number;
  missing_source_path_count: number;
  missing_source_file_count: number;
  family_counts: Record<string, number>;
  material_type_counts: Record<string, number>;
  availability_status_counts: Record<string, number>;
  source_kind_counts: Record<string, number>;
  gate_ready_family_material_counts: Record<string, Record<string, number>>;
  duplicate_material_keys: string[];
  issues: CatalogMaterialManifestPreviewIssue[];
  preview_entries: CatalogMaterialManifestPreviewEntry[];
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
}

export interface RetrievalBlockTrace {
  block_id?: string;
  source_title?: string;
  source_section_id?: string;
  section_path?: string;
  heading_path?: string[];
  selection_score?: number;
  selection_reasons?: string[];
}

export interface RetrievalTokenBudget {
  section_material_tokens?: number;
  asset_tokens?: number;
  within_budget?: boolean;
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
}

export interface ReusePack {
  generation_mode?: string;
  reuse_level?: string;
  reusable_blocks?: ReuseBlockLike[];
  recommended_assets?: RecommendedAsset[];
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
  token_budget?: RetrievalTokenBudget;
  refinement_status?: string;
  refinement_fallback_reason?: string | null;
  refinement_error?: string | null;
  assembled_block_count?: number;
  selected_citation_ids?: string[];
}

export interface ReviewResolutionTraceEntry {
  task_id?: string;
  task_type?: string;
  code?: string | null;
  message?: string | null;
  section_id?: string | null;
  section_title?: string | null;
  level?: string | null;
  blocking_level?: string | null;
  status?: string | null;
  resolution?: unknown;
  resolved_at?: string | null;
  suggested_action?: string | null;
  details?: Record<string, unknown>;
}

export interface SectionValidatorResult {
  recommended_assets?: RecommendedAsset[];
  generation_mode?: string;
  reuse_pack?: ReusePack;
  generation_details?: SectionGenerationDetails;
  review_resolution_trace?: ReviewResolutionTraceEntry[];
  review_resolutions?: Record<string, unknown>;
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
  validator_result?: SectionValidatorResult;
  created_at?: string;
  updated_at?: string;
}

export interface ReviewTask {
  id: string;
  task_type: 'param_conflict' | 'figure_confirm' | 'content_review' | 'final_review';
  blocking_level: 'P0' | 'P1';
  payload: ReviewTaskPayload;
  assignee_user_id?: string | null;
  status: 'open' | 'in_progress' | 'resolved' | 'rejected';
  created_at?: string;
  resolved_at?: string | null;
}

export interface ReviewTaskPayload {
  code?: string;
  message?: string;
  section_id?: string | null;
  section_title?: string | null;
  draft_version?: number;
  outline_id?: string | null;
  title?: string;
  param_name?: string;
  values?: string[];
  level?: string;
  suggested_action?: string | null;
  details?: Record<string, unknown>;
  signature?: string;
  resolution?: unknown;
  [key: string]: unknown;
}

export interface ValidationIssue {
  code: string;
  message: string;
  level?: string;
  section_id?: string | null;
  section_title?: string | null;
  suggested_action?: string | null;
  details?: Record<string, unknown>;
}

export interface ValidationReport {
  id: string;
  project_id: string;
  draft_version: number;
  outline_id?: string | null;
  requirement_card_id?: string | null;
  evidence_bundle_id?: string | null;
  status: string;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
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
