/**
 * Utility functions extracted from SectionBlock.tsx for reuse across editor components.
 */

import type {
  Citation,
  EvidenceCard,
  RecommendedAsset,
  ReuseBlockLike,
  ReuseRetrievalTrace,
  SectionDraft,
  SectionGenerationDetails,
  SectionValidatorResult,
} from '@/lib/types';
import { markdownTableCandidate, mergeMarkdownTableBlocks } from './sectionBlockHelpers';


export function normalizeHeadingPath(value: string[] | string | undefined): string {
  if (!value) return '';
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean).join(' > ');
  }
  return String(value)
    .split('>')
    .map((item) => item.trim())
    .filter(Boolean)
    .join(' > ');
}

export function metadataNumber(metadata: Record<string, unknown>, key: string): number {
  const value = metadata[key];
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

export function renderCitationLabel(citation: Citation): string {
  const headingPath = citation.heading_path?.join(' > ');
  return [citation.source_title, headingPath].filter(Boolean).join(' / ');
}

export function getStatusVariant(status: SectionDraft['status']): 'default' | 'secondary' | 'warning' | 'success' | 'destructive' {
  if (status === 'approved') return 'success';
  if (status === 'review_required') return 'warning';
  if (status === 'rejected') return 'destructive';
  if (status === 'edited') return 'secondary';
  return 'default';
}

export function formatIntentLabel(key: string): string {
  if (key === 'title_text') return 'Title Intent';
  if (key === 'detail_text') return 'Detail Intent';
  if (key === 'context_text') return 'Context Intent';
  return key;
}

export function formatTraceMetric(value: number | string | undefined, digits = 2): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toFixed(digits);
  }
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  return 'n/a';
}

export function formatSelectionReason(reason: string): string {
  const normalized = String(reason || '').trim();
  if (!normalized) return '';
  const [rawKey, rawValue] = normalized.split('=', 2);
  const value = rawValue?.trim();
  switch (rawKey) {
    case 'generation_mode_not_reuse_first':
      return '当前章节不在 reuse_first 模式';
    case 'no_reusable_blocks':
      return '当前没有可复用块';
    case 'top_section_score':
      return `Top1 章节分数 ${formatTraceMetric(value, 4)}`;
    case 'top_section_id':
      return `Top1 章节 ID ${value || 'unknown'}`;
    case 'runner_up_score':
      return `Top2 章节分数 ${formatTraceMetric(value, 4)}`;
    case 'lead_score':
      return `Top1 与 Top2 分差 ${formatTraceMetric(value, 4)}`;
    case 'full_section_block_count':
      return `Top1 对齐块数 ${value || '0'}`;
    case 'full_section_within_budget':
      return value === 'True' || value === 'true' ? '整章材料在 token 预算内' : '整章材料超出 token 预算';
    case 'selected_baseline_fallback':
      return '本次走 baseline fallback';
    case 'selected_full_section':
      return '本次走 full_section';
    case 'section_pack_due_to_missing_top_section':
      return '降级到 section_pack：没有稳定的 Top1 章节';
    case 'section_pack_due_to_low_top_section_score':
      return '降级到 section_pack：Top1 章节分数不够高';
    case 'section_pack_due_to_low_section_lead':
      return '降级到 section_pack：Top1 与 Top2 分差不足';
    case 'section_pack_due_to_missing_aligned_blocks':
      return '降级到 section_pack：Top1 章节下没有对齐块';
    case 'section_pack_due_to_token_budget':
      return '降级到 section_pack：整章材料超出 token 预算';
    case 'selected_section_pack':
      return '本次走 section_pack';
    case 'single_section_candidate':
      return '只有一个章节候选';
    case 'no_top_section_block_alignment':
      return 'Top1 章节没有找到对齐块';
    case 'no_section_candidate':
      return '当前没有章节候选';
    default:
      return normalized.replace(/_/g, ' ');
  }
}

export function formatKnowledgeWikiPriorLabel(key: string): string {
  switch (key) {
    case 'knowledge_wiki_product_match':
      return '产品族命中';
    case 'knowledge_wiki_module_match':
      return '模块命中';
    case 'knowledge_wiki_product_section_prior':
      return '产品族章节先验';
    case 'knowledge_wiki_product_equipment_prior':
      return '产品族设备先验';
    default:
      return key.replace(/^knowledge_wiki_/, '').replace(/_/g, ' ');
  }
}

export function buildKnowledgeWikiPriorBreakdownText(
  breakdown: Record<string, number> | undefined,
): string {
  if (!breakdown) return '';
  const priorTotal = typeof breakdown.knowledge_wiki_prior_total === 'number' ? breakdown.knowledge_wiki_prior_total : 0;
  if (!(priorTotal > 0)) return '';
  const parts = [
    `AI Wiki prior +${priorTotal.toFixed(2)}`,
    ...Object.entries(breakdown)
      .filter(([key, value]) => key !== 'knowledge_wiki_prior_total' && key !== 'base_score' && key !== 'final_score' && typeof value === 'number' && value > 0)
      .map(([key, value]) => `${formatKnowledgeWikiPriorLabel(key)} +${value.toFixed(2)}`),
  ];
  return parts.join(' / ');
}

