import type { RecommendedAsset, ReuseBlockLike } from '@/lib/types';


export function parseMarkdownTableParts(markdown: string): { header: string; delimiter: string; rows: string[] } | null {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  for (let index = 0; index < lines.length - 1; index += 1) {
    const header = lines[index].trim();
    const delimiter = lines[index + 1].trim();
    if (!header.includes('|')) {
      continue;
    }
    if (!/^\|?[\s:|-]+\|?$/.test(delimiter) || !delimiter.includes('-')) {
      continue;
    }
    const rows: string[] = [];
    for (let rowIndex = index + 2; rowIndex < lines.length; rowIndex += 1) {
      const row = lines[rowIndex].trim();
      if (!row) {
        continue;
      }
      if (!row.includes('|')) {
        break;
      }
      rows.push(row);
    }
    return { header, delimiter, rows };
  }
  return null;
}


export function mergeMarkdownTableBlocks(blocks: ReuseBlockLike[]): string | undefined {
  const mergedRows: string[] = [];
  let header = '';
  let delimiter = '';

  for (const block of blocks) {
    const content = block.content_md?.trim();
    if (!content) {
      continue;
    }
    const table = parseMarkdownTableParts(content);
    if (!table) {
      return undefined;
    }
    if (!header) {
      header = table.header;
      delimiter = table.delimiter;
    }
    mergedRows.push(...table.rows);
  }

  if (!header || !delimiter) {
    return undefined;
  }

  return [header, delimiter, ...mergedRows].join('\n');
}


export function markdownTableCandidate(text: string): boolean {
  return text.includes('|') && /\n\|?[-: ]+\|[-|: ]+/.test(text);
}


export function isVisualAsset(asset?: RecommendedAsset): boolean {
  return asset?.asset_type === 'figure' || asset?.asset_type === 'formula_candidate';
}
