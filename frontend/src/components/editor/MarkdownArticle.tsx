'use client';

import { ComponentProps } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Enhanced prose class list for article-quality markdown rendering.
 *
 * Key design decisions (locked in output/draft-refactor-uiux.md):
 * - H1: 30px bold, bottom border, generous top spacing
 * - H2: 24px semibold, left blue accent line
 * - H3: 20px semibold
 * - Body: 1.85 line-height, slate-700
 * - Blockquote: blue accent left border, subtle background
 * - Tables: preserved from original (rounded, bordered)
 */
const ARTICLE_PROSE_CLASSES = [
  'prose prose-slate max-w-none',
  // --- Headings ---
  '[&_h1]:text-[1.875rem] [&_h1]:font-bold [&_h1]:text-slate-900',
  '[&_h1]:mt-10 [&_h1]:mb-4 [&_h1]:pb-3 [&_h1]:border-b [&_h1]:border-slate-200',
  '[&_h1:first-child]:mt-0',
  '[&_h2]:text-2xl [&_h2]:font-semibold [&_h2]:text-slate-800',
  '[&_h2]:mt-8 [&_h2]:mb-3 [&_h2]:pl-4 [&_h2]:border-l-[3px] [&_h2]:border-blue-500',
  '[&_h2:first-child]:mt-0',
  '[&_h3]:text-xl [&_h3]:font-semibold [&_h3]:text-slate-700',
  '[&_h3]:mt-6 [&_h3]:mb-2',
  '[&_h3:first-child]:mt-0',
  '[&_h4]:text-lg [&_h4]:font-medium [&_h4]:text-slate-700',
  '[&_h4]:mt-5 [&_h4]:mb-2',
  '[&_h5]:text-base [&_h5]:font-medium [&_h5]:text-slate-600',
  '[&_h5]:mt-4 [&_h5]:mb-1.5',
  '[&_h6]:text-sm [&_h6]:font-medium [&_h6]:text-slate-500 [&_h6]:uppercase [&_h6]:tracking-wide',
  '[&_h6]:mt-4 [&_h6]:mb-1.5',
  // --- Paragraphs ---
  '[&_p]:leading-[1.85] [&_p]:text-slate-700',
  '[&_p+p]:mt-5',
  // --- Lists ---
  '[&_ul]:list-disc [&_ol]:list-decimal',
  '[&_ul]:pl-6 [&_ol]:pl-6',
  '[&_li]:my-2 [&_li]:leading-[1.75] [&_li]:text-slate-700',
  // --- Blockquote ---
  '[&_blockquote]:border-l-4 [&_blockquote]:border-blue-400',
  '[&_blockquote]:bg-blue-50/60 [&_blockquote]:rounded-r-xl',
  '[&_blockquote]:px-5 [&_blockquote]:py-4 [&_blockquote]:my-6',
  '[&_blockquote]:text-slate-700 [&_blockquote]:not-italic',
  '[&_blockquote_p]:leading-[1.75]',
  // --- Tables (preserved from original) ---
  '[&_table]:my-4 [&_table]:w-full [&_table]:border-collapse',
  '[&_table]:overflow-hidden [&_table]:rounded-2xl',
  '[&_table]:border [&_table]:border-slate-200 [&_table]:bg-white',
  '[&_th]:border-b [&_th]:border-slate-200 [&_th]:bg-slate-100',
  '[&_th]:px-3 [&_th]:py-2 [&_th]:text-left',
  '[&_th]:text-xs [&_th]:font-semibold [&_th]:uppercase [&_th]:tracking-wide [&_th]:text-slate-600',
  '[&_td]:border-b [&_td]:border-slate-100 [&_td]:px-3 [&_td]:py-2 [&_td]:align-top [&_td]:text-sm',
  // --- Strong/Em ---
  '[&_strong]:text-slate-900 [&_strong]:font-semibold',
].join(' ');

const COMPACT_PROSE_CLASSES = [
  'prose prose-sm prose-slate max-w-none',
  '[&_table]:my-4 [&_table]:w-full [&_table]:border-collapse',
  '[&_table]:overflow-hidden [&_table]:rounded-2xl',
  '[&_table]:border [&_table]:border-slate-200 [&_table]:bg-white',
  '[&_th]:border-b [&_th]:border-slate-200 [&_th]:bg-slate-100',
  '[&_th]:px-3 [&_th]:py-2 [&_th]:text-left',
  '[&_th]:text-xs [&_th]:font-semibold [&_th]:uppercase [&_th]:tracking-wide [&_th]:text-slate-600',
  '[&_td]:border-b [&_td]:border-slate-100 [&_td]:px-3 [&_td]:py-2 [&_td]:align-top [&_td]:text-sm',
  '[&_li]:my-1.5 [&_p]:leading-7',
].join(' ');


export function MarkdownArticle({ markdown, compact = false }: { markdown: string; compact?: boolean }) {
  return (
    <div className={compact ? COMPACT_PROSE_CLASSES : ARTICLE_PROSE_CLASSES}>
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