export function buildSectionCandidateBreakdownText(
  breakdown: Record<string, number> | undefined,
): string {
  if (!breakdown) return '';
  const parts: string[] = [];
  if (typeof breakdown.base === 'number' && breakdown.base > 0) {
    parts.push(`base ${breakdown.base.toFixed(2)}`);
  }
  if (typeof breakdown.hybrid_rrf === 'number' && breakdown.hybrid_rrf > 0) {
    parts.push(`rrf +${breakdown.hybrid_rrf.toFixed(2)}`);
  }
  if (typeof breakdown.hybrid_rerank === 'number' && breakdown.hybrid_rerank > 0) {
    parts.push(`rerank +${breakdown.hybrid_rerank.toFixed(2)}`);
  }
  if (typeof breakdown.semantic === 'number' && breakdown.semantic > 0) {
    parts.push(`semantic ${breakdown.semantic.toFixed(2)}`);
  }
  return parts.join(' / ');
}

export function buildBlockRetrievalBreakdownText(
  breakdown: Record<string, number> | undefined,
): string {
  if (!breakdown) return '';
  const parts: string[] = [];
  if (typeof breakdown.base === 'number' && breakdown.base > 0) {
    parts.push(`base ${breakdown.base.toFixed(2)}`);
  }
  if (typeof breakdown.hybrid_rrf === 'number' && breakdown.hybrid_rrf > 0) {
    parts.push(`rrf +${breakdown.hybrid_rrf.toFixed(2)}`);
  }
  if (typeof breakdown.hybrid_rerank === 'number' && breakdown.hybrid_rerank > 0) {
    parts.push(`rerank +${breakdown.hybrid_rerank.toFixed(2)}`);
  }
  return parts.join(' / ');
}

// --- Editable block types ---

export type EditableBlockKind = 'heading' | 'table' | 'list' | 'quote' | 'paragraph' | 'placeholder';

export interface EditableBlock {
  id: string;
  kind: EditableBlockKind;
  content: string;
}

export interface PreviewMarkdownSegment {
  kind: 'markdown';
  markdown: string;
}

export interface PreviewAssetSegment {
  kind: 'asset';
  assetType: string;
  assetId: string;
  label?: string;
}

export type PreviewSegment = PreviewMarkdownSegment | PreviewAssetSegment;

export const INLINE_ASSET_PLACEHOLDER_PATTERN = /\[\[ASSET:([A-Z_]+):([^\]]+)\]\]/g;

export function classifyMarkdownBlock(content: string): EditableBlockKind {
  const trimmed = content.trim();
  const lines = trimmed.split('\n').filter((line) => line.trim());
  if (!trimmed) return 'paragraph';
  if (/^\s*(?:[-*+]\s+)?\[\[ASSET:[A-Z_]+:[^\]]+\]\]\s*(.*)?$/.test(trimmed)) return 'placeholder';
  if (/^#{1,6}\s+/.test(trimmed)) return 'heading';
  if (lines.length > 1 && lines.every((line) => line.trim().startsWith('|'))) return 'table';
  if (lines.every((line) => /^(-|\*|\d+\.)\s+/.test(line.trim()))) return 'list';
  if (lines.every((line) => line.trim().startsWith('>'))) return 'quote';
  return 'paragraph';
}

export function parseMarkdownBlocks(markdown: string): EditableBlock[] {
  const normalized = markdown.replace(/\r\n/g, '\n').trim();
  if (!normalized) return [];
  return normalized.split(/\n{2,}/).map((content, index) => ({
    id: `block-${index}-${content.slice(0, 16)}`,
    kind: classifyMarkdownBlock(content),
    content,
  }));
}

export function stringifyMarkdownBlocks(blocks: EditableBlock[]): string {
  return blocks
    .map((block) => block.content.trim())
    .filter(Boolean)
    .join('\n\n');
}

export function parseAssetPlaceholder(token: string): { assetId: string; assetType: string } | null {
  const match = token.match(/\[\[ASSET:([A-Z_]+):([^\]]+)\]\]/);
  if (!match) return null;
  return {
    assetType: match[1],
    assetId: match[2],
  };
}

export function buildAssetPlaceholder(asset: RecommendedAsset): string | null {
  if (!asset.asset_id) {
    return null;
  }
  const normalizedType = asset.asset_type === 'formula_candidate' ? 'FORMULA' : asset.asset_type.toUpperCase();
  return `[[ASSET:${normalizedType}:${asset.asset_id}]]`;
}

