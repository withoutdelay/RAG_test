import { create } from 'zustand';
import { OutlineNode, SectionDraft } from '@/lib/types';

interface AgentProgress {
  currentAgent: string;
  currentSection: number;
  progress: number;
}

interface EditorState {
  taskId: string | null;
  outline: OutlineNode | null;
  sections: SectionDraft[];
  globalParams: Record<string, string>;
  agentProgress: AgentProgress;

  setTaskId: (id: string | null) => void;
  setOutline: (outline: OutlineNode | null) => void;
  setSections: (sections: SectionDraft[]) => void;
  updateSection: (index: number, patch: Partial<SectionDraft>) => void;
  appendSectionDelta: (index: number, delta: string) => void;
  setAgentProgress: (progress: AgentProgress) => void;
}

export const useEditorStore = create<EditorState>((set) => ({
  taskId: null,
  outline: null,
  sections: [],
  globalParams: {},
  agentProgress: { currentAgent: '', currentSection: 0, progress: 0 },

  setTaskId: (id) => set({ taskId: id }),
  setOutline: (outline) => set({ outline }),
  setSections: (sections) => set({ sections }),
  updateSection: (index, patch) => set((state) => {
    const newSections = [...state.sections];
    if (newSections[index]) {
      newSections[index] = { ...newSections[index], ...patch };
    }
    return { sections: newSections };
  }),
  appendSectionDelta: (index, delta) => set((state) => {
    const newSections = [...state.sections];
    if (newSections[index]) {
      newSections[index] = { ...newSections[index], content_md: (newSections[index].content_md || '') + delta };
    }
    return { sections: newSections };
  }),
  setAgentProgress: (progress) => set({ agentProgress: progress }),
}));
