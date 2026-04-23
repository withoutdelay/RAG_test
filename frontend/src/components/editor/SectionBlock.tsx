'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  ExternalLink,
  Eye,
  FileCode2,
  FileText,
  GalleryVerticalEnd,
  Layers3,
  Loader2,
  RotateCcw,
  Save,
  Sparkles,
  Target,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import api, { buildAssetContentUrl, getApiErrorMessage } from '@/lib/api';
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
import { toast } from 'sonner';
import { MarkdownArticle } from './MarkdownArticle';
import { isVisualAsset, markdownTableCandidate, mergeMarkdownTableBlocks } from './sectionBlockHelpers';

type EditableBlockKind = 'heading' | 'table' | 'list' | 'quote' | 'paragraph' | 'placeholder';

interface EditableBlock {
  id: string;
  kind: EditableBlockKind;
  content: string;
}

interface PreviewMarkdownSegment {
  kind: 'markdown';
  markdown: string;
}

interface PreviewAssetSegment {
  kind: 'asset';
  assetType: string;
  assetId: string;
  label?: string;
}

type PreviewSegment = PreviewMarkdownSegment | PreviewAssetSegment;

function getStatusVariant(status: SectionDraft['status']): 'default' | 'secondary' | 'warning' | 'success' | 'destructive' {
  if (status === 'approved') return 'success';
  if (status === 'review_required') return 'warning';
  if (status === 'rejected') return 'destructive';
  if (status === 'edited') return 'secondary';
  return 'default';
}

function renderCitationLabel(citation: Citation): string {
  const headingPath = citation.heading_path?.join(' > ');
  return [citation.source_title, headingPath].filter(Boolean).join(' / ');
}

function normalizeHeadingPath(value: string[] | string | undefined): string {
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

function formatIntentLabel(key: string): string {
  if (key === 'title_text') return 'Title Intent';
  if (key === 'detail_text') return 'Detail Intent';
  if (key === 'context_text') return 'Context Intent';
  return key;
}

function formatTraceMetric(value: number | string | undefined, digits = 2): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toFixed(digits);
  }
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  return 'n/a';
}

