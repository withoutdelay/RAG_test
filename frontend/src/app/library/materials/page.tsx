'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, Database, Eye, FileText, Image as ImageIcon, Loader2, RefreshCw, ShieldAlert } from 'lucide-react';
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
  main_indexed: 'Main Indexed',
  review_pending: 'Review Pending',
  holdout_eval: 'Holdout Eval',
  conversion_required: 'Conversion Required',
  conversion_failed: 'Conversion Failed',
  excluded: 'Excluded',
};

const ROUTE_OPTIONS: MaterialRoute[] = [
  'main_indexed',
  'review_pending',
  'holdout_eval',
  'conversion_required',
  'excluded',
];
const LIBRARY_JOB_POLL_INTERVAL_MS = 3000;
const LIBRARY_JOB_POLL_TIMEOUT_MS = 2 * 60 * 60 * 1000;

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

function routeBadgeClass(route: MaterialRoute): string {
  switch (route) {
    case 'main_indexed':
      return 'bg-green-500 hover:bg-green-600';
    case 'review_pending':
      return 'border-amber-300 bg-amber-50 text-amber-800';
    case 'holdout_eval':
      return 'border-blue-300 bg-blue-50 text-blue-800';
    case 'conversion_required':
    case 'conversion_failed':
      return 'border-slate-300 bg-slate-50 text-slate-800';
    case 'excluded':
      return 'border-red-300 bg-red-50 text-red-800';
    default:
      return '';
  }
}

function parseStatusIcon(item: LibraryMaterial) {
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
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildProgress, setRebuildProgress] = useState<string | null>(null);
  const [queueStatus, setQueueStatus] = useState<JobQueueStatus | null>(null);
  const [activeRoute, setActiveRoute] = useState<MaterialRoute | 'all'>('all');
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedMaterial, setSelectedMaterial] = useState<LibraryMaterialDetail | null>(null);

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

  const openMaterialDetail = async (sampleId: string) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setSelectedMaterial(null);
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
    return allItems.filter((item) => item.route === activeRoute);
  }, [activeRoute, payload]);

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

  const reparseOne = async (sampleId: string) => {
    setBusySampleId(sampleId);
    setRebuildProgress('Submitting material reparse job...');
    try {
      const res = await api.post(`/library/materials/${sampleId}/reparse`, {});
      const accepted = res.data as JobAccepted;
      setRebuildProgress(`Reparse queued: ${accepted.job_id}`);
      await waitForLibraryJob(accepted.next_poll);
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

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-2xl font-bold">Historical Library Materials</h2>
          <p className="text-muted-foreground mt-1">
            Audit real proposal files before they enter the reusable library, AI Wiki, and visual index.
          </p>
        </div>
        <div className="flex items-center gap-2">
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

      {rebuildProgress && (
        <Alert>
          {rebuilding || busySampleId ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
          <AlertTitle>Library rebuild status</AlertTitle>
          <AlertDescription>
            {rebuildProgress}
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
                        <span>{formatBytes(item.file_size_bytes)}</span>
                        <span>profile: {item.detected_profile || 'unknown'}</span>
                        <span>parse: {item.parse_status}</span>
                        {item.parser_backend ? <span>parser: {item.parser_backend}</span> : null}
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={item.route === 'main_indexed' ? 'default' : 'outline'} className={routeBadgeClass(item.route)}>
                        {ROUTE_LABELS[item.route]}
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
                        onClick={() => void reparseOne(item.sample_id)}
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

                  {item.quality_flags.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {item.quality_flags.map((flag) => (
                        <Badge key={flag} variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">
                          {flag}
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
        <DialogContent className="max-h-[92vh] max-w-[1120px] p-0">
          <DialogHeader className="border-b px-5 py-4">
            <DialogTitle className="pr-10">
              {selectedMaterial?.file_name || 'Material detail'}
            </DialogTitle>
            <DialogDescription>
              Inspect parsed chunks, figure assets, table fallbacks, and quality flags before promoting this material.
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-[78vh]">
            {detailLoading ? (
              <div className="flex items-center justify-center py-16 text-muted-foreground">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                Loading material detail...
              </div>
            ) : selectedMaterial ? (
              <div className="space-y-5 p-5">
                <div className="grid gap-3 md:grid-cols-5">
                  <div className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">Route</p>
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
                        {flag}
                      </Badge>
                    ))}
                  </div>
                )}

                <div>
                  <div className="mb-3 flex items-center gap-2">
                    <ImageIcon className="h-4 w-4 text-muted-foreground" />
                    <h3 className="font-semibold">Parsed Assets</h3>
                  </div>
                  {selectedMaterial.assets.length === 0 ? (
                    <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
                      No parsed assets for this material.
                    </div>
                  ) : (
                    <div className="grid gap-3 lg:grid-cols-2">
                      {selectedMaterial.assets.map((asset) => (
                        <div key={asset.id} className="rounded-md border p-3">
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
                              <img src={buildAssetContentUrl(asset.id)} alt={asset.title || asset.id} className="max-h-56 w-full object-contain" />
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
