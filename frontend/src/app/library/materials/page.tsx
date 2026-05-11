'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, Database, Eye, FileText, Image as ImageIcon, Loader2, RefreshCw, ShieldAlert, UploadCloud } from 'lucide-react';
import api, { buildAssetContentUrl, getApiErrorMessage } from '@/lib/api';
import type {
  JobAccepted,
  JobRead,
  LibraryMaterial,
  LibraryMaterialAsset,
  LibraryMaterialDetail,
  LibraryMaterialsResponse,
  MaterialRoute,
} from '@/lib/types';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';

const ROUTE_LABELS: Record<MaterialRoute, string> = {
  main_indexed: '主库',
  review_pending: '待审阅',
  holdout_eval: '验证集',
  conversion_required: '待转换',
  conversion_failed: '转换失败',
  excluded: '已排除',
};

const ROUTE_OPTIONS: MaterialRoute[] = [
  'main_indexed',
  'review_pending',
  'holdout_eval',
  'conversion_required',
  'excluded',
];
const IMPORT_ROUTE_OPTIONS: MaterialRoute[] = [
  'main_indexed',
  'review_pending',
  'holdout_eval',
];
const PROCESSING_PARSE_STATUSES = new Set(['pending', 'queued', 'parsing']);
const LIBRARY_JOB_POLL_INTERVAL_MS = 3000;
const LIBRARY_JOB_POLL_TIMEOUT_MS = 2 * 60 * 60 * 1000;
type MaterialFilter = MaterialRoute | 'all' | 'processing';
type AssetFilter = 'all' | 'figure' | 'table' | 'fallback' | 'review_required' | 'repaired' | 'rejected';

const ASSET_FILTER_LABELS: Record<AssetFilter, string> = {
  all: 'All Assets',
  figure: 'Figures',
  table: 'Tables',
  fallback: 'Storage Fallback',
  review_required: 'Needs Review',
  repaired: 'Repaired',
  rejected: 'Rejected',
};

const QUALITY_FLAG_LABELS: Record<string, string> = {
  requires_aliyun_docmind: '需阿里云解析',
  parse_failed: '解析失败',
  parse_insufficient: '解析不充分',
  processing: '解析中',
  not_ingested: '未入库',
  expected_images_but_no_figure_assets: '应有图片但未解析出图资产',
  all_assets_are_storage_fallback: '图片仅有源文件回退',
  conversion_required: '待转换',
};

interface JobQueueStatus {
  worker_count: number;
  queued_count: number;
  running_count: number;
  queued?: Array<{ job_id: string; job_type: string; label: string }>;
  running?: Array<{ job_id: string; job_type: string; label: string }>;
}

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

function formatLibraryJobProgress(job: JobRead): string {
  const progress = job.output_ref?.progress;
  const completed = progress?.completed_materials;
  const total = progress?.total_materials;
  const fileName = progress?.current_file_name;

  if (job.status === 'queued') {
    return 'Library rebuild queued. Waiting for backend worker...';
  }
  if (typeof completed === 'number' && typeof total === 'number' && total > 0) {
    const current = fileName ? `: ${fileName}` : '';
    return `Rebuilding ${Math.min(completed, total)} / ${total}${current}`;
  }
  if (job.status === 'running') {
    return 'Library rebuild running...';
  }
  return `Library rebuild status: ${job.status}`;
}

function formatBytes(value: number | undefined): string {
  if (!value || !Number.isFinite(value)) return '0 MB';
  return `${(value / 1024 / 1024).toFixed(2)} MB`;
}

