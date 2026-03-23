'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useParams } from 'next/navigation';
import { FileText, ClipboardList, Database, LayoutList, PenTool, CheckCircle, Download, Loader2, ArrowLeft } from 'lucide-react';
import { cn } from '@/lib/utils';
import api from '@/lib/api';
import { useProjectStore } from '@/stores/projectStore';
import { Badge } from '@/components/ui/badge';
import { buttonVariants } from '@/components/ui/button';

export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  const params = useParams();
  const pathname = usePathname();
  const projectId = params.id as string;
  const { currentProject, setCurrentProject } = useProjectStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchProject = async () => {
      try {
        setLoading(true);
        const res = await api.get(`/projects/${projectId}`);
        setCurrentProject(res.data);
      } catch (err) {
        console.error('Failed to load project', err);
      } finally {
        setLoading(false);
      }
    };
    if (projectId) {
      fetchProject();
    }
  }, [projectId, setCurrentProject]);

  const navItems = [
    { name: 'Overview', href: `/projects/${projectId}`, icon: FileText, exact: true },
    { name: 'Documents', href: `/projects/${projectId}/documents`, icon: Database },
    { name: 'Requirement', href: `/projects/${projectId}/requirement`, icon: ClipboardList },
    { name: 'Evidence', href: `/projects/${projectId}/evidence`, icon: LayoutList },
    { name: 'Outline', href: `/projects/${projectId}/outline`, icon: PenTool },
    { name: 'Drafts', href: `/projects/${projectId}/editor`, icon: FileText },
    { name: 'Validation', href: `/projects/${projectId}/validation`, icon: CheckCircle },
    { name: 'Export', href: `/projects/${projectId}/export`, icon: Download },
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full min-h-[500px]">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!currentProject) {
    return (
      <div className="flex flex-col items-center justify-center h-full py-12">
        <h2 className="text-xl font-bold">Project not found</h2>
        <Link href="/projects" className={buttonVariants({ variant: "link", className: "mt-4" })}>Go back to projects</Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full space-y-4">
      <div className="flex items-center space-x-4 border-b pb-4">
        <Link href="/projects" className={buttonVariants({ variant: "ghost", size: "icon" })}><ArrowLeft className="h-5 w-5"/></Link>
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight">{currentProject.name}</h1>
          <div className="flex items-center space-x-2 mt-1">
            <Badge variant="outline">{currentProject.status}</Badge>
            {currentProject.industry && <span className="text-xs text-muted-foreground">{currentProject.industry}</span>}
          </div>
        </div>
      </div>

      <div className="flex flex-col lg:flex-row gap-6 h-[calc(100vh-140px)]">
        <nav className="flex space-x-2 lg:flex-col lg:space-x-0 lg:space-y-1 lg:w-48 overflow-x-auto pb-2 lg:pb-0 flex-shrink-0">
          {navItems.map((item) => {
            const isActive = item.exact 
              ? pathname === item.href 
              : pathname.startsWith(item.href);
              
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center px-3 py-2 text-sm font-medium rounded-md whitespace-nowrap",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted"
                )}
              >
                <item.icon className={cn("mr-2 h-4 w-4", isActive ? "text-primary-foreground" : "text-muted-foreground")} />
                {item.name}
              </Link>
            )
          })}
        </nav>
        
        <main className="flex-1 overflow-y-auto bg-card rounded-lg border shadow-sm flex flex-col">
          {children}
        </main>
      </div>
    </div>
  );
}
