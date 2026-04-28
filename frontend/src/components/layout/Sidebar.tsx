'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, FolderOpen, Settings, HelpCircle, Library } from 'lucide-react';
import { cn } from '@/lib/utils';

type SidebarProps = {
  className?: string;
};

export function Sidebar({ className }: SidebarProps) {
  const pathname = usePathname();

  return (
    <div className={cn("pb-12 border-r bg-muted/20 h-full w-64 flex-shrink-0 relative", className)}>
      <div className="space-y-4 py-4">
        <div className="px-3 py-2">
          <h2 className="mb-2 px-4 text-lg font-semibold tracking-tight">
            Presale Copilot
          </h2>
          <div className="space-y-1">
            <Link href="/">
              <span className={cn(
                "flex items-center rounded-md px-3 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground",
                pathname === '/' ? "bg-accent" : "transparent"
              )}>
                <Home className="mr-2 h-4 w-4" />
                Dashboard
              </span>
            </Link>
            <Link href="/projects">
              <span className={cn(
                "flex items-center rounded-md px-3 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground",
                pathname.startsWith('/projects') ? "bg-accent" : "transparent"
              )}>
                <FolderOpen className="mr-2 h-4 w-4" />
                Projects
              </span>
            </Link>
            <Link href="/library/materials">
              <span className={cn(
                "flex items-center rounded-md px-3 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground",
                pathname.startsWith('/library') ? "bg-accent" : "transparent"
              )}>
                <Library className="mr-2 h-4 w-4" />
                Historical Library
              </span>
            </Link>
          </div>
        </div>
      </div>
      <div className="mt-auto absolute bottom-4 px-3 w-64">
        <div className="space-y-1">
            <Link href="/settings">
              <span className="flex items-center rounded-md px-3 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground transparent">
                <Settings className="mr-2 h-4 w-4" />
                Settings
              </span>
            </Link>
            <Link href="/help">
              <span className="flex items-center rounded-md px-3 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground transparent">
                <HelpCircle className="mr-2 h-4 w-4" />
                Help
              </span>
            </Link>
        </div>
      </div>
    </div>
  );
}