function metricValue(item: LibraryMaterial, key: string, fallback: number): number {
  const value = item.manifest_metrics?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function formatAssetDimensions(asset: LibraryMaterialAsset): string {
  if (!asset.width || !asset.height) return 'size n/a';
  return `${asset.width}x${asset.height}`;
}

function assetBadgeClass(asset: LibraryMaterialAsset): string {
  if (asset.visual_role === 'asset_fragment') return 'border-red-300 bg-red-50 text-red-800';
  if (asset.storage_fallback) return 'border-amber-300 bg-amber-50 text-amber-800';
  if (asset.visual_role === 'layout_drawing') return 'border-blue-300 bg-blue-50 text-blue-800';
  return 'border-slate-300 bg-slate-50 text-slate-800';
}

function canPreviewAssetImage(asset: LibraryMaterialAsset): boolean {
  return asset.asset_type === 'figure' && !asset.storage_fallback && asset.visual_role !== 'asset_fragment';
}

function hasAssetRepair(asset: LibraryMaterialAsset): boolean {
  return Boolean(asset.metadata?.asset_repair_method || asset.quality_flags.includes('pdf_page_render_candidate_requires_review'));
}

function matchesAssetFilter(asset: LibraryMaterialAsset, filter: AssetFilter): boolean {
  switch (filter) {
    case 'figure':
      return asset.asset_type === 'figure';
    case 'table':
      return asset.asset_type === 'table';
    case 'fallback':
      return asset.storage_fallback;
    case 'review_required':
      return asset.review_required || asset.asset_audit_status === 'review_pending';
    case 'repaired':
      return hasAssetRepair(asset);
    case 'rejected':
      return asset.asset_audit_status === 'rejected' || asset.preserve_in_vector_db === false;
    case 'all':
    default:
      return true;
  }
}

function countAssetsByFilter(assets: LibraryMaterialAsset[], filter: AssetFilter): number {
  return assets.filter((asset) => matchesAssetFilter(asset, filter)).length;
}

function materialStatusLabel(item: LibraryMaterial): string {
  if (item.material_status_label) return item.material_status_label;
  if (item.parse_status === 'done') {
    return item.route === 'main_indexed' ? '主库已入库' : ROUTE_LABELS[item.route];
  }
  if (PROCESSING_PARSE_STATUSES.has(item.parse_status)) return '解析中';
  if (item.parse_status === 'failed') return '解析失败';
  if (item.parse_status === 'parse_insufficient') return '解析不充分';
  return item.parse_status || '未知状态';
}

function materialStatusClass(item: LibraryMaterial): string {
  const status = item.material_status || item.parse_status;
  switch (status) {
    case 'main_indexed':
      return 'bg-green-500 hover:bg-green-600';
    case 'review_pending':
      return 'border-amber-300 bg-amber-50 text-amber-800';
    case 'holdout_eval':
      return 'border-blue-300 bg-blue-50 text-blue-800';
    case 'processing':
      return 'border-blue-300 bg-blue-50 text-blue-800';
    case 'cloud_parse_required':
      return 'border-red-300 bg-red-50 text-red-800';
    case 'parse_failed':
    case 'conversion_failed':
      return 'border-red-300 bg-red-50 text-red-800';
    case 'parse_insufficient':
    case 'conversion_required':
      return 'border-amber-300 bg-amber-50 text-amber-800';
    case 'excluded':
      return 'border-slate-300 bg-slate-50 text-slate-800';
    default:
      return '';
  }
}

function formatQualityFlag(flag: string): string {
  return QUALITY_FLAG_LABELS[flag] || flag.replace(/^parse_status:/, '解析状态：');
}

function parseStatusIcon(item: LibraryMaterial) {
  if (item.material_status === 'processing' || PROCESSING_PARSE_STATUSES.has(item.parse_status)) {
    return <Loader2 className="h-4 w-4 animate-spin text-blue-600" />;
  }
  if (item.material_status === 'cloud_parse_required' || item.requires_cloud_parse) {
    return <ShieldAlert className="h-4 w-4 text-red-600" />;
  }
  if (item.quality_flags.length > 0) {
    return <ShieldAlert className="h-4 w-4 text-amber-600" />;
  }
  if (item.parse_status === 'done') {
    return <CheckCircle2 className="h-4 w-4 text-green-600" />;
  }
  return <AlertCircle className="h-4 w-4 text-muted-foreground" />;
}

export default function LibraryMaterialsPage() {
  const [payload, setPayload] = useState<LibraryMaterialsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busySampleId, setBusySampleId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importRoute, setImportRoute] = useState<MaterialRoute>('main_indexed');
  const [importProgress, setImportProgress] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildProgress, setRebuildProgress] = useState<string | null>(null);
  const [queueStatus, setQueueStatus] = useState<JobQueueStatus | null>(null);
  const [activeRoute, setActiveRoute] = useState<MaterialFilter>('all');
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedMaterial, setSelectedMaterial] = useState<LibraryMaterialDetail | null>(null);
  const [assetFilter, setAssetFilter] = useState<AssetFilter>('all');
  const importFileInputRef = useRef<HTMLInputElement>(null);

  const loadMaterials = useCallback(async () => {
    try {
      const res = await api.get('/library/materials');
      setPayload(res.data as LibraryMaterialsResponse);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to load library materials'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMaterials();
  }, [loadMaterials]);

  useEffect(() => {
    if (!payload?.items.some((item) => PROCESSING_PARSE_STATUSES.has(item.parse_status))) {
      return;
    }
    const timer = window.setTimeout(() => {
      void loadMaterials();
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [loadMaterials, payload]);

  const openMaterialDetail = async (sampleId: string) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setSelectedMaterial(null);
    setAssetFilter('all');
    try {
      const res = await api.get(`/library/materials/${sampleId}`);
      setSelectedMaterial(res.data as LibraryMaterialDetail);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to load material details'));
      setDetailOpen(false);
    } finally {
      setDetailLoading(false);
    }
  };

  const loadQueueStatus = useCallback(async () => {
    try {
      const res = await api.get('/jobs/queue');
      setQueueStatus(res.data as JobQueueStatus);
    } catch {
      setQueueStatus(null);
    }
  }, []);

  useEffect(() => {
    void loadQueueStatus();
    const timer = window.setInterval(() => {
      void loadQueueStatus();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadQueueStatus]);

  const waitForLibraryJob = useCallback(async (nextPoll: string): Promise<JobRead> => {
    const pollPath = normalizePollPath(nextPoll);
    const deadline = Date.now() + LIBRARY_JOB_POLL_TIMEOUT_MS;

    while (Date.now() < deadline) {
      const jobResponse = (await api.get(pollPath)) as { data: JobRead };
      const job = jobResponse.data;
      setRebuildProgress(formatLibraryJobProgress(job));
      void loadQueueStatus();

      if (job.status === 'succeeded') {
        return job;
      }
      if (job.status === 'failed') {
        const detail = job.output_ref?.error || job.error_code || 'Library rebuild job failed';
        throw new Error(String(detail));
      }

      await sleep(LIBRARY_JOB_POLL_INTERVAL_MS);
    }

    throw new Error('Library rebuild is still running after the local polling window. Refresh this page later.');
  }, [loadQueueStatus]);

  const items = useMemo(() => {
    const allItems = payload?.items || [];
    if (activeRoute === 'all') return allItems;
    if (activeRoute === 'processing') {
      return allItems.filter((item) => PROCESSING_PARSE_STATUSES.has(item.parse_status));
    }
    return allItems.filter((item) => item.route === activeRoute);
  }, [activeRoute, payload]);

  const selectedAssets = useMemo(() => selectedMaterial?.assets || [], [selectedMaterial]);
  const filteredAssets = useMemo(
    () => selectedAssets.filter((asset) => matchesAssetFilter(asset, assetFilter)),
    [assetFilter, selectedAssets]
  );
  const assetFilterOptions = useMemo(
    () =>
      (Object.keys(ASSET_FILTER_LABELS) as AssetFilter[]).map((filter) => ({
        filter,
        label: ASSET_FILTER_LABELS[filter],
        count: countAssetsByFilter(selectedAssets, filter),
      })),
    [selectedAssets]
  );

  const rebuildAll = async () => {
    setRebuilding(true);
    setRebuildProgress('Submitting library rebuild job...');
    try {
      const res = await api.post('/library/materials/rebuild', { force: false });
      const accepted = res.data as JobAccepted;
      setRebuildProgress(`Library rebuild queued: ${accepted.job_id}`);
      await waitForLibraryJob(accepted.next_poll);
      toast.success('Library rebuild completed');
      await loadMaterials();
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to queue library rebuild'));
      setRebuildProgress(getApiErrorMessage(error, 'Library rebuild failed'));
    } finally {
      setRebuilding(false);
    }
  };

  const handleImportClick = () => {
    importFileInputRef.current?.click();
  };

  const importHistoricalMaterials = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    setImporting(true);
    let processedCount = 0;
    let queuedCount = 0;
    let duplicateCount = 0;
    try {
      for (const file of files) {
        setImportProgress(`正在导入 ${processedCount + 1} / ${files.length}: ${file.name}`);
        const formData = new FormData();
        formData.append('file', file);
        formData.append('route', importRoute);
        const res = await api.post('/library/materials/import', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        const accepted = (res.data || {}) as Partial<JobAccepted> & {
          message?: string;
          filename?: string;
          duplicate?: boolean;
        };
        processedCount += 1;
        if (accepted.duplicate) {
          duplicateCount += 1;
        } else {
          queuedCount += 1;
        }
        setImportProgress(accepted.message || `历史方案导入已排队：${accepted.filename || file.name}`);
      }
      const summaryParts = [];
      if (queuedCount > 0) summaryParts.push(`已排队 ${queuedCount} 份`);
      if (duplicateCount > 0) summaryParts.push(`已忽略重复 ${duplicateCount} 份`);
      toast.success(`${summaryParts.join('，') || '已处理 0 份'} / 共 ${files.length} 份历史方案`);
      await loadQueueStatus();
      await loadMaterials();
    } catch (error) {
      toast.error(
        getApiErrorMessage(
          error,
          files.length > 1
            ? `出错前已处理 ${processedCount}/${files.length} 份历史方案`
            : '历史方案导入失败'
        )
      );
      setImportProgress(getApiErrorMessage(error, '历史方案导入失败'));
    } finally {
      setImporting(false);
      if (importFileInputRef.current) importFileInputRef.current.value = '';
    }
  };

  const reparseOne = async (item: LibraryMaterial) => {
    setBusySampleId(item.sample_id);
    setRebuildProgress('Submitting material reparse job...');
    try {
      if (item.source_kind === 'uploaded_document' && item.document_id) {
        const res = await api.post(`/documents/${item.document_id}/reparse`);
        const accepted = res.data as { message?: string };
        setRebuildProgress(accepted.message || 'Material reparse completed');
      } else {
        const res = await api.post(`/library/materials/${item.sample_id}/reparse`, {});
        const accepted = res.data as JobAccepted;
        setRebuildProgress(`Reparse queued: ${accepted.job_id}`);
        await waitForLibraryJob(accepted.next_poll);
      }
      toast.success('Material reparse completed');
      await loadMaterials();
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to queue material reparse'));
      setRebuildProgress(getApiErrorMessage(error, 'Material reparse failed'));
    } finally {
      setBusySampleId(null);
    }
  };

  const routeOne = async (sampleId: string, route: MaterialRoute) => {
    setBusySampleId(sampleId);
    try {
      await api.post(`/library/materials/${sampleId}/route`, {
        route,
        reason: `Marked from library audit page as ${route}`,
      });
      toast.success('Material route updated');
      await loadMaterials();
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to update material route'));
    } finally {
      setBusySampleId(null);
    }
  };

  const summary = payload?.summary;
  const issueCount = payload?.items.filter((item) => item.quality_flags.length > 0).length || 0;
  const processingCount = payload?.items.filter((item) => PROCESSING_PARSE_STATUSES.has(item.parse_status)).length || 0;
  const activityProgress = rebuilding || busySampleId ? rebuildProgress : (importProgress || rebuildProgress);
  const activityTitle = importing ? 'Historical proposal import' : 'Library rebuild status';

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-2xl font-bold">Historical Library Materials</h2>
          <p className="text-muted-foreground mt-1">
            Audit real proposal files before they enter the reusable library, AI Wiki, and visual index.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 rounded-md border bg-background px-2 py-1 text-sm">
            <span className="text-muted-foreground">Import route</span>
            <select
              className="bg-transparent text-sm outline-none"
              value={importRoute}
              onChange={(event) => setImportRoute(event.target.value as MaterialRoute)}
              disabled={importing}
            >
              {IMPORT_ROUTE_OPTIONS.map((route) => (
                <option key={route} value={route}>
                  {ROUTE_LABELS[route]}
                </option>
              ))}
            </select>
          </label>
          <input
            type="file"
            ref={importFileInputRef}
            onChange={importHistoricalMaterials}
            className="hidden"
            multiple
            accept=".pdf,.docx,.doc,.txt,.md"
          />
          <Button onClick={handleImportClick} disabled={importing || rebuilding}>
            {importing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UploadCloud className="mr-2 h-4 w-4" />}
            {importing ? 'Importing...' : 'Import Historical Proposal'}
          </Button>
          <Button variant="outline" onClick={() => void loadMaterials()} disabled={loading || rebuilding}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
          <Button onClick={rebuildAll} disabled={rebuilding}>
            {rebuilding ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Database className="mr-2 h-4 w-4" />}
            Rebuild Library
          </Button>
        </div>
      </div>

      {activityProgress && (
        <Alert>
          {rebuilding || busySampleId || importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
          <AlertTitle>{activityTitle}</AlertTitle>
          <AlertDescription>
            {activityProgress}
            {queueStatus && (
              <span className="mt-1 block text-xs text-muted-foreground">
                Queue: {queueStatus.running_count} running / {queueStatus.queued_count} waiting / {queueStatus.worker_count} worker(s)
              </span>
            )}
          </AlertDescription>
        </Alert>
      )}

      {issueCount > 0 && (
        <Alert>
          <ShieldAlert className="h-4 w-4" />
          <AlertTitle>Audit issues need review</AlertTitle>
          <AlertDescription>
            {issueCount} material(s) have parse, asset, or routing flags. They should not be promoted blindly.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-3 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Total Materials</CardDescription>
            <CardTitle>{summary?.total ?? 0}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Main Indexed</CardDescription>
            <CardTitle>{summary?.main_indexed_count ?? 0}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Indexed Chunks</CardDescription>
            <CardTitle>{summary?.indexed_chunk_count ?? 0}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Figure Assets</CardDescription>
            <CardTitle>{summary?.figure_asset_count ?? 0}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant={activeRoute === 'all' ? 'default' : 'outline'} size="sm" onClick={() => setActiveRoute('all')}>
          All
        </Button>
        <Button
          variant={activeRoute === 'processing' ? 'default' : 'outline'}
          size="sm"
          onClick={() => setActiveRoute('processing')}
        >
          Processing {processingCount}
        </Button>
        {ROUTE_OPTIONS.map((route) => (
          <Button
            key={route}
            variant={activeRoute === route ? 'default' : 'outline'}
            size="sm"
            onClick={() => setActiveRoute(route)}
          >
            {ROUTE_LABELS[route]} {payload?.summary.route_counts?.[route] ?? 0}
          </Button>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Material Audit</CardTitle>
          <CardDescription>
            Route decisions control whether a material is reusable evidence, validation holdout, conversion work, or excluded.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-10 text-center text-muted-foreground">Loading materials...</div>
          ) : items.length === 0 ? (
            <div className="py-10 text-center text-muted-foreground">No materials match this filter.</div>
          ) : (
            <div className="divide-y rounded-md border">
              {items.map((item) => (
                <div key={item.sample_id} className="p-4 space-y-3">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        {parseStatusIcon(item)}
                        <p className="font-medium">{item.file_name}</p>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                        <Badge variant="outline">{item.file_format}</Badge>
                        {item.source_kind === 'uploaded_document' ? <Badge variant="outline">已上传</Badge> : null}
                        <span>{formatBytes(item.file_size_bytes)}</span>
                        <span>路线：{ROUTE_LABELS[item.route]}</span>
                        <span>profile: {item.detected_profile || 'unknown'}</span>
                        {item.parser_backend ? <span>parser: {item.parser_backend}</span> : null}
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge
                        variant={item.material_status === 'main_indexed' ? 'default' : 'outline'}
                        className={materialStatusClass(item)}
                      >
                        {materialStatusLabel(item)}
                      </Badge>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void openMaterialDetail(item.sample_id)}
                      >
                        <Eye className="mr-2 h-4 w-4" />
                        Review details
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void reparseOne(item)}
                        disabled={busySampleId === item.sample_id}
                      >
                        {busySampleId === item.sample_id ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                        Reparse
                      </Button>
                    </div>
                  </div>

                  <div className="grid gap-2 text-sm md:grid-cols-5">
                    <div>chunks: {item.indexed_chunk_count}/{item.chunk_count}</div>
                    <div>figures: {item.figure_asset_count}</div>
                    <div>fallback assets: {item.storage_fallback_asset_count}</div>
                    <div>manifest images: {metricValue(item, 'image_count', item.image_count)}</div>
                    <div>tables: {metricValue(item, 'table_count', item.table_count)}</div>
                  </div>

                  {item.requires_cloud_parse ? (
                    <p className="text-sm text-red-700">
                      本地解析链路无法稳定处理该文档，需要启用阿里云文档解析后重新解析。
                    </p>
                  ) : null}

                  {item.quality_flags.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {item.quality_flags.map((flag) => (
                        <Badge key={flag} variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">
                          {formatQualityFlag(flag)}
                        </Badge>
                      ))}
                    </div>
                  )}

                  <div className="flex flex-wrap gap-2">
                    {ROUTE_OPTIONS.map((route) => (
                      <Button
                        key={route}
                        variant={item.route === route ? 'default' : 'outline'}
                        size="sm"
                        onClick={() => void routeOne(item.sample_id, route)}
                        disabled={busySampleId === item.sample_id || item.route === route}
                      >
                        {ROUTE_LABELS[route]}
                      </Button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="h-[92vh] w-[calc(100vw-1rem)] max-w-[calc(100vw-1rem)] grid-rows-[auto_minmax(0,1fr)] gap-0 overflow-hidden p-0 sm:max-w-[96vw] xl:max-w-[1440px]">
          <DialogHeader className="border-b px-5 py-4">
            <DialogTitle className="pr-10">
              {selectedMaterial?.file_name || 'Material detail'}
            </DialogTitle>
            <DialogDescription>
              Inspect parsed chunks, figure assets, table fallbacks, and quality flags before promoting this material.
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="min-h-0">
            {detailLoading ? (
              <div className="flex items-center justify-center py-16 text-muted-foreground">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                Loading material detail...
              </div>
            ) : selectedMaterial ? (
              <div className="space-y-5 p-5">
                <div className="grid gap-3 md:grid-cols-6">
                  <div className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">状态</p>
                    <p className="mt-1 font-medium">{materialStatusLabel(selectedMaterial)}</p>
                  </div>
                  <div className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">路线</p>
                    <p className="mt-1 font-medium">{ROUTE_LABELS[selectedMaterial.route]}</p>
                  </div>
                  <div className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">Chunks</p>
                    <p className="mt-1 font-medium">{selectedMaterial.indexed_chunk_count}/{selectedMaterial.chunk_count}</p>
                  </div>
                  <div className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">Assets</p>
                    <p className="mt-1 font-medium">{selectedMaterial.assets.length}</p>
                  </div>
                  <div className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">Figures</p>
                    <p className="mt-1 font-medium">{selectedMaterial.figure_asset_count}</p>
                  </div>
                  <div className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">Fallback</p>
                    <p className="mt-1 font-medium">{selectedMaterial.storage_fallback_asset_count}</p>
                  </div>
                </div>

                {selectedMaterial.quality_flags.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {selectedMaterial.quality_flags.map((flag) => (
                      <Badge key={flag} variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">
                        {formatQualityFlag(flag)}
                      </Badge>
                    ))}
                  </div>
                )}

                {selectedMaterial.requires_cloud_parse ? (
                  <Alert>
                    <ShieldAlert className="h-4 w-4" />
                    <AlertTitle>需要阿里云文档解析</AlertTitle>
                    <AlertDescription>
                      本地解析结果不足或失败。配置阿里云文档解析 AccessKey 后，重新解析该材料即可进入云解析链路。
                    </AlertDescription>
                  </Alert>
                ) : null}

                <div>
                  <div className="mb-3 flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                    <div className="flex items-center gap-2">
                      <ImageIcon className="h-4 w-4 text-muted-foreground" />
                      <h3 className="font-semibold">Parsed Assets</h3>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {assetFilterOptions.map((option) => (
                        <Button
                          key={option.filter}
                          type="button"
                          variant={assetFilter === option.filter ? 'default' : 'outline'}
                          size="sm"
                          onClick={() => setAssetFilter(option.filter)}
                          className="h-8"
                        >
                          {option.label} {option.count}
                        </Button>
                      ))}
                    </div>
                  </div>
                  {selectedMaterial.assets.length === 0 ? (
                    <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
                      No parsed assets for this material.
                    </div>
                  ) : filteredAssets.length === 0 ? (
                    <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
                      No assets match this category.
                    </div>
                  ) : (
                    <div className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-3">
                      {filteredAssets.map((asset) => (
                        <div key={asset.id} className="rounded-md border bg-background p-3">
                          <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="outline" className={assetBadgeClass(asset)}>
                                  {asset.asset_type}
                                </Badge>
                                {asset.visual_role ? <Badge variant="outline">{asset.visual_role}</Badge> : null}
                                {asset.asset_audit_status ? (
                                  <Badge variant="outline" className="border-slate-300 bg-slate-50 text-slate-800">
                                    {asset.asset_audit_status}
                                  </Badge>
                                ) : null}
                                {asset.asset_quality_score !== undefined && asset.asset_quality_score !== null ? (
                                  <Badge variant="outline" className="border-emerald-300 bg-emerald-50 text-emerald-800">
                                    quality {asset.asset_quality_score}
                                  </Badge>
                                ) : null}
                                {asset.storage_fallback ? <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">storage fallback</Badge> : null}
                                {hasAssetRepair(asset) ? <Badge variant="outline" className="border-cyan-300 bg-cyan-50 text-cyan-800">repaired</Badge> : null}
                              </div>
                              <p className="mt-2 text-sm font-medium leading-snug">{asset.title || asset.heading_path || asset.source_ref || asset.id}</p>
                              <p className="mt-1 text-xs text-muted-foreground">
                                {[formatAssetDimensions(asset), asset.source_ref, asset.source_section_id].filter(Boolean).join(' / ')}
                              </p>
                            </div>
                            <a
                              href={buildAssetContentUrl(asset.id)}
                              target="_blank"
                              rel="noreferrer"
                              className="text-xs font-medium text-muted-foreground hover:text-foreground"
                            >
                              Open
                            </a>
                          </div>
                          {canPreviewAssetImage(asset) ? (
                            <div className="overflow-hidden rounded-md border bg-muted">
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img src={buildAssetContentUrl(asset.id)} alt={asset.title || asset.id} className="max-h-80 w-full object-contain" />
                            </div>
                          ) : asset.raw_table_markdown ? (
                            <pre className="max-h-44 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap">
                              {asset.raw_table_markdown.slice(0, 1200)}
                            </pre>
                          ) : (
                            <div className="rounded-md border border-dashed p-4 text-xs text-muted-foreground">
                              {asset.storage_fallback
                                ? 'No reconstructed asset preview. This entry points back to the source document.'
                                : 'No image preview available for this asset.'}
                            </div>
                          )}
                          {asset.quality_flags.length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1">
                              {asset.quality_flags.map((flag) => (
                                <Badge key={flag} variant="outline" className="border-red-300 bg-red-50 text-red-800">
                                  {flag}
                                </Badge>
                              ))}
                            </div>
                          )}
                          {asset.asset_audit_reasons && asset.asset_audit_reasons.length > 0 && (
                            <p className="mt-2 text-xs text-muted-foreground">
                              Audit: {asset.asset_audit_reasons.join(' / ')}
                            </p>
                          )}
                          {(asset.context_before || asset.context_after) && (
                            <p className="mt-2 line-clamp-3 text-xs text-muted-foreground">
                              {[asset.context_before, asset.context_after].filter(Boolean).join(' / ')}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div>
                  <div className="mb-3 flex items-center gap-2">
                    <FileText className="h-4 w-4 text-muted-foreground" />
                    <h3 className="font-semibold">Parsed Chunks</h3>
                  </div>
                  <div className="space-y-2">
                    {selectedMaterial.chunks.slice(0, 40).map((chunk) => (
                      <div key={chunk.id} className="rounded-md border p-3">
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                          <Badge variant={chunk.indexed ? 'default' : 'outline'}>{chunk.indexed ? 'indexed' : 'not indexed'}</Badge>
                          <Badge variant="outline">{chunk.chunk_type}</Badge>
                          <span className="text-xs text-muted-foreground">#{chunk.chunk_index}</span>
                          {chunk.heading_path ? <span className="text-xs text-muted-foreground">{chunk.heading_path}</span> : null}
                        </div>
                        <p className="text-xs leading-5 text-muted-foreground whitespace-pre-wrap">{chunk.content_preview}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </div>
  );
}
