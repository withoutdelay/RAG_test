'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, GalleryVerticalEnd, Search } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import type { SectionDraft } from '@/lib/types';
import {
  formatIntentLabel,
  formatTraceMetric,
  formatSelectionReason,
  buildSectionCandidateBreakdownText,
  buildBlockRetrievalBreakdownText,
  buildKnowledgeWikiPriorBreakdownText,
  getGenerationDetails,
  getAssetTrace,
  getReuseTrace,
} from './sectionBlockUtils';


interface RetrievalTracePanelProps {
  section: SectionDraft;
}


export function RetrievalTracePanel({ section }: RetrievalTracePanelProps) {
  const [isOpen, setIsOpen] = useState(false);

  const generationDetails = getGenerationDetails(section);
  const reuseTrace = getReuseTrace(section);
  const assetTrace = getAssetTrace(section);
  const assetStability = assetTrace?.diagnostics?.asset_stability;
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

  return (
    <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50"
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className="flex items-center gap-2">
          <Search className="h-4 w-4 text-slate-500" />
          <h4 className="text-sm font-semibold text-slate-900">Retrieval Trace</h4>
        </div>
        <div className="flex items-center gap-2">
          {generationDetails?.retrieval_mode && (
            <Badge variant="outline" className="text-xs">{generationDetails.retrieval_mode}</Badge>
          )}
          {isOpen ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
        </div>
      </button>

      {isOpen && (
        <div className="border-t border-slate-200 p-4 space-y-4">
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

          {assetTrace ? (
            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Asset Stability</p>
              <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <GalleryVerticalEnd className="h-4 w-4 text-slate-500" />
                  <Badge variant="outline">selected {assetTrace.selected_count ?? 0}</Badge>
                  <Badge variant="outline">candidates {assetTrace.candidate_count ?? 0}</Badge>
                  {assetStability?.filtered_count ? (
                    <Badge variant="warning">filtered {assetStability.filtered_count}</Badge>
                  ) : null}
                  {assetStability?.missing_asset_diagnostics?.length ? (
                    <Badge variant="warning">missing explained</Badge>
                  ) : null}
                </div>
                {assetStability?.selected_primary ? (
                  <p className="mt-3 text-xs text-slate-600">
                    Primary: {assetStability.selected_primary.display_title || assetStability.selected_primary.title || assetStability.selected_primary.asset_id}
                  </p>
                ) : null}
                {assetStability?.filter_reason_counts && Object.keys(assetStability.filter_reason_counts).length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {Object.entries(assetStability.filter_reason_counts).map(([reason, count]) => (
                      <Badge key={reason} variant="outline" className="bg-white text-slate-600">
                        {reason}: {count}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {sectionCandidates.length > 0 ? (
            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Section Candidates</p>
              {sectionCandidates.map((item, index) => {
                const breakdownText = buildSectionCandidateBreakdownText(item.score_breakdown);
                const traceText = item.reason_trace?.slice(0, 3).join(' / ') || '';
                return (
                  <div key={`${item.section_id || item.section_path || 'candidate'}-${index}`} className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
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
                  </div>
                );
              })}
            </div>
          ) : null}

          {selectedSections.length > 0 ? (
            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Selected Sections</p>
              {selectedSections.map((item, index) => {
                const breakdownText = buildSectionCandidateBreakdownText(item.score_breakdown);
                const traceText = item.reason_trace?.slice(0, 3).join(' / ') || '';
                return (
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
                    {breakdownText ? <p className="mt-2 text-[11px] text-slate-500">{breakdownText}</p> : null}
                    {item.reason ? <p className="mt-2 text-xs text-slate-600">{item.reason}</p> : null}
                    {traceText ? <p className="mt-2 text-[11px] text-slate-500 line-clamp-3">{traceText}</p> : null}
                  </div>
                );
              })}
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
                        <p className="mt-1 text-xs leading-5 text-slate-500">{item.section_path || normalizeHeadingPathLocal(item.heading_path)}</p>
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
      )}
    </div>
  );
}

function normalizeHeadingPathLocal(value: string[] | string | undefined): string {
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
