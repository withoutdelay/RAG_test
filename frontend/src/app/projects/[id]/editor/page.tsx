'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { AlertTriangle, ArrowRight, CheckCircle2, FileText, Loader2, PlayCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Badge } from '@/components/ui/badge';
import { SectionBlock } from '@/components/editor/SectionBlock';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { buildReviewResolutionPayload, issueFromTaskPayload, ReviewTaskActionStatus } from '@/lib/review-tasks';
import { EvidenceBundle, EvidenceCard, ReviewTask, SectionDraft } from '@/lib/types';
import { toast } from 'sonner';

function getOrderedActionableReviewTasks(tasks: ReviewTask[]): ReviewTask[] {
  return [
    ...tasks.filter((task) => task.status === 'open' || task.status === 'in_progress'),
    ...tasks.filter((task) => task.status === 'rejected'),
  ];
}

function pickNextReviewTask(
  tasks: ReviewTask[],
  options: { excludedTaskIds?: string[]; preferredSectionId?: string } = {},
): ReviewTask | null {
  const actionableTasks = getOrderedActionableReviewTasks(tasks);
  if (actionableTasks.length === 0) {
    return null;
  }

  const excludedTaskIds = new Set((options.excludedTaskIds || []).filter(Boolean));
  const preferredSectionId = String(options.preferredSectionId || '').trim();
  if (preferredSectionId) {
    const sameSectionTask = actionableTasks.find((task) => {
      if (excludedTaskIds.has(task.id)) {
        return false;
      }
      return String(issueFromTaskPayload(task.payload || {}).section_id || '').trim() === preferredSectionId;
    });
    if (sameSectionTask) {
      return sameSectionTask;
    }
  }

  return actionableTasks.find((task) => !excludedTaskIds.has(task.id)) || actionableTasks[0] || null;
}

function buildExportReadyUrl(projectId: string, source: 'editor' | 'validation'): string {
  return `/projects/${projectId}/export?review_completed=1&source=${source}`;
}

