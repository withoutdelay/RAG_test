'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { Download, FileDown, Copy, CheckCircle2, Loader2, Sparkles, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';
import api, { buildLatestExportDownloadUrl, getApiErrorMessage, isNotFoundError } from '@/lib/api';
import { ExportData, Project, ValidationReport } from '@/lib/types';
import Markdown from 'react-markdown';
import { saveAs } from 'file-saver';

export default function ExportPage() {
  const params = useParams();
  const projectId = params.id as string;
  const [exportData, setExportData] = useState<ExportData | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [validationReport, setValidationReport] = useState<ValidationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [copied, setCopied] = useState(false);

  const fetchExportState = useCallback(async () => {
    try {
      setLoading(true);
      const [projectRes, validationRes, exportRes] = await Promise.all([
        api.get(`/projects/${projectId}`),
        api.get(`/projects/${projectId}/validation/latest`).catch((error) => {
          if (isNotFoundError(error)) return null;
          throw error;
        }),
        api.get(`/projects/${projectId}/exports/latest`).catch((error) => {
          if (isNotFoundError(error)) return null;
          throw error;
        }),
      ]);
      setProject(projectRes.data);
      setValidationReport(validationRes?.data ?? null);
      setExportData(exportRes?.data ?? null);
    } catch (error) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Failed to load export state'));
      setExportData(null);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      void fetchExportState();
    }
  }, [fetchExportState, projectId]);

  const exportable = project?.status === 'EXPORTABLE' || project?.status === 'EXPORTED';
  const canForceExport = Boolean(project && !exportable && (project.current_draft_version ?? 0) > 0);
  const validationBlocked = Boolean(validationReport && validationReport.status !== 'passed');
  const blockingErrors = validationReport?.errors ?? [];
  const exportBlockedMessage = validationBlocked
    ? `Latest validation is ${validationReport?.status}. You can still export a Word draft and finish edits manually.`
    : project && !exportable
      ? `Project status is ${project.status}. You can still export a Word draft and finish edits manually.`
      : null;

  const handleGenerateExport = async ({ force = false }: { force?: boolean } = {}) => {
    if (!exportable && !force) {
      toast.error(exportBlockedMessage || 'Project is not ready for export');
      return;
    }
    setExporting(true);
    try {
      await api.post(`/projects/${projectId}/export`, { format: 'docx', force });
      toast.success(force ? 'Draft Word document exported' : 'Word document exported');
      await fetchExportState();
    } catch (error) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Error exporting document'));
    } finally {
      setExporting(false);
    }
  };

  const handleCopy = () => {
    if (!exportData?.content_md) return;
    navigator.clipboard.writeText(exportData.content_md);
    setCopied(true);
    toast.success('Copied to clipboard');
    setTimeout(() => setCopied(false), 3000);
  };

  const handleDownload = async () => {
    if (!exportData) return;
    setDownloading(true);
    try {
      const response = await fetch(buildLatestExportDownloadUrl(projectId));
      if (!response.ok) {
        throw new Error('Failed to download export file');
      }
      const blob = await response.blob();
      saveAs(blob, exportData.file_name || `Project_${projectId}_Export.docx`);
    } catch (error) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Failed to download Word document'));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-slate-50 overflow-hidden">
      <div className="p-6 pb-4 shrink-0 bg-background border-b flex justify-between items-end">
        <div>
          <h2 className="text-2xl font-bold flex items-center">
            <Download className="mr-2 h-6 w-6 text-emerald-600" /> Final Output
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Compile all sections into a Word document for review and editing.
          </p>
        </div>
        <div className="flex space-x-3">
          <Button
            onClick={() => handleGenerateExport({ force: !exportable })}
            disabled={exporting || loading || (!exportable && !canForceExport)}
          >
            {exporting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
            {exporting ? 'Compiling...' : exportable ? 'Generate Word Export' : 'Export Draft Anyway'}
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="flex-1 flex justify-center items-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : !exportData ? (
        <div className="flex-1 p-6 flex items-center justify-center">
          <Card className="flex flex-col items-center justify-center p-12 text-center border-dashed border-2 w-full max-w-2xl">
            <FileDown className="h-12 w-12 text-muted-foreground opacity-50 mb-4" />
            <h3 className="text-lg font-medium">{exportable ? 'Ready to Assemble' : 'Draft Can Be Exported'}</h3>
            <p className="text-muted-foreground text-sm mt-1 mb-6">
              Export creates a Word document for manual review and editing.
            </p>
            {exportBlockedMessage ? (
              <div className="mb-6 w-full rounded-md border border-amber-200 bg-amber-50 p-4 text-left text-sm text-amber-900">
                <div className="flex items-start gap-2 font-medium">
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{exportBlockedMessage}</span>
                </div>
                {blockingErrors.length > 0 ? (
                  <ul className="mt-3 space-y-2">
                    {blockingErrors.slice(0, 4).map((error, index) => (
                      <li key={`${error.code}-${index}`} className="rounded bg-white/70 px-3 py-2">
                        <span className="font-mono text-xs">{error.code}</span>
                        <span className="ml-2">{error.message}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
            <Button
              onClick={() => handleGenerateExport({ force: !exportable })}
              size="lg"
              className="bg-emerald-600 hover:bg-emerald-700"
              disabled={exporting || (!exportable && !canForceExport)}
            >
              {exporting ? <Loader2 className="mr-2 h-5 w-5 animate-spin" /> : <Sparkles className="mr-2 h-5 w-5" />}
              {exportable ? 'Export Word Document' : 'Export Draft Anyway'}
            </Button>
          </Card>
        </div>
      ) : (
        <div className="flex-1 flex flex-col lg:flex-row gap-6 p-6 overflow-hidden">
          <div className="lg:w-1/3 flex flex-col space-y-6 shrink-0">
            <Card>
              <CardHeader className="pb-4">
                <CardTitle className="text-lg">Export Details</CardTitle>
                <CardDescription>File compilation completed</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4 text-sm">
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Filename</span>
                    <span className="font-medium text-right max-w-[200px] truncate" title={exportData.file_name}>{exportData.file_name || `Export_${projectId}.md`}</span>
                  </div>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Format</span>
                    <span className="font-medium uppercase">{exportData.file_type || 'docx'}</span>
                  </div>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-muted-foreground">Status</span>
                    <span className={`font-medium flex items-center ${exportData.status === 'forced' ? 'text-amber-700' : 'text-emerald-600'}`}>
                      <CheckCircle2 className="w-4 h-4 mr-1"/>
                      {exportData.status === 'forced' ? 'Draft Exported' : 'Success'}
                    </span>
                  </div>
                </div>
                
                <div className="mt-8 flex flex-col space-y-3">
                  <Button variant="outline" className="w-full justify-start font-normal" onClick={handleCopy}>
                    {copied ? <CheckCircle2 className="mr-2 h-4 w-4 text-green-500" /> : <Copy className="mr-2 h-4 w-4 text-slate-500" />}
                    {copied ? 'Copied Draft Text' : 'Copy Draft Text'}
                  </Button>
                  <Button className="w-full justify-start font-normal bg-slate-800 hover:bg-slate-900" onClick={handleDownload} disabled={downloading}>
                    {downloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
                    Download Word Document
                  </Button>
                </div>
              </CardContent>
            </Card>
            
            <div className="bg-blue-50 text-blue-800 text-sm p-4 rounded-lg border border-blue-100 italic">
              This is the initial compiled output. Download the Word document and continue editing manually when needed.
            </div>
          </div>
          
          <Card className="flex-1 flex flex-col shadow-sm border overflow-hidden bg-white">
            <div className="p-3 border-b bg-muted/30 flex justify-between items-center text-sm font-medium text-slate-600 shrink-0">
              <span>Preview</span>
              <Badge variant="outline" className="font-mono bg-white">{exportData.content_md?.length || 0} characters</Badge>
            </div>
            <ScrollArea className="flex-1 p-6 lg:p-10">
              <div className="prose prose-slate max-w-none dark:prose-invert">
                <Markdown>{exportData.content_md}</Markdown>
              </div>
            </ScrollArea>
          </Card>
        </div>
      )}
    </div>
  );
}
