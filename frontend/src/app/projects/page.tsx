'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { format } from 'date-fns';
import { Plus, FolderArchive, ArrowRight, Loader2 } from 'lucide-react';
import api from '@/lib/api';
import { Project } from '@/lib/types';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { CreateProjectModal } from '@/components/projects/CreateProjectModal';

type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'outline' | 'ghost' | 'link' | 'success' | 'warning';

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchProjects = async () => {
    try {
      setLoading(true);
      const res = await api.get('/projects');
      // Assume res.data is an array or has a list field
      const list = Array.isArray(res.data) ? res.data : (res.data.items || res.data.list || []);
      setProjects(list);
    } catch (err) {
      console.error('Failed to fetch projects', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProjects();
  }, []);

  const getStatusColor = (status: string): BadgeVariant => {
    const s = status.toUpperCase();
    if (s === 'FAILED') return 'destructive';
    if (['EXPORTABLE', 'EXPORTED'].includes(s)) return 'success';
    if (['REVIEW_REQUIRED', 'BLOCKED_FOR_CLARIFICATION'].includes(s)) return 'warning';
    if (s === 'CREATED') return 'secondary';
    return 'default';
  };

  return (
    <div className="flex-1 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Projects</h2>
          <p className="text-muted-foreground mt-2">
            Manage your presale proposal generations and analysis tasks.
          </p>
        </div>
        <CreateProjectModal>
          <Button size="lg">
            <Plus className="mr-2 h-5 w-5" />
            New Project
          </Button>
        </CreateProjectModal>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : projects.length === 0 ? (
        <div className="flex flex-col items-center justify-center border-2 border-dashed rounded-lg p-12 text-center h-64">
          <FolderArchive className="h-12 w-12 text-muted-foreground mb-4" />
          <h3 className="text-lg font-medium">No projects found</h3>
          <p className="text-muted-foreground text-sm mt-1 mb-4">
            Get started by creating a new intelligent workspace.
          </p>
          <CreateProjectModal />
        </div>
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((project) => (
            <Card key={project.id} className="flex flex-col hover:shadow-md transition-shadow">
              <CardHeader className="pb-4">
                <div className="flex justify-between items-start mb-2">
                  <Badge variant={getStatusColor(project.status)}>{project.status}</Badge>
                  {project.industry && (
                    <span className="text-xs font-medium text-muted-foreground bg-muted px-2 py-1 rounded truncate max-w-[100px]">
                      {project.industry}
                    </span>
                  )}
                </div>
                <CardTitle className="line-clamp-2 text-xl">{project.name}</CardTitle>
                <CardDescription className="line-clamp-2 min-h-10 mt-2">
                  {project.description || 'No description provided.'}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex-1">
                <div className="text-sm text-muted-foreground">
                  <div>Created: {project.created_at ? format(new Date(project.created_at), 'PPP') : 'Unknown'}</div>
                  {project.current_draft_version && (
                    <div className="mt-1">Version: {project.current_draft_version}</div>
                  )}
                </div>
              </CardContent>
              <CardFooter className="pt-4 border-t">
                <Link href={`/projects/${project.id}`} className={buttonVariants({ variant: "ghost", className: "w-full justify-between" })}>
                  <span className="font-medium">Open Project</span>
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
