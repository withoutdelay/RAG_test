'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowRight, FileText, Loader2, Network, Save, Wand2 } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { OutlineTree } from '@/components/outline/OutlineTree';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { JobAccepted, JobRead, Outline, OutlineNode } from '@/lib/types';
import { toast } from 'sonner';

const OUTLINE_POLL_INTERVAL_MS = 2000;
const OUTLINE_POLL_TIMEOUT_MS = 30 * 60 * 1000;

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

interface OutlineResponse {
  id: string;
  version: number;
  validator_status: string;
  outline_json: Record<string, unknown>;
}

function toOutlineState(payload: OutlineResponse): Outline {
  const outlineJson = payload.outline_json || {};
  const title = typeof outlineJson.title === 'string' ? outlineJson.title : 'Proposal Outline';
  const sections = Array.isArray(outlineJson.sections) ? (outlineJson.sections as OutlineNode[]) : [];

  return {
    id: payload.id,
    version: payload.version,
    title,
    sections,
    validator_status: payload.validator_status,
    outline_status:
      outlineJson.outline_status === 'candidate' || outlineJson.outline_status === 'approved'
        ? outlineJson.outline_status
        : undefined,
    generation_strategy: typeof outlineJson.generation_strategy === 'string' ? outlineJson.generation_strategy : undefined,
    approval_required: typeof outlineJson.approval_required === 'boolean' ? outlineJson.approval_required : undefined,
    approved_by_user: typeof outlineJson.approved_by_user === 'boolean' ? outlineJson.approved_by_user : undefined,
    approved_at: typeof outlineJson.approved_at === 'string' ? outlineJson.approved_at : undefined,
    reviewer_notes: typeof outlineJson.reviewer_notes === 'string' ? outlineJson.reviewer_notes : undefined,
  };
}

