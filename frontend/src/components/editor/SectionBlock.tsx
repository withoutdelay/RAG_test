'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  FileCode2,
  FileText,
  GalleryVerticalEnd,
  ShieldAlert,
  Layers3,
  Loader2,
  RotateCcw,
  Save,
  Sparkles,
  ChevronRight,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import api, { getApiErrorMessage } from '@/lib/api';
import type {
  Citation,
  EvidenceCard,
  JobAccepted,
  JobRead,
  RecommendedAsset,
  SectionDraft,
} from '@/lib/types';
import { toast } from 'sonner';
import { MarkdownArticle } from './MarkdownArticle';
import { AssetPreviewCard } from './AssetPreviewCard';
import { MaterialDetailDialog } from './MaterialDetailDialog';
import { AllCandidatesDialog } from './AllCandidatesDialog';
import { RetrievalTracePanel } from './RetrievalTracePanel';

import {
  renderCitationLabel,
  getStatusVariant,
  EditableBlock,
  parseMarkdownBlocks,
  stringifyMarkdownBlocks,
  parseAssetPlaceholder,
  buildAssetPlaceholder,
  blockLabel,
  buildPreviewSegments,
  getSectionAssetCandidates,
  getAssetTrace,
  isFilteredRecommendedAsset,
} from './sectionBlockUtils';

const SECTION_REGENERATION_POLL_INTERVAL_MS = 2000;
const SECTION_REGENERATION_POLL_TIMEOUT_MS = 30 * 60 * 1000;

function sleep(ms: number) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function normalizePollPath(nextPoll: string): string {
  if (nextPoll.startsWith('/api/v1/')) {
    return nextPoll.slice('/api/v1'.length);
  }
  return nextPoll;
}

