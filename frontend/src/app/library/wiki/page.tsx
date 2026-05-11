'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  AlertCircle,
  Ban,
  CheckCircle2,
  Eye,
  FileText,
  GitCompareArrows,
  GitMerge,
  Layers3,
  Loader2,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  ShieldAlert,
  XCircle,
} from 'lucide-react';
import api, { getApiErrorMessage } from '@/lib/api';
import type {
  WikiAuditDiff,
  WikiAuditDiffRecord,
  WikiAuditItem,
  WikiAuditItemDetail,
  WikiAuditItemsResponse,
  WikiAuditSummary,
} from '@/lib/types';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';

type WikiView = 'review_required' | 'published' | 'rejected' | 'quarantined' | 'all' | 'diff';

const VIEW_LABELS: Record<WikiView, string> = {
  review_required: '待审核',
  published: '已发布',
  rejected: '已拒绝',
  quarantined: '隔离区',
  all: '全部',
  diff: 'Diff',
};

const STATUS_LABELS: Record<string, string> = {
  review_required: '待审核',
  auto_approved: '自动通过',
  published: '已发布',
  rejected: '已拒绝',
  quarantined: '已隔离',
};

const TYPE_LABELS: Record<string, string> = {
  product_family: '产品族',
  section_template: '章节模板',
  term_alias: '术语别名',
  asset_type_rule: '图片类型',
};

const TYPE_OPTIONS = ['all', 'product_family', 'section_template', 'term_alias', 'asset_type_rule'];
const BLOCKING_QUALITY_FLAGS = new Set([
  'missing_source_evidence',
  'cross_section_contamination',
  'ocr_noise_high',
  'conflicts_with_published',
  'over_generic_term',
  'asset_type_uncertain',
  'source_section_missing',
]);

function viewToStatus(view: WikiView): string | undefined {
  if (view === 'diff' || view === 'all') return undefined;
  return view;
}

function typeLabel(value: string | undefined): string {
  return TYPE_LABELS[value || ''] || value || '未知类型';
}

function statusLabel(value: string | undefined): string {
  return STATUS_LABELS[value || ''] || value || '未知状态';
}

function statusClass(value: string | undefined): string {
  switch (value) {
    case 'published':
      return 'bg-emerald-600 hover:bg-emerald-700';
    case 'review_required':
      return 'border-amber-300 bg-amber-50 text-amber-800';
    case 'auto_approved':
      return 'border-blue-300 bg-blue-50 text-blue-800';
    case 'quarantined':
      return 'border-red-300 bg-red-50 text-red-800';
    case 'rejected':
      return 'border-slate-300 bg-slate-50 text-slate-700';
    default:
      return 'border-slate-300 bg-slate-50 text-slate-700';
  }
}

function qualityClass(item: WikiAuditItem): string {
  if (item.blocking_flag_count > 0) return 'text-red-700';
  if (item.quality_score >= 0.82) return 'text-emerald-700';
  if (item.quality_score >= 0.6) return 'text-amber-700';
  return 'text-red-700';
}

function isBlockingQualityFlag(flag: string): boolean {
  return flag.startsWith('blocking:') || BLOCKING_QUALITY_FLAGS.has(flag);
}

function scorePercent(score: number | undefined): number {
  if (!Number.isFinite(score)) return 0;
  return Math.max(0, Math.min(100, Math.round(Number(score) * 100)));
}

function summarizeCounts(summary: WikiAuditSummary | null) {
  return {
    draft: summary?.draft_total ?? 0,
    review: summary?.review_required_total ?? 0,
    published: summary?.published_total ?? 0,
    rejected: summary?.rejected_total ?? 0,
    quarantined: summary?.quarantined_total ?? 0,
    diff: summary?.diff_counts?.changed ?? 0,
  };
}

function evidenceTitle(evidence: WikiAuditItem['evidence'][number]): string {
  const heading = evidence.heading_path || '未标注章节';
  const section = evidence.source_section_id ? `#${evidence.source_section_id}` : 'section n/a';
  return `${section} · ${heading}`;
}

