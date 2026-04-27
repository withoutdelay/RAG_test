'use client';

import { useMemo } from 'react';
import { ExternalLink, FileText, Image as ImageIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { Citation, EvidenceCard, RecommendedAsset, SectionDraft } from '@/lib/types';
import { MarkdownArticle } from './MarkdownArticle';
import { AssetPreviewCard } from './AssetPreviewCard';
import { markdownTableCandidate } from './sectionBlockHelpers';
import {
  renderCitationLabel,
  resolveCitationSourceContent,
  normalizeHeadingPath,
} from './sectionBlockUtils';


interface MaterialDetailDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  type: 'citation' | 'asset';
  citation?: Citation;
  asset?: RecommendedAsset;
  section: SectionDraft;
  evidenceMap: Record<string, EvidenceCard>;
  isSelected?: boolean;
  onToggleSelect?: () => void;
  onRegenerateWith?: () => void;
  onInsertAsset?: () => void;
  regenerating?: boolean;
}


function ScoreCard({ label, value }: { label: string; value: string | number }) {
  const displayValue = typeof value === 'number' ? value.toFixed(2) : value;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-bold text-slate-900">{displayValue}</p>
    </div>
  );
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
      <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</p>
      {children}
    </div>
  );
}


export function MaterialDetailDialog({
  open,
  onOpenChange,
  type,
  citation,
  asset,
  section,
  evidenceMap,
  isSelected,
  onToggleSelect,
  onRegenerateWith,
  onInsertAsset,
  regenerating,
}: MaterialDetailDialogProps) {
  const title = type === 'citation'
    ? (citation?.source_title || 'Citation Detail')
    : (asset?.display_title || asset?.title || asset?.caption || 'Asset Detail');

  const subtitle = type === 'citation'
    ? (citation ? renderCitationLabel(citation) : '')
    : [asset?.document_name, asset?.heading_path].filter(Boolean).join(' / ');

  const citationContent = useMemo(() => {
    if (type !== 'citation' || !citation) return '';
    return resolveCitationSourceContent(section, citation, evidenceMap);
  }, [type, citation, section, evidenceMap]);

  const metadata = (asset?.metadata || {}) as Record<string, unknown>;
  const breakdown = asset?.score_breakdown || (metadata.retrieval_score_breakdown as Record<string, number | string> | undefined);

  const scoreEntries = useMemo(() => {
    if (type === 'citation' && citation?.relevance_score !== undefined) {
      return [{ label: 'Relevance', value: Number(citation.relevance_score) }];
    }
    if (!breakdown || typeof breakdown !== 'object') return [];
    const entries: { label: string; value: number }[] = [];
    if (typeof breakdown.textual === 'number') entries.push({ label: 'Textual', value: breakdown.textual });
    if (typeof breakdown.visual === 'number') entries.push({ label: 'Visual', value: breakdown.visual });
    if (typeof breakdown.structural === 'number') entries.push({ label: 'Structural', value: breakdown.structural });
    if (typeof breakdown.final === 'number') entries.push({ label: 'Final', value: breakdown.final });
    if (entries.length === 0 && asset?.score !== undefined) {
      entries.push({ label: 'Score', value: asset.score });
    }
    return entries;
  }, [type, citation, breakdown, asset]);

  const visualBackend =
    typeof breakdown?.visual_backend === 'string'
      ? String(breakdown.visual_backend)
      : typeof metadata.visual_backend === 'string'
        ? String(metadata.visual_backend)
        : '';
  const visualSource =
    typeof breakdown?.visual_source === 'string'
      ? String(breakdown.visual_source)
      : typeof metadata.visual_source === 'string'
        ? String(metadata.visual_source)
        : '';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[80vw] md:max-w-4xl lg:max-w-5xl xl:max-w-6xl h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {type === 'citation' ? <FileText className="h-5 w-5 text-slate-500" /> : <ImageIcon className="h-5 w-5 text-slate-500" />}
            {title}
          </DialogTitle>
          <DialogDescription>
            <span className="flex flex-wrap items-center gap-2">
              <span>{subtitle}</span>
              {type === 'asset' && asset?.asset_type && (
                <Badge variant="outline">{asset.asset_type}</Badge>
              )}
              {type === 'citation' && citation?.type && (
                <Badge variant="outline">{citation.type}</Badge>
              )}
              {asset?.page_no ? <span className="text-xs">Page {asset.page_no}</span> : null}
            </span>
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 space-y-4 overflow-y-auto pr-1">
          {/* Content preview */}
          {type === 'citation' && citationContent ? (
            <DetailSection title="内容预览">
              {markdownTableCandidate(citationContent) || citationContent.includes('#') ? (
                <MarkdownArticle markdown={citationContent} compact />
              ) : (
                <div className="whitespace-pre-wrap text-sm leading-7 text-slate-700">{citationContent}</div>
              )}
            </DetailSection>
          ) : null}

          {type === 'asset' && asset ? (
            <DetailSection title="资产预览">
              <AssetPreviewCard asset={asset} section={section} />
            </DetailSection>
          ) : null}

          {/* Detailed info */}
          <DetailSection title="详细信息">
            <div className="grid gap-2 text-sm">
              {type === 'citation' && citation?.source_title && (
                <div className="flex gap-2">
                  <span className="shrink-0 font-medium text-slate-600 w-20">来源文档</span>
                  <span className="text-slate-700">{citation.source_title}</span>
                </div>
              )}
              {type === 'citation' && citation?.heading_path && (
                <div className="flex gap-2">
                  <span className="shrink-0 font-medium text-slate-600 w-20">章节路径</span>
                  <span className="text-slate-700">{normalizeHeadingPath(citation.heading_path)}</span>
                </div>
              )}
              {type === 'asset' && asset?.document_name && (
                <div className="flex gap-2">
                  <span className="shrink-0 font-medium text-slate-600 w-20">来源文档</span>
                  <span className="text-slate-700">{asset.document_name}</span>
                </div>
              )}
              {type === 'asset' && asset?.heading_path && (
                <div className="flex gap-2">
                  <span className="shrink-0 font-medium text-slate-600 w-20">章节路径</span>
                  <span className="text-slate-700">{asset.heading_path}</span>
                </div>
              )}
              {type === 'asset' && asset?.visual_role && (
                <div className="flex gap-2">
                  <span className="shrink-0 font-medium text-slate-600 w-20">视觉角色</span>
                  <span className="text-slate-700">{asset.visual_role}</span>
                </div>
              )}
              {asset?.reason && (
                <div className="flex gap-2">
                  <span className="shrink-0 font-medium text-slate-600 w-20">命中原因</span>
                  <span className="text-slate-700">{asset.reason}</span>
                </div>
              )}
              {(visualBackend || visualSource) && (
                <div className="flex gap-2">
                  <span className="shrink-0 font-medium text-slate-600 w-20">视觉通道</span>
                  <span className="text-slate-700">{visualBackend || 'disabled'}{visualSource ? ` / ${visualSource}` : ''}</span>
                </div>
              )}
            </div>
          </DetailSection>

          {/* Score breakdown */}
          {scoreEntries.length > 0 && (
            <DetailSection title="检索分数">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {scoreEntries.map((entry) => (
                  <ScoreCard key={entry.label} label={entry.label} value={entry.value} />
                ))}
              </div>
              {Array.isArray(asset?.reason_trace) && asset.reason_trace.length > 0 && (
                <p className="mt-3 text-xs text-slate-500">{asset.reason_trace.slice(0, 5).join(' / ')}</p>
              )}
            </DetailSection>
          )}
        </div>

        <DialogFooter className="gap-2">
          {onToggleSelect && (
            <Button variant="outline" onClick={onToggleSelect}>
              {isSelected ? '取消选择' : '选中此依据'}
            </Button>
          )}
          {onRegenerateWith && (
            <Button variant="outline" onClick={onRegenerateWith} disabled={regenerating}>
              <ExternalLink className="mr-2 h-4 w-4" />
              仅用此依据重写
            </Button>
          )}
          {onInsertAsset && (
            <Button onClick={onInsertAsset}>
              插入正文
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
