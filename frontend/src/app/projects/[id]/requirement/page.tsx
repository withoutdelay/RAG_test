'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { AlertCircle, AlertTriangle, CheckCircle, FileText, Loader2, Save, Wand2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { ClarificationItem, RequirementCard } from '@/lib/types';
import { toast } from 'sonner';

function stringifyRequirementContent(content: Record<string, unknown> | undefined): string {
  return JSON.stringify(content ?? {}, null, 2);
}

function getClarificationLabel(item: ClarificationItem): string {
  return item.question || item.reason || item.field_name || item.item_id;
}

export default function RequirementPage() {
  const params = useParams();
  const projectId = params.id as string;
  const [card, setCard] = useState<RequirementCard | null>(null);
  const [loading, setLoading] = useState(true);
  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editContent, setEditContent] = useState('{}');

  const fetchRequirementCard = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.get(`/projects/${projectId}/requirement-card/latest`);
      const nextCard = res.data as RequirementCard;
      setCard(nextCard);
      setEditContent(stringifyRequirementContent(nextCard.content));
    } catch (error: unknown) {
      if (!isNotFoundError(error)) {
        toast.error(getApiErrorMessage(error, 'Failed to load requirement card'));
      }
      setCard(null);
      setEditContent('{}');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      void fetchRequirementCard();
    }
  }, [fetchRequirementCard, projectId]);

  const handleExtract = async () => {
    setExtracting(true);
    try {
      await api.post(`/projects/${projectId}/extract-requirement`, {});
      toast.success('Extraction completed');
      await fetchRequirementCard();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error extracting requirement'));
    } finally {
      setExtracting(false);
    }
  };

  const handleSaveContent = async () => {
    if (!card) return;

    let parsedContent: Record<string, unknown>;
    try {
      parsedContent = JSON.parse(editContent) as Record<string, unknown>;
    } catch {
      toast.error('Requirement content must be valid JSON');
      return;
    }

    setSaving(true);
    try {
      await api.patch(`/projects/${projectId}/requirement-card/${card.id}`, { content: parsedContent });
      toast.success('Requirement card saved');
      await fetchRequirementCard();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error saving requirement card'));
    } finally {
      setSaving(false);
    }
  };

  const handleResolveClarification = async (item: ClarificationItem) => {
    const resolutionText = window.prompt(`请输入处理结果：\n${getClarificationLabel(item)}`);
    if (!resolutionText) {
      return;
    }
    try {
      await api.post(`/projects/${projectId}/clarifications/${item.item_id}/resolve`, { resolution: resolutionText });
      toast.success('Clarification resolved');
      await fetchRequirementCard();
    } catch (error: unknown) {
      toast.error(getApiErrorMessage(error, 'Error resolving clarification'));
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">Requirement Card</h2>
          <p className="text-muted-foreground mt-1">
            Extract capabilities and constraints from uploaded project materials.
          </p>
        </div>
        <Button onClick={handleExtract} disabled={extracting || loading}>
          {extracting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Wand2 className="mr-2 h-4 w-4" />}
          {extracting ? 'Extracting...' : 'Extract Requirement Card'}
        </Button>
      </div>

      {loading ? (
        <div className="py-12 flex justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : !card ? (
        <Card className="flex flex-col items-center justify-center p-12 text-center border-dashed border-2">
          <FileText className="h-12 w-12 text-muted-foreground opacity-50 mb-4" />
          <h3 className="text-lg font-medium">No Requirement Card Found</h3>
          <p className="text-muted-foreground text-sm mt-1 mb-6">
            Upload project materials first, then extract the requirement card.
          </p>
          <Button onClick={handleExtract} disabled={extracting}>
            <Wand2 className="mr-2 h-4 w-4" /> Extract Requirements
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="col-span-1 lg:col-span-2 space-y-4">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between">
                <div>
                  <CardTitle>Requirement Content</CardTitle>
                  <CardDescription>Editable JSON snapshot of the current requirement card</CardDescription>
                </div>
                {card.confirmed_by_user ? (
                  <Badge variant="success" className="bg-green-100 text-green-800">
                    <CheckCircle className="mr-1 w-3 h-3" /> Confirmed
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-amber-600 border-amber-300">
                    <AlertCircle className="mr-1 w-3 h-3" /> Needs Review
                  </Badge>
                )}
              </CardHeader>
              <CardContent>
                <Textarea
                  value={editContent}
                  onChange={(event) => setEditContent(event.target.value)}
                  className="min-h-[420px] font-mono text-sm"
                  placeholder="Requirement content JSON"
                />
              </CardContent>
              <CardFooter className="justify-end border-t pt-4">
                <Button onClick={handleSaveContent} disabled={saving}>
                  {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                  Save Changes
                </Button>
              </CardFooter>
            </Card>
          </div>

          <div className="space-y-6">
            <Card className="border-red-200 bg-red-50/50">
              <CardHeader className="pb-3 text-red-900">
                <CardTitle className="flex items-center text-lg">
                  <AlertTriangle className="mr-2 h-5 w-5 text-red-600" />
                  Blocking Items ({card.blocking_items.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {card.blocking_items.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No P0 blocking issues.</p>
                ) : (
                  card.blocking_items.map((item) => (
                    <div
                      key={item.item_id}
                      className="bg-white p-3 rounded-md border border-red-200 shadow-sm text-sm"
                    >
                      <p className="font-semibold text-red-800 mb-1">{item.field_name}</p>
                      <p className="text-slate-700">{getClarificationLabel(item)}</p>
                      <div className="mt-3 flex gap-2">
                        <Button size="sm" variant="outline" className="w-full text-xs" onClick={() => void handleResolveClarification(item)}>
                          Resolve Clarification
                        </Button>
                      </div>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-lg">Missing Data ({card.missing_items.length})</CardTitle>
                <CardDescription>Fields that still need confirmation</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {card.missing_items.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No missing data detected.</p>
                ) : (
                  card.missing_items.map((item) => (
                    <div key={item.item_id} className="text-sm border-l-2 pl-3 py-1 border-amber-400">
                      <p className="font-medium">{item.field_name}</p>
                      <p className="text-muted-foreground text-xs mt-1">{getClarificationLabel(item)}</p>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
