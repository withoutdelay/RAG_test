'use client';

import { ComponentProps, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
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
  ThumbsDown,
} from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
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
import {
  buildSectionPlaceholderSuggestions,
  buildSectionReviewBatchBrief,
  buildReviewTaskRevisionBrief,
  ReviewTaskActionStatus,
  type ReviewTaskPlaceholderSuggestion,
  type ReviewTaskSuggestedBlock,
  issueFromTaskPayload,
  toRecord,
  toStringArray,
} from '@/lib/review-tasks';
import type {
  Citation,
  EvidenceCard,
  RecommendedAsset,
  ReviewTask,
  ReviewResolutionTraceEntry,
  ReuseBlockLike,
  ReuseRetrievalTrace,
  SectionDraft,
  SectionGenerationDetails,
  SectionValidatorResult,
} from '@/lib/types';
import { toast } from 'sonner';

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

interface BlockFillSuggestion {
  key: string;
  label: string;
  value: string;
  placeholder: string;
  lineIndex?: number;
  cellIndex?: number;
  rowDescriptor?: string;
}

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

function locateInsertedBlockId(nextBlocks: EditableBlock[], insertedMarkdown: string): string | null {
  const insertedBlocks = parseMarkdownBlocks(insertedMarkdown);
  if (insertedBlocks.length === 0 || nextBlocks.length === 0) {
    return null;
  }

  const insertedCount = insertedBlocks.length;
  const appendedStartIndex = nextBlocks.length - insertedCount;
  if (appendedStartIndex >= 0) {
    const appendedMatches = insertedBlocks.every(
      (block, index) => nextBlocks[appendedStartIndex + index]?.content.trim() === block.content.trim(),
    );
    if (appendedMatches) {
      return nextBlocks[appendedStartIndex]?.id || null;
    }
  }

  const firstInsertedContent = insertedBlocks[0]?.content.trim();
  if (!firstInsertedContent) {
    return null;
  }

  for (let index = nextBlocks.length - 1; index >= 0; index -= 1) {
    if (nextBlocks[index]?.content.trim() === firstInsertedContent) {
      return nextBlocks[index]?.id || null;
    }
  }

  return null;
}

function uniqueStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const items: string[] = [];
  for (const value of values) {
    const normalized = value.trim();
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    items.push(normalized);
  }
  return items;
}