function formatSectionRegenerationProgress(job: JobRead): string {
  const progress = job.output_ref?.progress;
  const title = progress?.current_section_title ? `: ${progress.current_section_title}` : '';
  const elapsed = typeof progress?.elapsed_ms === 'number' ? ` (${Math.round(progress.elapsed_ms / 1000)}s)` : '';
  if (job.status === 'queued') {
    return `Regeneration queued${title}${elapsed}`;
  }
  if (job.status === 'running') {
    const stage = progress?.stage ? String(progress.stage).replaceAll('_', ' ') : 'running';
    return `Regeneration ${stage}${title}${elapsed}`;
  }
  return `Regeneration status: ${job.status}`;
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
  const placeholder = block.kind === 'placeholder' ? parseAssetPlaceholder(block.content) : null;
  const previewSegments = buildPreviewSegments(block.content);
  const hasAssetSegments = previewSegments.some((segment) => segment.kind === 'asset');
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
      {hasAssetSegments ? (
        <div className="space-y-4">
          {previewSegments.map((segment, index) => {
            if (segment.kind === 'markdown') {
              return <MarkdownArticle key={`md-${index}`} markdown={segment.markdown} compact />;
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
      ) : (
        <MarkdownArticle markdown={block.content} compact />
      )}
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
  const [regenerationProgress, setRegenerationProgress] = useState<string | null>(null);
  const [selectedCitationIds, setSelectedCitationIds] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState('preview');
  
  // New Dialog States
  const [detailDialogOpen, setDetailDialogOpen] = useState(false);
  const [detailDialogType, setDetailDialogType] = useState<'citation' | 'asset'>('citation');
  const [detailDialogCitation, setDetailDialogCitation] = useState<Citation | undefined>(undefined);
  const [detailDialogAsset, setDetailDialogAsset] = useState<RecommendedAsset | undefined>(undefined);
  const [allCandidatesOpen, setAllCandidatesOpen] = useState(false);

  useEffect(() => {
    setContent(section.content_md || '');
    setBlocks(parseMarkdownBlocks(section.content_md || ''));
    setIsEditing(false);
    setActiveTab('preview');
    setSelectedCitationIds([]);
  }, [section.content_md, section.id, section.updated_at]);

  const selectedCitationCount = selectedCitationIds.length;
  const hasEditableContent = Boolean(content.trim());
  const visibleRecommendedAssets = useMemo(
    () => (section.recommended_assets || []).filter((asset) => !isFilteredRecommendedAsset(asset, section.title)),
    [section.recommended_assets, section.title],
  );
  const visibleAssetCandidates = useMemo(() => {
    const recommendedIds = new Set(
      visibleRecommendedAssets
        .map((asset) => asset.asset_id)
        .filter((assetId): assetId is string => Boolean(assetId)),
    );
    return getSectionAssetCandidates(section).filter((asset) => {
      if (isFilteredRecommendedAsset(asset, section.title)) return false;
      return !asset.asset_id || !recommendedIds.has(asset.asset_id);
    });
  }, [section, visibleRecommendedAssets]);
  const assetTrace = getAssetTrace(section);
  const assetDiagnostics = assetTrace?.diagnostics || {};
  const assetStabilityDiagnostics = assetDiagnostics.asset_stability;
  const filteredAssets = useMemo(
    () => (assetStabilityDiagnostics?.filtered_assets || assetDiagnostics.filtered_assets || []) as RecommendedAsset[],
    [assetDiagnostics.filtered_assets, assetStabilityDiagnostics?.filtered_assets],
  );
  const missingAssetDiagnostics = useMemo(
    () => assetStabilityDiagnostics?.missing_asset_diagnostics || assetDiagnostics.missing_asset_diagnostics || [],
    [assetDiagnostics.missing_asset_diagnostics, assetStabilityDiagnostics?.missing_asset_diagnostics],
  );
  const assetCount = visibleRecommendedAssets.length;
  const assetCandidateCount = visibleAssetCandidates.length;
  const filteredAssetCount = filteredAssets.length;
  const previewSegments = useMemo(() => buildPreviewSegments(content), [content]);
  const assetLookup = useMemo(
    () =>
      new Map(
        [...visibleRecommendedAssets, ...visibleAssetCandidates, ...filteredAssets]
          .filter((asset) => asset.asset_id)
          .map((asset) => [asset.asset_id as string, asset]),
      ),
    [visibleRecommendedAssets, visibleAssetCandidates, filteredAssets],
  );


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

  const waitForRegenerationJob = async (nextPoll: string): Promise<JobRead> => {
    const pollPath = normalizePollPath(nextPoll);
    const deadline = Date.now() + SECTION_REGENERATION_POLL_TIMEOUT_MS;

    while (Date.now() < deadline) {
      const jobResponse = (await api.get(pollPath)) as { data: JobRead };
      const job = jobResponse.data;
      setRegenerationProgress(formatSectionRegenerationProgress(job));

      if (job.status === 'succeeded') {
        return job;
      }
      if (job.status === 'failed') {
        const detail = job.output_ref?.error || job.error_code || 'Section regeneration job failed';
        throw new Error(String(detail));
      }

      await sleep(SECTION_REGENERATION_POLL_INTERVAL_MS);
    }

    throw new Error('Section regeneration is still running after the local polling window. Refresh this page later.');
  };

  const handleRegenerate = async (preferredIds?: string[]) => {
    setRegenerating(true);
    setRegenerationProgress('Submitting regeneration job...');
    try {
      const acceptedResponse = (await api.post(`/projects/${projectId}/sections/${section.section_id}/regenerate`, {
        preferred_citation_ids: preferredIds && preferredIds.length > 0 ? preferredIds : undefined,
      })) as { data: JobAccepted };
      setRegenerationProgress('Regeneration job accepted. Waiting for progress...');
      await waitForRegenerationJob(acceptedResponse.data.next_poll);
      toast.success(preferredIds?.length ? 'Section regenerated from selected evidence' : 'Section regenerated');
      setSelectedCitationIds([]);
      onRefresh();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error regenerating section'));
    } finally {
      setRegenerating(false);
      setRegenerationProgress(null);
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
                <span>{assetCount} recommended assets</span>
                {assetCandidateCount > 0 ? <span>{assetCandidateCount} candidates</span> : null}
                {filteredAssetCount > 0 ? <span>{filteredAssetCount} filtered assets</span> : null}
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
              {regenerationProgress && (
                <p className="basis-full text-right text-xs text-slate-500">{regenerationProgress}</p>
              )}
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
            {/* --- Citations (max 3) --- */}
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h4 className="text-sm font-semibold text-slate-900">Citations</h4>
                  <p className="text-xs text-slate-500">点击卡片查看详情，决定是否纳入本轮重写。</p>
                </div>
                <div className="flex items-center gap-2">
                  {selectedCitationCount > 0 ? (
                    <Badge variant="secondary" className="bg-blue-100 text-blue-800">
                      {selectedCitationCount} selected
                    </Badge>
                  ) : null}
                  <Badge variant="outline">{section.citation_refs.length}</Badge>
                </div>
              </div>

              {section.citation_refs.length > 0 ? (
                <div className="space-y-2">
                  {section.citation_refs.slice(0, 3).map((citation, index) => {
                    const citationId = citation.evidence_id || `citation-${index}`;
                    const isSelected = selectedCitationIds.includes(citationId);
                    return (
                      <button
                        key={`${citationId}-${index}`}
                        type="button"
                        className={`w-full rounded-xl border p-3 text-left transition-colors ${
                          isSelected
                            ? 'border-blue-300 bg-blue-50 border-l-2 border-l-blue-500'
                            : 'border-slate-200 bg-white hover:bg-slate-50 hover:shadow-sm'
                        }`}
                        onClick={() => {
                          setDetailDialogType('citation');
                          setDetailDialogCitation(citation);
                          setDetailDialogAsset(undefined);
                          setDetailDialogOpen(true);
                        }}
                      >
                        <div className="flex items-center gap-2">
                          <FileText className="h-4 w-4 text-slate-400 shrink-0" />
                          <span className="text-sm font-semibold text-slate-900 truncate">{citation.source_title || `Citation ${index + 1}`}</span>
                        </div>
                        <div className="mt-1.5 flex items-center gap-2">
                          {citation.type && <Badge variant="outline" className="text-xs">{citation.type}</Badge>}
                          <span className="text-xs text-slate-500">
                            {citation.relevance_score ? `Score: ${Number(citation.relevance_score).toFixed(2)}` : ''}
                          </span>
                        </div>
                        <p className="mt-1.5 text-xs text-slate-500 truncate">{renderCitationLabel(citation)}</p>
                      </button>
                    );
                  })}

                  {section.citation_refs.length > 3 && (
                    <button
                      type="button"
                      className="flex w-full items-center justify-end gap-1 px-1 py-2 text-sm font-medium text-blue-600 transition-colors hover:text-blue-800"
                      onClick={() => setAllCandidatesOpen(true)}
                    >
                      查看全部 {section.citation_refs.length} 个候选材料
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  )}
                </div>
              ) : (
                <p className="text-xs text-slate-500">No citations</p>
              )}
            </div>

            {/* --- Tables & Figures (max 3) --- */}
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h4 className="text-sm font-semibold text-slate-900">Tables & Figures</h4>
                  <p className="text-xs text-slate-500">点击卡片查看详情，决定是否插入正文。</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{assetCount + assetCandidateCount}</Badge>
                  {filteredAssetCount > 0 ? (
                    <Badge variant="warning" className="gap-1">
                      <ShieldAlert className="h-3.5 w-3.5" />
                      {filteredAssetCount}
                    </Badge>
                  ) : null}
                </div>
              </div>

              {assetCount > 0 || assetCandidateCount > 0 ? (
                <div className="space-y-2">
                  {[...visibleRecommendedAssets, ...visibleAssetCandidates].slice(0, 3).map((asset, index) => {
                    const placeholder = buildAssetPlaceholder(asset);
                    const alreadyUsed = placeholder ? content.includes(placeholder) : false;
                    return (
                      <button
                        key={`asset-card-${asset.asset_id || asset.title || index}`}
                        type="button"
                        className={`w-full rounded-xl border p-3 text-left transition-colors ${
                          alreadyUsed
                            ? 'border-emerald-300 bg-emerald-50/50'
                            : asset.review_required
                              ? 'border-amber-300 bg-amber-50/30 hover:shadow-sm'
                              : 'border-slate-200 bg-white hover:bg-slate-50 hover:shadow-sm'
                        }`}
                        onClick={() => {
                          setDetailDialogType('asset');
                          setDetailDialogAsset(asset);
                          setDetailDialogCitation(undefined);
                          setDetailDialogOpen(true);
                        }}
                      >
                        <div className="flex items-center gap-2">
                          <GalleryVerticalEnd className="h-4 w-4 text-slate-400 shrink-0" />
                          <span className="text-sm font-semibold text-slate-900 truncate">
                            {asset.display_title || asset.title || asset.caption || 'Asset'}
                          </span>
                          {alreadyUsed && <Badge variant="secondary" className="text-xs bg-emerald-100 text-emerald-700">已插入</Badge>}
                        </div>
                        <div className="mt-1.5 flex items-center gap-2">
                          <Badge variant="outline" className="text-xs">{asset.asset_type}</Badge>
                          <span className="text-xs text-slate-500">
                            {asset.score !== undefined ? `Score: ${asset.score.toFixed(2)}` : ''}
                          </span>
                        </div>
                        <p className="mt-1.5 text-xs text-slate-500 truncate">
                          {[asset.document_name, asset.heading_path].filter(Boolean).join(' / ')}
                        </p>
                      </button>
                    );
                  })}

                  {assetCount + assetCandidateCount > 3 && (
                    <button
                      type="button"
                      className="flex w-full items-center justify-end gap-1 px-1 py-2 text-sm font-medium text-blue-600 transition-colors hover:text-blue-800"
                      onClick={() => setAllCandidatesOpen(true)}
                    >
                      查看全部 {assetCount + assetCandidateCount} 个候选材料
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  )}
                  {filteredAssetCount > 0 ? (
                    <button
                      type="button"
                      className="flex w-full items-center justify-end gap-1 px-1 py-2 text-sm font-medium text-amber-700 transition-colors hover:text-amber-900"
                      onClick={() => setAllCandidatesOpen(true)}
                    >
                      查看 {filteredAssetCount} 个被过滤图资产
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  ) : null}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-center">
                  <p className="text-sm font-medium text-slate-700">当前还没有命中图表资产</p>
                  <p className="mt-1 text-xs text-slate-500">重新生成后，这里会显示命中的历史图表。</p>
                </div>
              )}
              {missingAssetDiagnostics.length > 0 ? (
                <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
                  同源章节包含图片线索，但未找到可直接进入正文的稳定图资产。
                  {typeof missingAssetDiagnostics[0]?.source_title === 'string' ? ` 来源：${missingAssetDiagnostics[0].source_title}` : ''}
                </div>
              ) : null}
            </div>

            {/* --- Assumptions --- */}
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

            {/* --- Retrieval Trace (collapsed by default) --- */}
            <RetrievalTracePanel section={section} />
          </div>
        </CardContent>
      </Card>

      {/* --- Single item detail dialog --- */}
      <MaterialDetailDialog
        open={detailDialogOpen}
        onOpenChange={setDetailDialogOpen}
        type={detailDialogType}
        citation={detailDialogCitation}
        asset={detailDialogAsset}
        section={section}
        evidenceMap={evidenceMap}
        isSelected={
          detailDialogType === 'citation' && detailDialogCitation?.evidence_id
            ? selectedCitationIds.includes(detailDialogCitation.evidence_id)
            : false
        }
        onToggleSelect={
          detailDialogType === 'citation' && detailDialogCitation
            ? () => toggleCitationSelection(detailDialogCitation)
            : undefined
        }
        onRegenerateWith={
          detailDialogType === 'citation' && detailDialogCitation?.evidence_id
            ? () => void handleRegenerate([detailDialogCitation.evidence_id!])
            : undefined
        }
        onInsertAsset={
          detailDialogType === 'asset' && detailDialogAsset
            ? () => {
                insertAssetPlaceholder(detailDialogAsset);
                setDetailDialogOpen(false);
              }
            : undefined
        }
        regenerating={regenerating}
      />

      {/* --- All candidates dialog --- */}
      <AllCandidatesDialog
        open={allCandidatesOpen}
        onOpenChange={setAllCandidatesOpen}
        section={section}
        evidenceMap={evidenceMap}
        citations={section.citation_refs}
        recommendedAssets={visibleRecommendedAssets}
        assetCandidates={visibleAssetCandidates}
        filteredAssets={filteredAssets}
        selectedCitationIds={selectedCitationIds}
        onToggleCitationSelection={toggleCitationSelection}
        onRegenerateWith={(ids) => void handleRegenerate(ids)}
        onInsertAsset={(asset) => insertAssetPlaceholder(asset)}
        contentMd={content}
        regenerating={regenerating}
      />
    </>
  );
}
