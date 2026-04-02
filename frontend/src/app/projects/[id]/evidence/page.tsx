'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import { Activity, Database, Link as LinkIcon, Loader2, Search } from 'lucide-react';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { CaseCandidate, EvidenceBundle } from '@/lib/types';
import { toast } from 'sonner';

export default function EvidencePage() {
  const params = useParams();
  const projectId = params.id as string;
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [retrieving, setRetrieving] = useState(false);

  const fetchEvidenceBundle = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.get(`/projects/${projectId}/evidence-bundles/latest`);
      setBundle(res.data as EvidenceBundle);
    } catch (error: unknown) {
      if (!isNotFoundError(error)) {
        toast.error(getApiErrorMessage(error, 'Failed to load evidence bundle'));
      }
      setBundle(null);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      void fetchEvidenceBundle();
    }
  }, [fetchEvidenceBundle, projectId]);

  const handleRetrieve = async () => {
    setRetrieving(true);
    try {
      await api.post(`/projects/${projectId}/retrieve-evidence`, {});
      toast.success('Evidence retrieval completed');
      await fetchEvidenceBundle();
    } catch (error: unknown) {
      if (isNotFoundError(error)) {
        toast.error('Retrieve Evidence requires a ready Requirement Card');
        return;
      }
      toast.error(getApiErrorMessage(error, 'Error retrieving evidence'));
    } finally {
      setRetrieving(false);
    }
  };

  const evidenceItems = useMemo(() => bundle?.content?.results ?? [], [bundle]);
  const caseCandidates = useMemo<CaseCandidate[]>(() => bundle?.content?.case_candidates ?? [], [bundle]);
  const queryText = bundle?.content?.query ?? 'Auto-generated context from requirements';
  const retrievalStrategy = bundle?.content?.retrieval_strategy ?? 'unknown';

  return (
    <div className="p-6 space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">Evidence Bundle</h2>
          <p className="text-muted-foreground mt-1">
            Historical references and reusable assets matched to the current requirement card.
          </p>
        </div>
        <Button onClick={handleRetrieve} disabled={retrieving || loading}>
          {retrieving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Database className="mr-2 h-4 w-4" />}
          {retrieving ? 'Retrieving...' : 'Retrieve Evidence'}
        </Button>
      </div>

      {loading ? (
        <div className="py-12 flex justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : !bundle ? (
        <Card className="flex flex-col items-center justify-center p-12 text-center border-dashed border-2">
          <Search className="h-12 w-12 text-muted-foreground opacity-50 mb-4" />
          <h3 className="text-lg font-medium">No Evidence Gathered</h3>
          <p className="text-muted-foreground text-sm mt-1 mb-6">
            Generate a requirement card first, then search for supporting evidence.
          </p>
          <Button onClick={handleRetrieve} disabled={retrieving}>
            <Database className="mr-2 h-4 w-4" /> Start Retrieval
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 h-[calc(100vh-280px)]">
          <div className="lg:col-span-1 space-y-6">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-lg">Retrieval Context</CardTitle>
                <CardDescription>Search parameters and quality score</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div>
                    <span className="text-sm font-medium text-muted-foreground block mb-2">Quality Score</span>
                    <div className="flex items-center space-x-3">
                      <Progress value={bundle.quality_score * 100} className="h-2 flex-1" />
                      <span className="text-sm font-bold w-12 text-right">
                        {(bundle.quality_score * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                  <div>
                    <span className="text-sm font-medium text-muted-foreground block mb-1">Generated Query</span>
                    <div className="bg-muted p-2 rounded text-xs leading-relaxed font-mono break-words">
                      {queryText}
                    </div>
                  </div>
                  <div className="pt-2 border-t flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Results Found</span>
                    <Badge variant="secondary">{evidenceItems.length}</Badge>
                  </div>
                  <div className="pt-2 border-t flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Matched Samples</span>
                    <Badge variant="secondary">{caseCandidates.length}</Badge>
                  </div>
                  <div className="pt-2 border-t flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Strategy</span>
                    <Badge variant="outline">{retrievalStrategy}</Badge>
                  </div>
                </div>
              </CardContent>
            </Card>

            {caseCandidates.length > 0 && (
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-lg">Matched Sample Files</CardTitle>
                  <CardDescription>Case-library matches selected before vector retrieval</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {caseCandidates.map((candidate) => (
                    <div key={candidate.sample_id} className="rounded-md border p-3 text-sm">
                      <div className="flex items-start justify-between gap-3">
                        <div className="font-medium leading-snug">{candidate.file_name}</div>
                        <Badge variant="outline">{(candidate.score * 100).toFixed(0)}%</Badge>
                      </div>
                      {candidate.reason && (
                        <p className="mt-2 text-xs text-muted-foreground">{candidate.reason}</p>
                      )}
                      {candidate.top_level_titles && candidate.top_level_titles.length > 0 && (
                        <p className="mt-2 text-xs text-muted-foreground line-clamp-3">
                          {candidate.top_level_titles.slice(0, 4).join(' / ')}
                        </p>
                      )}
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}
          </div>

          <div className="lg:col-span-3">
            <Card className="h-full flex flex-col">
              <CardHeader className="pb-3 border-b border-muted">
                <CardTitle className="text-xl flex items-center">
                  <Activity className="mr-2 h-5 w-5 text-primary" />
                  Relevant Evidence
                </CardTitle>
              </CardHeader>
              <ScrollArea className="flex-1 p-4">
                <Accordion className="w-full">
                  {evidenceItems.map((evidence, index) => (
                    <AccordionItem
                      key={evidence.evidence_id || index}
                      value={`item-${index}`}
                      className="border bg-card mb-4 rounded-lg px-4 shadow-sm"
                    >
                      <AccordionTrigger className="hover:no-underline py-4">
                        <div className="flex flex-col items-start text-left w-full mr-4 space-y-2">
                          <div className="flex items-center justify-between w-full">
                            <span className="font-semibold text-base line-clamp-1">{evidence.source_title}</span>
                            <Badge variant="outline" className="ml-2 bg-background whitespace-nowrap">
                              Score: {(evidence.relevance_score * 100).toFixed(0)}%
                            </Badge>
                          </div>
                          <div className="flex items-center text-xs text-muted-foreground">
                            <LinkIcon className="h-3 w-3 mr-1" />
                            <span className="line-clamp-1 font-mono">
                              {evidence.heading_path.join(' > ')}
                            </span>
                          </div>
                        </div>
                      </AccordionTrigger>
                      <AccordionContent className="pt-2 pb-4 border-t text-sm leading-relaxed text-slate-700">
                        {evidence.summary}
                      </AccordionContent>
                    </AccordionItem>
                  ))}
                </Accordion>
                {evidenceItems.length === 0 && (
                  <div className="text-center py-10 text-muted-foreground">
                    {caseCandidates.length > 0
                      ? 'Matched sample files were found, but no reusable section blocks passed the current vector retrieval filters.'
                      : 'No relevant historical data found.'}
                  </div>
                )}
              </ScrollArea>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
