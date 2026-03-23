'use client';

import { OutlineNode } from '@/lib/types';
import { ChevronDown, ChevronRight, Edit2, AlertCircle, FileText } from 'lucide-react';
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface OutlineTreeProps {
  data: OutlineNode[];
  onUpdate: (data: OutlineNode[]) => void;
  level?: number;
}

export function OutlineTree({ data, onUpdate, level = 0 }: OutlineTreeProps) {
  if (!data || data.length === 0) return null;

  return (
    <div className="space-y-2">
      {data.map((node, idx) => (
        <OutlineTreeNode 
          key={node.section_id || idx} 
          node={node} 
          level={level}
          onUpdate={(updatedNode) => {
            const newData = [...data];
            newData[idx] = updatedNode;
            onUpdate(newData);
          }}
        />
      ))}
    </div>
  );
}

function OutlineTreeNode({ node, level, onUpdate }: { node: OutlineNode; level: number; onUpdate: (n: OutlineNode) => void }) {
  const [expanded, setExpanded] = useState(true);
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(node.title);

  const hasChildren = node.children && node.children.length > 0;

  const handleSave = () => {
    onUpdate({ ...node, title: editTitle });
    setIsEditing(false);
  };

  return (
    <div>
      <div 
        className={`flex items-start group rounded-md border p-3 hover:bg-muted/50 transition-colors bg-card
          ${level === 0 ? 'mb-4 shadow-sm' : 'mb-2'}`}
        style={{ marginLeft: `${level * 1.5}rem` }}
      >
        <button 
          onClick={() => setExpanded(!expanded)} 
          className="mt-0.5 mr-2 text-muted-foreground w-5 h-5 flex items-center justify-center shrink-0"
        >
          {hasChildren ? (
            expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />
          ) : <FileText className="h-4 w-4 opacity-50" />}
        </button>

        <div className="flex-1 min-w-0">
          {isEditing ? (
            <div className="flex items-center space-x-2 w-full max-w-lg mb-1">
              <Input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} autoFocus className="h-8" />
              <Button size="sm" onClick={handleSave}>Save</Button>
              <Button size="sm" variant="ghost" onClick={() => setIsEditing(false)}>Cancel</Button>
            </div>
          ) : (
            <div className="flex items-center">
              <span className={`font-semibold line-clamp-1 ${level === 0 ? 'text-lg' : 'text-base'}`}>{node.title}</span>
              <Button 
                variant="ghost" 
                size="sm" 
                className="opacity-0 group-hover:opacity-100 h-6 px-2 ml-2 transition-opacity"
                onClick={() => setIsEditing(true)}
              >
                <Edit2 className="h-3 w-3 mr-1" /> Edit
              </Button>
            </div>
          )}
          
          {node.purpose && (
            <div className="text-sm text-muted-foreground mt-1.5 leading-relaxed pr-8">
              {node.purpose}
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            {node.mandatory && <Badge variant="destructive" className="text-[10px] h-5 py-0">Mandatory</Badge>}
            {node.needs_human_review && <Badge variant="warning" className="text-[10px] h-5 py-0 bg-amber-100 text-amber-800 hover:bg-amber-200 border-amber-200"><AlertCircle className="w-3 h-3 mr-1"/>Human Review</Badge>}
            {node.expected_evidence_types && node.expected_evidence_types.map((et, i) => (
              <Badge key={i} variant="outline" className="text-[10px] h-5 py-0 text-slate-500 bg-slate-50">{et}</Badge>
            ))}
          </div>
        </div>
      </div>

      {hasChildren && expanded && (
        <OutlineTree 
          data={node.children} 
          level={level + 1}
          onUpdate={(updatedChildren) => {
            onUpdate({ ...node, children: updatedChildren });
          }}
        />
      )}
    </div>
  );
}
