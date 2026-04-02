'use client';

import { useMemo, useState } from 'react';
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Edit2,
  FileText,
  Plus,
  PlusSquare,
  Trash2,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { OutlineNode } from '@/lib/types';

interface OutlineTreeProps {
  data: OutlineNode[];
  onUpdate: (data: OutlineNode[]) => void;
  level?: number;
}

function createOutlineNode(level: number): OutlineNode {
  const id = globalThis.crypto?.randomUUID?.() ?? `node-${Date.now()}`;
  return {
    section_id: `section-${id}`,
    title: level === 0 ? '新增章节' : '新增小节',
    purpose: '请补充本节写作目的、客户关注点和交付边界。',
    mandatory: false,
    expected_evidence_types: [],
    needs_human_review: false,
    children: [],
    section_class: 'custom',
    reuse_level: 'medium',
    generation_mode: 'baseline',
    asset_required: false,
    parameter_sensitive: false,
    customer_specificity: 'medium',
  };
}

export function OutlineTree({ data, onUpdate, level = 0 }: OutlineTreeProps) {
  const nodes = data || [];

  const handleAppendRoot = () => {
    onUpdate([...nodes, createOutlineNode(level)]);
  };

  return (
    <div className="space-y-3">
      {level === 0 && (
        <div className="flex items-center justify-between rounded-2xl border border-dashed border-slate-300 bg-slate-50/80 px-4 py-3">
          <div>
            <p className="text-sm font-semibold text-slate-800">大纲结构</p>
            <p className="text-xs text-slate-500">支持增删章节，新增节点后可直接修改标题和目的。</p>
          </div>
          <Button size="sm" variant="outline" onClick={handleAppendRoot}>
            <Plus className="mr-2 h-4 w-4" />
            Add Section
          </Button>
        </div>
      )}

      {nodes.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center">
          <FileText className="mx-auto mb-3 h-10 w-10 text-slate-300" />
          <p className="text-sm font-medium text-slate-700">当前还没有章节</p>
          <p className="mt-1 text-xs text-slate-500">先加一个章节，再继续补充目的、证据类型和生成策略。</p>
          <Button className="mt-4" size="sm" onClick={handleAppendRoot}>
            <Plus className="mr-2 h-4 w-4" />
            Create First Section
          </Button>
        </div>
      ) : (
        nodes.map((node, idx) => (
          <OutlineTreeNode
            key={node.section_id || idx}
            level={level}
            node={node}
            onUpdate={(updatedNode) => {
              const nextNodes = [...nodes];
              nextNodes[idx] = updatedNode;
              onUpdate(nextNodes);
            }}
            onAddSibling={() => {
              const nextNodes = [...nodes];
              nextNodes.splice(idx + 1, 0, createOutlineNode(level));
              onUpdate(nextNodes);
            }}
            onDelete={() => {
              const nextNodes = [...nodes];
              nextNodes.splice(idx, 1);
              onUpdate(nextNodes);
            }}
          />
        ))
      )}
    </div>
  );
}

