'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { UploadCloud, File, CheckCircle2, AlertCircle, XCircle, RefreshCw, Loader2 } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { toast } from 'sonner';
import api, { getApiErrorMessage } from '@/lib/api';
import { Document, HistoryLibraryRefreshStatus } from '@/lib/types';
import { format } from 'date-fns';

export default function DocumentsPage() {
  const params = useParams();
  const projectId = params.id as string;
  const [documents, setDocuments] = useState<Document[]>([]);
  const [historyLibraryStatus, setHistoryLibraryStatus] = useState<HistoryLibraryRefreshStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const docType = 'rfp';
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchDocuments = useCallback(async () => {
    try {
      const res = await api.get(`/projects/${projectId}/documents`);
      const list = Array.isArray(res.data) ? res.data : (res.data.items || res.data.list || []);
      setDocuments(list);
    } catch (error) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Failed to load documents'));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const fetchHistoryLibraryStatus = useCallback(async () => {
    try {
      const res = await api.get('/documents/history-library/status');
      setHistoryLibraryStatus(res.data);
    } catch (error) {
      console.error(error);
    }
  }, []);

  useEffect(() => {
    if (projectId) {
      void fetchDocuments();
      void fetchHistoryLibraryStatus();
    }
  }, [fetchDocuments, fetchHistoryLibraryStatus, projectId]);

  useEffect(() => {
    if (!historyLibraryStatus || !['queued', 'running'].includes(historyLibraryStatus.status)) {
      return;
    }
    const timer = window.setTimeout(() => {
      void fetchHistoryLibraryStatus();
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [fetchHistoryLibraryStatus, historyLibraryStatus]);

  useEffect(() => {
    if (!documents.some((document) => document.parse_status === 'parsing' || document.parse_status === 'pending')) {
      return;
    }
    const timer = window.setTimeout(() => {
      void fetchDocuments();
      void fetchHistoryLibraryStatus();
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [documents, fetchDocuments, fetchHistoryLibraryStatus]);

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    setUploading(true);
    let successCount = 0;
    let parseInsufficientCount = 0;
    let lastAccepted: { parse_status?: string; message?: string } | null = null;
    try {
      for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('doc_type', docType);
        const res = await api.post(`/projects/${projectId}/documents/upload`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' }
        });
        const accepted = res.data || {};
        lastAccepted = accepted;
        if (accepted.parse_status === 'parse_insufficient') {
          parseInsufficientCount += 1;
        }
        successCount += 1;
      }
      if (files.length === 1 && lastAccepted?.message) {
        if (lastAccepted.parse_status === 'parse_insufficient') {
          toast.warning(lastAccepted.message);
        } else if (lastAccepted.parse_status === 'parsing' || lastAccepted.parse_status === 'pending') {
          toast.info(lastAccepted.message);
        } else {
          toast.success(lastAccepted.message);
        }
      } else {
        toast.info(`Queued ${successCount}/${files.length} documents for parsing`);
        if (parseInsufficientCount > 0) {
          toast.warning(
            `${parseInsufficientCount} document(s) were saved but excluded from the historical library because the parse quality was insufficient.`
          );
        }
      }
      await fetchHistoryLibraryStatus();
      await fetchDocuments();
    } catch (error) {
      console.error(error);
      toast.error(
        getApiErrorMessage(
          error,
          files.length > 1
            ? `Imported ${successCount}/${files.length} documents before the error`
            : 'Error uploading document'
        )
      );
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleReparse = async (docId: string) => {
    try {
      const res = await api.post(`/documents/${docId}/reparse`);
      const accepted = res.data || {};
      if (accepted.parse_status === 'parse_insufficient') {
        toast.warning(accepted.message || 'Reparse completed, but the parse quality was insufficient.');
      } else {
        toast.success(accepted.message || 'Document reparsed successfully');
      }
      await fetchHistoryLibraryStatus();
      await fetchDocuments();
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Error triggering reparse'));
    }
  };

  const handleDelete = async (docId: string) => {
    if (!confirm('Are you sure you want to delete this document?')) return;
    try {
      await api.delete(`/documents/${docId}`);
      toast.success('Document deleted');
      await fetchHistoryLibraryStatus();
      await fetchDocuments();
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Error deleting document'));
    }
  };

  const renderStatus = (status: string) => {
    switch (status) {
      case 'done':
        return <Badge className="bg-green-500 hover:bg-green-600"><CheckCircle2 className="mr-1 w-3 h-3" /> Done</Badge>;
      case 'parsing':
        return <Badge variant="secondary" className="bg-blue-100 text-blue-800"><RefreshCw className="mr-1 w-3 h-3 animate-spin" /> Parsing</Badge>;
      case 'parse_insufficient':
        return <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800"><AlertCircle className="mr-1 w-3 h-3" /> Parse Insufficient</Badge>;
      case 'failed':
        return <Badge variant="destructive"><XCircle className="mr-1 w-3 h-3" /> Failed</Badge>;
      default:
        return <Badge variant="outline"><AlertCircle className="mr-1 w-3 h-3" /> {status}</Badge>;
    }
  };

  const formatStatusTime = (value?: string | null) => {
    if (!value) return '';
    return format(new Date(value), 'MMM d, yyyy HH:mm:ss');
  };

  const formatPipelineLabel = (name: string) => {
    switch (name) {
      case 'case_library':
        return 'Reuse Library + AI Wiki';
      case 'visual_cache':
        return 'Visual Index';
      default:
        return name;
    }
  };

  const readNumberStat = (key: string) => {
    const value = historyLibraryStatus?.stats?.[key];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };

  const formatDurationSeconds = (value?: number | null) => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
      return null;
    }
    if (value >= 60) {
      const minutes = Math.floor(value / 60);
      const seconds = value - minutes * 60;
      return `${minutes}m ${seconds.toFixed(seconds >= 10 ? 0 : 1)}s`;
    }
    if (value >= 10) {
      return `${value.toFixed(1)}s`;
    }
    if (value >= 1) {
      return `${value.toFixed(2)}s`;
    }
    return `${Math.round(value * 1000)}ms`;
  };

  const renderPipelineSummary = () => {
    if (!historyLibraryStatus?.pipelines) return null;
    const entries = Object.entries(historyLibraryStatus.pipelines);
    if (!entries.length) return null;
    return (
      <div className="mt-2 flex flex-wrap gap-2">
        {entries.map(([name, pipeline]) => {
          const durationLabel = formatDurationSeconds(pipeline.duration_seconds);
          return (
            <Badge key={name} variant="outline" className="text-xs font-normal">
              {formatPipelineLabel(name)}: {pipeline.status}
              {durationLabel ? ` · ${durationLabel}` : ''}
            </Badge>
          );
        })}
      </div>
    );
  };

  const renderHistoryLibraryAlert = () => {
    if (!historyLibraryStatus) return null;
    const cacheHitCount = readNumberStat('cache_hit_uploaded_documents');
    const cacheMissCount = readNumberStat('cache_miss_uploaded_documents');
    const uploadedOutlineCount = readNumberStat('uploaded_outline_documents');
    const visualCacheEntryCount = readNumberStat('visual_cache_entry_count');
    const visualQdrantIndexedPoints = readNumberStat('visual_qdrant_indexed_points');
    const visualQdrantSyncStatus =
      typeof historyLibraryStatus.stats?.visual_qdrant_sync_status === 'string'
        ? String(historyLibraryStatus.stats?.visual_qdrant_sync_status)
        : null;

    if (historyLibraryStatus.status === 'idle' && !historyLibraryStatus.last_success_at) {
      return null;
    }

    if (historyLibraryStatus.status === 'failed') {
      return (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Historical library refresh failed</AlertTitle>
          <AlertDescription>
            {historyLibraryStatus.error || 'The latest AI Wiki refresh did not complete successfully.'}
            {renderPipelineSummary()}
          </AlertDescription>
        </Alert>
      );
    }

    if (historyLibraryStatus.status === 'partial_failed') {
      return (
        <Alert>
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Historical refresh completed with partial failures</AlertTitle>
          <AlertDescription>
            {historyLibraryStatus.error || 'One background refresh pipeline failed while another completed successfully.'}
            {uploadedOutlineCount !== null
              ? ` Rebuilt ${uploadedOutlineCount} uploaded historical proposals before the failing pipeline stopped.`
              : ''}
            {cacheHitCount !== null || cacheMissCount !== null
              ? ` Projection cache hits ${cacheHitCount ?? 0}, misses ${cacheMissCount ?? 0}.`
              : ''}
            {visualQdrantIndexedPoints !== null
              ? ` Visual ANN index wrote ${visualQdrantIndexedPoints} points${visualQdrantSyncStatus ? ` (${visualQdrantSyncStatus})` : ''}.`
              : ''}
            {renderPipelineSummary()}
          </AlertDescription>
        </Alert>
      );
    }

    if (['queued', 'running'].includes(historyLibraryStatus.status)) {
      return (
        <Alert>
          <RefreshCw className="h-4 w-4 animate-spin" />
          <AlertTitle>Historical library refresh in progress</AlertTitle>
          <AlertDescription>
            {historyLibraryStatus.status === 'queued'
              ? 'Imported historical proposals are waiting to refresh the reuse library, AI Wiki, and visual index.'
              : 'Imported historical proposals are refreshing the reuse library, AI Wiki, and visual index.'}
            {historyLibraryStatus.started_at || historyLibraryStatus.requested_at
              ? ` Last update: ${formatStatusTime(historyLibraryStatus.started_at || historyLibraryStatus.requested_at)}.`
              : ''}
            {historyLibraryStatus.pending ? ' A newer refresh request is already queued.' : ''}
            {renderPipelineSummary()}
          </AlertDescription>
        </Alert>
      );
    }

    return (
      <Alert>
        <CheckCircle2 className="h-4 w-4" />
        <AlertTitle>Historical library, AI Wiki, and visual index are up to date</AlertTitle>
        <AlertDescription>
          Last completed at {formatStatusTime(historyLibraryStatus.last_success_at || historyLibraryStatus.finished_at)}.
          {uploadedOutlineCount !== null
            ? ` Compiled ${uploadedOutlineCount} uploaded historical proposals into the current library snapshot.`
            : ''}
          {cacheHitCount !== null || cacheMissCount !== null
            ? ` Projection cache hits ${cacheHitCount ?? 0}, misses ${cacheMissCount ?? 0}.`
            : ''}
          {visualCacheEntryCount !== null
            ? ` Visual index now contains ${visualCacheEntryCount} cached assets.`
            : ''}
          {visualQdrantIndexedPoints !== null
            ? ` ANN visual collection now contains ${visualQdrantIndexedPoints} indexed points${visualQdrantSyncStatus ? ` (${visualQdrantSyncStatus})` : ''}.`
            : ''}
          {renderPipelineSummary()}
        </AlertDescription>
      </Alert>
    );
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">Documents</h2>
          <p className="text-muted-foreground mt-1">
            Upload current project RFP documents. Historical proposals are managed from the library audit page.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link href="/library/materials" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
            Historical Library
          </Link>
          <input 
            type="file" 
            ref={fileInputRef} 
            onChange={handleFileChange} 
            className="hidden" 
            multiple
            accept=".pdf,.docx,.doc,.txt,.md"
          />
          <Button onClick={handleUploadClick} disabled={uploading}>
            {uploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UploadCloud className="mr-2 h-4 w-4" />}
            {uploading ? 'Uploading...' : 'Upload RFP'}
          </Button>
        </div>
      </div>

      {renderHistoryLibraryAlert()}

      <Card>
        <CardHeader>
          <CardTitle>Project Repository</CardTitle>
          <CardDescription>
            Documents are automatically parsed into intelligent chunks. Historical proposals also refresh the reuse library, AI Wiki, and visual index in the background.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-8 text-center text-muted-foreground">Loading documents...</div>
          ) : documents.length === 0 ? (
            <div className="py-12 text-center flex flex-col items-center">
              <File className="h-12 w-12 text-muted-foreground mb-4 opacity-50" />
              <p className="text-muted-foreground">No documents uploaded yet.</p>
            </div>
          ) : (
            <div className="divide-y border rounded-md">
              {documents.map((doc) => (
                <div key={doc.id} className="p-4 flex items-center justify-between hover:bg-muted/50 transition-colors">
                  <div className="flex items-start gap-4">
                    <File className="h-8 w-8 text-blue-500 mt-1" />
                    <div>
                      <p className="font-medium">{doc.filename}</p>
                      <div className="flex items-center gap-3 mt-1 text-sm text-muted-foreground">
                        <Badge variant="outline" className="text-xs font-normal">
                          {doc.doc_type === 'rfp' ? 'RFP' : 'Historical Data'}
                        </Badge>
                        <span>{(doc.file_size_bytes ? doc.file_size_bytes / 1024 / 1024 : 0).toFixed(2)} MB</span>
                        <span>{doc.created_at ? format(new Date(doc.created_at), 'MMM d, yyyy HH:mm') : ''}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    {renderStatus(doc.parse_status)}
                    <div className="flex space-x-2">
                      <Button variant="outline" size="sm" onClick={() => handleReparse(doc.id)}>
                        Reparse
                      </Button>
                      <Button variant="outline" size="sm" className="text-red-500 hover:text-red-700" onClick={() => handleDelete(doc.id)}>
                        Delete
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
