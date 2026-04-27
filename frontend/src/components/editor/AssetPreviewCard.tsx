'use client';

import { useState } from 'react';
import { ExternalLink, Eye } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { buildAssetContentUrl } from '@/lib/api';
import type { RecommendedAsset, SectionDraft } from '@/lib/types';
import { MarkdownArticle } from './MarkdownArticle';
import { isVisualAsset, markdownTableCandidate } from './sectionBlockHelpers';
import {
  findReusableBlockPreviewContent,
  getAssetTableMarkdown,
} from './sectionBlockUtils';


export function AssetPreviewCard({
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
  const tableMarkdown = getAssetTableMarkdown(asset);
  const supportContent = asset && !visualAsset ? tableMarkdown || findReusableBlockPreviewContent(section, asset) : undefined;
  const previewMarkdown = supportContent?.trim() || asset?.preview_text || '';
  const hasMarkdownTablePreview = Boolean(asset?.asset_type === 'table' && markdownTableCandidate(previewMarkdown));
  const title = placeholderLabel || asset?.display_title || asset?.title || asset?.caption || '已插入图表引用';
  const currentAssetId = asset?.asset_id || '';
  const imageFailed = imageState.assetId === currentAssetId ? imageState.failed : false;
  const binaryPreview = visualAsset || (asset?.asset_type === 'table' && !hasMarkdownTablePreview);
  const assetContentUrl = asset?.asset_id && binaryPreview ? buildAssetContentUrl(asset.asset_id) : null;
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
      {assetContentUrl && !imageFailed ? (
        <div className="mt-3 overflow-hidden rounded-[1.25rem] border border-white/80 bg-white/90 p-3 shadow-[0_20px_50px_-28px_rgba(15,23,42,0.45)]">
          <div className="relative overflow-hidden rounded-[1rem] border border-slate-200/80 bg-slate-50">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={assetContentUrl}
              alt={title}
              loading="lazy"
              className={`block w-full bg-transparent object-contain ${embedded ? 'max-h-[300px]' : asset?.asset_type === 'table' ? 'max-h-[560px]' : 'max-h-[420px]'}`}
              onError={() => setImageState({ assetId: currentAssetId, failed: true })}
            />
            {asset?.asset_type !== 'table' ? <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-slate-50/85" /> : null}
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-100 bg-emerald-50 px-2.5 py-1 font-medium text-emerald-700">
                <Eye className="h-3.5 w-3.5" />
                {asset?.asset_type === 'table' ? '表格原图预览' : '原图预览'}
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
          {assetContentUrl && imageFailed ? (
            <p className="mt-3 text-xs text-amber-700">原始资产加载失败，当前已回退到文本摘要视图。</p>
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