export function blockLabel(kind: EditableBlockKind): string {
  if (kind === 'heading') return '标题';
  if (kind === 'table') return '表格';
  if (kind === 'list') return '列表';
  if (kind === 'quote') return '说明框';
  if (kind === 'placeholder') return '图表引用';
  return '正文段';
}

export function buildPreviewSegments(content: string): PreviewSegment[] {
  const segments: PreviewSegment[] = [];
  const lines = content.split('\n');
  const buffer: string[] = [];
  const flushBuffer = () => {
    const markdown = buffer.join('\n').trim();
    if (markdown) {
      segments.push({ kind: 'markdown', markdown });
    }
    buffer.length = 0;
  };

  for (const line of lines) {
    const match = line.match(/^\s*(?:[-*+]\s+)?\[\[ASSET:([A-Z_]+):([^\]]+)\]\]\s*(.*)$/);
    if (match) {
      flushBuffer();
      segments.push({
        kind: 'asset',
        assetType: match[1],
        assetId: match[2],
        label: match[3]?.trim() || undefined,
      });
      continue;
    }

    const inlineMatches = [...line.matchAll(INLINE_ASSET_PLACEHOLDER_PATTERN)];
    if (inlineMatches.length > 0) {
      let cursor = 0;
      for (const inlineMatch of inlineMatches) {
        const matchIndex = inlineMatch.index ?? 0;
        let beforeText = line.slice(cursor, matchIndex);
        let nextCursor = matchIndex + inlineMatch[0].length;
        const followingPunctuation = line.slice(nextCursor).match(/^\s*([:：,，.。;；])/);
        if (followingPunctuation) {
          beforeText = `${beforeText.trimEnd()}${followingPunctuation[1]}`;
          nextCursor += followingPunctuation[0].length;
        }
        if (beforeText.trim()) {
          buffer.push(beforeText.trimEnd());
        }
        flushBuffer();
        segments.push({
          kind: 'asset',
          assetType: inlineMatch[1],
          assetId: inlineMatch[2],
        });
        cursor = nextCursor;
      }
      const trailingText = line.slice(cursor);
      if (trailingText.trim()) {
        buffer.push(trailingText.trimStart());
      }
      continue;
    }
    buffer.push(line);
  }

  flushBuffer();
  return segments;
}

// --- Data accessors ---

export function getReusableBlocks(section: SectionDraft): ReuseBlockLike[] {
  const reusePack = section.validator_result?.reuse_pack;
  return Array.isArray(reusePack?.reusable_blocks) ? reusePack.reusable_blocks : [];
}

export function getSectionValidatorResult(section: SectionDraft): SectionValidatorResult | undefined {
  return section.validator_result;
}

export function getSectionAssetCandidates(section: SectionDraft): RecommendedAsset[] {
  return (
    section.asset_candidates ||
    section.validator_result?.asset_candidates ||
    section.validator_result?.reuse_pack?.asset_candidates ||
    []
  );
}

export function getGenerationDetails(section: SectionDraft): SectionGenerationDetails | undefined {
  return getSectionValidatorResult(section)?.generation_details;
}

export function getReuseTrace(section: SectionDraft): ReuseRetrievalTrace | undefined {
  return getSectionValidatorResult(section)?.reuse_pack?.retrieval_trace;
}

export function reusableBlockOrder(block: ReuseBlockLike, fallbackIndex: number): number {
  if (typeof block.chunk_index === 'number' && Number.isFinite(block.chunk_index)) {
    return block.chunk_index;
  }
  return 10000 + fallbackIndex;
}

export function isFilteredRecommendedAsset(asset: RecommendedAsset, sectionTitle = ''): boolean {
  const metadata = (asset.metadata || {}) as Record<string, unknown>;
  if (asset.asset_type === 'table' && metadata.storage_fallback === true && !metadata.raw_table_markdown && !metadata.table_profile) {
    return true;
  }
  if (!isVisualAssetUtil(asset)) return false;
  const visualRole = String(asset.visual_role || metadata.visual_role || '').toLowerCase();
  const auditStatus = String(metadata.asset_audit_status || '').toLowerCase();
  const qualityScore = Number(metadata.asset_quality_score || 0);
  if (auditStatus === 'rejected') return true;
  if (visualRole === 'page_furniture' || visualRole === 'asset_fragment' || visualRole === 'text_fragment') return true;
  if (metadata.preserve_in_vector_db === false) return true;

  const width = metadataNumber(metadata, 'width') || metadataNumber(metadata, 'image_width') || metadataNumber(metadata, 'pixel_width');
  const height = metadataNumber(metadata, 'height') || metadataNumber(metadata, 'image_height') || metadataNumber(metadata, 'pixel_height');
  const signalText = [
    sectionTitle,
    asset.title,
    asset.display_title,
    asset.heading_path,
    asset.preview_text,
    metadata.context_before,
    metadata.context_after,
    metadata.section_path,
  ].join(' ');
  if (/总体方案|主回路|一次/.test(sectionTitle) && (visualRole === 'product_photo' || /(产品照片|设备照片|实拍|photo)/i.test(signalText))) {
    return true;
  }
  if (/总体方案/.test(sectionTitle) && /(房间布置|外形图|尺寸图|间距示意|检修通道|旧\s*SFC|输入输出变压器利旧)/i.test(signalText)) {
    return true;
  }
  if (auditStatus === 'review_pending' && qualityScore > 0 && qualityScore < 0.45) return true;
  if (width <= 0 || height <= 0) return false;
  const area = width * height;
  return Math.min(width, height) < 80 || (area < 12000 && Math.max(width, height) < 160);
}

