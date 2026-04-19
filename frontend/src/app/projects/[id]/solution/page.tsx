'use client';

import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  GitBranch,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  ShieldAlert,
  Sparkles,
  Trash2,
} from 'lucide-react';

import api, { getApiErrorMessage, isNotFoundError } from '@/lib/api';
import {
  CatalogCandidateScore,
  CatalogVersion,
  ProductCatalogSeries,
  Project,
  SolutionSelectedProduct,
  SolutionSnapshot,
} from '@/lib/types';
import { useProjectStore } from '@/stores/projectStore';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';

const stageItems = ['Requirement', 'Evidence', 'Solution', 'Outline', 'Drafts', 'Validation', 'Export'];
type EditableListField = 'key_constraints' | 'open_questions' | 'suggested_chapters';

type EditableStringListProps = {
  title: string;
  icon?: ReactNode;
  items: string[];
  emptyHint: string;
  addLabel: string;
  multiline?: boolean;
  onChange: (index: number, value: string) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
};

function buildMermaidCode(solution: SolutionSnapshot): string {
  const primary = solution.selected_products[0];
  const protocol = solution.interface_plan.dcs_protocol || '通讯链路';
  const support = solution.selected_products.slice(1);

  const lines = [
    'graph LR',
    '  A["现场供电"] --> B["主驱动"]',
    '  B --> C["工艺负载"]',
    `  B -.->|"${protocol}"| D["DCS"]`,
  ];

  if (primary) {
    lines[1] = `  A["${primary.rated_voltage || '供电侧'}"] --> B["${primary.name}"]`;
  }

  support.forEach((item, index) => {
    lines.push(`  B -.-> E${index}["${item.name}"]`);
  });

  return lines.join('\n');
}

function getProtocolOptions(solution: SolutionSnapshot): string[] {
  const preferred = solution.interface_plan.dcs_protocol || 'Modbus TCP';
  return Array.from(new Set([preferred, 'Profibus-DP', 'Profinet', 'Modbus TCP', 'IEC 61850']));
}