export default function WikiLibraryPage() {
  const [summary, setSummary] = useState<WikiAuditSummary | null>(null);
  const [itemsPayload, setItemsPayload] = useState<WikiAuditItemsResponse | null>(null);
  const [diff, setDiff] = useState<WikiAuditDiff | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeView, setActiveView] = useState<WikiView>('review_required');
  const [itemType, setItemType] = useState('all');
  const [query, setQuery] = useState('');
  const [busyItemId, setBusyItemId] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [selectedDetail, setSelectedDetail] = useState<WikiAuditItemDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editName, setEditName] = useState('');
  const [editAliases, setEditAliases] = useState('');
  const [editSummary, setEditSummary] = useState('');
  const [mergeIds, setMergeIds] = useState('');

  const counts = useMemo(() => summarizeCounts(summary), [summary]);

  const loadSummary = useCallback(async () => {
    const res = await api.get('/wiki/audit/summary');
    setSummary(res.data as WikiAuditSummary);
  }, []);

  const loadDiff = useCallback(async () => {
    const res = await api.get('/wiki/audit/diff');
    setDiff(res.data as WikiAuditDiff);
  }, []);

  const loadItems = useCallback(async () => {
    const params: Record<string, string | number> = { limit: 200 };
    const status = viewToStatus(activeView);
    if (status) params.status = status;
    if (itemType !== 'all') params.item_type = itemType;
    if (query.trim()) params.q = query.trim();
    const res = await api.get('/wiki/audit/items', { params });
    setItemsPayload(res.data as WikiAuditItemsResponse);
  }, [activeView, itemType, query]);

  const refreshAll = useCallback(async () => {
    try {
      setLoading(true);
      await Promise.all([loadSummary(), loadDiff(), loadItems()]);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to load AI Wiki governance data'));
    } finally {
      setLoading(false);
    }
  }, [loadDiff, loadItems, loadSummary]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    const item = selectedDetail?.item;
    setEditName(item?.canonical_name || '');
    setEditAliases((item?.aliases || []).join(', '));
    setEditSummary(item?.summary || '');
    setMergeIds('');
  }, [selectedDetail]);

  async function loadDetail(itemId: string) {
    try {
      setDetailLoading(true);
      const res = await api.get(`/wiki/audit/items/${encodeURIComponent(itemId)}`);
      setSelectedDetail(res.data as WikiAuditItemDetail);
      setDetailOpen(true);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to load wiki item'));
    } finally {
      setDetailLoading(false);
    }
  }

  async function reloadAfterAction(itemId?: string) {
    await Promise.all([loadSummary(), loadDiff(), loadItems()]);
    if (itemId && detailOpen) {
      await loadDetail(itemId);
    }
  }

  async function approveItem(item: WikiAuditItem) {
    try {
      setBusyItemId(item.item_id);
      await api.post(`/wiki/audit/items/${encodeURIComponent(item.item_id)}/approve`, { note: 'human_approved' });
      toast.success('已发布到 AI Wiki');
      await reloadAfterAction(item.item_id);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Approve failed'));
    } finally {
      setBusyItemId(null);
    }
  }

  async function rejectItem(item: WikiAuditItem, status: 'rejected' | 'quarantined' = 'rejected') {
    try {
      setBusyItemId(item.item_id);
      await api.post(`/wiki/audit/items/${encodeURIComponent(item.item_id)}/reject`, {
        reason: status === 'quarantined' ? 'manual_quarantine' : 'manual_reject',
        status,
      });
      toast.success(status === 'quarantined' ? '已移入隔离区' : '已拒绝候选项');
      await reloadAfterAction(item.item_id);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Reject failed'));
    } finally {
      setBusyItemId(null);
    }
  }

  async function saveEdits() {
    const item = selectedDetail?.item;
    if (!item) return;
    try {
      setBusyItemId(item.item_id);
      await api.post(`/wiki/audit/items/${encodeURIComponent(item.item_id)}/edit`, {
        canonical_name: editName.trim(),
        aliases: editAliases.split(',').map((alias) => alias.trim()).filter(Boolean),
        summary: editSummary.trim(),
      });
      toast.success('已保存人工修订');
      await reloadAfterAction(item.item_id);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Save failed'));
    } finally {
      setBusyItemId(null);
    }
  }

  async function mergeIntoSelected() {
    const item = selectedDetail?.item;
    const sourceIds = mergeIds.split(',').map((value) => value.trim()).filter(Boolean);
    if (!item || sourceIds.length === 0) return;
    try {
      setBusyItemId(item.item_id);
      await api.post(`/wiki/audit/items/${encodeURIComponent(item.item_id)}/merge`, {
        source_item_ids: sourceIds,
        note: 'manual_merge',
      });
      toast.success('已合并候选项');
      await reloadAfterAction(item.item_id);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Merge failed'));
    } finally {
      setBusyItemId(null);
    }
  }

  async function rebuildWiki() {
    try {
      setRebuilding(true);
      await api.post('/wiki/audit/rebuild');
      toast.success('已提交 AI Wiki 重建任务');
      await loadSummary();
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Rebuild failed'));
    } finally {
      setRebuilding(false);
    }
  }

  const items = itemsPayload?.items || [];

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="flex flex-col gap-3 border-b pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Layers3 className="h-4 w-4" />
            Knowledge Governance
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">AI Wiki 审计台</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            审核产品族、章节模板、术语别名与图片类型候选项，发布后的知识会进入生成链路。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={refreshAll} disabled={loading} title="刷新审计数据">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            刷新
          </Button>
          <Button onClick={rebuildWiki} disabled={rebuilding} title="重新编译历史资料 AI Wiki">
            {rebuilding ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            重建 Wiki
          </Button>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <Metric label="Draft" value={counts.draft} icon={<FileText className="h-4 w-4" />} />
        <Metric label="待审核" value={counts.review} icon={<ShieldAlert className="h-4 w-4" />} tone="amber" />
        <Metric label="Published" value={counts.published} icon={<CheckCircle2 className="h-4 w-4" />} tone="green" />
        <Metric label="Rejected" value={counts.rejected + counts.quarantined} icon={<XCircle className="h-4 w-4" />} />
        <Metric label="Changed Diff" value={counts.diff} icon={<GitCompareArrows className="h-4 w-4" />} tone="blue" />
      </div>

      {summary && summary.review_required_total === 0 && (
        <Alert>
          <CheckCircle2 className="h-4 w-4" />
          <AlertTitle>当前没有待审核 Wiki 候选项</AlertTitle>
          <AlertDescription>已发布层仍可继续编辑、合并或撤回。</AlertDescription>
        </Alert>
      )}

      <Tabs value={activeView} onValueChange={(value) => setActiveView(value as WikiView)} className="gap-3">
        <div className="flex flex-col gap-3 rounded-lg border bg-background p-3 xl:flex-row xl:items-center xl:justify-between">
          <TabsList className="flex-wrap justify-start">
            {(['review_required', 'published', 'rejected', 'quarantined', 'all', 'diff'] as WikiView[]).map((view) => (
              <TabsTrigger key={view} value={view}>
                {VIEW_LABELS[view]}
              </TabsTrigger>
            ))}
          </TabsList>
          <div className="flex flex-col gap-2 md:flex-row md:items-center">
            <div className="relative min-w-64">
              <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索名称、别名、来源"
                className="pl-8"
              />
            </div>
            <div className="flex flex-wrap gap-1">
              {TYPE_OPTIONS.map((option) => (
                <Button
                  key={option}
                  type="button"
                  variant={itemType === option ? 'secondary' : 'ghost'}
                  size="sm"
                  onClick={() => setItemType(option)}
                >
                  {option === 'all' ? '全部类型' : typeLabel(option)}
                </Button>
              ))}
            </div>
          </div>
        </div>

        {(['review_required', 'published', 'rejected', 'quarantined', 'all'] as WikiView[]).map((view) => (
          <TabsContent key={view} value={view} className="space-y-3">
            {loading ? (
              <LoadingState />
            ) : items.length === 0 ? (
              <EmptyState />
            ) : (
              <div className="space-y-2">
                {items.map((item) => (
                  <WikiItemRow
                    key={`${item.layer}:${item.item_id}`}
                    item={item}
                    busy={busyItemId === item.item_id}
                    onOpen={() => void loadDetail(item.item_id)}
                    onApprove={() => void approveItem(item)}
                    onReject={() => void rejectItem(item)}
                    onQuarantine={() => void rejectItem(item, 'quarantined')}
                  />
                ))}
              </div>
            )}
          </TabsContent>
        ))}

        <TabsContent value="diff" className="space-y-3">
          <DiffSection title="新增候选" records={diff?.added || []} onOpen={(itemId) => void loadDetail(itemId)} />
          <DiffSection title="内容变化" records={diff?.changed || []} onOpen={(itemId) => void loadDetail(itemId)} />
          <DiffSection title="发布层缺失" records={diff?.removed || []} onOpen={(itemId) => void loadDetail(itemId)} />
          <DiffSection title="状态变化" records={diff?.status_changed || []} onOpen={(itemId) => void loadDetail(itemId)} />
        </TabsContent>
      </Tabs>

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-h-[92vh] max-w-6xl overflow-hidden p-0">
          <DialogHeader className="border-b p-4">
            <DialogTitle className="flex items-center gap-2">
              {detailLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              {selectedDetail?.item.canonical_name || 'Wiki Item'}
            </DialogTitle>
            <DialogDescription>
              {selectedDetail?.item.item_id} · {typeLabel(selectedDetail?.item.item_type)}
            </DialogDescription>
          </DialogHeader>
          {selectedDetail?.item && (
            <div className="grid min-h-0 gap-0 lg:grid-cols-[1.5fr_1fr]">
              <ScrollArea className="max-h-[76vh] border-r">
                <div className="space-y-4 p-4">
                  <ItemHeader item={selectedDetail.item} />
                  <section className="space-y-2">
                    <h2 className="text-sm font-semibold">来源证据</h2>
                    <div className="space-y-2">
                      {selectedDetail.item.evidence.map((evidence, index) => (
                        <div key={`${evidence.source_section_id}-${index}`} className="rounded-lg border bg-muted/20 p-3">
                          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <FileText className="h-3.5 w-3.5" />
                            <span>{evidence.source_document || selectedDetail.item.source_document || 'source n/a'}</span>
                            <span>{evidenceTitle(evidence)}</span>
                          </div>
                          <p className="mt-2 text-sm leading-6 text-foreground">{evidence.evidence_quote || 'No quote'}</p>
                        </div>
                      ))}
                    </div>
                  </section>
                  <section className="space-y-2">
                    <h2 className="text-sm font-semibold">审计事件</h2>
                    <div className="space-y-2">
                      {selectedDetail.audit_events.length === 0 ? (
                        <p className="text-sm text-muted-foreground">暂无人工审计事件。</p>
                      ) : (
                        selectedDetail.audit_events.map((event, index) => (
                          <div key={index} className="rounded-lg border p-2 text-xs">
                            <div className="font-medium">{String(event.event || 'event')}</div>
                            <div className="text-muted-foreground">
                              {String(event.created_at || '')} · {String(event.actor || '')}
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </section>
                </div>
              </ScrollArea>
              <ScrollArea className="max-h-[76vh]">
                <div className="space-y-4 p-4">
                  <section className="space-y-2">
                    <h2 className="text-sm font-semibold">人工修订</h2>
                    <Input value={editName} onChange={(event) => setEditName(event.target.value)} />
                    <Input value={editAliases} onChange={(event) => setEditAliases(event.target.value)} placeholder="别名，逗号分隔" />
                    <Textarea value={editSummary} onChange={(event) => setEditSummary(event.target.value)} className="min-h-24" />
                    <Button onClick={() => void saveEdits()} disabled={busyItemId === selectedDetail.item.item_id} className="w-full">
                      <Save className="h-4 w-4" />
                      保存修订
                    </Button>
                  </section>

                  <section className="space-y-2">
                    <h2 className="text-sm font-semibold">合并</h2>
                    <Input
                      value={mergeIds}
                      onChange={(event) => setMergeIds(event.target.value)}
                      placeholder="source_item_id, source_item_id"
                    />
                    <Button
                      variant="outline"
                      onClick={() => void mergeIntoSelected()}
                      disabled={!mergeIds.trim() || busyItemId === selectedDetail.item.item_id}
                      className="w-full"
                    >
                      <GitMerge className="h-4 w-4" />
                      合并到当前项
                    </Button>
                  </section>

                  <section className="space-y-2">
                    <h2 className="text-sm font-semibold">发布控制</h2>
                    <div className="grid grid-cols-2 gap-2">
                      <Button
                        onClick={() => void approveItem(selectedDetail.item)}
                        disabled={busyItemId === selectedDetail.item.item_id}
                      >
                        <CheckCircle2 className="h-4 w-4" />
                        发布
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => void rejectItem(selectedDetail.item)}
                        disabled={busyItemId === selectedDetail.item.item_id}
                      >
                        <XCircle className="h-4 w-4" />
                        拒绝
                      </Button>
                      <Button
                        variant="destructive"
                        onClick={() => void rejectItem(selectedDetail.item, 'quarantined')}
                        disabled={busyItemId === selectedDetail.item.item_id}
                      >
                        <Ban className="h-4 w-4" />
                        隔离
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => void rejectItem(selectedDetail.item)}
                        disabled={selectedDetail.item.status !== 'published' || busyItemId === selectedDetail.item.item_id}
                      >
                        <RotateCcw className="h-4 w-4" />
                        撤回
                      </Button>
                    </div>
                  </section>
                </div>
              </ScrollArea>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Metric({
  label,
  value,
  icon,
  tone = 'default',
}: {
  label: string;
  value: number;
  icon: ReactNode;
  tone?: 'default' | 'amber' | 'green' | 'blue';
}) {
  const toneClass = {
    default: 'text-slate-700 bg-slate-50',
    amber: 'text-amber-700 bg-amber-50',
    green: 'text-emerald-700 bg-emerald-50',
    blue: 'text-blue-700 bg-blue-50',
  }[tone];
  return (
    <div className="rounded-lg border bg-background p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm text-muted-foreground">{label}</span>
        <span className={`rounded-md p-1.5 ${toneClass}`}>{icon}</span>
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function WikiItemRow({
  item,
  busy,
  onOpen,
  onApprove,
  onReject,
  onQuarantine,
}: {
  item: WikiAuditItem;
  busy: boolean;
  onOpen: () => void;
  onApprove: () => void;
  onReject: () => void;
  onQuarantine: () => void;
}) {
  return (
    <article className="rounded-lg border bg-background p-3">
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_220px_280px] xl:items-center">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{typeLabel(item.item_type)}</Badge>
            <Badge className={statusClass(item.status)} variant={item.status === 'published' ? 'default' : 'outline'}>
              {statusLabel(item.status)}
            </Badge>
            {item.blocking_flag_count > 0 && (
              <Badge variant="outline" className="border-red-300 bg-red-50 text-red-800">
                blocking {item.blocking_flag_count}
              </Badge>
            )}
          </div>
          <button type="button" onClick={onOpen} className="block max-w-full text-left text-base font-semibold hover:text-primary">
            <span className="line-clamp-1">{item.canonical_name}</span>
          </button>
          <p className="line-clamp-2 text-sm leading-6 text-muted-foreground">{item.summary}</p>
          <div className="flex flex-wrap gap-1">
            {(item.aliases || []).slice(0, 6).map((alias) => (
              <Badge key={alias} variant="secondary" className="font-normal">
                {alias}
              </Badge>
            ))}
          </div>
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">质量分</span>
            <span className={`font-semibold tabular-nums ${qualityClass(item)}`}>{scorePercent(item.quality_score)}%</span>
          </div>
          <Progress value={scorePercent(item.quality_score)} />
          <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
            <span>证据 {item.hit_count}</span>
            <span>来源 {item.source_document_count}</span>
          </div>
        </div>
        <div className="flex flex-wrap justify-start gap-2 xl:justify-end">
          <Button variant="outline" size="sm" onClick={onOpen} title="查看证据">
            <Eye className="h-4 w-4" />
            查看
          </Button>
          <Button size="sm" onClick={onApprove} disabled={busy || item.status === 'published'} title="发布到 published Wiki">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
            发布
          </Button>
          <Button variant="outline" size="sm" onClick={onReject} disabled={busy} title="拒绝候选项">
            <XCircle className="h-4 w-4" />
            拒绝
          </Button>
          <Button variant="destructive" size="sm" onClick={onQuarantine} disabled={busy} title="隔离候选项">
            <Ban className="h-4 w-4" />
            隔离
          </Button>
        </div>
      </div>
      <div className="mt-3 grid gap-2 border-t pt-3 text-xs text-muted-foreground md:grid-cols-3">
        <span className="truncate">source: {item.source_document || item.source_documents?.[0] || 'n/a'}</span>
        <span className="truncate">section: {item.source_section_id || 'n/a'}</span>
        <span className="truncate">updated: {item.updated_at || 'n/a'}</span>
      </div>
    </article>
  );
}

function ItemHeader({ item }: { item: WikiAuditItem }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{typeLabel(item.item_type)}</Badge>
        <Badge className={statusClass(item.status)} variant={item.status === 'published' ? 'default' : 'outline'}>
          {statusLabel(item.status)}
        </Badge>
        <span className={`text-sm font-semibold tabular-nums ${qualityClass(item)}`}>{scorePercent(item.quality_score)}%</span>
      </div>
      <p className="text-sm leading-6">{item.summary}</p>
      <div className="flex flex-wrap gap-1">
        {(item.aliases || []).map((alias) => (
          <Badge key={alias} variant="secondary" className="font-normal">
            {alias}
          </Badge>
        ))}
      </div>
      {item.quality_flags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {item.quality_flags.map((flag) => (
            <Badge key={flag} variant="outline" className={isBlockingQualityFlag(flag) ? 'border-red-300 bg-red-50 text-red-800' : ''}>
              {flag}
            </Badge>
          ))}
        </div>
      )}
    </section>
  );
}

function DiffSection({ title, records, onOpen }: { title: string; records: WikiAuditDiffRecord[]; onOpen: (itemId: string) => void }) {
  return (
    <section className="rounded-lg border bg-background">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <GitCompareArrows className="h-4 w-4" />
          {title}
        </div>
        <Badge variant="outline">{records.length}</Badge>
      </div>
      {records.length === 0 ? (
        <p className="p-3 text-sm text-muted-foreground">暂无差异。</p>
      ) : (
        <div className="divide-y">
          {records.slice(0, 80).map((record) => (
            <button
              key={`${title}-${record.item_id}`}
              type="button"
              className="grid w-full gap-2 px-3 py-2 text-left hover:bg-muted/50 md:grid-cols-[minmax(0,1fr)_180px_220px]"
              onClick={() => onOpen(record.item_id)}
            >
              <span className="truncate font-medium">{record.canonical_name || record.item_id}</span>
              <span className="text-sm text-muted-foreground">{typeLabel(record.item_type)}</span>
              <span className="truncate text-xs text-muted-foreground">{record.changed_fields.join(', ') || 'item'}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function LoadingState() {
  return (
    <div className="flex min-h-40 items-center justify-center rounded-lg border bg-background text-sm text-muted-foreground">
      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
      Loading Wiki governance data
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex min-h-40 items-center justify-center rounded-lg border bg-background text-sm text-muted-foreground">
      <AlertCircle className="mr-2 h-4 w-4" />
      当前筛选下没有候选项
    </div>
  );
}
