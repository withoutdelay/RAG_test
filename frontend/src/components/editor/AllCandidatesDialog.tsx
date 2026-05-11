'use client';

import { useState, useRef, useEffect } from 'react';
import { ChevronDown, ChevronRight, FileText, GalleryVerticalEnd, Layers3 } from 'lucide-react';
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
  buildAssetPlaceholder,
} from './sectionBlockUtils';

function HorizontalScrollContainer({ children }: { children: React.ReactNode }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleWheel = (e: WheelEvent) => {
      const isScrollable = container.scrollWidth > container.clientWidth;
      if (!isScrollable) return;

      // If scrolling mostly vertically
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        e.preventDefault();
        container.scrollLeft += e.deltaY;
      }
    };

    container.addEventListener('wheel', handleWheel, { passive: false });
    
    return () => {
      container.removeEventListener('wheel', handleWheel);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="flex items-start gap-4 overflow-x-auto pb-4 px-1 snap-x scroll-smooth"
      style={{ scrollbarWidth: 'thin' }}
    >
      {children}
    </div>
  );
}


interface AllCandidatesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  section: SectionDraft;
  evidenceMap: Record<string, EvidenceCard>;
  citations: Citation[];
  recommendedAssets: RecommendedAsset[];
  assetCandidates: RecommendedAsset[];
  filteredAssets?: RecommendedAsset[];
  selectedCitationIds: string[];
  onToggleCitationSelection: (citation: Citation) => void;
  onRegenerateWith: (ids?: string[]) => void;
  onInsertAsset: (asset: RecommendedAsset) => void;
  contentMd: string;
  regenerating: boolean;
}


