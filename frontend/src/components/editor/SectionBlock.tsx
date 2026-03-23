'use client';

import { useState } from 'react';
import { AlertCircle, Loader2, RotateCcw, Save } from 'lucide-react';
import Markdown from 'react-markdown';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import api, { getApiErrorMessage } from '@/lib/api';
import { Citation, SectionDraft } from '@/lib/types';
import { toast } from 'sonner';

function getStatusVariant(status: SectionDraft['status']): 'default' | 'secondary' | 'warning' | 'success' | 'destructive' {
  if (status === 'approved') return 'success';
  if (status === 'review_required') return 'warning';
  if (status === 'rejected') return 'destructive';
  if (status === 'edited') return 'secondary';
  return 'default';
}

function renderCitationLabel(citation: Citation): string {
  const headingPath = citation.heading_path?.join(' > ');
  return [citation.source_title, headingPath].filter(Boolean).join(' / ');
}

export function SectionBlock({
  section,
  projectId,
  onRefresh,
}: {
  section: SectionDraft;
  projectId: string;
  onRefresh: () => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [content, setContent] = useState(section.content_md || '');
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.patch(`/projects/${projectId}/sections/${section.section_id}`, { content_md: content });
      toast.success('Section saved');
      setIsEditing(false);
      onRefresh();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error saving section'));
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerate = async () => {
    setRegenerating(true);
    try {
      await api.post(`/projects/${projectId}/sections/${section.section_id}/regenerate`, {});
      toast.success('Section regenerated');
      onRefresh();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error regenerating section'));
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <Card className="mb-6 border-l-4 border-l-slate-300">
      <div className="flex items-center justify-between p-4 border-b bg-muted/30">
        <div className="flex items-center gap-3">
          <h3 className="font-semibold text-lg">{section.title}</h3>
          <Badge variant={getStatusVariant(section.status)}>{section.status}</Badge>
        </div>
        <div className="flex space-x-2">
          {!isEditing && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setIsEditing(true);
                setContent(section.content_md || '');
              }}
            >
              Edit
            </Button>
          )}
          {isEditing && (
            <>
              <Button size="sm" variant="outline" onClick={() => setIsEditing(false)}>
                Cancel
              </Button>
              <Button size="sm" onClick={handleSave} disabled={saving}>
                {saving ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}
                Save
              </Button>
            </>
          )}
          <Button size="sm" variant="secondary" onClick={handleRegenerate} disabled={regenerating}>
            {regenerating ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <RotateCcw className="w-4 h-4 mr-1" />}
            Regenerate
          </Button>
        </div>
      </div>

      <CardContent className="p-0 flex flex-col md:flex-row">
        <div className={`p-4 md:p-6 flex-1 ${isEditing ? 'bg-slate-50' : ''}`}>
          {isEditing ? (
            <Textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              className="min-h-[300px] font-mono whitespace-pre-wrap"
            />
          ) : section.content_md ? (
            <div className="prose prose-sm max-w-none dark:prose-invert">
              <Markdown>{section.content_md}</Markdown>
            </div>
          ) : (
            <div className="py-8 text-center text-muted-foreground italic">
              Empty section. Click regenerate to draft from the current outline and evidence.
            </div>
          )}
        </div>
        <div className="w-full md:w-64 border-t md:border-t-0 md:border-l bg-slate-50/50 p-4 shrink-0 space-y-6">
          <div>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Citations</h4>
            {section.citation_refs.length > 0 ? (
              <ul className="space-y-2">
                {section.citation_refs.map((citation, index) => (
                  <li key={citation.evidence_id || index} className="text-xs bg-white border p-2 rounded shadow-sm">
                    <span className="font-medium text-blue-600 block line-clamp-1">
                      {citation.source_title || citation.evidence_id || 'Citation'}
                    </span>
                    <span className="text-muted-foreground line-clamp-2 mt-0.5 block">
                      {renderCitationLabel(citation)}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">No citations</p>
            )}
          </div>
          <div>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Assumptions</h4>
            {section.assumptions.length > 0 ? (
              <ul className="space-y-2">
                {section.assumptions.map((assumption, index) => (
                  <li
                    key={index}
                    className="text-xs bg-amber-50 border border-amber-200 p-2 rounded text-amber-900 flex items-start"
                  >
                    <AlertCircle className="w-3 h-3 mr-1.5 mt-0.5 shrink-0 text-amber-600" />
                    <span>{typeof assumption === 'string' ? assumption : JSON.stringify(assumption)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">No specific assumptions</p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
