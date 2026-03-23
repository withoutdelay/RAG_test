'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import { UploadCloud, File, CheckCircle2, AlertCircle, XCircle, RefreshCw, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import api, { getApiErrorMessage } from '@/lib/api';
import { Document } from '@/lib/types';
import { format } from 'date-fns';

export default function DocumentsPage() {
  const params = useParams();
  const projectId = params.id as string;
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
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

  useEffect(() => {
    if (projectId) {
      void fetchDocuments();
    }
  }, [fetchDocuments, projectId]);

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);
    // Simple logic: if filename has 'rfp' or '需求' mark as RFP, else historical
    const docType = file.name.toLowerCase().includes('rfp') ? 'rfp' : 'historical_proposal';
    formData.append('doc_type', docType);
    
    setUploading(true);
    try {
      await api.post(`/projects/${projectId}/documents/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      toast.success('Document uploaded successfully');
      await fetchDocuments();
    } catch (error) {
      console.error(error);
      toast.error(getApiErrorMessage(error, 'Error uploading document'));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleReparse = async (docId: string) => {
    try {
      await api.post(`/documents/${docId}/reparse`);
      toast.success('Reparse triggered');
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
      case 'failed':
        return <Badge variant="destructive"><XCircle className="mr-1 w-3 h-3" /> Failed</Badge>;
      default:
        return <Badge variant="outline"><AlertCircle className="mr-1 w-3 h-3" /> {status}</Badge>;
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">Documents</h2>
          <p className="text-muted-foreground mt-1">
            Upload RFP documents and historical technical proposals.
          </p>
        </div>
        <div>
          <input 
            type="file" 
            ref={fileInputRef} 
            onChange={handleFileChange} 
            className="hidden" 
            accept=".pdf,.docx,.doc,.txt,.md"
          />
          <Button onClick={handleUploadClick} disabled={uploading}>
            {uploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UploadCloud className="mr-2 h-4 w-4" />}
            {uploading ? 'Uploading...' : 'Upload Document'}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Project Repository</CardTitle>
          <CardDescription>
            Documents are automatically parsed into intelligent chunks.
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