function CategorySection({
  title,
  icon: Icon,
  count,
  children,
  defaultOpen = true,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  count: number;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  if (count === 0) return null;

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/50 overflow-hidden">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-100/50"
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-slate-500" />
          <span className="text-sm font-semibold text-slate-800">{title}</span>
          <Badge variant="secondary" className="text-xs">{count}</Badge>
        </div>
        {isOpen ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
      </button>
      {isOpen && (
        <div className="border-t border-slate-200 p-4">
          {children}
        </div>
      )}
    </div>
  );
}


function CitationRow({
  citation,
  index,
  section,
  evidenceMap,
  isSelected,
  onToggleSelect,
  onRegenerateWith,
  regenerating,
}: {
  citation: Citation;
  index: number;
  section: SectionDraft;
  evidenceMap: Record<string, EvidenceCard>;
  isSelected: boolean;
  onToggleSelect: () => void;
  onRegenerateWith: () => void;
  regenerating: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const sourceContent = resolveCitationSourceContent(section, citation, evidenceMap);

  return (
    <div
      className={`w-[360px] shrink-0 flex flex-col rounded-xl border transition-colors snap-start ${
        isSelected
          ? 'border-blue-300 bg-blue-50'
          : 'border-slate-200 bg-white hover:border-slate-300'
      }`}
    >
      <button
        type="button"
        className="flex w-full items-start justify-between gap-3 p-3 text-left"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-900">{citation.source_title || `Citation ${index + 1}`}</span>
            {citation.type && <Badge variant="outline" className="text-xs">{citation.type}</Badge>}
          </div>
          <p className="mt-1 text-xs text-slate-500 truncate">{renderCitationLabel(citation)}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {citation.relevance_score !== undefined && (
            <Badge variant="outline">{Number(citation.relevance_score).toFixed(2)}</Badge>
          )}
          {expanded ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-slate-200 p-3 space-y-3">
          <div 
            className="max-h-[300px] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-3 custom-scrollbar"
            onWheel={(e) => e.stopPropagation()}
          >
            {sourceContent ? (
              markdownTableCandidate(sourceContent) || sourceContent.includes('#') ? (
                <MarkdownArticle markdown={sourceContent} compact />
              ) : (
                <div className="whitespace-pre-wrap text-sm leading-7 text-slate-700">{sourceContent}</div>
              )
            ) : (
              <p className="text-sm text-slate-500">暂无原文内容</p>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" onClick={onToggleSelect}>
              {isSelected ? '取消选择' : '选中此依据'}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={onRegenerateWith}
              disabled={regenerating}
            >
              仅用此依据重写
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}


function AssetRow({
  asset,
  section,
  contentMd,
  onInsert,
}: {
  asset: RecommendedAsset;
  section: SectionDraft;
  contentMd: string;
  onInsert: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const placeholder = buildAssetPlaceholder(asset);
  const alreadyUsed = placeholder ? contentMd.includes(placeholder) : false;

  return (
    <div className={`w-[360px] shrink-0 flex flex-col rounded-xl border transition-colors snap-start ${
      asset.review_required ? 'border-amber-300 bg-amber-50/30' : 'border-slate-200 bg-white hover:border-slate-300'
    }`}>
      <button
        type="button"
        className="flex w-full items-start justify-between gap-3 p-3 text-left"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-900">{asset.display_title || asset.title || asset.caption || 'Asset'}</span>
            <Badge variant="outline" className="text-xs">{asset.asset_type}</Badge>
            {alreadyUsed && <Badge variant="secondary" className="text-xs bg-emerald-100 text-emerald-700">已插入</Badge>}
          </div>
          <p className="mt-1 text-xs text-slate-500 truncate">
            {[asset.document_name, asset.heading_path].filter(Boolean).join(' / ')}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {asset.score !== undefined && (
            <Badge variant="outline">{asset.score.toFixed(2)}</Badge>
          )}
          {expanded ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-slate-200 p-3 space-y-3">
          <AssetPreviewCard asset={asset} section={section} />
          <div className="flex justify-end">
            <Button size="sm" variant="outline" onClick={onInsert}>
              {alreadyUsed ? '再次定位到该图表' : '插入正文'}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}


export function AllCandidatesDialog({
  open,
  onOpenChange,
  section,
  evidenceMap,
  citations,
  recommendedAssets,
  assetCandidates,
  filteredAssets = [],
  selectedCitationIds,
  onToggleCitationSelection,
  onRegenerateWith,
  onInsertAsset,
  contentMd,
  regenerating,
}: AllCandidatesDialogProps) {
  const totalCount = citations.length + recommendedAssets.length + assetCandidates.length + filteredAssets.length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[90vw] xl:max-w-[1400px] h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Layers3 className="h-5 w-5 text-slate-500" />
            全部候选材料
            <Badge variant="secondary">{totalCount}</Badge>
          </DialogTitle>
          <DialogDescription>
            {section.title} — 按类别浏览所有候选材料，点击展开查看详情
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 space-y-4 overflow-y-auto pr-1">
          <CategorySection title="Citations" icon={FileText} count={citations.length}>
            <HorizontalScrollContainer>
              {citations.map((citation, index) => {
                const citationId = citation.evidence_id || `citation-${index}`;
                return (
                  <CitationRow
                    key={`${citationId}-${index}`}
                    citation={citation}
                    index={index}
                    section={section}
                    evidenceMap={evidenceMap}
                    isSelected={selectedCitationIds.includes(citationId)}
                    onToggleSelect={() => onToggleCitationSelection(citation)}
                    onRegenerateWith={() => onRegenerateWith(citation.evidence_id ? [citation.evidence_id] : undefined)}
                    regenerating={regenerating}
                  />
                );
              })}
            </HorizontalScrollContainer>
          </CategorySection>

          <CategorySection title="Recommended Assets" icon={GalleryVerticalEnd} count={recommendedAssets.length}>
            <HorizontalScrollContainer>
              {recommendedAssets.map((asset, index) => (
                <AssetRow
                  key={`recommended-${asset.asset_id || asset.title || index}`}
                  asset={asset}
                  section={section}
                  contentMd={contentMd}
                  onInsert={() => onInsertAsset(asset)}
                />
              ))}
            </HorizontalScrollContainer>
          </CategorySection>

          <CategorySection title="More Candidates" icon={GalleryVerticalEnd} count={assetCandidates.length} defaultOpen={false}>
            <HorizontalScrollContainer>
              {assetCandidates.map((asset, index) => (
                <AssetRow
                  key={`candidate-${asset.asset_id || asset.title || index}`}
                  asset={asset}
                  section={section}
                  contentMd={contentMd}
                  onInsert={() => onInsertAsset(asset)}
                />
              ))}
            </HorizontalScrollContainer>
          </CategorySection>

          <CategorySection title="Filtered Assets" icon={GalleryVerticalEnd} count={filteredAssets.length} defaultOpen={false}>
            <HorizontalScrollContainer>
              {filteredAssets.map((asset, index) => (
                <AssetRow
                  key={`filtered-${asset.asset_id || asset.title || index}`}
                  asset={asset}
                  section={section}
                  contentMd={contentMd}
                  onInsert={() => onInsertAsset(asset)}
                />
              ))}
            </HorizontalScrollContainer>
          </CategorySection>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
