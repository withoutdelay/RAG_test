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
import { ReviewTask, ValidationReport } from '@/lib/types';

type ReviewTaskActionStatus = 'resolved' | 'rejected';

export default function ValidationPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [reviewTasks, setReviewTasks] = useState<ReviewTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [validating, setValidating] = useState(false);
  const [actingTaskId, setActingTaskId] = useState<string | null>(null);

  const fetchValidation = useCallback(async () => {
    try {
      const res = await api.get(`/projects/${projectId}/validation/latest`);
      setReport(res.data);
    } catch (error) {
      if (!isNotFoundError(error)) {
        console.error(error);
        toast.error(getApiErrorMessage(error, 'Failed to load validation report'));
      }
      setReport(null);
    }
  }, [projectId]);

  const fetchReviewTasks = useCallback(async () => {
    try {
      const res = await api.get(`/projects/${projectId}/review-tasks`);
      setReviewTasks(Array.isArray(res.data) ? res.data : []);
    } catch (error) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Failed to load review tasks'));
      setReviewTasks([]);
    }
  }, [projectId]);

  const refreshValidationData = useCallback(async () => {
    try {
      setLoading(true);
      await Promise.all([fetchValidation(), fetchReviewTasks()]);
    } finally {
      setLoading(false);
    }
  }, [fetchReviewTasks, fetchValidation]);

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
      await refreshValidationData();
    } catch (error) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Error running validation'));
    } finally {
      setValidating(false);
    }
  };

  const handleTaskAction = async (
    taskId: string,
    resolution: string,
    status: ReviewTaskActionStatus,
  ) => {
    setActingTaskId(taskId);
    try {
      await api.post(`/projects/${projectId}/review-tasks/${taskId}/resolve`, {
        resolution,
        status,
      });
      toast.success(status === 'resolved' ? 'Review task resolved' : 'Review task rejected');
      await refreshValidationData();
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
            <Button onClick={() => router.push(`/projects/${projectId}/export`)} className="bg-green-600 hover:bg-green-700">
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
                <ul className="space-y-2">
                  {report.errors?.map((err, index) => (
                    <li key={`error-${index}`} className="text-xs bg-red-50 text-red-800 p-2 rounded border border-red-100">
                      <strong>[{err.code}]</strong> {err.message}
                    </li>
                  ))}
                  {report.warnings?.map((warn, index) => (
                    <li key={`warn-${index}`} className="text-xs bg-amber-50 text-amber-800 p-2 rounded border border-amber-100">
                      <strong>[{warn.code}]</strong> {warn.message}
                    </li>
                  ))}
                  {!report.errors?.length && !report.warnings?.length && (
                    <li className="text-xs text-muted-foreground text-center py-4">No system warnings.</li>
                  )}
                </ul>
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
                          onAction={handleTaskAction}
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
                          onAction={handleTaskAction}
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
  onAction,
}: {
  task: ReviewTask;
  actingTaskId: string | null;
  onAction: (taskId: string, resolution: string, status: ReviewTaskActionStatus) => void;
}) {
  const [resolution, setResolution] = useState('');
  const isActionable = task.status === 'open' || task.status === 'in_progress';
  const isActing = actingTaskId === task.id;

  return (
    <div className={`p-4 border rounded-lg ${task.blocking_level === 'P0' ? 'border-red-300 bg-red-50/30' : 'border-amber-200 bg-amber-50/20'}`}>
      <div className="flex justify-between items-start mb-2">
        <div className="flex items-center space-x-2">
          <Badge variant={task.blocking_level === 'P0' ? 'destructive' : 'warning'} className="text-xs">
            {task.blocking_level}
          </Badge>
          <span className="font-semibold text-sm uppercase text-slate-700">{task.task_type.replace('_', ' ')}</span>
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

      {Object.keys(task.payload || {}).length > 0 && (
        <pre className="text-xs bg-black/5 p-2 rounded font-mono mb-4 whitespace-pre-wrap">
          {JSON.stringify(task.payload, null, 2)}
        </pre>
      )}

      <div className="mt-4 pt-4 border-t flex flex-col space-y-3">
        <Textarea
          placeholder={isActionable ? 'Enter clarification or modification instructions...' : 'This task can no longer be updated directly.'}
          value={resolution}
          onChange={(event) => setResolution(event.target.value)}
          className="text-sm min-h-[80px]"
          disabled={!isActionable}
        />
        {isActionable ? (
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              className="text-red-700 border-red-300 hover:bg-red-50"
              onClick={() => onAction(task.id, resolution, 'rejected')}
              disabled={isActing || !resolution.trim()}
            >
              {isActing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ThumbsDown className="mr-2 h-4 w-4" />}
              Reject
            </Button>
            <Button
              size="sm"
              className="self-end"
              onClick={() => onAction(task.id, resolution, 'resolved')}
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
