'use client';

import { useProjectStore } from '@/stores/projectStore';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { format } from 'date-fns';

export default function ProjectOverviewPage() {
  const { currentProject } = useProjectStore();

  if (!currentProject) return null;

  return (
    <div className="p-6 space-y-6">
      <h2 className="text-2xl font-bold">Project Overview</h2>
      
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{currentProject.status}</div>
          </CardContent>
        </Card>
        
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Draft Version</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{currentProject.current_draft_version || 'None'}</div>
          </CardContent>
        </Card>
        
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Created At</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-xl font-bold">
              {currentProject.created_at ? format(new Date(currentProject.created_at), 'PPP') : 'Unknown'}
            </div>
          </CardContent>
        </Card>
      </div>
      
      <Card>
        <CardHeader>
          <CardTitle>Project Details</CardTitle>
          <CardDescription>Basic information about this intelligent workspace.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <p className="text-sm font-medium text-muted-foreground">Name</p>
              <p className="mt-1">{currentProject.name}</p>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Product Line</p>
              <p className="mt-1">{currentProject.product_line || 'Not specified'}</p>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Industry</p>
              <p className="mt-1">{currentProject.industry || 'Not specified'}</p>
            </div>
          </div>
          <div className="pt-2 border-t mt-4">
            <p className="text-sm font-medium text-muted-foreground">Description</p>
            <p className="mt-1 whitespace-pre-wrap">{currentProject.description || 'No description provided.'}</p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
