'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowRight, CheckCircle2, Loader2, PlayCircle, ShieldCheck, ThumbsDown, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import {
  ReviewTaskActionStatus,
  buildReviewResolutionPayload,
  issueFromTaskPayload,
  toRecord,
  toRecordArray,
  toStringArray,
} from '@/lib/review-tasks';
import { ReviewTask, ValidationIssue, ValidationReport } from '@/lib/types';

const DETAIL_LABELS: Record<string, string> = {
  missing_fields: 'Missing Fields',
  missing_items: 'Missing Items',
  values: 'Conflicting Values',
  missing_products: 'Missing Products',
  expected_products: 'Expected Products',
  expected_catalog_interface_signals: 'Expected Interface Signals',
  missing_series_codes: 'Missing Series',
};

const TOKEN_LABELS: Record<string, string> = {
  primary_product: 'Primary Product',
  voltage_level: 'Voltage Level',
  power_rating: 'Power Rating',
  model_number: 'Model Number',
  dcs_protocol: 'DCS Protocol',
  catalog_interface_signals: 'Catalog Interface Signals',
  DI: 'DI',
  DO: 'DO',
  AI: 'AI',
  AO: 'AO',
};

function humanizeToken(value: string): string {
  return TOKEN_LABELS[value] || value.replace(/_/g, ' ');
}

function humanizeDetailKey(value: string): string {
  return DETAIL_LABELS[value] || value.replace(/_/g, ' ');
}

function formatScalarValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(', ');
  if (value && typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function formatRelationType(value: unknown): string {
  const relation = String(value || '').trim().toLowerCase();
  if (relation === 'requires') return 'Required';
  if (relation === 'recommended') return 'Recommended';
  if (relation === 'optional') return 'Optional';
  if (relation === 'conflicts_with') return 'Conflict';
  return String(value || '');
}

function getOrderedActionableReviewTasks(tasks: ReviewTask[]): ReviewTask[] {
  return [
    ...tasks.filter((task) => task.status === 'open' || task.status === 'in_progress'),
    ...tasks.filter((task) => task.status === 'rejected'),
  ];
}

function pickNextReviewTask(tasks: ReviewTask[], excludedTaskIds: string[] = []): ReviewTask | null {
  const actionableTasks = getOrderedActionableReviewTasks(tasks);
  if (actionableTasks.length === 0) {
    return null;
  }
  const excluded = new Set(excludedTaskIds.filter(Boolean));
  return actionableTasks.find((task) => !excluded.has(task.id)) || actionableTasks[0] || null;
}

function buildExportReadyUrl(projectId: string): string {
  return `/projects/${projectId}/export?review_completed=1&source=validation`;
}

function IssueStructuredDetails({ issue }: { issue: ValidationIssue }) {
  const details = toRecord(issue.details);
  const missingFields = toStringArray(details.missing_fields).map(humanizeToken);
  const missingItems = toStringArray(details.missing_items).map(humanizeToken);
  const values = toStringArray(details.values);
  const missingProducts = toStringArray(details.missing_products);
  const expectedProducts = toStringArray(details.expected_products);
  const expectedSignals = toStringArray(details.expected_catalog_interface_signals);
  const uncoveredActions = toRecordArray(details.uncovered_actions);
  const genericDetails = Object.entries(details).filter(
    ([key]) =>
      ![
        'missing_fields',
        'missing_items',
        'values',
        'missing_products',
        'expected_products',
        'expected_catalog_interface_signals',
        'uncovered_actions',
      ].includes(key)
  );

  if (
    missingFields.length === 0 &&
    missingItems.length === 0 &&
    values.length === 0 &&
    missingProducts.length === 0 &&
    expectedProducts.length === 0 &&
    expectedSignals.length === 0 &&
    uncoveredActions.length === 0 &&
    genericDetails.length === 0
  ) {
    return null;
  }

  return (
    <div className="mt-3 space-y-3 rounded-md border border-border/70 bg-white/70 p-3">
      {missingFields.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Missing Fields</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {missingFields.map((item) => (
              <Badge key={`missing-field-${item}`} variant="warning" className="text-[11px]">
                {item}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {missingItems.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Missing Items</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {missingItems.map((item) => (
              <Badge key={`missing-item-${item}`} variant="warning" className="text-[11px]">
                {item}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {values.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Conflicting Values</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {values.map((item) => (
              <Badge key={`value-${item}`} variant="outline" className="text-[11px]">
                {item}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {missingProducts.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Missing Products</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {missingProducts.map((item) => (
              <Badge key={`missing-product-${item}`} variant="warning" className="text-[11px]">
                {item}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {expectedProducts.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Expected Products</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {expectedProducts.map((item) => (
              <Badge key={`expected-product-${item}`} variant="outline" className="text-[11px]">
                {item}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {expectedSignals.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Expected Interface Signals</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {expectedSignals.map((item) => (
              <Badge key={`expected-signal-${item}`} variant="outline" className="text-[11px]">
                {item}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {uncoveredActions.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Compatibility Gaps</p>
          <div className="mt-2 space-y-2">
            {uncoveredActions.map((action, index) => (
              <div key={`uncovered-action-${index}`} className="rounded-md border border-border/70 bg-[#fbfcf8] px-3 py-2 text-xs leading-5 text-muted-foreground">
                {(() => {
                  const targetFamilyCode = String(action.target_family_code || '').trim();
                  const relationType = String(action.relation_type || '').trim();
                  const condition = String(action.condition || '').trim();
                  const missingSeries = toStringArray(action.missing_series_codes);

                  return (
                    <>
                      <div className="flex flex-wrap items-center gap-2">
                        {targetFamilyCode && (
                          <Badge variant="outline" className="text-[11px]">
                            {targetFamilyCode}
                          </Badge>
                        )}
                        {relationType && (
                          <Badge variant="warning" className="text-[11px]">
                            {formatRelationType(relationType)}
                          </Badge>
                        )}
                      </div>
                      {condition && <p className="mt-2">{condition}</p>}
                      {missingSeries.length > 0 && (
                        <p className="mt-2">Missing: {missingSeries.join(', ')}</p>
                      )}
                    </>
                  );
                })()}
              </div>
            ))}
          </div>
        </div>
      )}

      {genericDetails.length > 0 && (
        <div className="space-y-2">
          {genericDetails.map(([key, value]) => (
            <div key={`generic-detail-${key}`} className="text-xs leading-5 text-muted-foreground">
              <span className="font-medium text-foreground">{humanizeDetailKey(key)}:</span>{' '}
              {formatScalarValue(value)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function IssueSummaryCard({
  issue,
  tone,
}: {
  issue: ValidationIssue;
  tone: 'error' | 'warning';
}) {
  const isError = tone === 'error';
  return (
    <div
      className={`rounded-md border p-3 ${
        isError ? 'border-red-200 bg-white text-red-900' : 'border-amber-200 bg-white text-amber-900'
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={isError ? 'destructive' : 'warning'} className="text-[11px]">
          {issue.code}
        </Badge>
        {issue.section_title && (
          <Badge variant="outline" className="text-[11px]">
            {issue.section_title}
          </Badge>
        )}
      </div>
      <p className="mt-2 text-xs leading-5">{issue.message}</p>
      {issue.suggested_action && (
        <p className="mt-2 text-[11px] leading-5 text-muted-foreground">{issue.suggested_action}</p>
      )}
      <IssueStructuredDetails issue={issue} />
    </div>
  );
}

export default function ValidationPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [reviewTasks, setReviewTasks] = useState<ReviewTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [validating, setValidating] = useState(false);
  const [actingTaskId, setActingTaskId] = useState<string | null>(null);
  const [focusedTaskId, setFocusedTaskId] = useState<string | null>(null);

  const loadValidationData = useCallback(async ({ showLoading = true }: { showLoading?: boolean } = {}) => {
    try {
      if (showLoading) {
        setLoading(true);
      }

      let nextReport: ValidationReport | null = null;
      let nextReviewTasks: ReviewTask[] = [];
      try {
        const reportRes = await api.get(`/projects/${projectId}/validation/latest`);
        nextReport = reportRes.data as ValidationReport;
      } catch (error) {
        if (!isNotFoundError(error)) {
          console.error(error);
          toast.error(getApiErrorMessage(error, 'Failed to load validation report'));
        }
      }

      try {
        const tasksRes = await api.get(`/projects/${projectId}/review-tasks`);
        nextReviewTasks = Array.isArray(tasksRes.data) ? (tasksRes.data as ReviewTask[]) : [];
      } catch (error) {
        console.error(error);
        toast.error(getApiErrorMessage(error, 'Failed to load review tasks'));
      }

      setReport(nextReport);
      setReviewTasks(nextReviewTasks);
      return {
        report: nextReport,
        reviewTasks: nextReviewTasks,
      };
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }, [projectId]);

  const refreshValidationData = useCallback(async () => {
    await loadValidationData({ showLoading: true });
  }, [loadValidationData]);

  useEffect(() => {
    if (projectId) {
      void refreshValidationData();
    }
  }, [projectId, refreshValidationData]);

  const handleValidate = async () => {
    setValidating(true);
    try {
      await api.post(`/projects/${projectId}/validate`, {});
      toast.success('Validation process started');
      await loadValidationData({ showLoading: false });
    } catch (error) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Error running validation'));
    } finally {
      setValidating(false);
    }
  };

  const rerunValidationAndAdvance = useCallback(
    async (excludedTaskIds: string[]) => {
      setValidating(true);
      try {
        await api.post(`/projects/${projectId}/validate`, {});
        const nextData = await loadValidationData({ showLoading: false });
        const nextTask = pickNextReviewTask(nextData.reviewTasks, excludedTaskIds);
        setFocusedTaskId(nextTask?.id || null);
        if (nextTask) {
          const nextIssue = issueFromTaskPayload(nextTask.payload || {});
          toast.success(
            `Validation rerun complete. Next task: ${nextIssue.code || nextIssue.section_title || nextTask.task_type}`,
          );
        } else {
          if (nextData.report?.status === 'passed') {
            toast.success('Validation rerun complete. Export is ready.');
            router.push(buildExportReadyUrl(projectId));
            return;
          }
          toast.success('Validation rerun complete. No blocking tasks remain.');
        }
      } catch (error) {
        console.error(error);
        toast.error(getApiErrorMessage(error, 'Review task resolved, but validation rerun failed'));
        await loadValidationData({ showLoading: false });
      } finally {
        setValidating(false);
      }
    },
    [loadValidationData, projectId, router],
  );

  const handleTaskAction = async (
    task: ReviewTask,
    resolution: string,
    status: ReviewTaskActionStatus,
  ) => {
    setActingTaskId(task.id);
    try {
      await api.post(`/projects/${projectId}/review-tasks/${task.id}/resolve`, {
        resolution: buildReviewResolutionPayload(task, resolution, status),
        status,
      });
      if (status === 'resolved') {
        await rerunValidationAndAdvance([task.id]);
      } else {
        toast.success('Review task rejected');
        await loadValidationData({ showLoading: false });
        setFocusedTaskId(task.id);
      }
    } catch (error) {
      console.error(error);
      toast.error(getApiErrorMessage(error, `Error ${status === 'resolved' ? 'resolving' : 'rejecting'} task`));
    } finally {
      setActingTaskId(null);
    }
  };

  const activeTasks = useMemo(
    () => reviewTasks.filter((task) => task.status === 'open' || task.status === 'in_progress'),
    [reviewTasks],
  );
  const rejectedTasks = useMemo(
    () => reviewTasks.filter((task) => task.status === 'rejected'),
    [reviewTasks],
  );
  const blockingTasks = useMemo(() => [...activeTasks, ...rejectedTasks], [activeTasks, rejectedTasks]);
  const resolvedTasks = useMemo(
    () => reviewTasks.filter((task) => task.status === 'resolved'),
    [reviewTasks],
  );
  const focusedTask = useMemo(
    () => reviewTasks.find((task) => task.id === focusedTaskId) || null,
    [focusedTaskId, reviewTasks],
  );
  const focusedIssue = focusedTask ? issueFromTaskPayload(focusedTask.payload || {}) : null;

  useEffect(() => {
    const orderedTasks = getOrderedActionableReviewTasks(reviewTasks);
    if (orderedTasks.length === 0) {
      if (focusedTaskId !== null) {
        setFocusedTaskId(null);
      }
      return;
    }
    if (!focusedTaskId || !orderedTasks.some((task) => task.id === focusedTaskId)) {
      setFocusedTaskId(orderedTasks[0].id);
    }
  }, [focusedTaskId, reviewTasks]);

  useEffect(() => {
    if (!focusedTaskId) {
      return;
    }
    const element = document.getElementById(`review-task-${focusedTaskId}`);
    if (!element) {
      return;
    }
    window.setTimeout(() => {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 120);
  }, [focusedTaskId, reviewTasks]);

  return (
    <div className="p-6 space-y-6 flex flex-col h-full overflow-hidden">
      <div className="flex justify-between items-center shrink-0">
        <div>
          <h2 className="text-2xl font-bold flex items-center">
            <ShieldCheck className="mr-2 h-6 w-6 text-indigo-600" /> Cross-Validation
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Semantic review and global parameter consistency check across all drafts.
          </p>
        </div>
        <div className="flex space-x-2">
          {report?.status === 'passed' && blockingTasks.length === 0 && (
            <Button onClick={() => router.push(buildExportReadyUrl(projectId))} className="bg-green-600 hover:bg-green-700">
              Proceed to Export <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          )}
          <Button onClick={handleValidate} disabled={validating || loading}>
            {validating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlayCircle className="mr-2 h-4 w-4" />}
            {validating ? 'Validating...' : 'Run Validation'}
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="flex-1 flex justify-center items-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : !report ? (
        <Card className="flex flex-col items-center justify-center p-12 text-center border-dashed border-2 flex-1">
          <ShieldCheck className="h-12 w-12 text-muted-foreground opacity-50 mb-4" />
          <h3 className="text-lg font-medium">No Validation Run</h3>
          <p className="text-muted-foreground text-sm mt-1 mb-6">
            After generating section drafts, run the semantic validator to catch inconsistencies.
          </p>
          <Button onClick={handleValidate} disabled={validating}>
            <PlayCircle className="mr-2 h-4 w-4" /> Start Validation
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 overflow-hidden">
          <div className="lg:col-span-1 space-y-6 overflow-y-auto">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-lg">Validation Status</CardTitle>
                <CardDescription>Overall quality report</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">Global Status</span>
                    {report.status === 'passed' ? (
                      <Badge variant="success" className="bg-green-100 text-green-800">
                        <CheckCircle2 className="w-3 h-3 mr-1" /> Passed
                      </Badge>
                    ) : report.status === 'blocked' ? (
                      <Badge variant="destructive">
                        <AlertTriangle className="w-3 h-3 mr-1" /> Blocked
                      </Badge>
                    ) : (
                      <Badge variant="warning">
                        <AlertTriangle className="w-3 h-3 mr-1" /> Review Required
                      </Badge>
                    )}
                  </div>

                  <div className="pt-2 border-t text-sm">
                    <div className="flex justify-between py-1 text-red-600">
                      <span>Errors Found</span>
                      <span className="font-bold">{report.errors?.length || 0}</span>
                    </div>
                    <div className="flex justify-between py-1 text-amber-600">
                      <span>Warnings</span>
                      <span className="font-bold">{report.warnings?.length || 0}</span>
                    </div>
                    <div className="flex justify-between py-1 text-indigo-600">
                      <span>Blocking Review Tasks</span>
                      <span className="font-bold">{blockingTasks.length}</span>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-lg">System Logs</CardTitle>
              </CardHeader>
               <CardContent>
                 <div className="space-y-4">
                  <details className="group border rounded-md overflow-hidden bg-white" open={(report.errors?.length || 0) > 0}>
                    <summary className="p-2.5 bg-red-50 text-red-800 font-semibold cursor-pointer border-b border-red-100 flex items-center focus:outline-none focus:ring-1 focus:ring-red-300 text-sm">
                      <AlertTriangle className="w-4 h-4 mr-2" />
                      Critical Errors ({report.errors?.length || 0})
                    </summary>
                    <div className="p-3 space-y-2 bg-red-50/30">
                      {!report.errors?.length ? (
                        <p className="text-xs text-muted-foreground">No errors found.</p>
                      ) : (
                        report.errors.map((err, index) => (
                          <IssueSummaryCard key={`error-${index}`} issue={err} tone="error" />
                        ))
                      )}
                    </div>
                  </details>

                  <details className="group border rounded-md overflow-hidden bg-white" open={(report.warnings?.length || 0) > 0}>
                    <summary className="p-2.5 bg-amber-50 text-amber-800 font-semibold cursor-pointer border-b border-amber-100 flex items-center focus:outline-none focus:ring-1 focus:ring-amber-300 text-sm">
                      <AlertTriangle className="w-4 h-4 mr-2" />
                      Warnings ({report.warnings?.length || 0})
                    </summary>
                    <div className="p-3 space-y-2 bg-amber-50/30">
                      {!report.warnings?.length ? (
                        <p className="text-xs text-muted-foreground">No warnings.</p>
                      ) : (
                        report.warnings.map((warn, index) => (
                          <IssueSummaryCard key={`warn-${index}`} issue={warn} tone="warning" />
                        ))
                      )}
                    </div>
                  </details>
                 </div>
               </CardContent>
            </Card>
          </div>

          <div className="lg:col-span-2 flex flex-col h-full bg-card rounded-xl border shadow-sm">
            <div className="p-4 border-b bg-muted/30">
              <h3 className="font-semibold text-lg flex items-center">
                Human Review Tasks
                <Badge variant="secondary" className="ml-2 bg-indigo-100 text-indigo-800">
                  {blockingTasks.length} Blocking
                </Badge>
              </h3>
            </div>

            <ScrollArea className="flex-1 p-4">
              {focusedTask && (
                <div className="mb-4 rounded-md border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-950">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">Current review target</span>
                    <Badge variant="outline">{focusedIssue?.code || focusedTask.task_type}</Badge>
                    {focusedIssue?.section_title ? <Badge variant="outline">{focusedIssue.section_title}</Badge> : null}
                  </div>
                  <p className="mt-2 leading-6">
                    {focusedIssue?.message || 'Resolve this item or open it in the editor for in-context revision.'}
                  </p>
                </div>
              )}

              {blockingTasks.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                  <CheckCircle2 className="w-12 h-12 text-green-500 mb-4 opacity-70" />
                  <p className="font-medium text-slate-800">All blocking tasks cleared</p>
                  <p className="text-sm mt-1">Ready for export</p>
                </div>
              ) : (
                <div className="space-y-6">
                  {activeTasks.length > 0 && (
                    <div className="space-y-4">
                      <div className="flex items-center justify-between">
                        <h4 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                          Open Tasks
                        </h4>
                        <Badge variant="outline">{activeTasks.length}</Badge>
                      </div>
                      {activeTasks.map((task) => (
                        <ReviewTaskCard
                          key={task.id}
                          task={task}
                          actingTaskId={actingTaskId}
                          isFocused={focusedTaskId === task.id}
                          onFocus={() => setFocusedTaskId(task.id)}
                          onAction={handleTaskAction}
                          projectId={projectId}
                        />
                      ))}
                    </div>
                  )}

                  {rejectedTasks.length > 0 && (
                    <div className="space-y-4">
                      <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">
                        Some review tasks were explicitly rejected. Update the related drafts, then run validation again to get a fresh review state.
                      </div>
                      <div className="flex items-center justify-between">
                        <h4 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                          Rejected Tasks
                        </h4>
                        <Badge variant="destructive">{rejectedTasks.length}</Badge>
                      </div>
                      {rejectedTasks.map((task) => (
                        <ReviewTaskCard
                          key={task.id}
                          task={task}
                          actingTaskId={actingTaskId}
                          isFocused={focusedTaskId === task.id}
                          onFocus={() => setFocusedTaskId(task.id)}
                          onAction={handleTaskAction}
                          projectId={projectId}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}

              {resolvedTasks.length > 0 && (
                <div className="mt-8">
                  <h4 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-4 px-2">
                    Recently Resolved
                  </h4>
                  <div className="space-y-3 opacity-70">
                    {resolvedTasks.map((task) => (
                      <div key={task.id} className="border p-3 rounded-lg flex justify-between items-center text-sm">
                        <div>
                          <span className="font-medium">{task.task_type}</span>
                          {typeof task.payload?.message === 'string' && (
                            <p className="text-xs text-muted-foreground mt-1">{task.payload.message}</p>
                          )}
                        </div>
                        <Badge variant="outline" className="text-xs font-normal">
                          Resolved
                        </Badge>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </ScrollArea>
          </div>
        </div>
      )}
    </div>
  );
}

function ReviewTaskCard({
  task,
  actingTaskId,
  isFocused,
  onFocus,
  onAction,
  projectId,
}: {
  task: ReviewTask;
  actingTaskId: string | null;
  isFocused: boolean;
  onFocus: () => void;
  onAction: (task: ReviewTask, resolution: string, status: ReviewTaskActionStatus) => void;
  projectId: string;
}) {
  const [resolution, setResolution] = useState('');
  const isActionable = task.status === 'open' || task.status === 'in_progress';
  const isActing = actingTaskId === task.id;
  const issue = issueFromTaskPayload(task.payload || {});
  const payloadDetails = toRecord(task.payload?.details);
  const router = useRouter();

  return (
    <div
      id={`review-task-${task.id}`}
      onClick={onFocus}
      className={`rounded-lg border p-4 transition-all ${
        isFocused
          ? 'ring-2 ring-indigo-300 ring-offset-2 ring-offset-white'
          : ''
      } ${task.blocking_level === 'P0' ? 'border-red-300 bg-red-50/30' : 'border-amber-200 bg-amber-50/20'}`}
    >
      <div className="flex justify-between items-start mb-2">
        <div className="flex items-center space-x-2">
          <Badge variant={task.blocking_level === 'P0' ? 'destructive' : 'warning'} className="text-xs">
            {task.blocking_level}
          </Badge>
          <span className="font-semibold text-sm uppercase text-slate-700">{task.task_type.replace('_', ' ')}</span>
          {issue.code && (
            <Badge variant="outline" className="text-[11px]">
              {issue.code}
            </Badge>
          )}
          {issue.section_title && (
            <Badge variant="outline" className="text-[11px]">
              {issue.section_title}
            </Badge>
          )}
          {isFocused && (
            <Badge variant="outline" className="text-[11px] border-indigo-300 bg-indigo-100 text-indigo-900">
              Focused
            </Badge>
          )}
        </div>
        <Badge variant="outline" className="uppercase text-xs">
          {task.status}
        </Badge>
      </div>

      <div className="text-sm text-slate-800 my-3 leading-relaxed break-words">
        {typeof task.payload?.message === 'string'
          ? task.payload.message
          : 'Review consistency or verify content parameters.'}
      </div>

      {issue.suggested_action && (
        <p className="rounded-md border border-border/70 bg-white/70 px-3 py-2 text-xs leading-5 text-muted-foreground">
          {issue.suggested_action}
        </p>
      )}

      <IssueStructuredDetails issue={{ ...issue, details: payloadDetails }} />

      {Object.keys(task.payload || {}).length > 0 && (
        <details className="mt-4 rounded-md border border-border/70 bg-white/70">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted-foreground">
            Raw Payload
          </summary>
          <pre className="border-t border-border/70 p-3 text-xs font-mono whitespace-pre-wrap">
            {JSON.stringify(task.payload, null, 2)}
          </pre>
        </details>
      )}

      <div className="mt-4 pt-4 border-t flex flex-col space-y-3">
        <Textarea
          placeholder={isActionable ? 'Enter clarification or modification instructions...' : 'This task can no longer be updated directly.'}
          value={resolution}
          onChange={(event) => setResolution(event.target.value)}
          className="text-sm min-h-[80px]"
          disabled={!isActionable}
        />
        {issue.section_id && (
          <div className="flex justify-end">
            <Button
              size="sm"
              variant="outline"
              onClick={() => router.push(`/projects/${projectId}/editor?section_id=${issue.section_id}&review_task_id=${task.id}`)}
            >
              Open in Editor
            </Button>
          </div>
        )}
        {isActionable ? (
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              className="text-slate-600 border-slate-300 hover:bg-slate-100"
              onClick={() => onAction(task, resolution, 'rejected')}
              disabled={isActing || !resolution.trim()}
            >
              {isActing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ThumbsDown className="mr-2 h-4 w-4" />}
              Reject
            </Button>
            <Button
              size="sm"
              className="self-end bg-green-600 hover:bg-green-700 text-white"
              onClick={() => onAction(task, resolution, 'resolved')}
              disabled={isActing || !resolution.trim()}
            >
              {isActing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
              Resolve
            </Button>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Update the related draft and re-run validation to generate a fresh actionable task.
          </p>
        )}
      </div>
    </div>
  );
}
