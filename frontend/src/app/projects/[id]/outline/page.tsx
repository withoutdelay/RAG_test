'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowRight, FileText, Loader2, Network, Save, Wand2 } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { OutlineTree } from '@/components/outline/OutlineTree';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { Outline, OutlineNode } from '@/lib/types';
import { toast } from 'sonner';

interface OutlineResponse {
  id: string;
  version: number;
  validator_status: string;
  outline_json: {
    title?: string;
    sections?: OutlineNode[];
  };
}

function toOutlineState(payload: OutlineResponse): Outline {
  return {
    id: payload.id,
    version: payload.version,
    title: payload.outline_json?.title || 'Proposal Outline',
    sections: payload.outline_json?.sections || [],
    validator_status: payload.validator_status,
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

  const fetchOutline = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.get(`/projects/${projectId}/outlines/latest`);
      setOutline(toOutlineState(res.data as OutlineResponse));
    } catch (error: unknown) {
      if (!isNotFoundError(error)) {
        console.error(error);
        toast.error(getApiErrorMessage(error, 'Failed to load outline'));
      }
      setOutline(null);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      void fetchOutline();
    }
  }, [fetchOutline, projectId]);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      await api.post(`/projects/${projectId}/generate-outline`, {});
      toast.success('Outline generated');
      await fetchOutline();
    } catch (error: unknown) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Error generating outline'));
    } finally {
      setGenerating(false);
    }
  };

  const handleSave = async () => {
    if (!outline) return;
    setSaving(true);
    try {
      await api.patch(`/projects/${projectId}/outlines/${outline.id}`, {
        outline_json: {
          title: outline.title,
          sections: outline.sections,
        },
      });
      toast.success('Outline saved successfully');
      await fetchOutline();
    } catch (error: unknown) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Error saving outline'));
    } finally {
      setSaving(false);
    }
  };

  const handleUpdateTree = (updatedNodes: OutlineNode[]) => {
    if (!outline) return;
    setOutline({ ...outline, sections: updatedNodes });
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
              <Button variant="outline" onClick={handleSave} disabled={saving}>
                {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                Save
              </Button>
              <Button onClick={() => router.push(`/projects/${projectId}/editor`)}>
                Go to Drafts <ArrowRight className="ml-2 h-4 w-4" />
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
