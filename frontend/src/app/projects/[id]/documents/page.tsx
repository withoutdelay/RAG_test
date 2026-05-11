'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { UploadCloud, File, CheckCircle2, AlertCircle, XCircle, RefreshCw, Loader2, Clock } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import api, { getApiErrorMessage } from '@/lib/api';
import { Document } from '@/lib/types';
import { format } from 'date-fns';

// Project RFP upload page — uses the lightweight parser and does NOT feed the
// historical proposal library / AI wiki / visual index.  Historical ingestion has
// a separate entry point under /library/materials.
const PARSE_STATUS_LABEL: Record<string, string> = {
  parsing: '需求解析中',
  pending: '已加入队列',
  queued: '已加入队列',
  done: '需求解析完成',
  parse_insufficient: '需求解析不充分',
  failed: '需求解析失败',
};

export default function DocumentsPage() {
  const params = useParams();
  const projectId = params.id as string;
  const [documents, setDocuments] = useState<Document[]>([]);
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
      toast.error(getApiErrorMessage(error, '加载需求文档失败'));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      void fetchDocuments();
    }
  }, [fetchDocuments, projectId]);

  useEffect(() => {
    // Poll the project documents list while any RFP is still parsing.  We no
    // longer poll /documents/history-library/status — the project documents page
    // is intentionally decoupled from the historical library refresh lifecycle.
    if (!documents.some((document) => document.parse_status === 'parsing' || document.parse_status === 'pending')) {
      return;
    }
    const timer = window.setTimeout(() => {
      void fetchDocuments();
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [documents, fetchDocuments]);

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
        toast.info(`已接收 ${successCount}/${files.length} 份需求文档，正在后台轻量解析`);
        if (parseInsufficientCount > 0) {
          toast.warning(`${parseInsufficientCount} 份文档文本抽取不充分，已保存原文，需人工复核。`);
        }
      }
      await fetchDocuments();
    } catch (error) {
      console.error(error);
      toast.error(
        getApiErrorMessage(
          error,
          files.length > 1
            ? `在第 ${successCount + 1} 份文档处出错，已成功上传 ${successCount}/${files.length} 份`
            : '需求文档上传失败'
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
        toast.warning(accepted.message || '重新解析完成，但解析结果不充分。');
      } else {
        toast.success(accepted.message || '已触发重新解析');
      }
      await fetchDocuments();
    } catch (error) {
      toast.error(getApiErrorMessage(error, '触发重新解析失败'));
    }
  };

  const handleDelete = async (docId: string) => {
    if (!confirm('确定删除该需求文档？此操作不可恢复。')) return;
    try {
      await api.delete(`/documents/${docId}`);
      toast.success('需求文档已删除');
      await fetchDocuments();
    } catch (error) {
      toast.error(getApiErrorMessage(error, '删除需求文档失败'));
    }
  };

  const renderStatus = (status: string) => {
    const label = PARSE_STATUS_LABEL[status] ?? status;
    switch (status) {
      case 'done':
        return (
          <Badge className="bg-green-500 hover:bg-green-600">
            <CheckCircle2 className="mr-1 w-3 h-3" /> {label}
          </Badge>
        );
      case 'parsing':
        return (
          <Badge variant="secondary" className="bg-blue-100 text-blue-800">
            <RefreshCw className="mr-1 w-3 h-3 animate-spin" /> {label}
          </Badge>
        );
      case 'pending':
      case 'queued':
        return (
          <Badge variant="secondary" className="bg-slate-100 text-slate-700">
            <Clock className="mr-1 w-3 h-3" /> {label}
          </Badge>
        );
      case 'parse_insufficient':
        return (
          <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">
            <AlertCircle className="mr-1 w-3 h-3" /> {label}
          </Badge>
        );
      case 'failed':
        return (
          <Badge variant="destructive">
            <XCircle className="mr-1 w-3 h-3" /> {label}
          </Badge>
        );
      default:
        return (
          <Badge variant="outline">
            <AlertCircle className="mr-1 w-3 h-3" /> {label}
          </Badge>
        );
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">项目需求文档</h2>
          <p className="text-muted-foreground mt-1">
            上传 RFP 用于提取项目需求和约束；仅做轻量文本抽取，不进入历史方案库。
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link href="/library/materials" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
            历史方案库
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
            {uploading ? '上传中...' : '上传需求文档'}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>项目需求文档列表</CardTitle>
          <CardDescription>
            仅做需求文本抽取，不做切片/向量化/图表入库，也不参与历史方案复用。旧版 .doc 如无法解析请上传 .docx / .pdf。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-8 text-center text-muted-foreground">需求文档加载中…</div>
          ) : documents.length === 0 ? (
            <div className="py-12 text-center flex flex-col items-center">
              <File className="h-12 w-12 text-muted-foreground mb-4 opacity-50" />
              <p className="text-muted-foreground">尚未上传项目需求文档。</p>
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
                          {doc.doc_type === 'rfp' ? '项目需求' : '历史方案'}
                        </Badge>
                        <span>{(doc.file_size_bytes ? doc.file_size_bytes / 1024 / 1024 : 0).toFixed(2)} MB</span>
                        <span>{doc.created_at ? format(new Date(doc.created_at), 'yyyy-MM-dd HH:mm') : ''}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    {renderStatus(doc.parse_status)}
                    <div className="flex space-x-2">
                      <Button variant="outline" size="sm" onClick={() => handleReparse(doc.id)}>
                        重新解析
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-red-500 hover:text-red-700"
                        onClick={() => handleDelete(doc.id)}
                      >
                        删除
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
