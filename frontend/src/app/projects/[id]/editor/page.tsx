'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowRight, FileText, Loader2, PlayCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { SectionBlock } from '@/components/editor/SectionBlock';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { EvidenceBundle, EvidenceCard, JobAccepted, JobRead, SectionDraft } from '@/lib/types';
import { toast } from 'sonner';

const GENERATION_POLL_INTERVAL_MS = 3000;
const GENERATION_POLL_TIMEOUT_MS = 2 * 60 * 60 * 1000;

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

function formatGenerationProgress(job: JobRead): string {
  const progress = job.output_ref?.progress;
  const completed = progress?.completed_sections;
  const total = progress?.total_sections;
  const title = progress?.current_section_title;

  if (job.status === 'queued') {
    return 'Generation queued. Waiting for backend worker...';
  }
  if (typeof completed === 'number' && typeof total === 'number' && total > 0) {
    const current = title ? `: ${title}` : '';
    return `Generating ${Math.min(completed, total)} / ${total}${current}`;
  }
  if (job.status === 'running') {
    return 'Generation running. Waiting for the current LLM call...';
  }
  return `Generation status: ${job.status}`;
}

export default function EditorPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;
  const [sections, setSections] = useState<SectionDraft[]>([]);
  const [evidenceMap, setEvidenceMap] = useState<Record<string, EvidenceCard>>({});
  const [loading, setLoading] = useState(true);
  const [generatingAll, setGeneratingAll] = useState(false);
  const [generationProgress, setGenerationProgress] = useState<string | null>(null);

  const fetchSections = useCallback(async () => {
    try {
      setLoading(true);
      const [sectionsRes, evidenceRes] = await Promise.all([
        api.get(`/projects/${projectId}/sections`),
        api.get(`/projects/${projectId}/evidence-bundles/latest`).catch((error: unknown) => {
          if (isNotFoundError(error)) {
            return null;
          }
          throw error;
        }),
      ]);
      setSections((sectionsRes.data as SectionDraft[]) || []);
      const nextEvidenceMap: Record<string, EvidenceCard> = {};
      const evidenceBundle = evidenceRes?.data as EvidenceBundle | undefined;
      for (const item of evidenceBundle?.content?.results || []) {
        if (item?.evidence_id) {
          nextEvidenceMap[item.evidence_id] = item;
        }
      }
      setEvidenceMap(nextEvidenceMap);
    } catch (error: unknown) {
      if (!isNotFoundError(error)) {
        toast.error(getApiErrorMessage(error, 'Failed to load drafts'));
      }
      setSections([]);
      setEvidenceMap({});
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      void fetchSections();
    }
  }, [fetchSections, projectId]);

  const waitForGenerationJob = useCallback(async (nextPoll: string): Promise<JobRead> => {
    const pollPath = normalizePollPath(nextPoll);
    const deadline = Date.now() + GENERATION_POLL_TIMEOUT_MS;

    while (Date.now() < deadline) {
      const jobResponse = (await api.get(pollPath)) as { data: JobRead };
      const job = jobResponse.data;
      setGenerationProgress(formatGenerationProgress(job));

      if (job.status === 'succeeded') {
        return job;
      }
      if (job.status === 'failed') {
        const detail = job.output_ref?.error || job.error_code || 'Generation job failed';
        throw new Error(String(detail));
      }

      await sleep(GENERATION_POLL_INTERVAL_MS);
    }

    throw new Error('Generation is still running after the local polling window. Refresh this page later to load completed drafts.');
  }, []);

  const handleGenerateAll = async () => {
    setGeneratingAll(true);
    setGenerationProgress('Submitting generation job...');
    try {
      const acceptedResponse = (await api.post(`/projects/${projectId}/generate-sections`, {})) as { data: JobAccepted };
      setGenerationProgress('Generation job accepted. Waiting for progress...');
      await waitForGenerationJob(acceptedResponse.data.next_poll);
      toast.success('Section drafts generated');
      await fetchSections();
      setGenerationProgress(null);
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error generating sections'));
      setGenerationProgress(getApiErrorMessage(error, 'Generation failed. Check the backend log for details.'));
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
        <div className="flex flex-col items-end gap-2">
          <div className="flex space-x-3">
            <Button variant="outline" onClick={() => void fetchSections()} disabled={loading || generatingAll}>
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
          {generationProgress && (
            <p className="max-w-md text-right text-xs text-muted-foreground">{generationProgress}</p>
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
          <div className="max-w-[1400px] mx-auto">
            {sections.map((section) => (
              <SectionBlock
                key={section.section_id}
                section={section}
                projectId={projectId}
                evidenceMap={evidenceMap}
                onRefresh={() => void fetchSections()}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
