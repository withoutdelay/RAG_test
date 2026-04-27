'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import { Activity, Database, Link as LinkIcon, Loader2, Search } from 'lucide-react';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { CaseCandidate, EvidenceBundle, JobAccepted, JobRead } from '@/lib/types';
import { toast } from 'sonner';

function buildCaseCandidateBreakdownText(candidate: CaseCandidate): string {
  const breakdown = candidate.score_breakdown ?? {};
  const parts: string[] = [];
  if (typeof breakdown.base === 'number' && breakdown.base > 0) {
    parts.push(`base ${(breakdown.base * 100).toFixed(0)}%`);
  }
  if (typeof breakdown.hybrid_rrf === 'number' && breakdown.hybrid_rrf > 0) {
    parts.push(`rrf +${(breakdown.hybrid_rrf * 100).toFixed(0)}%`);
  }
  if (typeof breakdown.hybrid_rerank === 'number' && breakdown.hybrid_rerank > 0) {
    parts.push(`rerank +${(breakdown.hybrid_rerank * 100).toFixed(0)}%`);
  }
  return parts.join(' / ');
}

function buildEvidenceBreakdownText(evidence: EvidenceBundle['content']['results'][number]): string {
  const breakdown = evidence.retrieval_score_breakdown ?? {};
  const parts: string[] = [];
  if (typeof breakdown.semantic === 'number' && breakdown.semantic > 0) {
    parts.push(`semantic ${(breakdown.semantic * 100).toFixed(0)}%`);
  }
  if (typeof breakdown.sparse === 'number' && breakdown.sparse > 0) {
    parts.push(`sparse ${(breakdown.sparse * 100).toFixed(0)}%`);
  }
  if (typeof breakdown.rerank === 'number' && breakdown.rerank > 0) {
    parts.push(`rerank ${(breakdown.rerank * 100).toFixed(0)}%`);
  }
  if (typeof breakdown.final === 'number' && breakdown.final > 0) {
    parts.push(`final ${(breakdown.final * 100).toFixed(0)}%`);
  }
  return parts.join(' / ');
}

const RETRIEVAL_JOB_POLL_INTERVAL_MS = 2000;
const RETRIEVAL_JOB_POLL_TIMEOUT_MS = 10 * 60 * 1000;

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

function formatRetrievalProgress(job: JobRead): string {
  const stage = job.output_ref?.progress?.stage;
  if (job.status === 'queued') {
    return 'Evidence retrieval queued. Waiting for backend worker...';
  }
  if (stage === 'resolving_requirement') {
    return 'Resolving requirement card...';
  }
  if (stage === 'retrieving') {
    return 'Searching historical evidence...';
  }
  if (job.status === 'running') {
    return 'Evidence retrieval running...';
  }
  return `Evidence retrieval status: ${job.status}`;
}

export default function EvidencePage() {
  const params = useParams();
  const projectId = params.id as string;
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [retrieving, setRetrieving] = useState(false);
  const [retrievalProgress, setRetrievalProgress] = useState<string | null>(null);

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

  const waitForRetrievalJob = useCallback(async (nextPoll: string): Promise<JobRead> => {
    const pollPath = normalizePollPath(nextPoll);
    const deadline = Date.now() + RETRIEVAL_JOB_POLL_TIMEOUT_MS;

    while (Date.now() < deadline) {
      const jobResponse = (await api.get(pollPath)) as { data: JobRead };
      const job = jobResponse.data;
      setRetrievalProgress(formatRetrievalProgress(job));

      if (job.status === 'succeeded') {
        return job;
      }
      if (job.status === 'failed') {
        const detail = job.output_ref?.error || job.error_code || 'Evidence retrieval job failed';
        throw new Error(String(detail));
      }

      await sleep(RETRIEVAL_JOB_POLL_INTERVAL_MS);
    }

    throw new Error('Evidence retrieval is still running after the local polling window. Refresh this page later.');
  }, []);

  const handleRetrieve = async () => {
    setRetrieving(true);
    setRetrievalProgress('Submitting evidence retrieval job...');
    try {
      const res = await api.post(`/projects/${projectId}/retrieve-evidence`, {});
      const accepted = res.data as JobAccepted;
      setRetrievalProgress(`Evidence retrieval queued: ${accepted.job_id}`);
      await waitForRetrievalJob(accepted.next_poll);
      toast.success('Evidence retrieval completed');
      await fetchEvidenceBundle();
    } catch (error: unknown) {
      if (isNotFoundError(error)) {
        toast.error('Retrieve Evidence requires a ready Requirement Card');
        return;
      }
      toast.error(getApiErrorMessage(error, 'Error retrieving evidence'));
      setRetrievalProgress(getApiErrorMessage(error, 'Evidence retrieval failed'));
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

      {retrievalProgress && (
        <Alert>
          {retrieving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
          <AlertDescription>{retrievalProgress}</AlertDescription>
        </Alert>
      )}

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
            {retrieving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Database className="mr-2 h-4 w-4" />}
            {retrieving ? 'Retrieving...' : 'Start Retrieval'}
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
                      {buildCaseCandidateBreakdownText(candidate) && (
                        <p className="mt-2 text-[11px] text-muted-foreground">
                          {buildCaseCandidateBreakdownText(candidate)}
                        </p>
                      )}
                      {candidate.reason && (
                        <p className="mt-2 text-xs text-muted-foreground">{candidate.reason}</p>
                      )}
                      {candidate.reason_trace && candidate.reason_trace.length > 0 && (
                        <p className="mt-2 text-[11px] text-muted-foreground line-clamp-3">
                          {candidate.reason_trace.slice(0, 3).join(' / ')}
                        </p>
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
                      key={`${evidence.evidence_id || 'evidence'}-${index}`}
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
                        {buildEvidenceBreakdownText(evidence) && (
                          <p className="mt-3 text-[11px] text-muted-foreground">
                            {buildEvidenceBreakdownText(evidence)}
                          </p>
                        )}
                        {evidence.reason_trace && evidence.reason_trace.length > 0 && (
                          <p className="mt-2 text-[11px] text-muted-foreground line-clamp-3">
                            {evidence.reason_trace.slice(0, 4).join(' / ')}
                          </p>
                        )}
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