function formatPower(value?: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value} kW` : '待确认';
}

function formatScore(value?: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '--';
}

function formatPowerRange(minPower?: number | null, maxPower?: number | null): string {
  const minLabel = typeof minPower === 'number' && Number.isFinite(minPower) ? `${minPower}` : '待确认';
  const maxLabel = typeof maxPower === 'number' && Number.isFinite(maxPower) ? `${maxPower}` : '待确认';
  return `${minLabel} - ${maxLabel} kW`;
}

function formatDateTime(value?: string | null): string {
  if (!value) return '未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '未知';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function normalizeStringList(items: string[]): string[] {
  return items.map((item) => item.trim()).filter(Boolean);
}

function normalizeIoAllocation(ioAllocation?: Record<string, number>): Record<string, number> {
  return ['DI', 'DO', 'AI', 'AO'].reduce<Record<string, number>>((acc, key) => {
    const value = ioAllocation?.[key];
    if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
      acc[key] = value;
    }
    return acc;
  }, {});
}

function EditableStringList({
  title,
  icon,
  items,
  emptyHint,
  addLabel,
  multiline = false,
  onChange,
  onAdd,
  onRemove,
}: EditableStringListProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {icon}
        <p className="text-sm font-semibold text-foreground">{title}</p>
      </div>

      {items.length > 0 ? (
        items.map((item, index) => (
          <div key={`${title}-${index}`} className="flex items-start gap-2 rounded-md border border-border bg-[#fbfcf8] p-3">
            {multiline ? (
              <Textarea
                value={item}
                onChange={(event) => onChange(index, event.target.value)}
                className="min-h-[88px] flex-1 resize-none border-border bg-white text-sm leading-6"
              />
            ) : (
              <Input
                value={item}
                onChange={(event) => onChange(index, event.target.value)}
                className="h-10 flex-1 border-border bg-white text-sm"
              />
            )}
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              onClick={() => onRemove(index)}
              aria-label={`Remove ${title} ${index + 1}`}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))
      ) : (
        <div className="rounded-md border border-dashed border-border bg-[#fbfcf8] px-4 py-3 text-sm leading-6 text-muted-foreground">
          {emptyHint}
        </div>
      )}

      <Button type="button" variant="outline" size="sm" onClick={onAdd}>
        <Plus className="mr-1 h-3.5 w-3.5" />
        {addLabel}
      </Button>
    </div>
  );
}

export default function SolutionPage() {
  const params = useParams();
  const projectId = params.id as string;
  const { currentProject } = useProjectStore();

  const [solution, setSolution] = useState<SolutionSnapshot | null>(null);
  const [solutionHistory, setSolutionHistory] = useState<SolutionSnapshot[]>([]);
  const [comparisonSnapshotId, setComparisonSnapshotId] = useState<string | null>(null);
  const [catalogVersions, setCatalogVersions] = useState<CatalogVersion[]>([]);
  const [catalogSeries, setCatalogSeries] = useState<ProductCatalogSeries[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmationNotes, setConfirmationNotes] = useState('');

  const mermaidCode = useMemo(() => (solution ? buildMermaidCode(solution) : ''), [solution]);
  const protocolOptions = useMemo(() => (solution ? getProtocolOptions(solution) : []), [solution]);
  const solutionId = solution?.id || null;
  const activeCatalogVersion = solution?.source_catalog_version || solution?.selection_reason.catalog_version || null;
  const candidateScores = useMemo<CatalogCandidateScore[]>(
    () => solution?.selection_reason.candidate_scores || [],
    [solution]
  );
  const catalogSeriesMap = useMemo(
    () => new Map(catalogSeries.map((series) => [series.code, series])),
    [catalogSeries]
  );
  const selectedSeriesCodes = useMemo(
    () => new Set((solution?.selected_products || []).map((item) => item.series_code)),
    [solution]
  );
  const comparisonSnapshot = useMemo(
    () =>
      solutionHistory.find((item) => item.id === comparisonSnapshotId) ||
      solutionHistory.find((item) => item.id !== solutionId) ||
      null,
    [comparisonSnapshotId, solutionHistory, solutionId]
  );
  const comparisonAddedProducts = useMemo(() => {
    if (!solution || !comparisonSnapshot) return [];
    const baselineCodes = new Set(comparisonSnapshot.selected_products.map((item) => item.series_code));
    return solution.selected_products.filter((item) => !baselineCodes.has(item.series_code));
  }, [comparisonSnapshot, solution]);
  const comparisonRemovedProducts = useMemo(() => {
    if (!solution || !comparisonSnapshot) return [];
    const currentCodes = new Set(solution.selected_products.map((item) => item.series_code));
    return comparisonSnapshot.selected_products.filter((item) => !currentCodes.has(item.series_code));
  }, [comparisonSnapshot, solution]);

  const applySolution = (nextSolution: SolutionSnapshot) => {
    setSolution(nextSolution);
    setConfirmationNotes(nextSolution.confirmation_notes || '');
  };

  const refreshSolutionHistory = useCallback(
    async (currentSnapshotId?: string | null) => {
      try {
        const res = await api.get(`/projects/${projectId}/solutions`, { params: { limit: 8 } });
        const rows = res.data || [];
        setSolutionHistory(rows);
        setComparisonSnapshotId((prev) => {
          if (prev && rows.some((item: SolutionSnapshot) => item.id === prev && item.id !== currentSnapshotId)) {
            return prev;
          }
          const fallback = rows.find((item: SolutionSnapshot) => item.id !== currentSnapshotId);
          return fallback?.id || null;
        });
      } catch (error) {
        if (!isNotFoundError(error)) {
          toast.error(getApiErrorMessage(error, 'Failed to load solution history'));
        }
        setSolutionHistory([]);
        setComparisonSnapshotId(null);
      }
    },
    [projectId]
  );

  const recalculateIoAllocation = useCallback(
    (
      products: SolutionSelectedProduct[],
      fallback: Record<string, number> | undefined
    ): Record<string, number> => {
      const totals: Record<string, number> = {};
      let matchedCount = 0;

      products.forEach((product) => {
        const series = catalogSeriesMap.get(product.series_code);
        if (!series) return;
        matchedCount += 1;
        const multiplier = Math.max(product.quantity || 1, 1);
        Object.entries(series.io_allocation || {}).forEach(([key, value]) => {
          if (typeof value !== 'number' || !Number.isFinite(value)) return;
          totals[key] = (totals[key] || 0) + value * multiplier;
        });
      });

      return matchedCount > 0 ? totals : normalizeIoAllocation(fallback);
    },
    [catalogSeriesMap]
  );

  const buildCatalogProduct = useCallback(
    (
      series: ProductCatalogSeries,
      options?: {
        role?: string;
        quantity?: number;
        ratedVoltage?: string;
        ratedPowerKw?: number | null;
      }
    ): SolutionSelectedProduct => ({
      role: options?.role || (series.role_type === 'primary' ? '主驱动' : series.series_name),
      series_code: series.code,
      name: series.series_name,
      family: series.family,
      topology: series.topology || undefined,
      rated_voltage: options?.ratedVoltage || series.voltage_levels[0] || '待确认',
      rated_power_kw: options?.ratedPowerKw ?? null,
      quantity: Math.max(options?.quantity || 1, 1),
      config: series.standard_configs[0]?.config_name || '标准配置',
      vendor: series.vendor || '标准产品',
      rationale: '从产品目录手动同步到当前方案。',
    }),
    []
  );

  useEffect(() => {
    const fetchSolution = async () => {
      try {
        setLoading(true);
        const res = await api.get(`/projects/${projectId}/solutions/latest`);
        applySolution(res.data);
        await refreshSolutionHistory(res.data.id);
      } catch (error) {
        if (isNotFoundError(error)) {
          setSolution(null);
          setSolutionHistory([]);
          setComparisonSnapshotId(null);
          setConfirmationNotes('');
        } else {
          toast.error(getApiErrorMessage(error, 'Failed to load solution workspace'));
        }
      } finally {
        setLoading(false);
      }
    };

    if (projectId) {
      void fetchSolution();
    }
  }, [projectId, refreshSolutionHistory]);

  useEffect(() => {
    const fetchCatalogExplorer = async () => {
      if (!solutionId) {
        setCatalogVersions([]);
        setCatalogSeries([]);
        return;
      }

      try {
        setCatalogLoading(true);
        const [versionsRes, seriesRes] = await Promise.all([
          api.get('/catalog/versions'),
          api.get('/catalog/series', {
            params: {
              published_only: true,
              catalog_version: activeCatalogVersion || undefined,
            },
          }),
        ]);
        setCatalogVersions(versionsRes.data || []);
        setCatalogSeries(seriesRes.data || []);
      } catch (error) {
        toast.error(getApiErrorMessage(error, 'Failed to load catalog explorer'));
      } finally {
        setCatalogLoading(false);
      }
    };

    void fetchCatalogExplorer();
  }, [activeCatalogVersion, solutionId]);

  const handleGenerate = async (forceRefresh = true) => {
    try {
      setGenerating(true);
      const res = await api.post(`/projects/${projectId}/design-solution`, {
        force_refresh: forceRefresh,
      });
      applySolution(res.data);
      await refreshSolutionHistory(res.data.id);
      toast.success(forceRefresh ? 'Solution recommendation refreshed' : 'Solution recommendation created');
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to generate solution recommendation'));
    } finally {
      setGenerating(false);
    }
  };

  const updateProduct = (index: number, patch: Partial<SolutionSelectedProduct>) => {
    setSolution((prev) => {
      if (!prev) return prev;
      const nextProducts = prev.selected_products.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item
      );
      return { ...prev, selected_products: nextProducts };
    });
  };

  const removeProduct = (index: number) => {
    setSolution((prev) => {
      if (!prev || index === 0) return prev;
      const nextProducts = prev.selected_products.filter((_, itemIndex) => itemIndex !== index);
      return {
        ...prev,
        selected_products: nextProducts,
        interface_plan: {
          ...prev.interface_plan,
          io_allocation: recalculateIoAllocation(nextProducts, prev.interface_plan.io_allocation),
        },
      };
    });
    toast.success('配套设备已从当前方案移除');
  };

  const updateStringList = (field: EditableListField, index: number, value: string) => {
    setSolution((prev) => {
      if (!prev) return prev;
      const nextItems = [...prev[field]];
      nextItems[index] = value;
      return { ...prev, [field]: nextItems };
    });
  };

  const addStringListItem = (field: EditableListField) => {
    setSolution((prev) => (prev ? { ...prev, [field]: [...prev[field], ''] } : prev));
  };

  const removeStringListItem = (field: EditableListField, index: number) => {
    setSolution((prev) => {
      if (!prev) return prev;
      return { ...prev, [field]: prev[field].filter((_, itemIndex) => itemIndex !== index) };
    });
  };

  const updateInterfaceAllocation = (channel: 'DI' | 'DO' | 'AI' | 'AO', value: string) => {
    setSolution((prev) => {
      if (!prev) return prev;
      const numericValue = Math.max(0, Number(value) || 0);
      return {
        ...prev,
        interface_plan: {
          ...prev.interface_plan,
          io_allocation: {
            ...prev.interface_plan.io_allocation,
            [channel]: numericValue,
          },
        },
      };
    });
  };

  const persistSolution = async (snapshot: SolutionSnapshot, notes: string): Promise<SolutionSnapshot> => {
    const payload = {
      solution_summary: snapshot.solution_summary.trim(),
      selected_products: snapshot.selected_products.map((product) => ({
        ...product,
        role: product.role.trim(),
        name: product.name.trim(),
        config: product.config?.trim() || undefined,
        vendor: product.vendor?.trim() || undefined,
        rationale: product.rationale?.trim() || undefined,
      })),
      interface_plan: {
        ...snapshot.interface_plan,
        dcs_protocol: snapshot.interface_plan.dcs_protocol?.trim() || undefined,
        io_allocation: normalizeIoAllocation(snapshot.interface_plan.io_allocation),
        notes: snapshot.interface_plan.notes?.trim() || undefined,
      },
      key_constraints: normalizeStringList(snapshot.key_constraints),
      open_questions: normalizeStringList(snapshot.open_questions),
      suggested_chapters: normalizeStringList(snapshot.suggested_chapters),
      confirmation_notes: notes.trim(),
    };

    const res = await api.patch(`/projects/${projectId}/solutions/${snapshot.id}`, payload);
    applySolution(res.data);
    await refreshSolutionHistory(res.data.id);
    return res.data;
  };

  const handleSave = async () => {
    if (!solution) return;
    try {
      setSaving(true);
      await persistSolution(solution, confirmationNotes);
      toast.success('Solution adjustments saved');
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to save solution adjustments'));
    } finally {
      setSaving(false);
    }
  };

  const handleConfirm = async () => {
    if (!solution) return;
    try {
      setConfirming(true);
      const latestSolution = await persistSolution(solution, confirmationNotes);
      const res = await api.post(`/projects/${projectId}/solutions/${latestSolution.id}/confirm`, {
        confirmation_notes: confirmationNotes,
        confirmed_by_user: true,
      });
      applySolution(res.data);
      toast.success('Solution snapshot confirmed');
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to confirm solution snapshot'));
    } finally {
      setConfirming(false);
    }
  };

  const applySeriesAsPrimary = (series: ProductCatalogSeries) => {
    setSolution((prev) => {
      if (!prev) return prev;
      const currentPrimary = prev.selected_products[0];
      const nextPrimary = buildCatalogProduct(series, {
        role: '主驱动',
        quantity: currentPrimary?.quantity || 1,
        ratedVoltage: currentPrimary?.rated_voltage || series.voltage_levels[0] || '待确认',
        ratedPowerKw: currentPrimary?.rated_power_kw ?? null,
      });
      const nextProducts = [nextPrimary, ...prev.selected_products.slice(1)];
      const nextProtocol = series.communication_protocols.includes(prev.interface_plan.dcs_protocol || '')
        ? prev.interface_plan.dcs_protocol
        : series.communication_protocols[0] || prev.interface_plan.dcs_protocol;
      const nextWhySelected = Array.from(
        new Set([...(prev.selection_reason.why_selected || []), `已手动将主驱动切换为 ${series.series_name}。`])
      );
      return {
        ...prev,
        source_catalog_version: series.catalog_version,
        selected_products: nextProducts,
        interface_plan: {
          ...prev.interface_plan,
          dcs_protocol: nextProtocol,
          io_allocation: recalculateIoAllocation(nextProducts, prev.interface_plan.io_allocation),
        },
        selection_reason: {
          ...prev.selection_reason,
          catalog_version: series.catalog_version,
          why_selected: nextWhySelected,
        },
      };
    });
    toast.success(`已将 ${series.series_name} 设为主驱动`);
  };

  const syncOrAddSupportSeries = (series: ProductCatalogSeries) => {
    setSolution((prev) => {
      if (!prev) return prev;
      const currentPrimary = prev.selected_products[0];
      const nextSupport = buildCatalogProduct(series, {
        role: series.series_name,
        quantity: currentPrimary?.quantity || 1,
        ratedVoltage: currentPrimary?.rated_voltage || series.voltage_levels[0] || '待确认',
        ratedPowerKw: currentPrimary?.rated_power_kw ?? null,
      });
      const existingIndex = prev.selected_products.findIndex((item, index) => index > 0 && item.series_code === series.code);
      const nextProducts =
        existingIndex >= 0
          ? prev.selected_products.map((item, index) => (index === existingIndex ? { ...item, ...nextSupport } : item))
          : [...prev.selected_products, nextSupport];
      return {
        ...prev,
        selected_products: nextProducts,
        interface_plan: {
          ...prev.interface_plan,
          io_allocation: recalculateIoAllocation(nextProducts, prev.interface_plan.io_allocation),
        },
        selection_reason: {
          ...prev.selection_reason,
          why_selected: Array.from(
            new Set([...(prev.selection_reason.why_selected || []), `已手动同步目录项 ${series.series_name}。`])
          ),
        },
      };
    });
    toast.success(selectedSeriesCodes.has(series.code) ? `已同步 ${series.series_name}` : `已将 ${series.series_name} 加入方案`);
  };

  const project = currentProject as Project | null;

  if (loading) {
    return (
      <div className="flex min-h-[520px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  if (!solution) {
    return (
      <div className="flex flex-col gap-6 p-6">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-5">
          <div className="space-y-2">
            <p className="text-sm font-medium uppercase tracking-[0.08em] text-muted-foreground">Solution Workspace</p>
            <h2 className="text-3xl font-semibold text-foreground">先生成项目级方案快照</h2>
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
              这一阶段会把项目字段和最新需求卡整理成可编辑的方案基线，后续 Outline 和 Drafts 会以这份快照作为事实输入。
            </p>
          </div>
          <Button onClick={() => void handleGenerate(false)} disabled={generating} className="h-11 px-5">
            {generating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
            Generate Recommendation
          </Button>
        </div>

        <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
          <section className="rounded-lg border border-border bg-card p-5">
            <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Project Frame</p>
            <div className="mt-4 space-y-4 text-sm text-foreground">
              <div>
                <p className="text-muted-foreground">Project</p>
                <p className="mt-1 font-medium">{project?.name || 'Unknown project'}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Industry</p>
                <p className="mt-1 font-medium">{project?.industry || 'Not specified'}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Product Line</p>
                <p className="mt-1 font-medium">{project?.product_line || 'Not specified'}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Description</p>
                <p className="mt-1 whitespace-pre-wrap leading-6">{project?.description || 'No description provided yet.'}</p>
              </div>
            </div>
          </section>

          <section className="rounded-lg border border-dashed border-border bg-[#fbfcf8] p-6">
            <div className="flex items-start gap-3">
              <GitBranch className="mt-0.5 h-5 w-5 text-primary" />
              <div className="space-y-3">
                <h3 className="text-lg font-semibold text-foreground">当前还没有方案快照</h3>
                <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
                  首版推荐会先用项目字段和最新需求卡生成主驱动、配套设备、接口计划和建议章节。之后可以在这个页面直接调整数量、协议和确认备注。
                </p>
                <p className="text-sm leading-6 text-muted-foreground">
                  如果后面需要对照真实方案文档，我会把原分支对应资料拷贝到当前 worktree，再进一步校准章节结构和设备配置。
                </p>
              </div>
            </div>
          </section>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 bg-[#f4f5f1] p-4 text-foreground sm:p-6">
      <div className="rounded-lg border border-border bg-card px-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Solution Workspace</p>
              <Badge variant={solution.confirmed_by_user ? 'success' : 'warning'}>
                {solution.confirmed_by_user ? 'Confirmed' : 'Draft'}
              </Badge>
              <Badge variant="outline">v{solution.version}</Badge>
            </div>
            <h2 className="text-2xl font-semibold text-foreground">{project?.name || 'Project Solution'}</h2>
            <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
              这份方案快照会成为后续 Outline 和 Drafts 的产品事实输入。当前实现先完成方案基线、人工调整和确认闭环。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void handleGenerate(true)} disabled={generating}>
              {generating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              Refresh
            </Button>
            <Button variant="outline" onClick={() => void handleSave()} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
              Save
            </Button>
            <Button onClick={() => void handleConfirm()} disabled={confirming || solution.confirmed_by_user}>
              {confirming ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
              Confirm Solution
            </Button>
          </div>
        </div>

        <p className="mt-3 text-sm text-muted-foreground">
          确认操作会先持久化当前页面上的所有人工调整，再锁定这一版方案快照。
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          {stageItems.map((item) => (
            <div
              key={item}
              className={`rounded-full border px-3 py-1 text-xs font-medium ${
                item === 'Solution'
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border bg-secondary text-secondary-foreground'
              }`}
            >
              {item}
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)_320px]">
        <section className="rounded-lg border border-border bg-card p-5">
          <div className="space-y-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Requirement Frame</p>
              <h3 className="mt-2 text-lg font-semibold">输入与候选设备</h3>
            </div>
            <div className="rounded-md border border-border bg-[#fbfcf8] p-4 text-sm leading-6 text-muted-foreground">
              <p><span className="font-medium text-foreground">Industry:</span> {project?.industry || 'Not specified'}</p>
              <p><span className="font-medium text-foreground">Product Line:</span> {project?.product_line || 'Not specified'}</p>
              <p className="mt-2 whitespace-pre-wrap">{project?.description || 'Project description is not available yet.'}</p>
            </div>

            <Separator />

            <div className="space-y-3">
              <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Selected Products</p>
              {solution.selected_products.map((product, index) => (
                <div key={`${product.series_code}-${index}`} className="rounded-md border border-border bg-white p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-foreground">{product.role}</p>
                      <p className="mt-1 text-sm text-muted-foreground">{product.name}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{product.config || '标准配置'}</Badge>
                      {index > 0 && (
                        <Button
                          type="button"
                          variant="outline"
                          size="icon-sm"
                          onClick={() => removeProduct(index)}
                          aria-label={`Remove selected product ${index + 1}`}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </div>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Voltage</p>
                      <p className="mt-1 font-medium text-foreground">{product.rated_voltage || '待确认'}</p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Power</p>
                      <p className="mt-1 font-medium text-foreground">{formatPower(product.rated_power_kw)}</p>
                    </div>
                  </div>
                  <div className="mt-3">
                    <label className="text-xs uppercase tracking-[0.08em] text-muted-foreground" htmlFor={`quantity-${index}`}>
                      Quantity
                    </label>
                    <Input
                      id={`quantity-${index}`}
                      type="number"
                      min={1}
                      value={product.quantity}
                      onChange={(event) => updateProduct(index, { quantity: Math.max(1, Number(event.target.value) || 1) })}
                      className="mt-1 h-9"
                    />
                  </div>
                  <div className="mt-3">
                    <label className="text-xs uppercase tracking-[0.08em] text-muted-foreground" htmlFor={`config-${index}`}>
                      Config
                    </label>
                    <Input
                      id={`config-${index}`}
                      value={product.config || ''}
                      onChange={(event) => updateProduct(index, { config: event.target.value })}
                      className="mt-1 h-9"
                    />
                  </div>
                  <div className="mt-3">
                    <label className="text-xs uppercase tracking-[0.08em] text-muted-foreground" htmlFor={`rationale-${index}`}>
                      Rationale
                    </label>
                    <Textarea
                      id={`rationale-${index}`}
                      value={product.rationale || ''}
                      onChange={(event) => updateProduct(index, { rationale: event.target.value })}
                      className="mt-1 min-h-[88px] resize-none border-border bg-[#fbfcf8] text-sm leading-6"
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="rounded-lg border border-border bg-card p-5">
          <div className="space-y-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Solution Canvas</p>
              <h3 className="mt-2 text-lg font-semibold">方案摘要、系统链路和章节建议</h3>
            </div>

            <Textarea
              value={solution.solution_summary}
              onChange={(event) => setSolution({ ...solution, solution_summary: event.target.value })}
              className="min-h-[96px] resize-none border-border bg-[#fbfcf8] text-sm leading-6"
            />

            <Tabs defaultValue="rendered" className="w-full">
              <TabsList variant="line" className="w-full justify-start border-b border-border px-0 pb-2">
                <TabsTrigger value="rendered">Rendered</TabsTrigger>
                <TabsTrigger value="code">Mermaid</TabsTrigger>
                <TabsTrigger value="tables">Tables</TabsTrigger>
                <TabsTrigger value="chapters">Chapters</TabsTrigger>
              </TabsList>

              <TabsContent value="rendered" className="pt-4">
                <div className="rounded-lg border border-border bg-[#fbfcf8] p-5">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="rounded-md border border-border bg-white px-4 py-3 text-sm font-medium text-foreground">
                      现场供电
                    </div>
                    <span className="text-muted-foreground">→</span>
                    <div className="rounded-md border border-primary/20 bg-primary/8 px-4 py-3 text-sm font-medium text-foreground">
                      {solution.selected_products[0]?.name || '主驱动'}
                    </div>
                    <span className="text-muted-foreground">→</span>
                    <div className="rounded-md border border-border bg-white px-4 py-3 text-sm font-medium text-foreground">
                      工艺负载
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-3">
                    {solution.selected_products.slice(1).map((product, index) => (
                      <div key={`${product.series_code}-support-${index}`} className="rounded-md border border-border bg-white px-4 py-3 text-sm">
                        <p className="font-medium text-foreground">{product.name}</p>
                        <p className="mt-1 text-muted-foreground">{product.role}</p>
                      </div>
                    ))}
                  </div>
                  <div className="mt-4 rounded-md border border-dashed border-border bg-white px-4 py-3 text-sm text-muted-foreground">
                    上位机通讯：<span className="font-medium text-foreground">{solution.interface_plan.dcs_protocol || '未指定'}</span>
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="code" className="pt-4">
                <pre className="overflow-x-auto rounded-lg border border-border bg-[#1f241f] p-5 text-sm leading-6 text-[#eef2ea]">
{mermaidCode}
                </pre>
              </TabsContent>

              <TabsContent value="tables" className="space-y-4 pt-4">
                <div className="rounded-lg border border-border bg-[#fbfcf8] p-4">
                  <p className="text-sm font-semibold text-foreground">接口信号表</p>
                  <div className="mt-3 overflow-x-auto">
                    <table className="min-w-full border-collapse text-left text-sm">
                      <thead>
                        <tr className="border-b border-border text-muted-foreground">
                          <th className="pb-2 pr-4 font-medium">Protocol</th>
                          <th className="pb-2 pr-4 font-medium">DI</th>
                          <th className="pb-2 pr-4 font-medium">DO</th>
                          <th className="pb-2 pr-4 font-medium">AI</th>
                          <th className="pb-2 font-medium">AO</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr className="text-foreground">
                          <td className="py-3 pr-4">{solution.interface_plan.dcs_protocol || '未指定'}</td>
                          <td className="py-3 pr-4">{solution.interface_plan.io_allocation?.DI ?? '-'}</td>
                          <td className="py-3 pr-4">{solution.interface_plan.io_allocation?.DO ?? '-'}</td>
                          <td className="py-3 pr-4">{solution.interface_plan.io_allocation?.AI ?? '-'}</td>
                          <td className="py-3">{solution.interface_plan.io_allocation?.AO ?? '-'}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-[#fbfcf8] p-4">
                  <p className="text-sm font-semibold text-foreground">供货清单表</p>
                  <div className="mt-3 overflow-x-auto">
                    <table className="min-w-full border-collapse text-left text-sm">
                      <thead>
                        <tr className="border-b border-border text-muted-foreground">
                          <th className="pb-2 pr-4 font-medium">Role</th>
                          <th className="pb-2 pr-4 font-medium">Product</th>
                          <th className="pb-2 pr-4 font-medium">Quantity</th>
                          <th className="pb-2 pr-4 font-medium">Voltage</th>
                          <th className="pb-2 font-medium">Config</th>
                        </tr>
                      </thead>
                      <tbody>
                        {solution.selected_products.map((product, index) => (
                          <tr key={`${product.series_code}-row-${index}`} className="border-b border-border/70 text-foreground last:border-b-0">
                            <td className="py-3 pr-4">{product.role}</td>
                            <td className="py-3 pr-4">{product.name}</td>
                            <td className="py-3 pr-4">{product.quantity}</td>
                            <td className="py-3 pr-4">{product.rated_voltage || '待确认'}</td>
                            <td className="py-3">{product.config || '标准配置'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="chapters" className="pt-4">
                <EditableStringList
                  title="Suggested Chapters"
                  items={solution.suggested_chapters}
                  emptyHint="当前还没有章节建议。"
                  addLabel="Add Chapter"
                  onChange={(index, value) => updateStringList('suggested_chapters', index, value)}
                  onAdd={() => addStringListItem('suggested_chapters')}
                  onRemove={(index) => removeStringListItem('suggested_chapters', index)}
                />
              </TabsContent>
            </Tabs>
          </div>
        </section>

        <section className="rounded-lg border border-border bg-card p-5">
          <div className="space-y-5">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Constraints and Trace</p>
              <h3 className="mt-2 text-lg font-semibold">约束、风险和确认</h3>
            </div>

            <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
              <label htmlFor="protocol" className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">
                DCS Protocol
              </label>
              <div className="mt-2 flex flex-wrap gap-2">
                {protocolOptions.map((protocol) => (
                  <button
                    type="button"
                    key={protocol}
                    onClick={() =>
                      setSolution({
                        ...solution,
                        interface_plan: { ...solution.interface_plan, dcs_protocol: protocol },
                      })
                    }
                    className={`rounded-full border px-3 py-1 text-xs font-medium ${
                      solution.interface_plan.dcs_protocol === protocol
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border bg-white text-foreground'
                    }`}
                  >
                    {protocol}
                  </button>
                ))}
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3">
                {(['DI', 'DO', 'AI', 'AO'] as const).map((channel) => (
                  <div key={channel}>
                    <label className="text-xs uppercase tracking-[0.08em] text-muted-foreground" htmlFor={`io-${channel}`}>
                      {channel}
                    </label>
                    <Input
                      id={`io-${channel}`}
                      type="number"
                      min={0}
                      value={solution.interface_plan.io_allocation?.[channel] ?? 0}
                      onChange={(event) => updateInterfaceAllocation(channel, event.target.value)}
                      className="mt-1 h-9 border-border bg-white"
                    />
                  </div>
                ))}
              </div>
              <div className="mt-4">
                <label htmlFor="interface-notes" className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">
                  Interface Notes
                </label>
                <Textarea
                  id="interface-notes"
                  value={solution.interface_plan.notes || ''}
                  onChange={(event) =>
                    setSolution({
                      ...solution,
                      interface_plan: { ...solution.interface_plan, notes: event.target.value },
                    })
                  }
                  className="mt-2 min-h-[96px] resize-none border-border bg-white text-sm leading-6"
                />
              </div>
            </div>

            <EditableStringList
              title="Key Constraints"
              icon={<ShieldAlert className="h-4 w-4 text-warning" />}
              items={solution.key_constraints}
              emptyHint="当前还没有录入约束项。"
              addLabel="Add Constraint"
              multiline
              onChange={(index, value) => updateStringList('key_constraints', index, value)}
              onAdd={() => addStringListItem('key_constraints')}
              onRemove={(index) => removeStringListItem('key_constraints', index)}
            />

            <EditableStringList
              title="Open Questions"
              icon={<AlertTriangle className="h-4 w-4 text-destructive" />}
              items={solution.open_questions}
              emptyHint="当前没有新的待确认项，必要时可以直接补充。"
              addLabel="Add Question"
              multiline
              onChange={(index, value) => updateStringList('open_questions', index, value)}
              onAdd={() => addStringListItem('open_questions')}
              onRemove={(index) => removeStringListItem('open_questions', index)}
            />

            <div className="space-y-3">
              <p className="text-sm font-semibold text-foreground">Trace Signals</p>
              <div className="flex flex-wrap gap-2">
                {(solution.selection_reason.matching_signals || []).map((signal) => (
                  <Badge key={signal} variant="outline">{signal}</Badge>
                ))}
              </div>
              <div className="space-y-2 text-sm leading-6 text-muted-foreground">
                {(solution.selection_reason.why_selected || []).map((reason) => (
                  <p key={reason}>{reason}</p>
                ))}
              </div>
            </div>

            <div className="space-y-3">
              <p className="text-sm font-semibold text-foreground">Confirmation Notes</p>
              <Textarea
                value={confirmationNotes}
                onChange={(event) => setConfirmationNotes(event.target.value)}
                className="min-h-[120px] resize-none border-border bg-[#fbfcf8] text-sm leading-6"
                placeholder="记录工程师确认、接口边界或后续需要核对的事项。"
              />
            </div>

            {(solution.selection_reason.risk_flags || []).length > 0 && (
              <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
                <p className="text-sm font-semibold text-foreground">Current Risks</p>
                <div className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">
                  {(solution.selection_reason.risk_flags || []).map((item) => (
                    <p key={item}>{item}</p>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      </div>

      <section className="rounded-lg border border-border bg-card p-5">
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Snapshot Compare</p>
          <h3 className="text-lg font-semibold text-foreground">方案版本历史与差异对比</h3>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            当前页面会保留最近几版方案快照，方便在自动刷新、人工调整和目录切换之后快速回看差异。
          </p>
        </div>

        <div className="mt-5 grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
          <div className="space-y-3">
            {solutionHistory.length > 0 ? (
              solutionHistory.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  onClick={() => setComparisonSnapshotId(item.id === solution.id ? comparisonSnapshotId : item.id)}
                  className={`w-full rounded-md border px-4 py-3 text-left ${
                    item.id === solution.id
                      ? 'border-primary/30 bg-primary/8'
                      : item.id === comparisonSnapshot?.id
                        ? 'border-border bg-white'
                        : 'border-border bg-[#fbfcf8]'
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-foreground">v{item.version}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(item.created_at)}</p>
                    </div>
                    <Badge variant={item.confirmed_by_user ? 'success' : item.id === solution.id ? 'warning' : 'outline'}>
                      {item.id === solution.id ? 'Current' : item.confirmed_by_user ? 'Confirmed' : 'Compare'}
                    </Badge>
                  </div>
                  <p className="mt-3 line-clamp-3 text-sm leading-6 text-muted-foreground">{item.solution_summary}</p>
                </button>
              ))
            ) : (
              <div className="rounded-md border border-dashed border-border bg-[#fbfcf8] px-4 py-3 text-sm text-muted-foreground">
                当前还没有历史快照。
              </div>
            )}
          </div>

          <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
            {comparisonSnapshot ? (
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-md border border-border bg-white p-3">
                    <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Current</p>
                    <p className="mt-2 text-sm font-semibold text-foreground">v{solution.version}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{solution.source_catalog_version || '未绑定目录版本'}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{solution.interface_plan.dcs_protocol || '未指定协议'}</p>
                  </div>
                  <div className="rounded-md border border-border bg-white p-3">
                    <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Compared Snapshot</p>
                    <p className="mt-2 text-sm font-semibold text-foreground">v{comparisonSnapshot.version}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{comparisonSnapshot.source_catalog_version || '未绑定目录版本'}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{comparisonSnapshot.interface_plan.dcs_protocol || '未指定协议'}</p>
                  </div>
                </div>

                <div className="grid gap-3 lg:grid-cols-2">
                  <div className="rounded-md border border-border bg-white p-3">
                    <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">新增设备</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {comparisonAddedProducts.length > 0 ? (
                        comparisonAddedProducts.map((item) => (
                          <Badge key={`added-${item.series_code}`} variant="success">
                            {item.name}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-sm text-muted-foreground">没有新增设备。</span>
                      )}
                    </div>
                  </div>
                  <div className="rounded-md border border-border bg-white p-3">
                    <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">移除设备</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {comparisonRemovedProducts.length > 0 ? (
                        comparisonRemovedProducts.map((item) => (
                          <Badge key={`removed-${item.series_code}`} variant="warning">
                            {item.name}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-sm text-muted-foreground">没有移除设备。</span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="grid gap-3 lg:grid-cols-2">
                  <div className="rounded-md border border-border bg-white p-3">
                    <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">当前摘要</p>
                    <p className="mt-3 text-sm leading-6 text-muted-foreground">{solution.solution_summary}</p>
                  </div>
                  <div className="rounded-md border border-border bg-white p-3">
                    <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">对比版本摘要</p>
                    <p className="mt-3 text-sm leading-6 text-muted-foreground">{comparisonSnapshot.solution_summary}</p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-border bg-white px-4 py-3 text-sm text-muted-foreground">
                生成下一版方案之后，这里会展示最近快照之间的差异。
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-border bg-card p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Database className="h-4 w-4 text-primary" />
              <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Catalog Explorer</p>
            </div>
            <h3 className="text-lg font-semibold text-foreground">目录版本、候选评分与产品卡明细</h3>
            <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
              当前方案已经切到 PostgreSQL 产品目录驱动，这里直接展示发布版本、候选分值和目录项明细，便于后续做方案对比和人工微调。
            </p>
          </div>
          {catalogLoading && (
            <div className="flex items-center gap-2 rounded-md border border-border bg-[#fbfcf8] px-3 py-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在同步目录数据
            </div>
          )}
        </div>

        <div className="mt-5 grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
          <div className="space-y-4">
            <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
              <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Published Version</p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Badge variant="outline">{activeCatalogVersion || '未绑定目录版本'}</Badge>
                {solution.source_catalog_version && <Badge variant="success">Snapshot Bound</Badge>}
              </div>
              <div className="mt-4 space-y-2 text-sm text-muted-foreground">
                {catalogVersions.map((item) => (
                  <div key={item.catalog_version} className="flex items-center justify-between rounded-md border border-border bg-white px-3 py-2">
                    <div>
                      <p className="font-medium text-foreground">{item.catalog_version}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{item.series_count} 个目录项</p>
                    </div>
                    <Badge variant={item.is_published ? 'success' : 'outline'}>
                      {item.is_published ? 'Published' : 'Draft'}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
              <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Candidate Scores</p>
              <div className="mt-4 space-y-3">
                {candidateScores.length > 0 ? (
                  candidateScores.map((candidate, index) => (
                    <div
                      key={`${candidate.series_code}-${index}`}
                      className={`rounded-md border px-3 py-3 ${
                        selectedSeriesCodes.has(candidate.series_code)
                          ? 'border-primary/30 bg-primary/8'
                          : 'border-border bg-white'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-foreground">{candidate.series_name}</p>
                          <p className="mt-1 text-xs text-muted-foreground">{candidate.series_code}</p>
                        </div>
                        <Badge variant={index === 0 ? 'success' : 'outline'}>{formatScore(candidate.score)}</Badge>
                      </div>
                      <div className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">
                        {candidate.reasons.map((reason) => (
                          <p key={`${candidate.series_code}-${reason}`}>{reason}</p>
                        ))}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="rounded-md border border-dashed border-border bg-white px-4 py-3 text-sm text-muted-foreground">
                    当前快照还没有记录候选评分。
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="space-y-4">
            <div className="grid gap-4 lg:grid-cols-2">
              {catalogSeries.map((series) => (
                <div
                  key={series.code}
                  className={`rounded-md border p-4 ${
                    selectedSeriesCodes.has(series.code) ? 'border-primary/30 bg-primary/8' : 'border-border bg-[#fbfcf8]'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold text-foreground">{series.series_name}</p>
                        <Badge variant={series.role_type === 'primary' ? 'success' : 'outline'}>
                          {series.role_type === 'primary' ? 'Primary' : 'Support'}
                        </Badge>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{series.code}</p>
                    </div>
                    {selectedSeriesCodes.has(series.code) && <Badge variant="warning">In Snapshot</Badge>}
                  </div>

                  <p className="mt-3 text-sm leading-6 text-muted-foreground">{series.description || '当前目录项还没有补充说明。'}</p>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <Badge variant="outline">{series.family}</Badge>
                    {series.voltage_levels.map((item) => (
                      <Badge key={`${series.code}-${item}`} variant="outline">
                        {item}
                      </Badge>
                    ))}
                  </div>

                  <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-md border border-border bg-white px-3 py-2">
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Power Range</p>
                      <p className="mt-1 font-medium text-foreground">{formatPowerRange(series.min_power_kw, series.max_power_kw)}</p>
                    </div>
                    <div className="rounded-md border border-border bg-white px-3 py-2">
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Topology</p>
                      <p className="mt-1 font-medium text-foreground">{series.topology || '未定义'}</p>
                    </div>
                  </div>

                  <div className="mt-4 space-y-3 text-sm">
                    <div className="flex flex-wrap gap-2">
                      {series.role_type === 'primary' ? (
                        <Button type="button" size="sm" onClick={() => applySeriesAsPrimary(series)}>
                          设为主驱动
                        </Button>
                      ) : (
                        <Button type="button" size="sm" onClick={() => syncOrAddSupportSeries(series)}>
                          {selectedSeriesCodes.has(series.code) ? '同步目录项' : '加入方案'}
                        </Button>
                      )}
                    </div>

                    <div>
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Protocols</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {series.communication_protocols.length > 0 ? (
                          series.communication_protocols.map((item) => (
                            <Badge key={`${series.code}-protocol-${item}`} variant="outline">
                              {item}
                            </Badge>
                          ))
                        ) : (
                          <span className="text-muted-foreground">无</span>
                        )}
                      </div>
                    </div>

                    <div>
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Standard Configs</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {series.standard_configs.map((config) => (
                          <Badge key={`${series.code}-config-${config.config_name}`} variant="secondary">
                            {config.config_name}
                          </Badge>
                        ))}
                      </div>
                    </div>

                    <div>
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Constraints</p>
                      <div className="mt-2 space-y-2">
                        {series.constraints.length > 0 ? (
                          series.constraints.map((constraint) => (
                            <div key={`${series.code}-${constraint.condition}-${constraint.action}`} className="rounded-md border border-border bg-white px-3 py-2">
                              <div className="flex items-center justify-between gap-2">
                                <p className="text-sm font-medium text-foreground">{constraint.condition}</p>
                                <Badge variant={constraint.severity === 'blocking' ? 'warning' : 'outline'}>
                                  {constraint.severity}
                                </Badge>
                              </div>
                              <p className="mt-1 text-sm leading-6 text-muted-foreground">{constraint.action}</p>
                            </div>
                          ))
                        ) : (
                          <div className="rounded-md border border-dashed border-border bg-white px-3 py-2 text-sm text-muted-foreground">
                            当前目录项没有额外约束。
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {!catalogLoading && catalogSeries.length === 0 && (
              <div className="rounded-md border border-dashed border-border bg-[#fbfcf8] px-4 py-3 text-sm text-muted-foreground">
                当前没有可展示的发布目录数据。
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