export default function OutlinePage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;
  const [outline, setOutline] = useState<Outline | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const [generationProgress, setGenerationProgress] = useState<string | null>(null);

  const fetchOutline = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.get(`/projects/${projectId}/outlines/latest`);
      setOutline(toOutlineState(res.data as OutlineResponse));
      setDirty(false);
    } catch (error: unknown) {
      if (!isNotFoundError(error)) {
        toast.error(getApiErrorMessage(error, 'Failed to load outline'));
      }
      setOutline(null);
      setDirty(false);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const checkOutlinePrerequisites = useCallback(async (): Promise<string[]> => {
    const missing: string[] = [];

    try {
      await api.get(`/projects/${projectId}/requirement-card/latest`);
    } catch (error: unknown) {
      if (isNotFoundError(error)) {
        missing.push('Requirement Card');
      } else {
        throw error;
      }
    }

    try {
      await api.get(`/projects/${projectId}/evidence-bundles/latest`);
    } catch (error: unknown) {
      if (isNotFoundError(error)) {
        missing.push('Evidence Bundle');
      } else {
        throw error;
      }
    }

    return missing;
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      void fetchOutline();
    }
  }, [fetchOutline, projectId]);

  useEffect(() => {
    if (!dirty) {
      return undefined;
    }

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [dirty]);

  const handleGenerate = async () => {
    setGenerating(true);
    setGenerationProgress('Submitting outline job...');
    try {
      const missing = await checkOutlinePrerequisites();
      if (missing.length > 0) {
        toast.error(`Generate Outline requires: ${missing.join(' + ')}`);
        return;
      }
      const acceptedResponse = await api.post(`/projects/${projectId}/generate-outline`, {});
      const accepted = acceptedResponse.data as JobAccepted;
      const pollPath = normalizePollPath(accepted.next_poll);
      const deadline = Date.now() + OUTLINE_POLL_TIMEOUT_MS;
      let completed = false;
      while (Date.now() < deadline) {
        const jobResponse = await api.get(pollPath);
        const job = jobResponse.data as JobRead;
        setGenerationProgress(job.status === 'running' ? 'Generating outline...' : `Outline job: ${job.status}`);
        if (job.status === 'succeeded') {
          completed = true;
          break;
        }
        if (job.status === 'failed') {
          throw new Error(String(job.output_ref?.error || job.error_code || 'Outline generation failed'));
        }
        await sleep(OUTLINE_POLL_INTERVAL_MS);
      }
      if (!completed) {
        throw new Error('Outline generation is still running after the local polling window.');
      }
      toast.success('Outline generated');
      await fetchOutline();
      setGenerationProgress(null);
    } catch (error: unknown) {
      if (isNotFoundError(error)) {
        toast.error('Generate Outline requires a ready Requirement Card and Evidence Bundle');
        return;
      }
      toast.error(getApiErrorMessage(error, 'Error generating outline'));
      setGenerationProgress(getApiErrorMessage(error, 'Outline generation failed'));
    } finally {
      setGenerating(false);
    }
  };

  const persistOutline = useCallback(
    async ({ refreshAfterSave, successMessage }: { refreshAfterSave: boolean; successMessage: string }) => {
      if (!outline) return false;

      setSaving(true);
      try {
        await api.patch(`/projects/${projectId}/outlines/${outline.id}`, {
          outline_json: outline,
        });
        toast.success(successMessage);
        setDirty(false);
        if (refreshAfterSave) {
          await fetchOutline();
        }
        return true;
      } catch (error: unknown) {
        toast.error(getApiErrorMessage(error, 'Error saving outline'));
        return false;
      } finally {
        setSaving(false);
      }
    },
    [fetchOutline, outline, projectId],
  );

  const handleSave = async () => {
    if (!outline) return;
    await persistOutline({
      refreshAfterSave: true,
      successMessage: 'Outline saved successfully',
    });
  };

  const handleApprove = async () => {
    if (!outline) return;
    if (dirty) {
      const saved = await persistOutline({ refreshAfterSave: false, successMessage: 'Outline saved before approval' });
      if (!saved) return;
    }
    
    setApproving(true);
    try {
      await api.post(`/projects/${projectId}/outlines/${outline.id}/approve`, {
        outline_json: {
          ...outline,
          outline_status: 'approved',
        },
        approved_by_user: true
      });
      toast.success('Outline approved successfully');
      await fetchOutline();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error approving outline'));
    } finally {
      setApproving(false);
    }
  };

  const handleProceedToDrafts = async () => {
    if (navigating) {
      return;
    }

    setNavigating(true);
    try {
      if (dirty) {
        const saved = await persistOutline({
          refreshAfterSave: false,
          successMessage: 'Outline saved. Moving to drafts.',
        });
        if (!saved) {
          return;
        }
      }
      router.push(`/projects/${projectId}/editor`);
    } finally {
      setNavigating(false);
    }
  };

  const handleUpdateTree = (updatedNodes: OutlineNode[]) => {
    if (!outline) return;
    setOutline({ ...outline, sections: updatedNodes });
    setDirty(true);
  };

  return (
    <div className="p-6 space-y-6 flex flex-col h-full overflow-hidden">
      <div className="flex justify-between items-center shrink-0">
        <div>
          <h2 className="text-2xl font-bold">Proposal Outline</h2>
          <p className="text-muted-foreground mt-1">
            Structure the proposal based on the gathered requirement card and evidence bundle.
          </p>
        </div>
        <div className="flex space-x-2">
          {outline ? (
            <>
              {dirty && (
                <Badge variant="warning" className="self-center">
                  Unsaved changes
                </Badge>
              )}
              <Button variant="outline" onClick={handleSave} disabled={saving || approving}>
                {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                Save
              </Button>
              {outline.outline_status === 'candidate' && (
                <Button variant="default" onClick={handleApprove} disabled={approving || saving}>
                  {approving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  Approve Outline
                </Button>
              )}
              <Button onClick={handleProceedToDrafts} disabled={saving || navigating || outline.outline_status === 'candidate'}>
                {navigating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ArrowRight className="mr-2 h-4 w-4" />}
                {outline.outline_status === 'candidate' ? 'Approve to Generate' : dirty ? 'Save & Go to Drafts' : 'Go to Drafts'}
              </Button>
            </>
          ) : (
            <Button onClick={handleGenerate} disabled={generating || loading}>
              {generating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Wand2 className="mr-2 h-4 w-4" />}
              {generating ? 'Generating...' : 'Generate Outline'}
            </Button>
          )}
        </div>
      </div>
      {generationProgress && (
        <p className="text-right text-xs text-muted-foreground">{generationProgress}</p>
      )}

      {loading ? (
        <div className="flex-1 flex justify-center items-center h-full">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : !outline ? (
        <Card className="flex flex-col items-center justify-center p-12 text-center border-dashed border-2 flex-1">
          <Network className="h-12 w-12 text-muted-foreground opacity-50 mb-4" />
          <h3 className="text-lg font-medium">No Outline Generated</h3>
          <p className="text-muted-foreground text-sm mt-1 mb-6">
            Generate an outline from the current requirement card and evidence bundle.
          </p>
          <Button onClick={handleGenerate} disabled={generating}>
            <Wand2 className="mr-2 h-4 w-4" /> Generate Outline
          </Button>
        </Card>
      ) : (
        <Card className="flex-1 flex flex-col overflow-hidden">
          <CardHeader className="shrink-0 pb-4 border-b">
            <CardTitle className="text-xl flex items-center">
              <FileText className="mr-2 h-5 w-5 text-primary" />
              {outline.title}
              {outline.outline_status && (
                <Badge variant={outline.outline_status === 'approved' ? 'default' : 'destructive'} className="ml-3">
                  {outline.outline_status.toUpperCase()}
                </Badge>
              )}
              {outline.generation_strategy && (
                <Badge variant="outline" className="ml-2">
                  Strategy: {outline.generation_strategy}
                </Badge>
              )}
            </CardTitle>
            <CardDescription>
              Review and adjust the generated structure before drafting sections.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0 flex-1 overflow-y-auto bg-slate-50/50">
            <div className="p-4 md:p-6 lg:px-8">
              <OutlineTree data={outline.sections} onUpdate={handleUpdateTree} />
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