function normalizeBlockSearchText(value: string): string {
  return value
    .toLowerCase()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, '')
    .replace(/[|`~!@#$%^&*+=<>{}\[\]\\/"'“”‘’：:；;，,。！？、()（）]/g, '');
}

function buildLocateCandidates(value: string): string[] {
  const raw = value.trim();
  if (!raw) {
    return [];
  }
  const colonSegments = raw
    .split(/[：:]/)
    .map((item) => item.trim())
    .filter(Boolean);
  const detailSegments = raw
    .split(/[；;，,、]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return uniqueStrings([raw, ...colonSegments.slice(1), ...detailSegments]).filter((item) => item.length >= 2);
}

function buildLocateTokens(value: string): string[] {
  const stopwords = new Set([
    '补充',
    '补齐',
    '至少',
    '一条',
    '正文',
    '写明',
    '说明',
    '提示',
    '建议',
    '当前',
    '章节',
    '接口',
    '设备',
    '内容',
    '缺失',
    '字段',
    '配套',
    '产品族',
    '风险提示',
    '待补充',
    '待确认项',
  ]);
  const tokens = buildLocateCandidates(value).flatMap((item) =>
    item
      .split(/[\s/|()（）【】\[\]]+/)
      .map((part) => part.trim())
      .filter(Boolean),
  );
  return uniqueStrings(tokens).filter((item) => item.length >= 2 && !stopwords.has(item));
}

function findBestMatchingBlockId(blocks: EditableBlock[], query: string): string | null {
  const candidates = buildLocateCandidates(query);
  const tokens = buildLocateTokens(query);
  if (candidates.length === 0 && tokens.length === 0) {
    return null;
  }

  let bestMatch: { blockId: string | null; score: number } = { blockId: null, score: 0 };
  for (const block of blocks) {
    const normalizedContent = normalizeBlockSearchText(block.content);
    if (!normalizedContent) {
      continue;
    }

    let score = 0;
    for (const candidate of candidates) {
      const normalizedCandidate = normalizeBlockSearchText(candidate);
      if (!normalizedCandidate) {
        continue;
      }
      if (normalizedContent.includes(normalizedCandidate)) {
        score = Math.max(score, Math.min(160, normalizedCandidate.length * 6));
      }
    }

    for (const token of tokens) {
      const normalizedToken = normalizeBlockSearchText(token);
      if (!normalizedToken || !normalizedContent.includes(normalizedToken)) {
        continue;
      }
      score += normalizedToken.length >= 6 ? 20 : 12;
    }

    if (block.kind === 'table' && tokens.length > 0) {
      score += 4;
    }

    if (score > bestMatch.score) {
      bestMatch = { blockId: block.id, score };
    }
  }

  return bestMatch.score >= 16 ? bestMatch.blockId : null;
}

function findPreferredSuggestedBlockKeys(blocks: ReviewTaskSuggestedBlock[], query: string): string[] {
  const candidates = buildLocateCandidates(query);
  const tokens = buildLocateTokens(query);
  if (blocks.length === 0 || (candidates.length === 0 && tokens.length === 0)) {
    return [];
  }

  const scored = blocks
    .map((block) => {
      const normalizedContent = normalizeBlockSearchText(`${block.title} ${block.markdown}`);
      let score = 0;
      for (const candidate of candidates) {
        const normalizedCandidate = normalizeBlockSearchText(candidate);
        if (normalizedCandidate && normalizedContent.includes(normalizedCandidate)) {
          score = Math.max(score, Math.min(180, normalizedCandidate.length * 6));
        }
      }
      for (const token of tokens) {
        const normalizedToken = normalizeBlockSearchText(token);
        if (!normalizedToken || !normalizedContent.includes(normalizedToken)) {
          continue;
        }
        score += normalizedToken.length >= 6 ? 18 : 10;
      }
      return { key: block.key, score };
    })
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score);

  if (!scored.length) {
    return [];
  }

  const threshold = Math.max(20, scored[0].score - 8);
  return scored
    .filter((item) => item.score >= threshold)
    .slice(0, 2)
    .map((item) => item.key);
}

const PLACEHOLDER_HINT_PATTERNS: Array<{ pattern: RegExp; label: string }> = [
  { pattern: /待补充主设备名称/g, label: '主设备名称' },
  { pattern: /待补充型号\/配置/g, label: '型号/配置' },
  { pattern: /待补充型号/g, label: '型号/配置' },
  { pattern: /待补充电压等级/g, label: '电压等级' },
  { pattern: /待补充容量\/功率/g, label: '容量/功率' },
  { pattern: /待补充协议/g, label: '协议' },
  { pattern: /待补充IO能力/g, label: 'IO 能力' },
  { pattern: /待补充接口边界/g, label: '接口边界' },
  { pattern: /待补充数量/g, label: '数量' },
  { pattern: /待补充设备/g, label: '设备名称' },
  { pattern: /待补充产品族/g, label: '配套产品族' },
  { pattern: /待补充缺失项/g, label: '缺失项' },
  { pattern: /待补充触发条件/g, label: '触发条件' },
  { pattern: /待补充/g, label: '待补充内容' },
  { pattern: /待确认项/g, label: '待确认项' },
];

const FILLABLE_PLACEHOLDER_TOKENS = [
  '待补充主设备名称',
  '待补充型号/配置',
  '待补充型号',
  '待补充电压等级',
  '待补充容量/功率',
  '待补充协议',
  '待补充IO能力',
  '待补充接口边界',
  '待补充数量',
  '待补充设备',
  '待补充产品族',
  '待补充缺失项',
  '待补充触发条件',
  '待补充',
] as const;

function parseMarkdownTableLine(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.includes('|')) {
    return null;
  }
  const normalized = trimmed.replace(/^\|/, '').replace(/\|$/, '');
  const cells = normalized.split('|').map((cell) => cell.trim());
  return cells.length > 0 ? cells : null;
}

function stringifyMarkdownTableLine(cells: string[]): string {
  return `| ${cells.map((cell) => cell.trim()).join(' | ')} |`;
}

function findFillablePlaceholderToken(cell: string): string | null {
  for (const token of FILLABLE_PLACEHOLDER_TOKENS) {
    if (cell.includes(token)) {
      return token;
    }
  }
  return null;
}

function inferSemanticLabelsFromText(text: string): string[] {
  const raw = String(text || '').trim();
  if (!raw) {
    return [];
  }
  const normalized = normalizeBlockSearchText(raw);
  if (!normalized) {
    return [];
  }

  const labels: string[] = [];
  if (normalized.includes('主设备') || normalized.includes('主要设备')) {
    labels.push('主设备名称', '设备名称');
  }
  if (normalized.includes('设备名称') || normalized.includes('设备清单') || normalized === '设备') {
    labels.push('设备名称');
  }
  if (normalized.includes('型号') || normalized.includes('配置')) {
    labels.push('型号/配置');
  }
  if (normalized.includes('电压')) {
    labels.push('电压等级');
  }
  if (normalized.includes('容量') || normalized.includes('功率')) {
    labels.push('容量/功率');
  }
  if (normalized.includes('数量') || normalized.includes('套数')) {
    labels.push('数量');
  }
  if (
    normalized.includes('通讯协议') ||
    normalized.includes('通信协议') ||
    normalized.includes('协议') ||
    normalized.includes('dcs')
  ) {
    labels.push('协议');
  }
  if (
    normalized.includes('io能力') ||
    normalized.includes('点表') ||
    normalized.includes('di') ||
    normalized.includes('do') ||
    normalized.includes('ai') ||
    normalized.includes('ao')
  ) {
    labels.push('IO 能力');
  }
  if (
    normalized.includes('接口边界') ||
    normalized.includes('关键接口') ||
    normalized.includes('接口信号') ||
    normalized.includes('信号边界')
  ) {
    labels.push('接口边界');
  }
  if (normalized.includes('产品族') || normalized.includes('family')) {
    labels.push('配套产品族');
  }
  if (normalized.includes('触发条件') || normalized.includes('切换条件')) {
    labels.push('触发条件');
  }
  if (normalized.includes('缺失项') || normalized.includes('缺少项') || normalized.includes('缺失配套')) {
    labels.push('缺失项');
  }
  return uniqueStrings(labels);
}

interface TablePlaceholderTarget {
  lineIndex: number;
  cellIndex: number;
  placeholder: string;
  semanticLabels: string[];
  rowDescriptor: string;
  rowContextText: string;
}

function lineIndexFromOffset(content: string, offset: number): number {
  const safeOffset = Math.max(0, Math.min(offset, content.length));
  return content.slice(0, safeOffset).split('\n').length - 1;
}

function isMeaningfulRowContextCell(cell: string): boolean {
  const value = String(cell || '').trim();
  if (!value) {
    return false;
  }
  if (findFillablePlaceholderToken(value)) {
    return false;
  }
  return !/^[\d.\-]+$/.test(value);
}

function pickTableRowDescriptor(rowCells: string[], headerCells: string[], currentCellIndex: number): string {
  const preferredHeaderIndex = headerCells.findIndex((header) => {
    const labels = inferSemanticLabelsFromText(header);
    return labels.includes('设备名称') || labels.includes('主设备名称');
  });
  if (
    preferredHeaderIndex >= 0 &&
    preferredHeaderIndex < rowCells.length &&
    preferredHeaderIndex !== currentCellIndex &&
    isMeaningfulRowContextCell(rowCells[preferredHeaderIndex])
  ) {
    return rowCells[preferredHeaderIndex].trim();
  }

  for (let index = 0; index < rowCells.length; index += 1) {
    if (index === currentCellIndex) {
      continue;
    }
    const cell = rowCells[index];
    if (isMeaningfulRowContextCell(cell)) {
      return cell.trim();
    }
  }

  return '';
}

function collectTablePlaceholderTargets(content: string): TablePlaceholderTarget[] {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  const targets: TablePlaceholderTarget[] = [];

  for (let lineIndex = 0; lineIndex < lines.length - 1; lineIndex += 1) {
    const headerCells = parseMarkdownTableLine(lines[lineIndex]);
    if (!headerCells) {
      continue;
    }
    const delimiter = lines[lineIndex + 1]?.trim() || '';
    if (!/^\|?[\s:|-]+\|?$/.test(delimiter) || !delimiter.includes('-')) {
      continue;
    }

    for (let rowIndex = lineIndex + 2; rowIndex < lines.length; rowIndex += 1) {
      const rowLine = lines[rowIndex]?.trim() || '';
      if (!rowLine) {
        continue;
      }
      const rowCells = parseMarkdownTableLine(rowLine);
      if (!rowCells) {
        break;
      }

      rowCells.forEach((cell, cellIndex) => {
        const placeholder = findFillablePlaceholderToken(cell);
        if (!placeholder) {
          return;
        }

        const rowDescriptor = pickTableRowDescriptor(rowCells, headerCells, cellIndex);
        const rowLabel = rowDescriptor || rowCells[0] || '';
        const columnLabel = headerCells[cellIndex] || '';
        const rowContextText = uniqueStrings(
          [rowDescriptor, rowLabel, columnLabel, ...rowCells.filter((item, index) => index !== cellIndex && isMeaningfulRowContextCell(item))]
            .map((item) => String(item || '').trim())
            .filter(Boolean),
        ).join(' / ');
        const semanticLabels = uniqueStrings([
          ...inferSemanticLabelsFromText(columnLabel),
          ...inferSemanticLabelsFromText(rowLabel),
          ...inferSemanticLabelsFromText(`${rowContextText} ${columnLabel}`),
        ]);

        targets.push({
          lineIndex: rowIndex,
          cellIndex,
          placeholder,
          semanticLabels,
          rowDescriptor,
          rowContextText,
        });
      });
    }

    lineIndex += 1;
  }

  return targets;
}

function getPlaceholderHints(content: string): string[] {
  if (!content.trim()) {
    return [];
  }
  const directHints = uniqueStrings(
    PLACEHOLDER_HINT_PATTERNS.flatMap(({ pattern, label }) =>
      new RegExp(pattern.source, pattern.flags).test(content) ? [label] : [],
    ),
  );
  const semanticHints = collectTablePlaceholderTargets(content)
    .filter((target) => target.placeholder === '待补充')
    .flatMap((target) => target.semanticLabels);
  const hints = uniqueStrings([...directHints, ...semanticHints]);
  const hasSpecificPlaceholderHint = hints.some((item) => item !== '待补充内容');
  return hasSpecificPlaceholderHint ? hints.filter((item) => item !== '待补充内容') : hints;
}

function findFirstPlaceholderRange(
  content: string,
  preferredStartOffset?: number | null,
): { start: number; end: number } | null {
  const matches: Array<{ start: number; end: number }> = [];
  for (const { pattern } of PLACEHOLDER_HINT_PATTERNS) {
    const matcher = new RegExp(pattern.source, pattern.flags);
    let match: RegExpExecArray | null;
    while ((match = matcher.exec(content)) !== null) {
      matches.push({
        start: match.index,
        end: match.index + match[0].length,
      });
      if (match.index === matcher.lastIndex) {
        matcher.lastIndex += 1;
      }
    }
  }

  if (matches.length === 0) {
    return null;
  }

  matches.sort((left, right) => {
    if (left.start === right.start) {
      return right.end - right.start - (left.end - left.start);
    }
    return left.start - right.start;
  });

  if (typeof preferredStartOffset === 'number') {
    const candidate = matches.find((item) => item.start >= preferredStartOffset);
    if (candidate) {
      return candidate;
    }
  }

  return matches[0] || null;
}

function getLineStartOffset(content: string, lineIndex: number): number {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  let offset = 0;
  for (let index = 0; index < lines.length; index += 1) {
    if (index === lineIndex) {
      return offset;
    }
    offset += lines[index].length + 1;
  }
  return offset;
}

function matchesNormalizedContext(term: string, normalizedContext: string): boolean {
  if (!normalizedContext) {
    return false;
  }
  const normalizedTerm = normalizeBlockSearchText(String(term || ''));
  return Boolean(normalizedTerm && normalizedContext.includes(normalizedTerm));
}

function getSuggestionRowScopeMatch(
  suggestion: ReviewTaskPlaceholderSuggestion,
  normalizedRowContext: string,
  normalizedRowDescriptor: string,
): { rowContextMatch: boolean; rowDescriptorMatch: boolean; hasScopedTerms: boolean } {
  const rowMatchTerms = (suggestion.matchTerms || []).filter(Boolean);
  return {
    rowContextMatch: rowMatchTerms.some((term) => matchesNormalizedContext(String(term || ''), normalizedRowContext)),
    rowDescriptorMatch:
      Boolean(normalizedRowDescriptor) &&
      rowMatchTerms.some((term) => matchesNormalizedContext(String(term || ''), normalizedRowDescriptor)),
    hasScopedTerms: rowMatchTerms.length > 0,
  };
}

function getRowScopedFillSuggestions(
  suggestions: BlockFillSuggestion[],
  activeLineIndex?: number | null,
): BlockFillSuggestion[] {
  if (typeof activeLineIndex !== 'number') {
    return [];
  }
  const rowSuggestions = suggestions.filter((item) => item.lineIndex === activeLineIndex);
  const seenCells = new Set<string>();
  const items: BlockFillSuggestion[] = [];
  for (const suggestion of rowSuggestions) {
    const cellKey = `${suggestion.lineIndex}:${suggestion.cellIndex}`;
    if (seenCells.has(cellKey)) {
      continue;
    }
    seenCells.add(cellKey);
    items.push(suggestion);
  }
  return items.sort((left, right) => (left.cellIndex ?? 0) - (right.cellIndex ?? 0));
}

function getBlockFillSuggestions(
  content: string,
  suggestions: ReviewTaskPlaceholderSuggestion[],
  activeLineIndex?: number | null,
): BlockFillSuggestion[] {
  if (!content.trim() || suggestions.length === 0) {
    return [];
  }

  const seen = new Set<string>();
  const items: Array<BlockFillSuggestion & { score: number }> = [];

  const tableTargets = markdownTableCandidate(content) ? collectTablePlaceholderTargets(content) : [];
  for (const target of tableTargets) {
    const normalizedRowContext = normalizeBlockSearchText(target.rowContextText);
    const normalizedRowDescriptor = normalizeBlockSearchText(target.rowDescriptor);
    const hasRowScopedSuggestions =
      Boolean(normalizedRowDescriptor) &&
      suggestions.some((candidate) =>
        getSuggestionRowScopeMatch(candidate, normalizedRowContext, normalizedRowDescriptor).rowContextMatch,
      );

    for (const suggestion of suggestions) {
      const directPlaceholderMatch = suggestion.placeholders.includes(target.placeholder);
      const suggestionSemanticLabels = uniqueStrings([suggestion.label, ...inferSemanticLabelsFromText(suggestion.label)]);
      const semanticMatch =
        target.placeholder === '待补充' &&
        suggestionSemanticLabels.some((item) => target.semanticLabels.includes(item));
      const { rowContextMatch, rowDescriptorMatch, hasScopedTerms } = getSuggestionRowScopeMatch(
        suggestion,
        normalizedRowContext,
        normalizedRowDescriptor,
      );
      const semanticScore =
        suggestionSemanticLabels.reduce((score, item) => score + (target.semanticLabels.includes(item) ? 20 : 0), 0);
      const activeLineBoost =
        typeof activeLineIndex === 'number'
          ? target.lineIndex === activeLineIndex
            ? 90
            : Math.max(0, 24 - Math.abs(target.lineIndex - activeLineIndex) * 6)
          : 0;

      if ((!directPlaceholderMatch && !semanticMatch && !rowContextMatch) || !suggestion.value.trim()) {
        continue;
      }
      if (hasRowScopedSuggestions && hasScopedTerms && !rowContextMatch && (directPlaceholderMatch || semanticMatch)) {
        continue;
      }

      const key = `${target.lineIndex}:${target.cellIndex}|${suggestion.label}|${suggestion.value}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      items.push({
        key: suggestion.key,
        label: suggestion.label,
        value: suggestion.value,
        placeholder: target.placeholder,
        lineIndex: target.lineIndex,
        cellIndex: target.cellIndex,
        rowDescriptor: target.rowDescriptor,
        score:
          (directPlaceholderMatch ? 100 : 0) +
          (semanticMatch ? 70 : 0) +
          (rowContextMatch ? 65 : 0) +
          (rowDescriptorMatch ? 30 : 0) +
          semanticScore +
          activeLineBoost -
          target.lineIndex * 2 -
          target.cellIndex,
      });
    }
  }

  for (const suggestion of suggestions) {
    const placeholder = suggestion.placeholders.find((token) => content.includes(token));
    if (!placeholder || !suggestion.value.trim()) {
      continue;
    }
    const key = `global|${placeholder}|${suggestion.label}|${suggestion.value}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    items.push({
      key: suggestion.key,
      label: suggestion.label,
      value: suggestion.value,
      placeholder,
      score: 16,
    });
  }

  return items
    .sort((left, right) => right.score - left.score)
    .map((item) => {
      const { score, ...rest } = item;
      void score;
      return rest;
    });
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

function getReviewResolutionTrace(section: SectionDraft): ReviewResolutionTraceEntry[] {
  const trace = getSectionValidatorResult(section)?.review_resolution_trace;
  return Array.isArray(trace)
    ? trace.filter((entry): entry is ReviewResolutionTraceEntry => Boolean(entry && typeof entry === 'object'))
    : [];
}

function humanizeTraceKey(key: string): string {
  const labels: Record<string, string> = {
    action: '动作',
    note: '说明',
    value: '取值',
    model_number: '型号',
    interface_type: '接口类型',
    catalog_interface_signals: '接口信号',
    compatibility_relations: '兼容关系',
    missing_fields: '缺失字段',
  };
  return labels[key] || key.replace(/_/g, ' ');
}

function formatTraceValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '未记录';
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (Array.isArray(value)) {
    const items = value.map((item) => formatTraceValue(item)).filter(Boolean);
    return items.length > 0 ? items.join(' / ') : '未记录';
  }
  if (typeof value === 'object') {
    const pairs = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== null && item !== undefined && item !== '')
      .slice(0, 3)
      .map(([key, item]) => `${humanizeTraceKey(key)}: ${formatTraceValue(item)}`);
    return pairs.length > 0 ? pairs.join(' / ') : '已处理';
  }
  return String(value);
}

function formatTraceStatus(status?: string | null): string {
  if (status === 'resolved') return 'resolved';
  if (status === 'rejected') return 'rejected';
  return status || 'unknown';
}

function formatTraceTaskType(taskType?: string | null): string {
  const labels: Record<string, string> = {
    content_review: 'content review',
    figure_confirm: 'figure confirm',
    param_conflict: 'param conflict',
    final_review: 'final review',
  };
  return labels[taskType || ''] || taskType || 'review task';
}

function formatTraceTimestamp(value?: string | null): string {
  if (!value) return 'time unavailable';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function summarizeTraceDetails(details?: Record<string, unknown>): string | null {
  if (!details) return null;
  const pairs = Object.entries(details)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .slice(0, 3)
    .map(([key, value]) => `${humanizeTraceKey(key)}: ${formatTraceValue(value)}`);
  return pairs.length > 0 ? pairs.join(' / ') : null;
}

function summarizeOpenTaskTags(task: ReviewTask): string[] {
  const details = toRecord(task.payload?.details);
  return [
    ...toStringArray(details.missing_fields),
    ...toStringArray(details.missing_items),
    ...toStringArray(details.expected_catalog_interface_signals),
    ...toStringArray(details.missing_products),
    ...toStringArray(details.missing_series_codes),
  ].slice(0, 5);
}

function SectionReviewTaskCard({
  task,
  isFocused,
  isActing,
  isSavingDraft,
  hasUnsavedChanges,
  activeLocateLabel,
  onAction,
  onSaveAndAction,
  onLocate,
  onApplySuggested,
}: {
  task: ReviewTask;
  isFocused: boolean;
  isActing: boolean;
  isSavingDraft: boolean;
  hasUnsavedChanges: boolean;
  activeLocateLabel: string | null;
  onAction: (task: ReviewTask, resolution: string, status: ReviewTaskActionStatus) => Promise<void>;
  onSaveAndAction: (task: ReviewTask, resolution: string, status: ReviewTaskActionStatus) => Promise<void>;
  onLocate: (query: string, label?: string, preferredSuggestedBlockKeys?: string[]) => void;
  onApplySuggested: (options: {
    suggestedBlocks: ReviewTaskSuggestedBlock[];
    preferredSuggestedBlockKeys: string[];
    locateQuery?: string;
    locateLabel?: string;
    focusLabel?: string;
  }) => void;
}) {
  const [resolution, setResolution] = useState('');
  const issue = issueFromTaskPayload(task.payload || {});
  const taskTags = summarizeOpenTaskTags(task);
  const revisionBrief = useMemo(() => buildReviewTaskRevisionBrief(task), [task]);
  const isActionable = task.status === 'open' || task.status === 'in_progress' || task.status === 'rejected';
  const handleLocate = (query: string, label?: string) => {
    if (!resolution.trim() && revisionBrief.resolutionTemplate.trim()) {
      setResolution(revisionBrief.resolutionTemplate);
    }
    onLocate(
      query,
      label,
      revisionBrief.suggestedBlocks.map((block) => block.key),
    );
  };
  const handleApplySuggested = () => {
    if (!resolution.trim() && revisionBrief.resolutionTemplate.trim()) {
      setResolution(revisionBrief.resolutionTemplate);
    }
    onApplySuggested({
      suggestedBlocks: revisionBrief.suggestedBlocks,
      preferredSuggestedBlockKeys: revisionBrief.suggestedBlocks.map((block) => block.key),
      locateQuery:
        revisionBrief.contentAnchors[0] ||
        revisionBrief.checklist[0] ||
        issue.section_title ||
        issue.code,
      locateLabel: issue.code || revisionBrief.contentAnchors[0] || revisionBrief.checklist[0],
      focusLabel: issue.code ? `骨架插入: ${issue.code}` : '骨架插入',
    });
  };

  return (
    <div
      className={`rounded-xl border p-3 ${
        task.blocking_level === 'P0' ? 'border-red-200 bg-red-50/60' : 'border-amber-200 bg-amber-50/60'
      } ${isFocused ? 'ring-2 ring-emerald-300 ring-offset-2 ring-offset-white' : ''}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={task.blocking_level === 'P0' ? 'destructive' : 'warning'}>{task.blocking_level}</Badge>
        {issue.code ? <Badge variant="outline">{issue.code}</Badge> : null}
        <Badge variant="secondary" className="bg-slate-100 text-slate-700">
          {task.task_type.replace(/_/g, ' ')}
        </Badge>
        <Badge variant="outline" className="uppercase">
          {task.status}
        </Badge>
      </div>

      <p className="mt-3 text-sm font-medium leading-6 text-slate-900">
        {issue.message || 'Review the current section and update the draft accordingly.'}
      </p>

      {issue.suggested_action ? (
        <p className="mt-2 rounded-md border border-white/80 bg-white/80 px-3 py-2 text-xs leading-5 text-slate-600">
          {issue.suggested_action}
        </p>
      ) : null}

      {taskTags.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {taskTags.map((item, index) => (
            <Badge key={`${task.id}-tag-${item}-${index}`} variant="outline" className="text-[11px]">
              {item}
            </Badge>
          ))}
        </div>
      ) : null}

      {revisionBrief.checklist.length > 0 ? (
        <div className="mt-3 rounded-md border border-white/80 bg-white/80 p-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
              Suggested Revision Checklist
            </p>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              onClick={() => setResolution(revisionBrief.resolutionTemplate)}
              disabled={!isActionable}
            >
              <Sparkles className="mr-1.5 h-3.5 w-3.5" />
              Use Suggested Note
            </Button>
          </div>
          <div className="mt-2 space-y-1.5 text-xs leading-5 text-slate-600">
            {revisionBrief.checklist.map((item, index) => (
              <div
                key={`${task.id}-checklist-${index}`}
                className={`flex items-start justify-between gap-3 rounded-md border px-2.5 py-2 ${
                  activeLocateLabel === item
                    ? 'border-emerald-300 bg-emerald-50/80 text-emerald-950'
                    : 'border-slate-200/80 bg-white/70'
                }`}
              >
                <p className="min-w-0 flex-1">{index + 1}. {item}</p>
                <Button
                  size="sm"
                  variant="ghost"
                  className={`h-6 shrink-0 px-2 text-[11px] ${
                    activeLocateLabel === item ? 'text-emerald-800 hover:text-emerald-900' : 'text-slate-500 hover:text-slate-900'
                  }`}
                  onClick={() => handleLocate(item, item)}
                >
                  定位
                </Button>
              </div>
            ))}
          </div>
          {revisionBrief.contentAnchors.length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {revisionBrief.contentAnchors.map((item, index) => (
                <button
                  key={`${task.id}-anchor-${item}-${index}`}
                  type="button"
                  onClick={() => handleLocate(item, item)}
                  className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] transition ${
                    activeLocateLabel === item
                      ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
                      : 'border-slate-200 bg-white text-slate-600 hover:border-emerald-300 hover:bg-emerald-50 hover:text-emerald-900'
                  }`}
                >
                  {item}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 space-y-3 border-t border-slate-200/80 pt-3">
        <Textarea
          placeholder="Enter what changed in this section, or the reason this task should stay rejected."
          value={resolution}
          onChange={(event) => setResolution(event.target.value)}
          className="min-h-[88px] text-sm"
          disabled={!isActionable}
        />
        {isActionable ? (
          <div className="flex items-center justify-end gap-2">
            {revisionBrief.suggestedBlocks.length > 0 ? (
              <Button
                size="sm"
                variant="outline"
                onClick={handleApplySuggested}
                disabled={isActing || isSavingDraft}
              >
                <Sparkles className="mr-2 h-4 w-4" />
                Insert Suggested + Fill Note
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="outline"
              onClick={() => void onAction(task, resolution, 'rejected')}
              disabled={isActing || isSavingDraft || !resolution.trim()}
            >
              {isActing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ThumbsDown className="mr-2 h-4 w-4" />}
              Reject
            </Button>
            {hasUnsavedChanges ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => void onSaveAndAction(task, resolution, 'resolved')}
                disabled={isActing || isSavingDraft || !resolution.trim()}
              >
                {isActing || isSavingDraft ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                Save Draft + Resolve
              </Button>
            ) : null}
            <Button
              size="sm"
              className="bg-green-600 text-white hover:bg-green-700"
              onClick={() => void onAction(task, resolution, 'resolved')}
              disabled={isActing || isSavingDraft || !resolution.trim()}
            >
              {isActing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
              Resolve
            </Button>
          </div>
        ) : (
          <p className="text-xs text-slate-500">This task is no longer actionable in the editor.</p>
        )}
      </div>
    </div>
  );
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

function parseMarkdownTableParts(markdown: string): { header: string; delimiter: string; rows: string[] } | null {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  for (let index = 0; index < lines.length - 1; index += 1) {
    const header = lines[index].trim();
    const delimiter = lines[index + 1].trim();
    if (!header.includes('|')) {
      continue;
    }
    if (!/^\|?[\s:|-]+\|?$/.test(delimiter) || !delimiter.includes('-')) {
      continue;
    }
    const rows: string[] = [];
    for (let rowIndex = index + 2; rowIndex < lines.length; rowIndex += 1) {
      const row = lines[rowIndex].trim();
      if (!row) {
        continue;
      }
      if (!row.includes('|')) {
        break;
      }
      rows.push(row);
    }
    return { header, delimiter, rows };
  }
  return null;
}

function mergeMarkdownTableBlocks(blocks: ReuseBlockLike[]): string | undefined {
  const mergedRows: string[] = [];
  let header = '';
  let delimiter = '';

  for (const block of blocks) {
    const content = block.content_md?.trim();
    if (!content) {
      continue;
    }
    const table = parseMarkdownTableParts(content);
    if (!table) {
      return undefined;
    }
    if (!header) {
      header = table.header;
      delimiter = table.delimiter;
    }
    mergedRows.push(...table.rows);
  }

  if (!header || !delimiter) {
    return undefined;
  }

  return [header, delimiter, ...mergedRows].join('\n');
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

function markdownTableCandidate(text: string): boolean {
  return text.includes('|') && /\n\|?[-: ]+\|[-|: ]+/.test(text);
}

function isVisualAsset(asset?: RecommendedAsset): boolean {
  return asset?.asset_type === 'figure' || asset?.asset_type === 'formula_candidate';
}

function MarkdownArticle({ markdown, compact = false }: { markdown: string; compact?: boolean }) {
  return (
    <div className={`prose prose-slate max-w-none ${compact ? 'prose-sm' : ''} [&_table]:my-4 [&_table]:w-full [&_table]:border-collapse [&_table]:overflow-hidden [&_table]:rounded-2xl [&_table]:border [&_table]:border-slate-200 [&_table]:bg-white [&_th]:border-b [&_th]:border-slate-200 [&_th]:bg-slate-100 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:text-xs [&_th]:font-semibold [&_th]:uppercase [&_th]:tracking-wide [&_th]:text-slate-600 [&_td]:border-b [&_td]:border-slate-100 [&_td]:px-3 [&_td]:py-2 [&_td]:align-top [&_td]:text-sm [&_li]:my-1.5 [&_p]:leading-7`}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          code(props: ComponentProps<'code'>) {
            const { className, children, ...rest } = props;
            return (
              <code className={`rounded bg-slate-100 px-1.5 py-0.5 text-[0.92em] text-slate-700 ${className || ''}`} {...rest}>
                {children}
              </code>
            );
          },
        }}
      >
        {markdown}
      </Markdown>
    </div>
  );
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
  reviewTasks,
  focusedReviewTaskId,
  actingTaskId,
  onReviewTaskAction,
  onBatchReviewTaskAction,
  onRefresh,
}: {
  section: SectionDraft;
  projectId: string;
  evidenceMap: Record<string, EvidenceCard>;
  reviewTasks: ReviewTask[];
  focusedReviewTaskId: string;
  actingTaskId: string | null;
  onReviewTaskAction: (task: ReviewTask, resolution: string, status: ReviewTaskActionStatus) => Promise<void>;
  onBatchReviewTaskAction: (tasks: ReviewTask[], resolution: string, status: ReviewTaskActionStatus) => Promise<void>;
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
  const [sectionBatchResolution, setSectionBatchResolution] = useState('');
  const [batchActing, setBatchActing] = useState(false);
  const [highlightedBlockId, setHighlightedBlockId] = useState<string | null>(null);
  const [activeLocateLabel, setActiveLocateLabel] = useState<string | null>(null);
  const [activeLocateBlockId, setActiveLocateBlockId] = useState<string | null>(null);
  const [activeSuggestedBlockKeys, setActiveSuggestedBlockKeys] = useState<string[]>([]);
  const [editorFocusBlockId, setEditorFocusBlockId] = useState<string | null>(null);
  const [locateFocusNonce, setLocateFocusNonce] = useState(0);
  const [blockSelectionStartById, setBlockSelectionStartById] = useState<Record<string, number>>({});
  const blockRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    setContent(section.content_md || '');
    setBlocks(parseMarkdownBlocks(section.content_md || ''));
    setIsEditing(false);
    setActiveTab('preview');
    setSelectedCitationIds([]);
    setActiveLocateLabel(null);
    setActiveLocateBlockId(null);
    setActiveSuggestedBlockKeys([]);
    setEditorFocusBlockId(null);
    setBlockSelectionStartById({});
  }, [section.content_md, section.id, section.updated_at]);

  useEffect(() => {
    setSectionBatchResolution('');
    setBatchActing(false);
    setHighlightedBlockId(null);
    setActiveLocateLabel(null);
    setActiveLocateBlockId(null);
    setActiveSuggestedBlockKeys([]);
    setEditorFocusBlockId(null);
    setBlockSelectionStartById({});
  }, [section.id, reviewTasks.length]);

  useEffect(() => {
    if (activeTab !== 'guided' || !highlightedBlockId) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      blockRefs.current[highlightedBlockId]?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    }, 120);
    return () => window.clearTimeout(timeoutId);
  }, [activeTab, highlightedBlockId, blocks]);

  useEffect(() => {
    if (!highlightedBlockId) {
      return;
    }
    const timeoutId = window.setTimeout(() => setHighlightedBlockId(null), 6000);
    return () => window.clearTimeout(timeoutId);
  }, [highlightedBlockId]);

  useEffect(() => {
    if (activeLocateBlockId && !blocks.some((block) => block.id === activeLocateBlockId)) {
      setActiveLocateBlockId(null);
      setActiveLocateLabel(null);
      setActiveSuggestedBlockKeys([]);
    }
    if (editorFocusBlockId && !blocks.some((block) => block.id === editorFocusBlockId)) {
      setEditorFocusBlockId(null);
    }
  }, [activeLocateBlockId, blocks, editorFocusBlockId]);

  useEffect(() => {
    if (activeTab !== 'guided' || !isEditing || !editorFocusBlockId) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      const container = blockRefs.current[editorFocusBlockId];
      const field = container?.querySelector('textarea, input');
      if (!(field instanceof HTMLTextAreaElement || field instanceof HTMLInputElement)) {
        return;
      }
      field.focus({ preventScroll: true });
      if (typeof field.setSelectionRange === 'function') {
        const preferredStartOffset = blockSelectionStartById[editorFocusBlockId];
        const placeholderRange = findFirstPlaceholderRange(field.value, preferredStartOffset);
        if (placeholderRange) {
          field.setSelectionRange(placeholderRange.start, placeholderRange.end);
          setBlockSelectionStartById((current) =>
            current[editorFocusBlockId] === placeholderRange.start
              ? current
              : {
                  ...current,
                  [editorFocusBlockId]: placeholderRange.start,
                },
          );
          return;
        }
        const textLength = field.value.length;
        field.setSelectionRange(textLength, textLength);
        setBlockSelectionStartById((current) =>
          current[editorFocusBlockId] === textLength
            ? current
            : {
                ...current,
                [editorFocusBlockId]: textLength,
              },
        );
      }
    }, 180);
    return () => window.clearTimeout(timeoutId);
  }, [activeTab, isEditing, editorFocusBlockId, locateFocusNonce, blocks, blockSelectionStartById]);

  const selectedCitationCount = selectedCitationIds.length;
  const hasEditableContent = Boolean(content.trim());
  const assetCount = section.recommended_assets?.length || 0;
  const generationDetails = getGenerationDetails(section);
  const reuseTrace = getReuseTrace(section);
  const selectedSections = generationDetails?.selected_sections || reuseTrace?.scoped_sections || [];
  const selectedBlocks = generationDetails?.selected_blocks || [];
  const tokenBudget = generationDetails?.token_budget;
  const previewSegments = useMemo(() => buildPreviewSegments(content), [content]);
  const reviewResolutionTrace = useMemo(() => getReviewResolutionTrace(section), [section]);
  const openReviewTaskCount = reviewTasks.length;
  const hasUnsavedChanges = useMemo(
    () => content.trim() !== String(section.content_md || '').trim(),
    [content, section.content_md],
  );
  const actionableReviewTasks = useMemo(
    () => reviewTasks.filter((task) => task.status === 'open' || task.status === 'in_progress' || task.status === 'rejected'),
    [reviewTasks],
  );
  const batchRevisionBrief = useMemo(() => buildSectionReviewBatchBrief(reviewTasks), [reviewTasks]);
  const sectionPlaceholderSuggestions = useMemo(
    () => buildSectionPlaceholderSuggestions(reviewTasks, section.global_param_snapshot),
    [reviewTasks, section.global_param_snapshot],
  );
  const prioritizedSuggestedBlocks = useMemo(() => {
    if (activeSuggestedBlockKeys.length === 0) {
      return batchRevisionBrief.suggestedBlocks;
    }
    const preferred = new Set(activeSuggestedBlockKeys);
    return [...batchRevisionBrief.suggestedBlocks].sort((left, right) => {
      const leftPreferred = preferred.has(left.key) ? 1 : 0;
      const rightPreferred = preferred.has(right.key) ? 1 : 0;
      return rightPreferred - leftPreferred;
    });
  }, [activeSuggestedBlockKeys, batchRevisionBrief.suggestedBlocks]);
  const recommendedSuggestedBlocks = useMemo(() => {
    if (activeSuggestedBlockKeys.length === 0) {
      return [] as ReviewTaskSuggestedBlock[];
    }
    const preferred = new Set(activeSuggestedBlockKeys);
    return prioritizedSuggestedBlocks.filter((block) => preferred.has(block.key));
  }, [activeSuggestedBlockKeys, prioritizedSuggestedBlocks]);
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
    setActiveLocateLabel(null);
    setActiveLocateBlockId(null);
    setActiveSuggestedBlockKeys([]);
    setEditorFocusBlockId(null);
  };

  const syncBlocksToContent = (nextBlocks: EditableBlock[]) => {
    setBlocks(nextBlocks);
    setContent(stringifyMarkdownBlocks(nextBlocks));
  };

  const saveSectionDraft = async ({ silent = false, keepEditing = false }: { silent?: boolean; keepEditing?: boolean } = {}) => {
    if (!hasUnsavedChanges) {
      return;
    }
    setSaving(true);
    try {
      await api.patch(`/projects/${projectId}/sections/${section.section_id}`, { content_md: content });
      if (!silent) {
        toast.success('Section saved');
      }
      if (!keepEditing) {
        setIsEditing(false);
        setActiveTab('preview');
      }
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error saving section'));
      throw error;
    } finally {
      setSaving(false);
    }
  };

  const handleSave = async () => {
    try {
      await saveSectionDraft();
      onRefresh();
    } catch {
      // saveSectionDraft already surfaced the error toast
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

  const handleBatchResolve = async () => {
    if (!sectionBatchResolution.trim() || actionableReviewTasks.length === 0) {
      return;
    }
    setBatchActing(true);
    try {
      await onBatchReviewTaskAction(actionableReviewTasks, sectionBatchResolution, 'resolved');
      setSectionBatchResolution('');
    } finally {
      setBatchActing(false);
    }
  };

  const handleSaveAndTaskAction = async (
    task: ReviewTask,
    resolution: string,
    status: ReviewTaskActionStatus,
  ) => {
    try {
      await saveSectionDraft({ silent: true, keepEditing: true });
    } catch {
      return;
    }
    await onReviewTaskAction(task, resolution, status);
  };

  const handleSaveAndBatchResolve = async () => {
    if (!sectionBatchResolution.trim() || actionableReviewTasks.length === 0) {
      return;
    }
    try {
      await saveSectionDraft({ silent: true, keepEditing: true });
    } catch {
      return;
    }
    await handleBatchResolve();
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

  const updateBlockSelection = (blockId: string, selectionStart: number | null | undefined) => {
    if (typeof selectionStart !== 'number' || Number.isNaN(selectionStart)) {
      return;
    }
    setBlockSelectionStartById((current) => {
      if (current[blockId] === selectionStart) {
        return current;
      }
      return {
        ...current,
        [blockId]: selectionStart,
      };
    });
  };

  const applyBlockFillSuggestion = (blockId: string, suggestion: BlockFillSuggestion) => {
    let updated = false;
    let nextSelectionStart: number | null = null;
    const nextBlocks = blocks.map((block) => {
      if (block.id !== blockId) {
        return block;
      }
      let nextContent = block.content;

      if (
        typeof suggestion.lineIndex === 'number' &&
        typeof suggestion.cellIndex === 'number'
      ) {
        const lines = block.content.split('\n');
        const line = lines[suggestion.lineIndex];
        const cells = line ? parseMarkdownTableLine(line) : null;
        if (
          line &&
          cells &&
          suggestion.cellIndex >= 0 &&
          suggestion.cellIndex < cells.length &&
          cells[suggestion.cellIndex]?.includes(suggestion.placeholder)
        ) {
          const nextCell = cells[suggestion.cellIndex].replace(suggestion.placeholder, suggestion.value);
          if (nextCell !== cells[suggestion.cellIndex]) {
            cells[suggestion.cellIndex] = nextCell;
            lines[suggestion.lineIndex] = stringifyMarkdownTableLine(cells);
            nextContent = lines.join('\n');
            nextSelectionStart = getLineStartOffset(nextContent, suggestion.lineIndex);
          }
        }
      } else if (block.content.includes(suggestion.placeholder)) {
        nextContent = block.content.replace(suggestion.placeholder, suggestion.value);
        nextSelectionStart = nextContent.indexOf(suggestion.value);
      }

      if (nextContent === block.content) {
        return block;
      }
      updated = true;
      return { ...block, content: nextContent };
    });

    if (!updated) {
      toast.message('当前块里没有可直接带入的位置');
      return;
    }

    syncBlocksToContent(nextBlocks);
    if (typeof nextSelectionStart === 'number') {
      setBlockSelectionStartById((current) => ({
        ...current,
        [blockId]: nextSelectionStart as number,
      }));
    }
    setEditorFocusBlockId(blockId);
    setLocateFocusNonce((current) => current + 1);
    toast.success(`已带入${suggestion.label}`);
  };

  const applyRowFillSuggestions = (blockId: string, suggestions: BlockFillSuggestion[]) => {
    if (suggestions.length === 0) {
      toast.message('当前行没有可批量带入的内容');
      return;
    }

    const rowSuggestions = getRowScopedFillSuggestions(suggestions, suggestions[0]?.lineIndex ?? null);
    if (rowSuggestions.length === 0) {
      toast.message('当前行没有可批量带入的内容');
      return;
    }

    let updated = false;
    let nextSelectionStart: number | null = null;
    const nextBlocks = blocks.map((block) => {
      if (block.id !== blockId) {
        return block;
      }

      const targetLineIndex = rowSuggestions[0]?.lineIndex;
      const lines = block.content.split('\n');
      const line = typeof targetLineIndex === 'number' ? lines[targetLineIndex] : null;
      const cells = line ? parseMarkdownTableLine(line) : null;
      if (typeof targetLineIndex !== 'number' || !line || !cells) {
        return block;
      }

      let localUpdated = false;
      for (const suggestion of rowSuggestions) {
        if (
          typeof suggestion.cellIndex !== 'number' ||
          suggestion.cellIndex < 0 ||
          suggestion.cellIndex >= cells.length
        ) {
          continue;
        }
        if (!cells[suggestion.cellIndex]?.includes(suggestion.placeholder)) {
          continue;
        }
        const nextCell = cells[suggestion.cellIndex].replace(suggestion.placeholder, suggestion.value);
        if (nextCell === cells[suggestion.cellIndex]) {
          continue;
        }
        cells[suggestion.cellIndex] = nextCell;
        localUpdated = true;
      }

      if (!localUpdated) {
        return block;
      }

      lines[targetLineIndex] = stringifyMarkdownTableLine(cells);
      updated = true;
      const nextContent = lines.join('\n');
      nextSelectionStart = getLineStartOffset(nextContent, targetLineIndex);
      return { ...block, content: nextContent };
    });

    if (!updated) {
      toast.message('当前行没有新的内容可带入');
      return;
    }

    syncBlocksToContent(nextBlocks);
    if (typeof nextSelectionStart === 'number') {
      setBlockSelectionStartById((current) => ({
        ...current,
        [blockId]: nextSelectionStart as number,
      }));
    }
    setEditorFocusBlockId(blockId);
    setLocateFocusNonce((current) => current + 1);
    toast.success(`已批量带入当前行 ${rowSuggestions.length} 项候选值`);
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

  const insertSuggestedMarkdown = (
    markdown: string,
    options?: { afterBlockId?: string | null; focusLabel?: string | null },
  ) => {
    const snippet = markdown.trim();
    if (!snippet) {
      return;
    }
    const currentBlocks = parseMarkdownBlocks(content);
    if (content.includes(snippet)) {
      const existingBlockId = locateInsertedBlockId(currentBlocks, snippet);
      if (existingBlockId) {
        setHighlightedBlockId(existingBlockId);
        setEditorFocusBlockId(existingBlockId);
        setLocateFocusNonce((current) => current + 1);
      }
      toast.message('This suggested block is already present in the draft');
      setActiveTab('guided');
      return;
    }

    const snippetBlocks = parseMarkdownBlocks(snippet);
    const insertionTargetId = options?.afterBlockId?.trim() || '';
    const insertionIndex = insertionTargetId ? currentBlocks.findIndex((block) => block.id === insertionTargetId) : -1;
    const mergedBlocks =
      insertionIndex >= 0
        ? [
            ...currentBlocks.slice(0, insertionIndex + 1),
            ...snippetBlocks,
            ...currentBlocks.slice(insertionIndex + 1),
          ]
        : [...currentBlocks, ...snippetBlocks];
    const nextContent = stringifyMarkdownBlocks(mergedBlocks);
    const nextBlocks = parseMarkdownBlocks(nextContent);
    const insertedBlockId =
      insertionIndex >= 0
        ? nextBlocks[insertionIndex + 1]?.id || null
        : locateInsertedBlockId(nextBlocks, snippet);
    setContent(nextContent);
    setBlocks(nextBlocks);
    setHighlightedBlockId(insertedBlockId);
    if (insertedBlockId) {
      setEditorFocusBlockId(insertedBlockId);
      setLocateFocusNonce((current) => current + 1);
    }
    if (options?.focusLabel?.trim()) {
      setActiveLocateLabel(options.focusLabel.trim());
      setActiveLocateBlockId(insertedBlockId);
    }
    setIsEditing(true);
    setActiveTab('guided');
    toast.success(insertionIndex >= 0 ? 'Suggested skeleton inserted near the current block' : 'Suggested skeleton inserted into the draft');
  };

  const insertAllSuggestedBlocks = () => {
    const combined = prioritizedSuggestedBlocks
      .map((item) => item.markdown.trim())
      .filter(Boolean)
      .join('\n\n');
    insertSuggestedMarkdown(combined);
  };

  const insertSuggestedMarkdownNearCurrentBlock = (markdown: string, label?: string) => {
    insertSuggestedMarkdown(markdown, {
      afterBlockId: activeLocateBlockId,
      focusLabel: label ? `骨架插入: ${label}` : '骨架插入',
    });
  };

  const insertAllSuggestedBlocksNearCurrentBlock = () => {
    const combined = prioritizedSuggestedBlocks
      .map((item) => item.markdown.trim())
      .filter(Boolean)
      .join('\n\n');
    insertSuggestedMarkdownNearCurrentBlock(combined, '批量骨架');
  };

  const locateDraftBlock = (query: string, label?: string, preferredSuggestedBlockKeys?: string[]): string | null => {
    const trimmedQuery = query.trim();
    if (!trimmedQuery) {
      return null;
    }
    if (!blocks.length) {
      toast.message('当前章节还没有可定位的草稿内容');
      return null;
    }

    const matchedBlockId = findBestMatchingBlockId(blocks, trimmedQuery);
    setActiveTab('guided');
    setIsEditing(true);

    if (!matchedBlockId) {
      setActiveLocateLabel(null);
      setActiveLocateBlockId(null);
      setActiveSuggestedBlockKeys([]);
      toast.message(label ? `当前草稿里还没找到“${label}”对应段落` : '当前草稿里还没找到对应段落');
      return null;
    }

    const nextSuggestedBlockKeys =
      preferredSuggestedBlockKeys && preferredSuggestedBlockKeys.length > 0
        ? preferredSuggestedBlockKeys
        : findPreferredSuggestedBlockKeys(batchRevisionBrief.suggestedBlocks, label?.trim() || trimmedQuery);
    setActiveLocateLabel(label?.trim() || trimmedQuery);
    setActiveLocateBlockId(matchedBlockId);
    setActiveSuggestedBlockKeys(nextSuggestedBlockKeys);
    setHighlightedBlockId(matchedBlockId);
    setEditorFocusBlockId(matchedBlockId);
    setLocateFocusNonce((current) => current + 1);
    return matchedBlockId;
  };

  const handleBatchLocate = (query: string, label?: string) => {
    if (!sectionBatchResolution.trim() && batchRevisionBrief.resolutionTemplate.trim()) {
      setSectionBatchResolution(batchRevisionBrief.resolutionTemplate);
    }
    locateDraftBlock(
      query,
      label,
      findPreferredSuggestedBlockKeys(batchRevisionBrief.suggestedBlocks, label?.trim() || query),
    );
  };

  const applySuggestedBlocks = (options: {
    suggestedBlocks: ReviewTaskSuggestedBlock[];
    preferredSuggestedBlockKeys?: string[];
    locateQuery?: string;
    locateLabel?: string;
    focusLabel?: string;
  }) => {
    const combined = options.suggestedBlocks.map((item) => item.markdown.trim()).filter(Boolean).join('\n\n');
    if (!combined) {
      return;
    }

    let targetBlockId = activeLocateBlockId;
    if (options.locateQuery?.trim()) {
      targetBlockId = locateDraftBlock(
        options.locateQuery,
        options.locateLabel,
        options.preferredSuggestedBlockKeys,
      ) || targetBlockId;
    } else if (options.preferredSuggestedBlockKeys && options.preferredSuggestedBlockKeys.length > 0) {
      setActiveSuggestedBlockKeys(options.preferredSuggestedBlockKeys);
    }

    insertSuggestedMarkdown(combined, {
      afterBlockId: targetBlockId,
      focusLabel: options.focusLabel || (options.locateLabel ? `骨架插入: ${options.locateLabel}` : '骨架插入'),
    });
  };

  const handleInsertRecommendedAndFillBatchNote = () => {
    if (!sectionBatchResolution.trim() && batchRevisionBrief.resolutionTemplate.trim()) {
      setSectionBatchResolution(batchRevisionBrief.resolutionTemplate);
    }
    if (recommendedSuggestedBlocks.length === 0) {
      return;
    }
    applySuggestedBlocks({
      suggestedBlocks: recommendedSuggestedBlocks,
      preferredSuggestedBlockKeys: recommendedSuggestedBlocks.map((block) => block.key),
      locateQuery: activeLocateLabel || undefined,
      locateLabel: activeLocateLabel || undefined,
      focusLabel: '骨架插入: 推荐骨架',
    });
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
                  <>
                    {activeLocateLabel ? (
                      <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 px-4 py-3">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div className="flex min-w-0 items-center gap-2">
                            <Target className="h-4 w-4 text-emerald-700" />
                            <p className="min-w-0 text-sm font-medium text-emerald-950">
                              当前定位: <span className="font-semibold">{activeLocateLabel}</span>
                            </p>
                          </div>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 px-2 text-xs text-emerald-800 hover:text-emerald-950"
                            onClick={() => {
                              setActiveLocateLabel(null);
                              setActiveLocateBlockId(null);
                              setActiveSuggestedBlockKeys([]);
                              setHighlightedBlockId(null);
                            }}
                          >
                            清除定位
                          </Button>
                        </div>
                      </div>
                    ) : null}

                    {blocks.map((block) => {
                      const placeholderHints = getPlaceholderHints(block.content);
                      const activeLineIndex =
                        typeof blockSelectionStartById[block.id] === 'number'
                          ? lineIndexFromOffset(block.content, blockSelectionStartById[block.id] as number)
                          : null;
                      const allBlockFillSuggestions = getBlockFillSuggestions(
                        block.content,
                        sectionPlaceholderSuggestions,
                        activeLineIndex,
                      );
                      const blockFillSuggestions = allBlockFillSuggestions.slice(0, 6);
                      const currentRowFillSuggestions = getRowScopedFillSuggestions(allBlockFillSuggestions, activeLineIndex);
                      const currentRowLabel =
                        currentRowFillSuggestions.find((item) => item.rowDescriptor)?.rowDescriptor || '当前行';
                      return !isEditing ? (
                        <div
                          key={block.id}
                          ref={(node) => {
                            blockRefs.current[block.id] = node;
                          }}
                          className={
                            highlightedBlockId === block.id
                              ? 'rounded-2xl ring-2 ring-emerald-300 ring-offset-2 ring-offset-white transition-all'
                              : ''
                          }
                        >
                          <ReadonlyBlock block={block} section={section} assetLookup={assetLookup} />
                        </div>
                      ) : (
                        <div
                          key={block.id}
                          ref={(node) => {
                            blockRefs.current[block.id] = node;
                          }}
                          className={`rounded-2xl border p-4 transition-all ${
                            highlightedBlockId === block.id
                              ? 'border-emerald-300 bg-emerald-50/80 ring-2 ring-emerald-300 ring-offset-2 ring-offset-white'
                              : 'border-slate-200 bg-slate-50/70'
                          }`}
                        >
                          <div className="mb-3 flex items-center justify-between gap-3">
                            <div className="flex items-center gap-2">
                              <Badge variant="outline">{blockLabel(block.kind)}</Badge>
                            </div>
                            {activeLocateBlockId === block.id ? (
                              <Badge variant="success" className="bg-emerald-100 text-emerald-800">
                                定位命中
                              </Badge>
                            ) : highlightedBlockId === block.id ? (
                              <Badge variant="success" className="bg-emerald-100 text-emerald-800">
                                New block
                              </Badge>
                            ) : null}
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
                              onSelect={(event) => updateBlockSelection(block.id, event.currentTarget.selectionStart)}
                              onClick={(event) => updateBlockSelection(block.id, event.currentTarget.selectionStart)}
                              onKeyUp={(event) => updateBlockSelection(block.id, event.currentTarget.selectionStart)}
                              className={
                                block.kind === 'table'
                                  ? 'min-h-[180px] font-mono text-xs'
                                  : 'min-h-[120px] text-sm leading-7'
                              }
                            />
                          )}

                          {placeholderHints.length > 0 ? (
                            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-600">
                              <span className="font-medium text-slate-700">建议先替换：</span>
                              {placeholderHints.map((item) => (
                                <Badge
                                  key={`${block.id}-placeholder-hint-${item}`}
                                  variant="outline"
                                  className="border-amber-200 bg-amber-50 text-amber-800"
                                >
                                  {item}
                                </Badge>
                              ))}
                            </div>
                          ) : null}

                          {blockFillSuggestions.length > 0 ? (
                            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-600">
                              <span className="font-medium text-slate-700">可直接带入：</span>
                              {block.kind === 'table' && currentRowFillSuggestions.length > 1 ? (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="h-7 border-emerald-300 bg-emerald-50 px-2 text-[11px] text-emerald-900 hover:bg-emerald-100"
                                  onClick={() => applyRowFillSuggestions(block.id, currentRowFillSuggestions)}
                                  title={`批量带入 ${currentRowLabel}`}
                                >
                                  批量带入当前行
                                </Button>
                              ) : null}
                              {blockFillSuggestions.map((item) => (
                                <Button
                                  key={`${block.id}-fill-suggestion-${item.key}-${item.placeholder}`}
                                  size="sm"
                                  variant="outline"
                                  className="h-7 max-w-full border-emerald-200 bg-white px-2 text-[11px] text-emerald-800 hover:bg-emerald-50 hover:text-emerald-900"
                                  onClick={() => applyBlockFillSuggestion(block.id, item)}
                                  title={`${item.label}: ${item.value}`}
                                >
                                  <span className="truncate">{item.label}: {item.value}</span>
                                </Button>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </>
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
                  <h4 className="text-sm font-semibold text-slate-900">Open Review Tasks</h4>
                  <p className="text-xs text-slate-500">在章节上下文里直接处理当前 review task。</p>
                </div>
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-slate-400" />
                  <Badge variant={openReviewTaskCount > 0 ? 'warning' : 'outline'}>{openReviewTaskCount}</Badge>
                </div>
              </div>

              {reviewTasks.length > 1 ? (
                <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50/70 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-emerald-800">
                        Section Batch Revision Brief
                      </p>
                      <p className="mt-1 text-xs leading-5 text-emerald-900">
                        合并同章节多个 review issues，先按一份修订清单完成正文调整，再批量 resolve。
                      </p>
                    </div>
                    {batchRevisionBrief.taskCodes.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {batchRevisionBrief.taskCodes.map((code, index) => (
                          <Badge key={`${section.section_id}-batch-code-${code}-${index}`} variant="outline" className="bg-white/80">
                            {code}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                  </div>

                      {batchRevisionBrief.checklist.length > 0 ? (
                    <div className="mt-3 space-y-1.5 text-xs leading-5 text-emerald-950">
                      {batchRevisionBrief.checklist.map((item, index) => (
                        <div
                          key={`${section.section_id}-batch-check-${index}`}
                          className={`flex items-start justify-between gap-3 rounded-md border px-2.5 py-2 ${
                            activeLocateLabel === item
                              ? 'border-emerald-300 bg-emerald-100/70'
                              : 'border-emerald-200/80 bg-white/75'
                          }`}
                        >
                          <p className="min-w-0 flex-1">{index + 1}. {item}</p>
                          <Button
                            size="sm"
                            variant="ghost"
                            className={`h-6 shrink-0 px-2 text-[11px] ${
                              activeLocateLabel === item ? 'text-emerald-900 hover:text-emerald-950' : 'text-emerald-700 hover:text-emerald-900'
                            }`}
                            onClick={() => handleBatchLocate(item, item)}
                          >
                            定位
                          </Button>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  {batchRevisionBrief.contentAnchors.length > 0 ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {batchRevisionBrief.contentAnchors.map((item, index) => (
                        <button
                          key={`${section.section_id}-batch-anchor-${item}-${index}`}
                          type="button"
                          onClick={() => handleBatchLocate(item, item)}
                          className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] transition ${
                            activeLocateLabel === item
                              ? 'border-emerald-300 bg-emerald-100 text-emerald-950'
                              : 'border-emerald-200 bg-white text-emerald-800 hover:border-emerald-300 hover:bg-emerald-50'
                          }`}
                        >
                          {item}
                        </button>
                      ))}
                    </div>
                  ) : null}

                  {batchRevisionBrief.suggestedBlocks.length > 0 ? (
                    <div className="mt-3 space-y-3 border-t border-emerald-200/80 pt-3">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-emerald-800">
                            Suggested Skeleton Blocks
                          </p>
                          {activeLocateLabel && recommendedSuggestedBlocks.length > 0 ? (
                            <p className="mt-1 text-xs text-emerald-900">
                              当前优先推荐：{activeLocateLabel}
                            </p>
                          ) : null}
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          className="border-emerald-200 bg-white/90"
                          onClick={insertAllSuggestedBlocks}
                        >
                          <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                          Insert All Skeletons
                        </Button>
                        {activeLocateBlockId ? (
                          <Button
                            size="sm"
                            variant="outline"
                            className="border-emerald-300 bg-emerald-50/80 text-emerald-900"
                            onClick={insertAllSuggestedBlocksNearCurrentBlock}
                          >
                            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                            Insert All Near Current Block
                          </Button>
                        ) : null}
                        {activeLocateBlockId && recommendedSuggestedBlocks.length > 0 ? (
                          <Button
                            size="sm"
                            variant="outline"
                            className="border-emerald-400 bg-emerald-100 text-emerald-950"
                            onClick={handleInsertRecommendedAndFillBatchNote}
                          >
                            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                            Insert Recommended + Fill Note
                          </Button>
                        ) : null}
                      </div>

                      {prioritizedSuggestedBlocks.map((block, index) => (
                        <div
                          key={`${section.section_id}-suggested-block-${block.key}-${index}`}
                          className={`rounded-md border p-3 ${
                            activeSuggestedBlockKeys.includes(block.key)
                              ? 'border-emerald-300 bg-emerald-50/75'
                              : 'border-emerald-200/80 bg-white/90'
                          }`}
                        >
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="flex items-center gap-2">
                              <Badge variant="outline" className="bg-white">
                                {block.kind}
                              </Badge>
                              {activeSuggestedBlockKeys.includes(block.key) ? (
                                <Badge variant="success" className="bg-emerald-100 text-emerald-900">
                                  Recommended Now
                                </Badge>
                              ) : null}
                              <p className="text-sm font-medium text-slate-900">{block.title}</p>
                            </div>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-7 px-2 text-xs"
                              onClick={() => insertSuggestedMarkdown(block.markdown)}
                            >
                              Insert into Draft
                            </Button>
                            {activeLocateBlockId ? (
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-7 px-2 text-xs text-emerald-800"
                                onClick={() => insertSuggestedMarkdownNearCurrentBlock(block.markdown, block.title)}
                              >
                                Insert Near Current Block
                              </Button>
                            ) : null}
                          </div>
                          <div className="mt-3 rounded-md border border-slate-200 bg-white p-3">
                            <MarkdownArticle markdown={block.markdown} compact />
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  <div className="mt-3 space-y-3 border-t border-emerald-200/80 pt-3">
                    <Textarea
                      placeholder="Enter one combined note for this section after you finish the merged revision."
                      value={sectionBatchResolution}
                      onChange={(event) => setSectionBatchResolution(event.target.value)}
                      className="min-h-[96px] bg-white/90 text-sm"
                      disabled={batchActing || actionableReviewTasks.length === 0}
                    />
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setSectionBatchResolution(batchRevisionBrief.resolutionTemplate)}
                        disabled={batchActing || saving || actionableReviewTasks.length === 0}
                      >
                        <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                        Use Suggested Batch Note
                      </Button>
                      {hasUnsavedChanges ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void handleSaveAndBatchResolve()}
                          disabled={batchActing || saving || !sectionBatchResolution.trim() || actionableReviewTasks.length === 0}
                        >
                          {batchActing || saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                          Save Draft + Resolve
                        </Button>
                      ) : null}
                      <Button
                        size="sm"
                        className="bg-emerald-600 text-white hover:bg-emerald-700"
                        onClick={() => void handleBatchResolve()}
                        disabled={batchActing || saving || !sectionBatchResolution.trim() || actionableReviewTasks.length === 0}
                      >
                        {batchActing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                        Resolve Section Tasks
                      </Button>
                    </div>
                  </div>
                </div>
              ) : null}

              {reviewTasks.length > 0 ? (
                <div className="space-y-3">
                  {reviewTasks.map((task) => (
                    <SectionReviewTaskCard
                      key={task.id}
                      task={task}
                      isFocused={focusedReviewTaskId === task.id}
                      isActing={actingTaskId === task.id}
                      isSavingDraft={saving}
                      hasUnsavedChanges={hasUnsavedChanges}
                      activeLocateLabel={activeLocateLabel}
                      onAction={onReviewTaskAction}
                      onSaveAndAction={handleSaveAndTaskAction}
                      onLocate={locateDraftBlock}
                      onApplySuggested={applySuggestedBlocks}
                    />
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-xs text-slate-500">
                  当前章节没有待处理的 review task。已处理结果会沉淀到下面的 trace 面板。
                </div>
              )}
            </div>

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
                    const isSelected = selectedCitationIds.includes(citationId);
                    const sourceContent = resolveCitationSourceContent(section, citation, evidenceMap);
                    return (
                      <div
                        key={citationId}
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

                {selectedSections.length > 0 ? (
                  <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Selected Sections</p>
                    {selectedSections.map((item, index) => (
                      <div key={`${item.section_id || item.section_path || 'section'}-${index}`} className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-slate-900">{item.file_name || item.source_heading || '历史章节'}</p>
                            <p className="mt-1 text-xs leading-5 text-slate-500">{item.section_path || '未标注章节路径'}</p>
                          </div>
                          {typeof item.score === 'number' ? (
                            <Badge variant="outline">{item.score.toFixed(2)}</Badge>
                          ) : null}
                        </div>
                        {item.reason ? <p className="mt-2 text-xs text-slate-600">{item.reason}</p> : null}
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
                    {selectedBlocks.map((item, index) => (
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
                        {item.selection_reasons?.length ? (
                          <p className="mt-2 text-xs text-slate-600">{item.selection_reasons.join(' / ')}</p>
                        ) : null}
                      </div>
                    ))}
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
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h4 className="text-sm font-semibold text-slate-900">Review Resolution Trace</h4>
                  <p className="text-xs text-slate-500">保留本章 review task 的处理结论，方便回看修改脉络。</p>
                </div>
                <Badge variant="outline">{reviewResolutionTrace.length} entries</Badge>
              </div>

              {reviewResolutionTrace.length > 0 ? (
                <div className="space-y-3">
                  {reviewResolutionTrace
                    .slice(-4)
                    .reverse()
                    .map((entry, index) => {
                      const detailSummary = summarizeTraceDetails(entry.details);
                      return (
                        <div
                          key={`${entry.task_id || entry.code || entry.task_type || 'trace'}-${index}`}
                          className="rounded-xl border border-slate-200 bg-slate-50/70 p-3"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            {entry.code ? <Badge variant="outline">{entry.code}</Badge> : null}
                            <Badge variant="secondary" className="bg-slate-100 text-slate-700">
                              {formatTraceTaskType(entry.task_type)}
                            </Badge>
                            <Badge variant={entry.status === 'resolved' ? 'success' : 'warning'}>
                              {formatTraceStatus(entry.status)}
                            </Badge>
                          </div>
                          {entry.message ? (
                            <p className="mt-3 text-sm font-medium leading-6 text-slate-900">{entry.message}</p>
                          ) : null}
                          <div className="mt-2 space-y-1 text-xs leading-5 text-slate-600">
                            <p>处理结果: {formatTraceValue(entry.resolution)}</p>
                            {entry.suggested_action ? <p>建议动作: {entry.suggested_action}</p> : null}
                            {detailSummary ? <p>关键信息: {detailSummary}</p> : null}
                            <p>{formatTraceTimestamp(entry.resolved_at)}</p>
                          </div>
                        </div>
                      );
                    })}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-xs text-slate-500">
                  当前还没有 review resolution trace。处理 validation task 后，这里会沉淀本章的修订痕迹。
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
