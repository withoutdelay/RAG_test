'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowRight, FileText, Loader2, PlayCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { SectionBlock } from '@/components/editor/SectionBlock';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { SectionDraft } from '@/lib/types';
import { toast } from 'sonner';

export default function EditorPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;
  const [sections, setSections] = useState<SectionDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [generatingAll, setGeneratingAll] = useState(false);

  const fetchSections = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.get(`/projects/${projectId}/sections`);
      setSections((res.data as SectionDraft[]) || []);
    } catch (error: unknown) {
      if (!isNotFoundError(error)) {
        console.error(error);
        toast.error(getApiErrorMessage(error, 'Failed to load drafts'));
      }
      setSections([]);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      void fetchSections();
    }
  }, [fetchSections, projectId]);

  const handleGenerateAll = async () => {
    setGeneratingAll(true);
    try {
      await api.post(`/projects/${projectId}/generate-sections`, {});
      toast.success('Section drafts generated');
      await fetchSections();
    } catch (error: unknown) {
      console.error(error);
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

  return (
    <div className="flex flex-col h-full bg-slate-50 overflow-hidden">
      <div className="p-6 pb-4 shrink-0 bg-background border-b flex justify-between items-end">
        <div>
          <h2 className="text-2xl font-bold flex items-center">
            <FileText className="mr-2 h-6 w-6" /> Section Drafts
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Review, edit, and regenerate section drafts produced from the outline and evidence bundle.
          </p>
        </div>
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
            {sections.map((section) => (
              <SectionBlock
                key={section.section_id}
                section={section}
                projectId={projectId}
                onRefresh={() => void fetchSections()}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
