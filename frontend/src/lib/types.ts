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
  validator_result?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
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