function formatSelectionReason(reason: string): string {
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

function formatKnowledgeWikiPriorLabel(key: string): string {
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

function buildKnowledgeWikiPriorBreakdownText(
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

function buildSectionCandidateBreakdownText(
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

function buildBlockRetrievalBreakdownText(
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

function classifyMarkdownBlock(content: string): EditableBlockKind {
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

function parseMarkdownBlocks(markdown: string): EditableBlock[] {
  const normalized = markdown.replace(/\r\n/g, '\n').trim();
  if (!normalized) return [];
  return normalized.split(/\n{2,}/).map((content, index) => ({
    id: `block-${index}-${content.slice(0, 16)}`,
    kind: classifyMarkdownBlock(content),
    content,
  }));
}

function stringifyMarkdownBlocks(blocks: EditableBlock[]): string {
  return blocks
    .map((block) => block.content.trim())
    .filter(Boolean)
    .join('\n\n');
}

function parseAssetPlaceholder(token: string): { assetId: string; assetType: string } | null {
  const match = token.match(/\[\[ASSET:([A-Z_]+):([^\]]+)\]\]/);
  if (!match) return null;
  return {
    assetType: match[1],
    assetId: match[2],
  };
}

function buildAssetPlaceholder(asset: RecommendedAsset): string | null {
  if (!asset.asset_id) {
    return null;
  }
  const normalizedType = asset.asset_type === 'formula_candidate' ? 'FORMULA' : asset.asset_type.toUpperCase();
  return `[[ASSET:${normalizedType}:${asset.asset_id}]]`;
}

function blockLabel(kind: EditableBlockKind): string {
  if (kind === 'heading') return '标题';
  if (kind === 'table') return '表格';
  if (kind === 'list') return '列表';
  if (kind === 'quote') return '说明框';
  if (kind === 'placeholder') return '图表引用';
  return '正文段';
}

function buildPreviewSegments(content: string): PreviewSegment[] {
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
    buffer.push(line);
  }

  flushBuffer();
  return segments;
}

function getReusableBlocks(section: SectionDraft): ReuseBlockLike[] {
  const reusePack = section.validator_result?.reuse_pack;
  return Array.isArray(reusePack?.reusable_blocks) ? reusePack.reusable_blocks : [];
}

function getSectionValidatorResult(section: SectionDraft): SectionValidatorResult | undefined {
  return section.validator_result;
}

function getGenerationDetails(section: SectionDraft): SectionGenerationDetails | undefined {
  return getSectionValidatorResult(section)?.generation_details;
}

function getReuseTrace(section: SectionDraft): ReuseRetrievalTrace | undefined {
  return getSectionValidatorResult(section)?.reuse_pack?.retrieval_trace;
}

function reusableBlockOrder(block: ReuseBlockLike, fallbackIndex: number): number {
  if (typeof block.chunk_index === 'number' && Number.isFinite(block.chunk_index)) {
    return block.chunk_index;
  }
  return 10000 + fallbackIndex;
}

function findMatchingReusableBlocks(
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

function findReusableBlockContent(
  section: SectionDraft,
  citationOrAsset: { evidence_id?: string; source_title?: string; heading_path?: string[] | string; document_name?: string },
): string | undefined {
  const matches = findMatchingReusableBlocks(section, citationOrAsset);
  return matches[0]?.content_md;
}

function findReusableBlockPreviewContent(section: SectionDraft, asset: RecommendedAsset): string | undefined {
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

function resolveCitationSourceContent(
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

function AssetPreviewCard({
  asset,
  placeholderLabel,
  section,
  embedded = false,
}: {
  asset?: RecommendedAsset;
  placeholderLabel?: string;
  section: SectionDraft;
  embedded?: boolean;
}) {
  const [imageState, setImageState] = useState<{ assetId: string; failed: boolean }>({
    assetId: asset?.asset_id || '',
    failed: false,
  });
  const visualAsset = isVisualAsset(asset);
  const supportContent = asset && !visualAsset ? findReusableBlockPreviewContent(section, asset) : undefined;
  const previewMarkdown = supportContent?.trim() || asset?.preview_text || '';
  const title = placeholderLabel || asset?.display_title || asset?.title || asset?.caption || '已插入图表引用';
  const currentAssetId = asset?.asset_id || '';
  const imageFailed = imageState.assetId === currentAssetId ? imageState.failed : false;
  const assetContentUrl = asset?.asset_id && visualAsset ? buildAssetContentUrl(asset.asset_id) : null;
  const summaryText = (asset?.caption || asset?.preview_text || '').trim();
  const metadata = (asset?.metadata || {}) as Record<string, unknown>;
  const breakdown = asset?.score_breakdown || (metadata.retrieval_score_breakdown as Record<string, number | string> | undefined);
  const visualBackend =
    typeof breakdown?.visual_backend === 'string'
      ? breakdown.visual_backend
      : typeof metadata.visual_backend === 'string'
        ? metadata.visual_backend
        : '';
  const visualSource =
    typeof breakdown?.visual_source === 'string'
      ? breakdown.visual_source
      : typeof metadata.visual_source === 'string'
        ? metadata.visual_source
        : '';
  const assetTraceText = Array.isArray(asset?.reason_trace) && asset.reason_trace.length > 0 ? asset.reason_trace.slice(0, 3).join(' / ') : '';
  const breakdownSummary =
    breakdown && typeof breakdown === 'object'
      ? [
          typeof breakdown.textual === 'number' ? `text ${breakdown.textual.toFixed(2)}` : null,
          typeof breakdown.visual === 'number' ? `visual ${breakdown.visual.toFixed(2)}` : null,
          typeof breakdown.structural === 'number' ? `struct ${breakdown.structural.toFixed(2)}` : null,
          typeof breakdown.final === 'number' ? `final ${breakdown.final.toFixed(2)}` : null,
        ]
          .filter(Boolean)
          .join(' / ')
      : '';

  return (
    <div className={`rounded-2xl border ${embedded ? 'border-emerald-200 bg-emerald-50/70' : asset?.review_required ? 'border-amber-300 bg-amber-50/70' : 'border-slate-200 bg-slate-50/70'} p-4`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className={embedded ? 'border-emerald-200 bg-white text-emerald-700' : ''}>
          {asset?.asset_type || 'asset'}
        </Badge>
        <span className="text-sm font-semibold text-slate-900">{title}</span>
        {asset?.page_no ? <span className="text-xs text-slate-500">Page {asset.page_no}</span> : null}
      </div>
      {(asset?.document_name || asset?.heading_path) && (
        <p className="mt-2 text-xs text-slate-500">
          {[asset?.document_name, asset?.heading_path].filter(Boolean).join(' / ')}
        </p>
      )}
      {visualAsset && assetContentUrl && !imageFailed ? (
        <div className="mt-3 overflow-hidden rounded-[1.25rem] border border-white/80 bg-white/90 p-3 shadow-[0_20px_50px_-28px_rgba(15,23,42,0.45)]">
          <div className="relative overflow-hidden rounded-[1rem] border border-slate-200/80 bg-slate-50">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={assetContentUrl}
              alt={title}
              loading="lazy"
              className={`block w-full bg-transparent object-contain ${embedded ? 'max-h-[300px]' : 'max-h-[420px]'}`}
              onError={() => setImageState({ assetId: currentAssetId, failed: true })}
            />
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-slate-50/85" />
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-100 bg-emerald-50 px-2.5 py-1 font-medium text-emerald-700">
                <Eye className="h-3.5 w-3.5" />
                原图预览
              </span>
              {asset?.visual_role ? <span>{asset.visual_role}</span> : null}
            </div>
            <a
              href={assetContentUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-slate-600 transition hover:text-slate-900"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              新窗口查看
            </a>
          </div>
          {summaryText ? (
            <p className="mt-3 text-sm leading-6 text-slate-700 whitespace-pre-wrap">{summaryText}</p>
          ) : (
            <p className="mt-3 text-xs text-slate-500">当前展示的是原图预览，导出时会继续引用这张历史图形资产。</p>
          )}
        </div>
      ) : (
        <div className="mt-3 rounded-xl border border-white/70 bg-white/80 p-3">
          {previewMarkdown ? (
            markdownTableCandidate(previewMarkdown) ? (
              <MarkdownArticle markdown={previewMarkdown} compact />
            ) : (
              <p className="text-sm leading-6 text-slate-700 whitespace-pre-wrap">{previewMarkdown}</p>
            )
          ) : (
            <p className="text-sm text-slate-500">该位置已绑定图表资产，导出时会继续引用对应资源。</p>
          )}
          {visualAsset && assetContentUrl && imageFailed ? (
            <p className="mt-3 text-xs text-amber-700">原图加载失败，当前已回退到文本摘要视图。</p>
          ) : null}
        </div>
      )}
      {asset?.reason ? <p className="mt-3 text-xs font-medium text-slate-600">命中原因：{asset.reason}</p> : null}
      {breakdownSummary ? <p className="mt-2 text-[11px] text-slate-500">检索分解：{breakdownSummary}</p> : null}
      {visualBackend || visualSource ? (
        <p className="mt-2 text-[11px] text-slate-500">
          视觉通道：{visualBackend || 'disabled'}
          {visualSource ? ` / ${visualSource}` : ''}
        </p>
      ) : null}
      {assetTraceText ? <p className="mt-2 text-[11px] text-slate-500 line-clamp-3">{assetTraceText}</p> : null}
    </div>
  );
}

function ReadonlyBlock({
  block,
  section,
  assetLookup,
}: {
  block: EditableBlock;
  section: SectionDraft;
  assetLookup: Map<string, RecommendedAsset>;
}) {
  const headingMatch = block.content.match(/^(#{1,6})\s*(.*)$/);
  const placeholder = parseAssetPlaceholder(block.content);
  if (block.kind === 'heading' && headingMatch?.[2]) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <Badge variant="outline">{blockLabel(block.kind)}</Badge>
        <p className="mt-3 text-lg font-semibold text-slate-900">{headingMatch[2]}</p>
      </div>
    );
  }

  if (placeholder) {
    return (
      <AssetPreviewCard
        asset={assetLookup.get(placeholder.assetId)}
        placeholderLabel={block.content.replace(/^.*\]\]\s*/, '').trim() || undefined}
        section={section}
      />
    );
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <Badge variant="outline">{blockLabel(block.kind)}</Badge>
        <span className="text-xs text-slate-400">Read only</span>
      </div>
      <MarkdownArticle markdown={block.content} compact />
    </div>
  );
}

export function SectionBlock({
  section,
  projectId,
  evidenceMap,
  onRefresh,
}: {
  section: SectionDraft;
  projectId: string;
  evidenceMap: Record<string, EvidenceCard>;
  onRefresh: () => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [content, setContent] = useState(section.content_md || '');
  const [blocks, setBlocks] = useState<EditableBlock[]>(parseMarkdownBlocks(section.content_md || ''));
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [selectedCitationIds, setSelectedCitationIds] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState('preview');
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);

  useEffect(() => {
    setContent(section.content_md || '');
    setBlocks(parseMarkdownBlocks(section.content_md || ''));
    setIsEditing(false);
    setActiveTab('preview');
    setSelectedCitationIds([]);
  }, [section.content_md, section.id, section.updated_at]);

  const selectedCitationCount = selectedCitationIds.length;
  const hasEditableContent = Boolean(content.trim());
  const assetCount = section.recommended_assets?.length || 0;
  const generationDetails = getGenerationDetails(section);
  const reuseTrace = getReuseTrace(section);
  const queryIntents = reuseTrace?.query_intents;
  const sectionCandidates = reuseTrace?.section_candidates || [];
  const selectedSections = generationDetails?.selected_sections || reuseTrace?.scoped_sections || [];
  const selectedBlocks = generationDetails?.selected_blocks || [];
  const selectionReason = generationDetails?.selection_reason;
  const tokenBudget = generationDetails?.token_budget;
  const knowledgeWikiTerms = reuseTrace?.knowledge_wiki_terms || [];
  const knowledgeWikiProductCards = reuseTrace?.knowledge_wiki_product_cards || [];
  const knowledgeWikiModuleCards = reuseTrace?.knowledge_wiki_module_cards || [];
  const knowledgeWikiPriorSummary = generationDetails?.knowledge_wiki_prior_summary;
  const hasKnowledgeWikiSignals =
    knowledgeWikiTerms.length > 0 ||
    knowledgeWikiProductCards.length > 0 ||
    knowledgeWikiModuleCards.length > 0 ||
    Boolean(knowledgeWikiPriorSummary?.prior_hit_block_count);
  const previewSegments = useMemo(() => buildPreviewSegments(content), [content]);
  const assetLookup = useMemo(
    () =>
      new Map(
        (section.recommended_assets || [])
          .filter((asset) => asset.asset_id)
          .map((asset) => [asset.asset_id as string, asset]),
      ),
    [section.recommended_assets],
  );

  const citationModalContent = useMemo(() => {
    if (!activeCitation) return '';
    return resolveCitationSourceContent(section, activeCitation, evidenceMap);
  }, [activeCitation, evidenceMap, section]);

  const resetDraftState = () => {
    setContent(section.content_md || '');
    setBlocks(parseMarkdownBlocks(section.content_md || ''));
    setIsEditing(false);
    setActiveTab('preview');
  };

  const syncBlocksToContent = (nextBlocks: EditableBlock[]) => {
    setBlocks(nextBlocks);
    setContent(stringifyMarkdownBlocks(nextBlocks));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.patch(`/projects/${projectId}/sections/${section.section_id}`, { content_md: content });
      toast.success('Section saved');
      setIsEditing(false);
      setActiveTab('preview');
      onRefresh();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error saving section'));
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerate = async (preferredIds?: string[]) => {
    setRegenerating(true);
    try {
      await api.post(`/projects/${projectId}/sections/${section.section_id}/regenerate`, {
        preferred_citation_ids: preferredIds && preferredIds.length > 0 ? preferredIds : undefined,
      });
      toast.success(preferredIds?.length ? 'Section regenerated from selected evidence' : 'Section regenerated');
      setSelectedCitationIds([]);
      onRefresh();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error regenerating section'));
    } finally {
      setRegenerating(false);
    }
  };

  const toggleCitationSelection = (citation: Citation) => {
    const citationId = citation.evidence_id;
    if (!citationId) {
      return;
    }
    setSelectedCitationIds((current) =>
      current.includes(citationId) ? current.filter((item) => item !== citationId) : [...current, citationId],
    );
  };

  const updateBlock = (blockId: string, nextContent: string) => {
    const nextBlocks = blocks.map((block) => (block.id === blockId ? { ...block, content: nextContent } : block));
    syncBlocksToContent(nextBlocks);
  };

  const updateHeadingBlock = (blockId: string, nextTitle: string) => {
    const nextBlocks = blocks.map((block) => {
      if (block.id !== blockId) return block;
      const match = block.content.match(/^(#{1,6})\s*(.*)$/);
      const prefix = match?.[1] || '##';
      return {
        ...block,
        content: `${prefix} ${nextTitle.trim()}`.trim(),
      };
    });
    syncBlocksToContent(nextBlocks);
  };

  const insertAssetPlaceholder = (asset: RecommendedAsset) => {
    const placeholder = buildAssetPlaceholder(asset);
    if (!placeholder) {
      toast.error('This asset is missing a stable identifier');
      return;
    }
    if (content.includes(placeholder)) {
      toast.message('This asset is already referenced in the draft');
      setActiveTab('preview');
      return;
    }

    const nextContent = content.trim() ? `${content.trim()}\n\n${placeholder}` : placeholder;
    setContent(nextContent);
    setBlocks(parseMarkdownBlocks(nextContent));
    setIsEditing(true);
    setActiveTab('preview');
    toast.success('Asset reference inserted into the draft');
  };

  return (
    <>
      <Card className="mb-8 overflow-hidden border-slate-200 shadow-sm">
        <div className="border-b bg-slate-50 px-5 py-4">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-xl font-semibold text-slate-900">{section.title}</h3>
                <Badge variant={getStatusVariant(section.status)}>{section.status}</Badge>
                {section.generation_mode === 'manual_only' && <Badge variant="destructive">Manual Only</Badge>}
                {section.generation_mode === 'reuse_first' && (
                  <Badge variant="secondary" className="bg-blue-100 text-blue-800 hover:bg-blue-200">
                    Reuse First
                  </Badge>
                )}
                {section.generation_mode === 'baseline' && <Badge variant="outline">Baseline</Badge>}
              </div>
              <div className="flex flex-wrap gap-4 text-xs text-slate-500">
                <span>{section.citation_refs.length} citations</span>
                <span>{assetCount} assets</span>
                <span>{hasEditableContent ? 'draft ready for review' : 'draft not generated yet'}</span>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {!isEditing ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setIsEditing(true);
                    setActiveTab('guided');
                    setContent(section.content_md || '');
                    setBlocks(parseMarkdownBlocks(section.content_md || ''));
                  }}
                >
                  <Sparkles className="mr-2 h-4 w-4" />
                  Guided Edit
                </Button>
              ) : (
                <>
                  <Button size="sm" variant="outline" onClick={resetDraftState}>
                    Cancel
                  </Button>
                  <Button size="sm" onClick={handleSave} disabled={saving}>
                    {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                    Save
                  </Button>
                </>
              )}
              <Button
                size="sm"
                variant="secondary"
                onClick={() => void handleRegenerate(selectedCitationIds)}
                disabled={regenerating}
              >
                {regenerating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
                {selectedCitationCount > 0 ? `Regenerate (${selectedCitationCount})` : 'Regenerate'}
              </Button>
            </div>
          </div>
        </div>

        <CardContent className="grid gap-0 p-0 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="min-w-0 border-b bg-white xl:border-b-0 xl:border-r">
            <Tabs value={activeTab} onValueChange={setActiveTab} className="gap-0">
              <div className="border-b px-5 pt-4">
                <TabsList variant="line" className="bg-transparent">
                  <TabsTrigger value="preview">
                    <FileText className="h-4 w-4" />
                    客户稿视图
                  </TabsTrigger>
                  <TabsTrigger value="guided">
                    <Layers3 className="h-4 w-4" />
                    分段编辑
                  </TabsTrigger>
                  <TabsTrigger value="source">
                    <FileCode2 className="h-4 w-4" />
                    源码
                  </TabsTrigger>
                </TabsList>
              </div>

              <TabsContent value="preview" className="p-5">
                {content.trim() ? (
                  <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-[inset_0_1px_0_rgba(255,255,255,0.65)]">
                    <div className="space-y-6">
                      {previewSegments.map((segment, index) => {
                        if (segment.kind === 'markdown') {
                          return <MarkdownArticle key={`md-${index}`} markdown={segment.markdown} />;
                        }
                        return (
                          <AssetPreviewCard
                            key={`asset-${segment.assetId}-${index}`}
                            asset={assetLookup.get(segment.assetId)}
                            placeholderLabel={segment.label}
                            section={section}
                            embedded
                          />
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  <div className="rounded-3xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
                    <FileText className="mx-auto mb-3 h-10 w-10 text-slate-300" />
                    <p className="text-sm font-medium text-slate-700">
                      {section.generation_mode === 'manual_only'
                        ? '本节当前标记为人工编写。'
                        : '当前没有客户稿正文。'}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {section.generation_mode === 'manual_only'
                        ? '你可以从右侧依据和图表开始整理。'
                        : '先点 Regenerate，或者从右侧选择依据后定向重生成。'}
                    </p>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="guided" className="space-y-4 p-5">
                {!hasEditableContent ? (
                  <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-5 py-8 text-center text-sm text-slate-500">
                    先生成或粘贴正文后，这里会按段落、表格和标题拆成更好编辑的块。
                  </div>
                ) : (
                  blocks.map((block) =>
                    !isEditing ? (
                      <ReadonlyBlock key={block.id} block={block} section={section} assetLookup={assetLookup} />
                    ) : (
                      <div key={block.id} className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2">
                            <Badge variant="outline">{blockLabel(block.kind)}</Badge>
                          </div>
                        </div>

                        {block.kind === 'heading' ? (
                          <Input
                            value={block.content.replace(/^#{1,6}\s*/, '')}
                            onChange={(event) => updateHeadingBlock(block.id, event.target.value)}
                          />
                        ) : (
                          <Textarea
                            value={block.content}
                            onChange={(event) => updateBlock(block.id, event.target.value)}
                            className={
                              block.kind === 'table'
                                ? 'min-h-[180px] font-mono text-xs'
                                : 'min-h-[120px] text-sm leading-7'
                            }
                          />
                        )}
                      </div>
                    ),
                  )
                )}
              </TabsContent>

              <TabsContent value="source" className="p-5">
                <Textarea
                  value={content}
                  onChange={(event) => {
                    setContent(event.target.value);
                    setBlocks(parseMarkdownBlocks(event.target.value));
                  }}
                  disabled={!isEditing}
                  className="min-h-[420px] font-mono text-xs leading-6"
                />
                <p className="mt-2 text-xs text-slate-500">
                  源码模式保留给高级调整。常规修改更建议用“客户稿视图”或“分段编辑”。
                </p>
              </TabsContent>
            </Tabs>
          </div>

          <div className="space-y-6 bg-slate-50 p-5">
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h4 className="text-sm font-semibold text-slate-900">Citations</h4>
                  <p className="text-xs text-slate-500">点击卡片查看整段原文，再决定是否纳入本轮重写。</p>
                </div>
                {selectedCitationCount > 0 ? (
                  <Badge variant="secondary" className="bg-blue-100 text-blue-800">
                    {selectedCitationCount} selected
                  </Badge>
                ) : null}
              </div>

              {section.citation_refs.length > 0 ? (
                <div className="space-y-3">
                  {section.citation_refs.map((citation, index) => {
                    const citationId = citation.evidence_id || `citation-${index}`;
                    const citationKey = `${citationId}-${index}`;
                    const isSelected = selectedCitationIds.includes(citationId);
                    const sourceContent = resolveCitationSourceContent(section, citation, evidenceMap);
                    return (
                      <div
                        key={citationKey}
                        className={`rounded-2xl border p-3 transition-colors ${
                          isSelected
                            ? 'border-blue-300 bg-blue-50 shadow-sm'
                            : 'border-slate-200 bg-slate-50/70 hover:border-slate-300 hover:bg-white'
                        }`}
                      >
                        <button
                          type="button"
                          className="w-full text-left"
                          onClick={() => setActiveCitation(citation)}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-sm font-semibold text-slate-900">{citation.source_title || 'Citation'}</p>
                              <p className="mt-1 text-xs text-slate-500">{renderCitationLabel(citation)}</p>
                            </div>
                            {citation.type ? (
                              <Badge variant="outline" className="shrink-0">
                                {citation.type}
                              </Badge>
                            ) : null}
                          </div>
                          <p className="mt-3 line-clamp-6 text-sm leading-6 text-slate-700">
                            {citation.excerpt || sourceContent || '点击查看原章节内容'}
                          </p>
                          <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
                            <span>
                              score {citation.relevance_score ? Number(citation.relevance_score).toFixed(2) : 'n/a'}
                            </span>
                            <span className="inline-flex items-center gap-1 font-medium text-blue-700">
                              <Eye className="h-3.5 w-3.5" />
                              查看原文
                            </span>
                          </div>
                        </button>

                        <div className="mt-3 flex flex-wrap justify-end gap-2 border-t border-slate-200 pt-3">
                          <Button size="sm" variant="outline" onClick={() => toggleCitationSelection(citation)}>
                            {isSelected ? '取消选择' : '选中此依据'}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => void handleRegenerate(citation.evidence_id ? [citation.evidence_id] : undefined)}
                            disabled={regenerating}
                          >
                            仅用此依据重写
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-xs text-slate-500">No citations</p>
              )}
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h4 className="text-sm font-semibold text-slate-900">Retrieval Trace</h4>
                  <p className="text-xs text-slate-500">查看本章是按章节包还是整章材料生成，以及命中的来源章节。</p>
                </div>
                <Target className="h-4 w-4 text-slate-400" />
              </div>

              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge variant="outline">{generationDetails?.retrieval_mode || 'unknown'}</Badge>
                  {generationDetails?.effective_path ? (
                    <Badge variant="secondary" className="bg-slate-100 text-slate-700">
                      {generationDetails.effective_path}
                    </Badge>
                  ) : null}
                  {tokenBudget ? (
                    <Badge variant={tokenBudget.within_budget ? 'success' : 'warning'}>
                      {tokenBudget.within_budget ? 'within budget' : 'over budget'}
                    </Badge>
                  ) : null}
                </div>

                {reuseTrace?.query ? (
                  <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Query</p>
                    <p className="mt-1 text-sm leading-6 text-slate-700">{reuseTrace.query}</p>
                  </div>
                ) : null}

                {queryIntents ? (
                  <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Query Intents</p>
                    <div className="space-y-2">
                      {(['title_text', 'detail_text', 'context_text'] as const).map((key) =>
                        queryIntents[key] ? (
                          <div key={key} className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                              {formatIntentLabel(key)}
                            </p>
                            <p className="mt-1 text-sm leading-6 text-slate-700">{queryIntents[key]}</p>
                          </div>
                        ) : null,
                      )}
                    </div>
                  </div>
                ) : null}

                {hasKnowledgeWikiSignals ? (
                  <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">AI Wiki Priors</p>
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-3">
                      <div className="flex flex-wrap gap-2">
                        {knowledgeWikiTerms.map((item, index) => (
                          <Badge key={`term-${item}-${index}`} variant="outline" className="border-emerald-200 bg-white text-emerald-700">
                            {item}
                          </Badge>
                        ))}
                        {knowledgeWikiProductCards.map((item, index) => (
                          <Badge key={`product-${item}-${index}`} variant="secondary" className="bg-emerald-100 text-emerald-800">
                            产品族 {item}
                          </Badge>
                        ))}
                        {knowledgeWikiModuleCards.map((item, index) => (
                          <Badge key={`module-${item}-${index}`} variant="secondary" className="bg-teal-100 text-teal-800">
                            模块 {item}
                          </Badge>
                        ))}
                      </div>
                      {knowledgeWikiPriorSummary ? (
                        <div className="mt-3 grid gap-3 sm:grid-cols-3">
                          <div className="rounded-xl border border-emerald-200 bg-white p-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Prior Hit Blocks</p>
                            <p className="mt-1 text-sm font-semibold text-slate-900">
                              {knowledgeWikiPriorSummary.prior_hit_block_count ?? 0} / {knowledgeWikiPriorSummary.selected_block_count ?? 0}
                            </p>
                          </div>
                          <div className="rounded-xl border border-emerald-200 bg-white p-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Total Boost</p>
                            <p className="mt-1 text-sm font-semibold text-slate-900">
                              {formatTraceMetric(knowledgeWikiPriorSummary.total_prior_boost)}
                            </p>
                          </div>
                          <div className="rounded-xl border border-emerald-200 bg-white p-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Max Boost</p>
                            <p className="mt-1 text-sm font-semibold text-slate-900">
                              {formatTraceMetric(knowledgeWikiPriorSummary.max_prior_boost)}
                            </p>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                {selectionReason ? (
                  <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Decision</p>
                    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        {selectionReason.mode ? <Badge variant="outline">{selectionReason.mode}</Badge> : null}
                        {selectionReason.top_section_id ? (
                          <Badge variant="secondary" className="bg-slate-100 text-slate-700">
                            top {selectionReason.top_section_id}
                          </Badge>
                        ) : null}
                        {selectionReason.full_section_within_budget !== undefined ? (
                          <Badge variant={selectionReason.full_section_within_budget ? 'success' : 'warning'}>
                            {selectionReason.full_section_within_budget ? 'full-section in budget' : 'full-section over budget'}
                          </Badge>
                        ) : null}
                      </div>
                      <div className="mt-3 grid gap-3 sm:grid-cols-2">
                        <div className="rounded-xl border border-slate-200 bg-white p-3">
                          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Top Score</p>
                          <p className="mt-1 text-sm font-semibold text-slate-900">
                            {formatTraceMetric(selectionReason.top_section_score)}
                          </p>
                        </div>
                        <div className="rounded-xl border border-slate-200 bg-white p-3">
                          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Lead</p>
                          <p className="mt-1 text-sm font-semibold text-slate-900">
                            {formatTraceMetric(selectionReason.lead_score)}
                          </p>
                        </div>
                        <div className="rounded-xl border border-slate-200 bg-white p-3">
                          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Runner Up</p>
                          <p className="mt-1 text-sm font-semibold text-slate-900">
                            {formatTraceMetric(selectionReason.runner_up_score)}
                          </p>
                        </div>
                        <div className="rounded-xl border border-slate-200 bg-white p-3">
                          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Aligned Blocks</p>
                          <p className="mt-1 text-sm font-semibold text-slate-900">
                            {selectionReason.full_section_block_count ?? 0}
                          </p>
                        </div>
                      </div>
                      {selectionReason.reasons?.length ? (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {selectionReason.reasons.map((item, index) => (
                            <Badge key={`${item}-${index}`} variant="outline" className="bg-white text-slate-600">
                              {formatSelectionReason(item)}
                            </Badge>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                {tokenBudget ? (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Section Tokens</p>
                      <p className="mt-1 text-sm font-semibold text-slate-900">{tokenBudget.section_material_tokens ?? 0}</p>
                    </div>
                    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Asset Tokens</p>
                      <p className="mt-1 text-sm font-semibold text-slate-900">{tokenBudget.asset_tokens ?? 0}</p>
                    </div>
                  </div>
                ) : null}

                {sectionCandidates.length > 0 ? (
                  <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Section Candidates</p>
                    {sectionCandidates.map((item, index) => (
                      <div key={`${item.section_id || item.section_path || 'candidate'}-${index}`} className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                        {(() => {
                          const breakdownText = buildSectionCandidateBreakdownText(item.score_breakdown);
                          const traceText = item.reason_trace?.slice(0, 3).join(' / ') || '';
                          return (
                            <>
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-slate-900">{item.file_name || item.source_heading || '历史章节'}</p>
                            <p className="mt-1 text-xs leading-5 text-slate-500">{item.section_path || '未标注章节路径'}</p>
                          </div>
                          {typeof item.score === 'number' ? (
                            <Badge variant="outline">{item.score.toFixed(2)}</Badge>
                          ) : null}
                        </div>
                        {breakdownText ? <p className="mt-2 text-[11px] text-slate-500">{breakdownText}</p> : null}
                        {item.reason ? <p className="mt-2 text-xs text-slate-600">{item.reason}</p> : null}
                        {traceText ? <p className="mt-2 text-[11px] text-slate-500 line-clamp-3">{traceText}</p> : null}
                            </>
                          );
                        })()}
                      </div>
                    ))}
                  </div>
                ) : null}

                {selectedSections.length > 0 ? (
                  <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Selected Sections</p>
                    {selectedSections.map((item, index) => (
                      <div key={`${item.section_id || item.section_path || 'section'}-${index}`} className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                        {(() => {
                          const breakdownText = buildSectionCandidateBreakdownText(item.score_breakdown);
                          const traceText = item.reason_trace?.slice(0, 3).join(' / ') || '';
                          return (
                            <>
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-slate-900">{item.file_name || item.source_heading || '历史章节'}</p>
                            <p className="mt-1 text-xs leading-5 text-slate-500">{item.section_path || '未标注章节路径'}</p>
                          </div>
                          {typeof item.score === 'number' ? (
                            <Badge variant="outline">{item.score.toFixed(2)}</Badge>
                          ) : null}
                        </div>
                        {breakdownText ? <p className="mt-2 text-[11px] text-slate-500">{breakdownText}</p> : null}
                        {item.reason ? <p className="mt-2 text-xs text-slate-600">{item.reason}</p> : null}
                        {traceText ? <p className="mt-2 text-[11px] text-slate-500 line-clamp-3">{traceText}</p> : null}
                            </>
                          );
                        })()}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-xs text-slate-500">
                    当前还没有章节级 trace。重新生成后，这里会显示命中的历史章节。
                  </div>
                )}

                {selectedBlocks.length > 0 ? (
                  <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Prompt Blocks</p>
                    {selectedBlocks.map((item, index) => {
                      const knowledgeWikiPriorText = buildKnowledgeWikiPriorBreakdownText(item.selection_score_breakdown);
                      const retrievalBreakdownText = buildBlockRetrievalBreakdownText(item.retrieval_score_breakdown);
                      const retrievalTraceText = item.retrieval_reason_trace?.slice(0, 3).join(' / ') || '';
                      return (
                        <div key={`${item.block_id || item.section_path || 'block'}-${index}`} className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-sm font-semibold text-slate-900">{item.source_title || '复用块'}</p>
                              <p className="mt-1 text-xs leading-5 text-slate-500">{item.section_path || normalizeHeadingPath(item.heading_path)}</p>
                            </div>
                            {typeof item.selection_score === 'number' ? (
                              <Badge variant="outline">{item.selection_score.toFixed(2)}</Badge>
                            ) : null}
                          </div>
                          {retrievalBreakdownText ? <p className="mt-2 text-[11px] text-slate-500">{retrievalBreakdownText}</p> : null}
                          {item.retrieval_reason ? <p className="mt-2 text-xs text-slate-600">{item.retrieval_reason}</p> : null}
                          {retrievalTraceText ? <p className="mt-2 text-[11px] text-slate-500 line-clamp-3">{retrievalTraceText}</p> : null}
                          {item.selection_reasons?.length ? (
                            <p className="mt-2 text-xs text-slate-600">{item.selection_reasons.join(' / ')}</p>
                          ) : null}
                          {knowledgeWikiPriorText ? <p className="mt-2 text-xs text-emerald-700">{knowledgeWikiPriorText}</p> : null}
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h4 className="text-sm font-semibold text-slate-900">Tables & Figures</h4>
                  <p className="text-xs text-slate-500">历史样板里命中的图表会在这里先展示，再决定是否插入正文。</p>
                </div>
                <GalleryVerticalEnd className="h-4 w-4 text-slate-400" />
              </div>

              {assetCount > 0 ? (
                <div className="space-y-3">
                  {(section.recommended_assets || []).map((asset, index) => {
                    const placeholder = buildAssetPlaceholder(asset);
                    const alreadyUsed = placeholder ? content.includes(placeholder) : false;
                    return (
                      <div
                        key={`${asset.asset_id || asset.title || asset.document_name}-${index}`}
                        className={asset.review_required ? 'rounded-2xl border border-amber-300 bg-amber-50/70 p-4' : 'rounded-2xl border border-slate-200 bg-slate-50/70 p-4'}
                      >
                        <AssetPreviewCard asset={asset} section={section} />
                        <div className="mt-3 flex items-center justify-between gap-3">
                          <span className="text-xs text-slate-500">
                            {alreadyUsed ? '已插入当前正文' : '尚未插入正文'}
                          </span>
                          <Button size="sm" variant="outline" onClick={() => insertAssetPlaceholder(asset)}>
                            {alreadyUsed ? '再次定位到该图表' : '插入正文'}
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center">
                  <p className="text-sm font-medium text-slate-700">当前还没有命中图表资产</p>
                  <p className="mt-1 text-xs text-slate-500">
                    这通常意味着该章节还没重新按历史样板检索，或者当前依据没有把图表相关内容带进来。
                  </p>
                </div>
              )}
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <h4 className="mb-2 text-sm font-semibold text-slate-900">Assumptions</h4>
              {section.assumptions.length > 0 ? (
                <div className="space-y-2">
                  {section.assumptions.map((assumption, index) => (
                    <div key={`${section.section_id}-assumption-${index}`} className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                      {typeof assumption === 'string' ? assumption : JSON.stringify(assumption)}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-slate-500">No specific assumptions</p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      <Dialog open={Boolean(activeCitation)} onOpenChange={(open) => !open && setActiveCitation(null)}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>{activeCitation?.source_title || 'Citation source'}</DialogTitle>
            <DialogDescription>
              {activeCitation ? renderCitationLabel(activeCitation) : '查看该引用对应的完整原章节。'}
            </DialogDescription>
          </DialogHeader>

          <div className="max-h-[70vh] overflow-y-auto rounded-2xl border border-slate-200 bg-slate-50 p-4">
            {citationModalContent ? (
              markdownTableCandidate(citationModalContent) || citationModalContent.includes('#') ? (
                <MarkdownArticle markdown={citationModalContent} compact />
              ) : (
                <div className="whitespace-pre-wrap text-sm leading-7 text-slate-700">{citationModalContent}</div>
              )
            ) : (
              <div className="text-sm text-slate-500">当前还没有拿到这条引用的完整原文。</div>
            )}
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => activeCitation && toggleCitationSelection(activeCitation)}
            >
              {activeCitation?.evidence_id && selectedCitationIds.includes(activeCitation.evidence_id) ? '取消选择' : '选中此依据'}
            </Button>
            <Button
              variant="outline"
              onClick={() => activeCitation?.evidence_id && void handleRegenerate([activeCitation.evidence_id])}
              disabled={!activeCitation?.evidence_id || regenerating}
            >
              <ExternalLink className="mr-2 h-4 w-4" />
              仅用此依据重写
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