export default function EditorPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const projectId = params.id as string;
  const targetSectionId = searchParams.get('section_id')?.trim() || '';
  const focusedReviewTaskId = searchParams.get('review_task_id')?.trim() || '';
  const [sections, setSections] = useState<SectionDraft[]>([]);
  const [reviewTasks, setReviewTasks] = useState<ReviewTask[]>([]);
  const [evidenceMap, setEvidenceMap] = useState<Record<string, EvidenceCard>>({});
  const [loading, setLoading] = useState(true);
  const [generatingAll, setGeneratingAll] = useState(false);
  const [actingTaskId, setActingTaskId] = useState<string | null>(null);

  const loadEditorData = useCallback(async ({ showLoading = true }: { showLoading?: boolean } = {}) => {
    try {
      if (showLoading) {
        setLoading(true);
      }
      const [sectionsRes, evidenceRes, reviewTasksRes] = await Promise.all([
        api.get(`/projects/${projectId}/sections`),
        api.get(`/projects/${projectId}/evidence-bundles/latest`).catch((error: unknown) => {
          if (isNotFoundError(error)) {
            return null;
          }
          throw error;
        }),
        api.get(`/projects/${projectId}/review-tasks`),
      ]);
      const nextSections = (sectionsRes.data as SectionDraft[]) || [];
      const nextReviewTasks = Array.isArray(reviewTasksRes.data) ? (reviewTasksRes.data as ReviewTask[]) : [];
      const nextEvidenceMap: Record<string, EvidenceCard> = {};
      const evidenceBundle = evidenceRes?.data as EvidenceBundle | undefined;
      for (const item of evidenceBundle?.content?.results || []) {
        if (item?.evidence_id) {
          nextEvidenceMap[item.evidence_id] = item;
        }
      }
      setSections(nextSections);
      setReviewTasks(nextReviewTasks);
      setEvidenceMap(nextEvidenceMap);
      return {
        sections: nextSections,
        reviewTasks: nextReviewTasks,
      };
    } catch (error: unknown) {
      if (!isNotFoundError(error)) {
        toast.error(getApiErrorMessage(error, 'Failed to load drafts'));
      }
      setSections([]);
      setReviewTasks([]);
      setEvidenceMap({});
      return {
        sections: [],
        reviewTasks: [],
      };
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }, [projectId]);

  const fetchEditorData = useCallback(async () => {
    await loadEditorData({ showLoading: true });
  }, [loadEditorData]);

  const updateFocusedTask = useCallback(
    (task: ReviewTask | null, fallbackSectionId?: string) => {
      const nextParams = new URLSearchParams(searchParams.toString());
      if (task) {
        const issue = issueFromTaskPayload(task.payload || {});
        const sectionId = String(issue.section_id || '').trim();
        if (sectionId) {
          nextParams.set('section_id', sectionId);
        } else {
          nextParams.delete('section_id');
        }
        nextParams.set('review_task_id', task.id);
      } else {
        nextParams.delete('review_task_id');
        if (fallbackSectionId) {
          nextParams.set('section_id', fallbackSectionId);
        } else {
          nextParams.delete('section_id');
        }
      }

      const query = nextParams.toString();
      router.replace(query ? `/projects/${projectId}/editor?${query}` : `/projects/${projectId}/editor`);
    },
    [projectId, router, searchParams],
  );

  const rerunValidationAndAdvance = useCallback(
    async ({
      excludedTaskIds,
      preferredSectionId,
    }: {
      excludedTaskIds: string[];
      preferredSectionId?: string;
    }) => {
      try {
        await api.post(`/projects/${projectId}/validate`, {});
      } catch (error: unknown) {
        await loadEditorData({ showLoading: false });
        toast.error(getApiErrorMessage(error, 'Review task resolved, but validation rerun failed'));
        return;
      }

      const nextData = await loadEditorData({ showLoading: false });
      const nextTask = pickNextReviewTask(nextData.reviewTasks, {
        excludedTaskIds,
        preferredSectionId,
      });
      if (nextTask) {
        updateFocusedTask(nextTask, preferredSectionId);
        const nextIssue = issueFromTaskPayload(nextTask.payload || {});
        toast.success(
          `Validation rerun complete. Next task: ${nextIssue.code || nextIssue.section_title || nextTask.task_type}`,
        );
      } else {
        try {
          const reportRes = await api.get(`/projects/${projectId}/validation/latest`);
          const nextReport = reportRes.data as { status?: string } | null;
          if (nextReport?.status === 'passed') {
            toast.success('Validation rerun complete. Export is ready.');
            router.push(buildExportReadyUrl(projectId, 'editor'));
            return;
          }
        } catch (error: unknown) {
          toast.error(getApiErrorMessage(error, 'Blocking tasks cleared, but export readiness check failed'));
          return;
        }
        updateFocusedTask(null, preferredSectionId);
        toast.success('Validation rerun complete. No blocking tasks remain.');
      }
    },
    [loadEditorData, projectId, router, updateFocusedTask],
  );

  useEffect(() => {
    if (projectId) {
      void fetchEditorData();
    }
  }, [fetchEditorData, projectId]);

  useEffect(() => {
    if (!targetSectionId || sections.length === 0) return;
    const element = document.getElementById(`section-${targetSectionId}`);
    if (!element) return;
    window.setTimeout(() => {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 120);
  }, [sections, targetSectionId]);

  const handleReviewTaskAction = async (
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
      const preferredSectionId = String(issueFromTaskPayload(task.payload || {}).section_id || '').trim() || targetSectionId;
      if (status === 'resolved') {
        await rerunValidationAndAdvance({
          excludedTaskIds: [task.id],
          preferredSectionId,
        });
      } else {
        toast.success('Review task rejected');
        await loadEditorData({ showLoading: false });
      }
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, `Error ${status === 'resolved' ? 'resolving' : 'rejecting'} task`));
    } finally {
      setActingTaskId(null);
    }
  };

  const handleBatchReviewTaskAction = async (
    tasks: ReviewTask[],
    resolution: string,
    status: ReviewTaskActionStatus,
  ) => {
    const actionableTasks = tasks.filter((task) => ['open', 'in_progress', 'rejected'].includes(task.status));
    if (actionableTasks.length === 0) {
      return;
    }
    try {
      for (const task of actionableTasks) {
        await api.post(`/projects/${projectId}/review-tasks/${task.id}/resolve`, {
          resolution: buildReviewResolutionPayload(task, resolution, status),
          status,
        });
      }
      const preferredSectionId =
        String(issueFromTaskPayload(actionableTasks[0]?.payload || {}).section_id || '').trim() || targetSectionId;
      if (status === 'resolved') {
        await rerunValidationAndAdvance({
          excludedTaskIds: actionableTasks.map((task) => task.id),
          preferredSectionId,
        });
      } else {
        toast.success(`${actionableTasks.length} review tasks rejected`);
        await loadEditorData({ showLoading: false });
      }
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, `Error ${status === 'resolved' ? 'resolving' : 'rejecting'} section tasks`));
    }
  };

  const handleGenerateAll = async () => {
    setGeneratingAll(true);
    try {
      await api.post(`/projects/${projectId}/generate-sections`, {});
      toast.success('Section drafts generated');
      await fetchEditorData();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error generating sections'));
    } finally {
      setGeneratingAll(false);
    }
  };

  const readyCount = useMemo(
    () => sections.filter((section) => Boolean(section.content_md?.trim())).length,
    [sections],
  );
  const readyProgress = sections.length > 0 ? (readyCount / sections.length) * 100 : 0;
  const canProceedToValidation = sections.length > 0 && readyCount === sections.length;
  const sectionTaskMap = useMemo(() => {
    const map: Record<string, ReviewTask[]> = {};
    for (const task of reviewTasks) {
      if (task.status === 'resolved') {
        continue;
      }
      const sectionId = String(task.payload?.section_id || '').trim();
      if (!sectionId) {
        continue;
      }
      map[sectionId] = [...(map[sectionId] || []), task];
    }
    return map;
  }, [reviewTasks]);
  const focusedReviewTask = useMemo(
    () => reviewTasks.find((task) => task.id === focusedReviewTaskId) || null,
    [focusedReviewTaskId, reviewTasks],
  );
  const focusedIssue = focusedReviewTask ? issueFromTaskPayload(focusedReviewTask.payload || {}) : null;

  return (
    <div className="flex flex-col h-full bg-slate-50 overflow-hidden">
      <div className="p-6 pb-4 shrink-0 bg-background border-b flex justify-between items-end">
        <div>
          <h2 className="text-2xl font-bold flex items-center">
            <FileText className="mr-2 h-6 w-6" /> Section Drafts
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Review the customer-facing draft, steer it with citations, and refine it without diving into raw Markdown by default.
          </p>
        </div>
        <div className="flex space-x-3">
          <Button variant="outline" onClick={() => void fetchEditorData()} disabled={loading || generatingAll}>
            Refresh
          </Button>
          <Button onClick={handleGenerateAll} disabled={generatingAll || loading}>
            {generatingAll ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlayCircle className="mr-2 h-4 w-4" />}
            {generatingAll ? 'Generating...' : 'Generate All Drafts'}
          </Button>
          {canProceedToValidation && (
            <Button onClick={() => router.push(`/projects/${projectId}/validation`)} className="bg-green-600 hover:bg-green-700">
              Proceed to Validation <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {sections.length > 0 && (
        <div className="px-6 py-3 bg-white border-b shrink-0 flex items-center justify-between text-sm">
          <div className="flex items-center space-x-4 flex-1">
            <span className="font-medium text-slate-600 text-xs uppercase tracking-wider">Draft Coverage</span>
            <Progress value={readyProgress} className="h-2 flex-1 max-w-md" />
            <span className="font-mono">{readyCount} / {sections.length}</span>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : sections.length === 0 ? (
          <div className="text-center py-20 bg-white border border-dashed rounded-lg">
            <FileText className="h-12 w-12 mx-auto text-muted-foreground opacity-50 mb-4" />
            <h3 className="text-lg font-medium">No Drafts Found</h3>
            <p className="text-muted-foreground text-sm mt-1 mb-6">
              Generate section drafts after the outline is ready.
            </p>
            <Button onClick={handleGenerateAll} disabled={generatingAll}>
              <PlayCircle className="mr-2 h-4 w-4" /> Generate All Drafts
            </Button>
          </div>
        ) : (
          <div className="max-w-5xl mx-auto">
            {focusedReviewTask && (
              <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                <div className="flex flex-wrap items-center gap-2">
                  <AlertTriangle className="h-4 w-4" />
                  <span className="font-semibold">Focused review task</span>
                  <Badge variant={focusedReviewTask.blocking_level === 'P0' ? 'destructive' : 'warning'}>
                    {focusedReviewTask.blocking_level}
                  </Badge>
                  {focusedIssue?.code ? <Badge variant="outline">{focusedIssue.code}</Badge> : null}
                  {focusedIssue?.section_title ? <Badge variant="outline">{focusedIssue.section_title}</Badge> : null}
                </div>
                <p className="mt-2 leading-6">
                  {focusedIssue?.message || 'Review this section issue in context and resolve it after editing.'}
                </p>
                {focusedIssue?.suggested_action ? (
                  <p className="mt-1 text-xs leading-5 text-amber-900/80">{focusedIssue.suggested_action}</p>
                ) : null}
                {focusedReviewTask.status === 'rejected' ? (
                  <div className="mt-2 inline-flex items-center gap-2 rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs text-red-700">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    This task was previously rejected. Update the draft, then reject or resolve again as needed.
                  </div>
                ) : null}
              </div>
            )}
            {targetSectionId && (
              <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
                Focused review target: section {targetSectionId}
              </div>
            )}
            {sections.map((section) => (
              <div
                key={section.section_id}
                id={`section-${section.section_id}`}
                className={targetSectionId === section.section_id ? 'scroll-mt-6 rounded-2xl ring-2 ring-emerald-300 ring-offset-2 ring-offset-slate-50' : 'scroll-mt-6'}
              >
                <SectionBlock
                  section={section}
                  projectId={projectId}
                  evidenceMap={evidenceMap}
                  reviewTasks={sectionTaskMap[section.section_id] || []}
                  focusedReviewTaskId={focusedReviewTaskId}
                  actingTaskId={actingTaskId}
                  onReviewTaskAction={handleReviewTaskAction}
                  onBatchReviewTaskAction={handleBatchReviewTaskAction}
                  onRefresh={() => void fetchEditorData()}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