function OutlineTreeNode({
  node,
  level,
  onUpdate,
  onAddSibling,
  onDelete,
}: {
  node: OutlineNode;
  level: number;
  onUpdate: (node: OutlineNode) => void;
  onAddSibling: () => void;
  onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const [isEditing, setIsEditing] = useState(node.title.includes('新增'));
  const [editTitle, setEditTitle] = useState(node.title);
  const [editPurpose, setEditPurpose] = useState(node.purpose || '');

  const hasChildren = Boolean(node.children && node.children.length > 0);
  const badgeRows = useMemo(
    () => ({
      expectedTypes: node.expected_evidence_types || [],
    }),
    [node.expected_evidence_types],
  );

  const handleSave = () => {
    onUpdate({
      ...node,
      title: editTitle.trim() || '未命名章节',
      purpose: editPurpose.trim(),
    });
    setIsEditing(false);
  };

  const handleAddChild = () => {
    onUpdate({
      ...node,
      children: [...(node.children || []), createOutlineNode(level + 1)],
    });
    setExpanded(true);
  };

  return (
    <div className="space-y-2">
      <div
        className={`group rounded-2xl border bg-white shadow-sm transition-colors hover:border-slate-300 ${
          level === 0 ? 'border-slate-200' : 'border-slate-100'
        }`}
        style={{ marginLeft: `${level * 1.25}rem` }}
      >
        <div className="flex items-start gap-3 p-4">
          <button
            onClick={() => setExpanded((current) => !current)}
            className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
          >
            {hasChildren ? (
              expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />
            ) : (
              <FileText className="h-4 w-4 opacity-60" />
            )}
          </button>

          <div className="min-w-0 flex-1">
            {isEditing ? (
              <div className="space-y-3">
                <Input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} autoFocus />
                <Textarea
                  value={editPurpose}
                  onChange={(event) => setEditPurpose(event.target.value)}
                  className="min-h-[84px]"
                  placeholder="补充本章节的目的、边界和客户关心的重点。"
                />
                <div className="flex gap-2">
                  <Button size="sm" onClick={handleSave}>
                    Save
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setIsEditing(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`font-semibold text-slate-900 ${level === 0 ? 'text-lg' : 'text-base'}`}>
                    {node.title}
                  </span>
                  {node.mandatory && <Badge variant="destructive">Mandatory</Badge>}
                  {node.needs_human_review && (
                    <Badge variant="warning" className="bg-amber-100 text-amber-800 hover:bg-amber-200">
                      <AlertCircle className="mr-1 h-3 w-3" />
                      Human Review
                    </Badge>
                  )}
                  {node.generation_mode === 'manual_only' && <Badge variant="destructive">Manual Only</Badge>}
                  {node.generation_mode === 'reuse_first' && (
                    <Badge variant="secondary" className="bg-blue-100 text-blue-800 hover:bg-blue-200">
                      Reuse First
                    </Badge>
                  )}
                  {node.generation_mode === 'baseline' && <Badge variant="outline">Baseline</Badge>}
                  {node.asset_required && (
                    <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">
                      Assets Required
                    </Badge>
                  )}
                </div>

                {node.purpose && <p className="mt-2 pr-4 text-sm leading-relaxed text-slate-600">{node.purpose}</p>}

                <div className="mt-3 flex flex-wrap gap-2">
                  {node.reuse_level && (
                    <Badge variant="outline" className="bg-slate-50 text-slate-500">
                      Reuse: {node.reuse_level}
                    </Badge>
                  )}
                  {badgeRows.expectedTypes.map((item, index) => (
                    <Badge key={`${node.section_id}-${item}-${index}`} variant="outline" className="bg-slate-50 text-slate-500">
                      {item}
                    </Badge>
                  ))}
                </div>
              </>
            )}
          </div>

          <div className="flex shrink-0 flex-col gap-2 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100">
            <Button size="sm" variant="ghost" onClick={() => setIsEditing(true)}>
              <Edit2 className="mr-2 h-3.5 w-3.5" />
              Edit
            </Button>
            <Button size="sm" variant="ghost" onClick={handleAddChild}>
              <PlusSquare className="mr-2 h-3.5 w-3.5" />
              Add Child
            </Button>
            <Button size="sm" variant="ghost" onClick={onAddSibling}>
              <Plus className="mr-2 h-3.5 w-3.5" />
              Add Below
            </Button>
            <Button size="sm" variant="ghost" className="text-red-600 hover:text-red-700" onClick={onDelete}>
              <Trash2 className="mr-2 h-3.5 w-3.5" />
              Delete
            </Button>
          </div>
        </div>
      </div>

      {hasChildren && expanded && (
        <OutlineTree
          data={node.children || []}
          level={level + 1}
          onUpdate={(updatedChildren) => {
            onUpdate({ ...node, children: updatedChildren });
          }}
        />
      )}
    </div>
  );
}