// Renamed to avoid conflict with sectionBlockHelpers.isVisualAsset
export function isVisualAssetUtil(asset?: RecommendedAsset): boolean {
  return asset?.asset_type === 'figure' || asset?.asset_type === 'formula_candidate';
}

export function findMatchingReusableBlocks(
  section: SectionDraft,
  citationOrAsset: { evidence_id?: string; source_title?: string; heading_path?: string[] | string; document_name?: string },
): ReuseBlockLike[] {
  const targetSource = citationOrAsset.source_title || citationOrAsset.document_name || '';
  const normalizedHeading = normalizeHeadingPath(citationOrAsset.heading_path);
  const exactMatch = citationOrAsset.evidence_id
    ? getReusableBlocks(section).find(
        (block) => citationOrAsset.evidence_id && block.block_id && block.block_id === citationOrAsset.evidence_id && block.content_md,
      )
    : undefined;

  if (exactMatch?.content_md) {
    return [exactMatch];
  }

  return getReusableBlocks(section)
    .map((block, index) => ({ block, index }))
    .filter(
      ({ block }) =>
        Boolean(block.content_md) &&
        (block.source_title || '') === targetSource &&
        normalizeHeadingPath(block.heading_path) === normalizedHeading,
    )
    .sort((left, right) => reusableBlockOrder(left.block, left.index) - reusableBlockOrder(right.block, right.index))
    .map(({ block }) => block)
    .filter((block, index, blocks) => {
      const signature = `${block.block_id || ''}|${block.content_md || ''}`;
      return blocks.findIndex((candidate) => `${candidate.block_id || ''}|${candidate.content_md || ''}` === signature) === index;
    });
}

export function findReusableBlockContent(
  section: SectionDraft,
  citationOrAsset: { evidence_id?: string; source_title?: string; heading_path?: string[] | string; document_name?: string },
): string | undefined {
  const matches = findMatchingReusableBlocks(section, citationOrAsset);
  return matches[0]?.content_md;
}

export function findReusableBlockPreviewContent(section: SectionDraft, asset: RecommendedAsset): string | undefined {
  const matches = findMatchingReusableBlocks(section, {
    document_name: asset.document_name,
    heading_path: asset.heading_path,
  }).filter((block) => block.content_md?.trim());

  if (!matches.length) {
    return undefined;
  }

  if (asset.asset_type !== 'table') {
    return matches[0]?.content_md?.trim();
  }

  const tableBlocks = matches.filter((block) => block.block_type === 'table' || markdownTableCandidate(block.content_md || ''));
  if (!tableBlocks.length) {
    return matches[0]?.content_md?.trim();
  }
  if (tableBlocks.length === 1) {
    return tableBlocks[0].content_md?.trim();
  }

  return mergeMarkdownTableBlocks(tableBlocks) || tableBlocks.map((block) => block.content_md?.trim()).filter(Boolean).join('\n\n');
}

export function getAssetTableMarkdown(asset?: RecommendedAsset): string | undefined {
  if (!asset || asset.asset_type !== 'table') {
    return undefined;
  }
  const metadata = (asset.metadata || {}) as Record<string, unknown>;
  for (const key of ['raw_table_markdown', 'table_markdown', 'reconstructed_table_markdown']) {
    const value = metadata[key];
    if (typeof value === 'string' && markdownTableCandidate(value)) {
      return value.trim();
    }
  }
  return undefined;
}

export function resolveCitationSourceContent(
  section: SectionDraft,
  citation: Citation,
  evidenceMap: Record<string, EvidenceCard>,
): string {
  if (citation.source_content?.trim()) {
    return citation.source_content;
  }
  const evidenceId = citation.evidence_id || '';
  if (evidenceId && evidenceMap[evidenceId]?.raw_content?.trim()) {
    return evidenceMap[evidenceId].raw_content || '';
  }
  return findReusableBlockContent(section, citation) || citation.excerpt || '';
}
