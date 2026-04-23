'use client';

import { ComponentProps } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';


export function MarkdownArticle({ markdown, compact = false }: { markdown: string; compact?: boolean }) {
  return (
    <div className={`prose prose-slate max-w-none ${compact ? 'prose-sm' : ''} [&_table]:my-4 [&_table]:w-full [&_table]:border-collapse [&_table]:overflow-hidden [&_table]:rounded-2xl [&_table]:border [&_table]:border-slate-200 [&_table]:bg-white [&_th]:border-b [&_th]:border-slate-200 [&_th]:bg-slate-100 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:text-xs [&_th]:font-semibold [&_th]:uppercase [&_th]:tracking-wide [&_th]:text-slate-600 [&_td]:border-b [&_td]:border-slate-100 [&_td]:px-3 [&_td]:py-2 [&_td]:align-top [&_td]:text-sm [&_li]:my-1.5 [&_p]:leading-7`}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          code(props: ComponentProps<'code'>) {
            const { className, children, ...rest } = props;
            return (
              <code className={`rounded bg-slate-100 px-1.5 py-0.5 text-[0.92em] text-slate-700 ${className || ''}`} {...rest}>
                {children}
              </code>
            );
          },
        }}
      >
        {markdown}
      </Markdown>
    </div>
  );
}
