'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'next/navigation';
import { Download, FileDown, Copy, CheckCircle2, Loader2, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { ExportData, ReviewTask, ValidationReport } from '@/lib/types';
import { issueFromTaskPayload } from '@/lib/review-tasks';
import Markdown from 'react-markdown';
import { saveAs } from 'file-saver';

function getCompletionSourceLabel(source: string): string {
  if (source === 'editor') {
    return 'Editor';
  }
  if (source === 'validation') {
    return 'Validation';
  }
  return 'Review flow';
}

function getValidationStatusLabel(status: string): string {
  if (status === 'passed') {
    return 'Passed';
  }
  if (status === 'blocked') {
    return 'Blocked';
  }
  if (status === 'review_required') {
    return 'Review Required';
  }
  return status || 'Unknown';
}

function getValidationStatusVariant(
  status: string,
): 'default' | 'secondary' | 'warning' | 'success' | 'destructive' {
  if (status === 'passed') {
    return 'success';
  }
  if (status === 'blocked') {
    return 'destructive';
  }
  if (status === 'review_required') {
    return 'warning';
  }
  return 'secondary';
}

function formatTimestamp(value?: string | null): string {
  if (!value) {
    return 'N/A';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', { hour12: false });
}

function summarizeResolution(value: unknown): string {
  if (!value) {
    return '已处理';
  }
  if (typeof value === 'string') {
    return value.trim() || '已处理';
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const note = String(record.note || '').trim();
    if (note) {
      return note;
    }
    const action = String(record.action || '').trim();
    const decision = String(record.value || '').trim();
    if (action || decision) {
      return [action, decision].filter(Boolean).join(' / ');
    }
  }
  return '已处理';
}

export default function ExportPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const projectId = params.id as string;
  const [exportData, setExportData] = useState<ExportData | null>(null);
  const [validationReport, setValidationReport] = useState<ValidationReport | null>(null);
  const [reviewTasks, setReviewTasks] = useState<ReviewTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [autoExportAttempted, setAutoExportAttempted] = useState(false);
  const reviewCompleted = searchParams.get('review_completed') === '1';
  const completionSource = getCompletionSourceLabel(searchParams.get('source')?.trim() || '');

  const fetchExport = useCallback(async () => {
    try {
      setLoading(true);
      const [exportRes, reportRes, reviewTasksRes] = await Promise.all([
        api.get(`/projects/${projectId}/exports/latest`).catch((error) => {
          if (isNotFoundError(error)) {
            return null;
          }
          throw error;
        }),
        api.get(`/projects/${projectId}/validation/latest`).catch((error) => {
          if (isNotFoundError(error)) {
            return null;
          }
          throw error;
        }),
        api.get(`/projects/${projectId}/review-tasks`).catch((error) => {
          if (isNotFoundError(error)) {
            return null;
          }
          throw error;
        }),
      ]);
      setExportData((exportRes?.data as ExportData | undefined) || null);
      setValidationReport((reportRes?.data as ValidationReport | undefined) || null);
      setReviewTasks(Array.isArray(reviewTasksRes?.data) ? (reviewTasksRes?.data as ReviewTask[]) : []);
    } catch (error) {
      if (!isNotFoundError(error)) {
        console.error(error);
        toast.error(getApiErrorMessage(error, 'Failed to load export workspace'));
      }
      setExportData(null);
      setValidationReport(null);
      setReviewTasks([]);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      void fetchExport();
    }
  }, [fetchExport, projectId]);

  const handleGenerateExport = useCallback(
    async ({ auto = false }: { auto?: boolean } = {}) => {
      setExporting(true);
      try {
        await api.post(`/projects/${projectId}/export`, { format: 'markdown' });
        toast.success(auto ? 'Latest export generated automatically' : 'Document complied and exported');
        await fetchExport();
      } catch (error) {
        console.error(error);
        toast.error(getApiErrorMessage(error, auto ? 'Error auto-generating latest export' : 'Error exporting document'));
      } finally {
        setExporting(false);
      }
    },
    [fetchExport, projectId],
  );

  const handleCopy = () => {
    if (!exportData?.content_md) return;
    navigator.clipboard.writeText(exportData.content_md);
    setCopied(true);
    toast.success('Copied to clipboard');
    setTimeout(() => setCopied(false), 3000);
  };

  const handleDownload = () => {
    if (!exportData?.content_md) return;
    const blob = new Blob([exportData.content_md], { type: 'text/markdown;charset=utf-8' });
    saveAs(blob, exportData.file_name || `Project_${projectId}_Export.md`);
  };

  const resolvedTasks = useMemo(
    () => reviewTasks.filter((task) => task.status === 'resolved'),
    [reviewTasks],
  );
  const blockingTasks = useMemo(
    () => reviewTasks.filter((task) => ['open', 'in_progress', 'rejected'].includes(task.status)),
    [reviewTasks],
  );
  const resolvedSectionCount = useMemo(() => {
    const sectionKeys = new Set(
      resolvedTasks
        .map((task) => {
          const issue = issueFromTaskPayload(task.payload || {});
          return String(issue.section_title || issue.section_id || '').trim();
        })
        .filter(Boolean),
    );
    return sectionKeys.size;
  }, [resolvedTasks]);
  const resolvedCodeSummary = useMemo(() => {
    const counts = new Map<string, number>();
    resolvedTasks.forEach((task) => {
      const issue = issueFromTaskPayload(task.payload || {});
      const key = String(issue.code || task.task_type).trim();
      if (!key) {
        return;
      }
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return Array.from(counts.entries())
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
      .slice(0, 8);
  }, [resolvedTasks]);
  const latestResolvedTasks = useMemo(
    () =>
      [...resolvedTasks]
        .sort((left, right) => {
          const rightTime = new Date(right.resolved_at || right.created_at || 0).getTime();
          const leftTime = new Date(left.resolved_at || left.created_at || 0).getTime();
          return rightTime - leftTime;
        })
        .slice(0, 4),
    [resolvedTasks],
  );
  const exportNeedsRefresh = useMemo(() => {
    if (!reviewCompleted || validationReport?.status !== 'passed' || blockingTasks.length > 0) {
      return false;
    }
    if (!exportData) {
      return true;
    }
    if (validationReport.id && exportData.validation_report_id !== validationReport.id) {
      return true;
    }
    const exportTime = new Date(exportData.created_at || 0).getTime();
    const validationTime = new Date(validationReport.created_at || 0).getTime();
    return Boolean(validationTime && exportTime && exportTime < validationTime);
  }, [blockingTasks.length, exportData, reviewCompleted, validationReport]);

  useEffect(() => {
    if (loading || exporting || autoExportAttempted || !exportNeedsRefresh) {
      return;
    }
    setAutoExportAttempted(true);
    void handleGenerateExport({ auto: true });
  }, [autoExportAttempted, exportNeedsRefresh, exporting, handleGenerateExport, loading]);

  return (
    <div className="flex flex-col h-full bg-slate-50 overflow-hidden">
      <div className="p-6 pb-4 shrink-0 bg-background border-b flex justify-between items-end">
        <div>
          <h2 className="text-2xl font-bold flex items-center">
            <Download className="mr-2 h-6 w-6 text-emerald-600" /> Final Output
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Compile all sections into a final comprehensive Markdown or Docx document.
          </p>
        </div>
        <div className="flex space-x-3">
          <Button onClick={() => void handleGenerateExport()} disabled={exporting || loading}>
            {exporting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
            {exporting ? 'Compiling...' : 'Generate New Export'}
          </Button>
        </div>
      </div>

      {reviewCompleted ? (
        <div className="px-6 pt-4">
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">
            <div className="flex flex-wrap items-center gap-2">
              <CheckCircle2 className="h-4 w-4" />
              <span className="font-semibold">All blocking review tasks are cleared</span>
              <Badge variant="outline" className="border-emerald-300 bg-white text-emerald-900">
                From {completionSource}
              </Badge>
            </div>
            <p className="mt-2 leading-6">
              {exportNeedsRefresh
                ? 'Validation has reached an exportable state. A latest export is being prepared automatically.'
                : 'Validation has reached an exportable state. You can compile the final document now or review the latest export below.'}
            </p>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="flex-1 flex justify-center items-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : !exportData ? (
        <div className="flex-1 p-6 flex items-center justify-center">
          <Card className="flex flex-col items-center justify-center p-12 text-center border-dashed border-2 w-full max-w-2xl">
            <FileDown className="h-12 w-12 text-muted-foreground opacity-50 mb-4" />
            <h3 className="text-lg font-medium">Ready to Assemble</h3>
            <p className="text-muted-foreground text-sm mt-1 mb-6">
              The AI has finished drafting all sections and resolving tasks. 
              Click below to combine them into the final file.
            </p>
            <Button onClick={() => void handleGenerateExport()} size="lg" className="bg-emerald-600 hover:bg-emerald-700">
              <Sparkles className="mr-2 h-5 w-5" /> Compile Final Document
            </Button>
          </Card>
        </div>
      ) : (
        <div className="flex-1 flex flex-col lg:flex-row gap-6 p-6 overflow-hidden">
          <div className="lg:w-1/3 flex flex-col space-y-6 shrink-0">
            <Card>
              <CardHeader className="pb-4">
                <CardTitle className="text-lg">Review Proof Pack</CardTitle>
                <CardDescription>Latest validation snapshot and resolved review trail</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4 text-sm">
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Validation</span>
                    <Badge variant={getValidationStatusVariant(validationReport?.status || '')}>
                      {getValidationStatusLabel(validationReport?.status || '')}
                    </Badge>
                  </div>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Validated At</span>
                    <span className="font-medium text-right">
                      {formatTimestamp(validationReport?.created_at)}
                    </span>
                  </div>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Resolved Tasks</span>
                    <span className="font-medium">{resolvedTasks.length}</span>
                  </div>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Sections Touched</span>
                    <span className="font-medium">{resolvedSectionCount}</span>
                  </div>
                  <div className="flex justify-between pb-2">
                    <span className="text-muted-foreground">Blocking Left</span>
                    <span className="font-medium">{blockingTasks.length}</span>
                  </div>
                </div>

                {resolvedCodeSummary.length > 0 ? (
                  <div className="mt-5">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                      Cleared Review Codes
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {resolvedCodeSummary.map(([code, count]) => (
                        <Badge key={`resolved-code-${code}`} variant="outline" className="bg-white">
                          {code} x{count}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}

                {latestResolvedTasks.length > 0 ? (
                  <div className="mt-5">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                      Latest Resolutions
                    </p>
                    <div className="mt-3 space-y-3">
                      {latestResolvedTasks.map((task) => {
                        const issue = issueFromTaskPayload(task.payload || {});
                        const summary = summarizeResolution(task.payload?.resolution);
                        return (
                          <div key={task.id} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant="outline">{issue.code || task.task_type}</Badge>
                              {issue.section_title ? (
                                <Badge variant="outline" className="bg-white">
                                  {issue.section_title}
                                </Badge>
                              ) : null}
                            </div>
                            <p className="mt-2 text-xs leading-5 text-slate-700">{summary}</p>
                            <p className="mt-1 text-[11px] text-slate-500">
                              {formatTimestamp(task.resolved_at || task.created_at)}
                            </p>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-4">
                <CardTitle className="text-lg">Export Details</CardTitle>
                <CardDescription>File compilation completed</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4 text-sm">
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Filename</span>
                    <span className="font-medium text-right max-w-[200px] truncate" title={exportData.file_name}>{exportData.file_name || `Export_${projectId}.md`}</span>
                  </div>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Format</span>
                    <span className="font-medium uppercase">{exportData.file_type || 'Markdown'}</span>
                  </div>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Status</span>
                    <span className="font-medium flex items-center text-emerald-600">
                      <CheckCircle2 className="w-4 h-4 mr-1"/> Success
                    </span>
                  </div>
                </div>
                
                <div className="mt-8 flex flex-col space-y-3">
                  <Button variant="outline" className="w-full justify-start font-normal" onClick={handleCopy}>
                    {copied ? <CheckCircle2 className="mr-2 h-4 w-4 text-green-500" /> : <Copy className="mr-2 h-4 w-4 text-slate-500" />}
                    {copied ? 'Copied Markdown' : 'Copy Full Content'}
                  </Button>
                  <Button className="w-full justify-start font-normal bg-slate-800 hover:bg-slate-900" onClick={handleDownload}>
                    <Download className="mr-2 h-4 w-4" /> Download as File
                  </Button>
                </div>
              </CardContent>
            </Card>
            
            <div className="bg-blue-50 text-blue-800 text-sm p-4 rounded-lg border border-blue-100 italic">
              This is the initial compiled output. You can safely download it to Word or keep iterating on the drafts.
            </div>
          </div>
          
          <Card className="flex-1 flex flex-col shadow-sm border overflow-hidden bg-white">
            <div className="p-3 border-b bg-muted/30 flex justify-between items-center text-sm font-medium text-slate-600 shrink-0">
              <span>Preview</span>
              <Badge variant="outline" className="font-mono bg-white">{exportData.content_md?.length || 0} characters</Badge>
            </div>
            <ScrollArea className="flex-1 p-6 lg:p-10">
              <div className="prose prose-slate max-w-none dark:prose-invert">
                <Markdown>{exportData.content_md}</Markdown>
              </div>
            </ScrollArea>
          </Card>
        </div>
      )}
    </div>
  );
}
