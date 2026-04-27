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
  CatalogMaterialManifestPreview,
  CatalogMaterialReadiness,
  ProductCatalogInterface,
  CatalogVersion,
  ProductCatalogCompatibility,
  ProductCatalogFamily,
  ProductCatalogMaterial,
  ProductCatalogModel,
  ProductCatalogSeries,
  Project,
  SolutionCompatibilityAction,
  SolutionSelectedProduct,
  SolutionSelectionReason,
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
type MaterialSourceFilter = 'gate_eligible' | 'all' | 'synthetic_only';
type ManifestPreviewSourceKind = 'auto' | 'customer_provided' | 'synthetic_test_only';

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

function formatCompatibilityRelation(relationType: string): string {
  if (relationType === 'requires') return 'Required';
  if (relationType === 'recommended') return 'Recommended';
  if (relationType === 'optional') return 'Optional';
  if (relationType === 'conflicts_with') return 'Conflict';
  return relationType;
}

function formatMaterialType(materialType: string): string {
  if (materialType === 'proposal_sample') return 'Proposal Sample';
  if (materialType === 'product_manual') return 'Product Manual';
  if (materialType === 'standard_bom') return 'Standard BOM';
  if (materialType === 'interface_schedule') return 'Interface Schedule';
  if (materialType === 'selection_rule') return 'Selection Rule';
  if (materialType === 'model_alias_map') return 'Alias Map';
  if (materialType === 'diagram_template') return 'Diagram Template';
  if (materialType === 'service_plan') return 'Service Plan';
  return materialType;
}

function formatMaterialStatus(status: string): string {
  if (status === 'review_needed') return 'Review Needed';
  if (status === 'asset_only') return 'Asset Only';
  if (status === 'available') return 'Available';
  return status;
}

function formatInterfaceType(interfaceType: string): string {
  if (interfaceType === 'communication') return 'Communication';
  if (interfaceType === 'io_signal') return 'I/O Signal';
  if (interfaceType === 'power') return 'Power';
  return interfaceType;
}

function getMaterialStatusVariant(status: string): 'success' | 'warning' | 'outline' {
  if (status === 'available') return 'success';
  if (status === 'review_needed') return 'warning';
  return 'outline';
}

function formatMaterialSourceKind(sourceKind: string): string {
  if (sourceKind === 'private_sample') return 'Real Sample';
  if (sourceKind === 'customer_provided') return 'Customer Material';
  if (sourceKind === 'private_customer') return 'Customer Material';
  if (sourceKind === 'synthetic_test_only') return 'Synthetic Test Only';
  return sourceKind || 'Unknown Source';
}

function formatManifestIssueType(issueType: string): string {
  if (issueType === 'duplicate_material_key') return 'Duplicate material_key';
  if (issueType === 'missing_source_path') return 'Missing source_path';
  if (issueType === 'missing_source_file') return 'Missing source file';
  if (issueType === 'inferred_family_code') return 'Inferred family_code';
  if (issueType === 'inferred_material_type') return 'Inferred material_type';
  if (issueType === 'inferred_availability_status') return 'Inferred availability_status';
  if (issueType === 'non_gate_source_kind') return 'Non-gate source kind';
  return issueType;
}

function getMaterialSourceVariant(sourceKind: string): 'success' | 'warning' | 'outline' {
  if (sourceKind === 'synthetic_test_only') return 'warning';
  if (sourceKind === 'customer_provided' || sourceKind === 'private_customer') return 'success';
  return 'outline';
}

function isGateEligibleMaterial(material: ProductCatalogMaterial): boolean {
  return material.source_kind !== 'synthetic_test_only';
}

function getCompatibilityVariant(relationType: string): 'success' | 'warning' | 'outline' {
  if (relationType === 'requires') return 'warning';
  if (relationType === 'recommended') return 'success';
  return 'outline';
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

function dedupeStringList(items: Array<string | null | undefined>): string[] {
  const seen = new Set<string>();
  const result: string[] = [];

  items.forEach((item) => {
    const normalized = String(item || '').trim();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    result.push(normalized);
  });

  return result;
}

function formatRecordValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(', ');
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .slice(0, 3)
      .map(([key, item]) => `${key}=${formatRecordValue(item)}`)
      .join('; ');
  }
  return String(value);
}

function summarizeRecord(record?: Record<string, unknown>, limit = 3): string[] {
  return Object.entries(record || {})
    .slice(0, limit)
    .map(([key, value]) => `${key}: ${formatRecordValue(value)}`);
}

function summarizeStringList(items?: string[], limit = 3): string[] {
  return (items || []).map((item) => String(item).trim()).filter(Boolean).slice(0, limit);
}

function isGeneratedCompatibilityReason(reason: string): boolean {
  return reason.startsWith('产品族兼容规则');
}

function isGeneratedCompatibilityRisk(flag: string): boolean {
  return flag.startsWith('缺少必需配套目录项') || flag.startsWith('缺少推荐配套目录项');
}

function isCompatibilityGeneratedProduct(product: SolutionSelectedProduct): boolean {
  return product.config === '兼容规则补充' || String(product.rationale || '').includes('产品族兼容规则');
}

function getCompatibilityStatus(action: SolutionCompatibilityAction): {
  label: string;
  variant: 'success' | 'warning' | 'outline';
} {
  if (!action.applies) return { label: 'Skipped', variant: 'outline' };
  if ((action.missing_series_codes || []).length > 0) return { label: 'Missing', variant: 'warning' };
  if ((action.added_series_codes || []).length > 0) return { label: 'Added', variant: 'success' };
  if ((action.covered_series_codes || []).length > 0) return { label: 'Covered', variant: 'success' };
  return { label: 'Evaluated', variant: 'outline' };
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
  const [catalogFamilies, setCatalogFamilies] = useState<ProductCatalogFamily[]>([]);
  const [catalogSeries, setCatalogSeries] = useState<ProductCatalogSeries[]>([]);
  const [catalogMaterials, setCatalogMaterials] = useState<ProductCatalogMaterial[]>([]);
  const [catalogModels, setCatalogModels] = useState<ProductCatalogModel[]>([]);
  const [catalogInterfaces, setCatalogInterfaces] = useState<ProductCatalogInterface[]>([]);
  const [materialReadiness, setMaterialReadiness] = useState<CatalogMaterialReadiness | null>(null);
  const [manifestPreview, setManifestPreview] = useState<CatalogMaterialManifestPreview | null>(null);
  const [manifestPreviewPath, setManifestPreviewPath] = useState<string>('');
  const [manifestPreviewSourceKind, setManifestPreviewSourceKind] = useState<ManifestPreviewSourceKind>('auto');
  const [manifestPreviewLoading, setManifestPreviewLoading] = useState(false);
  const [manifestPreviewReplaceExisting, setManifestPreviewReplaceExisting] = useState(true);
  const [catalogFamilyFilter, setCatalogFamilyFilter] = useState<string | null>(null);
  const [materialSourceFilter, setMaterialSourceFilter] = useState<MaterialSourceFilter>('gate_eligible');
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
  const snapshotCatalogModelMatches = useMemo(
    () => solution?.selection_reason.catalog_model_matches || [],
    [solution]
  );
  const snapshotCatalogInterfaceEntries = useMemo(
    () => solution?.interface_plan.catalog_interface_entries || [],
    [solution]
  );
  const catalogSeriesMap = useMemo(
    () => new Map(catalogSeries.map((series) => [series.code, series])),
    [catalogSeries]
  );
  const catalogFamilyMap = useMemo(
    () => new Map(catalogFamilies.map((family) => [family.code, family])),
    [catalogFamilies]
  );
  const selectedSeriesCodes = useMemo(
    () => new Set((solution?.selected_products || []).map((item) => item.series_code)),
    [solution]
  );
  const selectedFamilyCodes = useMemo(
    () =>
      new Set(
        (solution?.selected_products || [])
          .map((item) => catalogSeriesMap.get(item.series_code)?.family_code)
          .filter((item): item is string => Boolean(item))
      ),
    [catalogSeriesMap, solution]
  );
  const selectedFamilyCodeList = useMemo(() => Array.from(selectedFamilyCodes).sort(), [selectedFamilyCodes]);
  const filteredCatalogSeries = useMemo(
    () => (catalogFamilyFilter ? catalogSeries.filter((series) => series.family_code === catalogFamilyFilter) : catalogSeries),
    [catalogFamilyFilter, catalogSeries]
  );
  const filteredCatalogMaterials = useMemo(
    () => {
      const familyScoped = catalogFamilyFilter
        ? catalogMaterials.filter((item) => item.family_code === catalogFamilyFilter)
        : catalogMaterials;
      if (materialSourceFilter === 'synthetic_only') {
        return familyScoped.filter((item) => !isGateEligibleMaterial(item));
      }
      if (materialSourceFilter === 'gate_eligible') {
        return familyScoped.filter((item) => isGateEligibleMaterial(item));
      }
      return familyScoped;
    },
    [catalogFamilyFilter, catalogMaterials, materialSourceFilter]
  );
  const syntheticMaterials = useMemo(
    () => catalogMaterials.filter((item) => !isGateEligibleMaterial(item)),
    [catalogMaterials]
  );
  const filteredSeriesCodes = useMemo(
    () => new Set(filteredCatalogSeries.map((series) => series.code)),
    [filteredCatalogSeries]
  );
  const filteredCatalogModels = useMemo(
    () => catalogModels.filter((item) => filteredSeriesCodes.has(item.series_code)),
    [catalogModels, filteredSeriesCodes]
  );
  const filteredCatalogInterfaces = useMemo(
    () => catalogInterfaces.filter((item) => filteredSeriesCodes.has(item.series_code)),
    [catalogInterfaces, filteredSeriesCodes]
  );
  const materialTypeSummary = useMemo(() => {
    const summary = new Map<string, number>();
    catalogMaterials.forEach((item) => {
      summary.set(item.material_type, (summary.get(item.material_type) || 0) + 1);
    });
    return Array.from(summary.entries()).sort((a, b) => b[1] - a[1]);
  }, [catalogMaterials]);
  const materialStatusSummary = useMemo(() => {
    const summary = new Map<string, number>();
    catalogMaterials.forEach((item) => {
      summary.set(item.availability_status, (summary.get(item.availability_status) || 0) + 1);
    });
    return Array.from(summary.entries()).sort((a, b) => b[1] - a[1]);
  }, [catalogMaterials]);
  const materialSourceSummary = useMemo(() => {
    const summary = new Map<string, number>();
    catalogMaterials.forEach((item) => {
      const key = item.source_kind || 'unknown';
      summary.set(key, (summary.get(key) || 0) + 1);
    });
    return Array.from(summary.entries()).sort((a, b) => b[1] - a[1]);
  }, [catalogMaterials]);
  const interfaceTypeSummary = useMemo(() => {
    const summary = new Map<string, number>();
    filteredCatalogInterfaces.forEach((item) => {
      summary.set(item.interface_type, (summary.get(item.interface_type) || 0) + 1);
    });
    return Array.from(summary.entries()).sort((a, b) => b[1] - a[1]);
  }, [filteredCatalogInterfaces]);
  const catalogModelsBySeries = useMemo(() => {
    const summary = new Map<string, ProductCatalogModel[]>();
    catalogModels.forEach((item) => {
      summary.set(item.series_code, [...(summary.get(item.series_code) || []), item]);
    });
    return summary;
  }, [catalogModels]);
  const catalogInterfacesBySeries = useMemo(() => {
    const summary = new Map<string, ProductCatalogInterface[]>();
    catalogInterfaces.forEach((item) => {
      summary.set(item.series_code, [...(summary.get(item.series_code) || []), item]);
    });
    return summary;
  }, [catalogInterfaces]);
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
  const manifestPreviewSourceSummary = useMemo(
    () => Object.entries(manifestPreview?.source_kind_counts || {}).sort((a, b) => b[1] - a[1]),
    [manifestPreview]
  );
  const manifestPreviewEntryPreview = useMemo(
    () => (manifestPreview?.preview_entries || []).slice(0, 6),
    [manifestPreview]
  );

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

  const hasBypassIntent = useCallback(
    (
      products: SolutionSelectedProduct[],
      selectionReason?: SolutionSelectionReason | null
    ): boolean => {
      const traceTexts = [
        ...products.flatMap((product) => [product.role, product.name, product.config, product.rationale]),
        ...(selectionReason?.matching_signals || []),
        ...(selectionReason?.why_selected || []),
        ...(selectionReason?.risk_flags || []),
      ];
      return traceTexts.some((item) => /(bypass|旁路|切换|工频|检修不停机|不停机)/i.test(String(item || '')));
    },
    []
  );

  const compatibilityRuleApplies = useCallback(
    (
      rule: ProductCatalogCompatibility,
      products: SolutionSelectedProduct[],
      selectionReason?: SolutionSelectionReason | null
    ): boolean => {
      const relationType = String(rule.relation_type || 'recommended').toLowerCase();
      if (relationType === 'requires') return true;

      const triggerText = [rule.condition, rule.description, products[0]?.config].filter(Boolean).join(' ');
      if (/(旁路|切换|检修不停机|不停机|bypass)/i.test(triggerText)) {
        return hasBypassIntent(products, selectionReason);
      }
      return false;
    },
    [hasBypassIntent]
  );

  const compatibilityOptionalApplies = useCallback(
    (
      rule: ProductCatalogCompatibility,
      products: SolutionSelectedProduct[],
      selectionReason?: SolutionSelectionReason | null
    ): boolean => {
      const relationType = String(rule.relation_type || 'recommended').toLowerCase();
      if (relationType === 'requires') {
        return hasBypassIntent(products, selectionReason);
      }
      return compatibilityRuleApplies(rule, products, selectionReason);
    },
    [compatibilityRuleApplies, hasBypassIntent]
  );

  const buildCompatibilityRationale = useCallback(
    (
      rule: ProductCatalogCompatibility,
      primarySeries: ProductCatalogSeries,
      targetSeries: ProductCatalogSeries
    ): string => {
      const relationType = String(rule.relation_type || 'recommended').toLowerCase();
      const prefix =
        relationType === 'requires'
          ? `${primarySeries.series_name} 的产品族兼容规则要求补齐 ${targetSeries.series_name}。`
          : `${primarySeries.series_name} 的产品族兼容规则建议补齐 ${targetSeries.series_name}。`;
      const details = [rule.condition, rule.description].filter(Boolean).join(' ');
      return details ? `${prefix} ${details}` : prefix;
    },
    []
  );

  const reconcileCompatibilitySelection = useCallback(
    (
      products: SolutionSelectedProduct[],
      selectionReason: SolutionSelectionReason,
      options?: {
        manualReason?: string;
        enforceMissing?: boolean;
      }
    ): { products: SolutionSelectedProduct[]; selectionReason: SolutionSelectionReason } => {
      const nextProducts = [...products];
      const primary = nextProducts[0];
      const preservedWhySelected = (selectionReason.why_selected || []).filter(
        (reason) => !isGeneratedCompatibilityReason(reason)
      );
      const preservedRiskFlags = (selectionReason.risk_flags || []).filter((flag) => !isGeneratedCompatibilityRisk(flag));

      if (!primary) {
        return {
          products: nextProducts,
          selectionReason: {
            ...selectionReason,
            why_selected: dedupeStringList([...preservedWhySelected, options?.manualReason]),
            risk_flags: preservedRiskFlags,
            compatibility_actions: [],
          },
        };
      }

      const primarySeries = catalogSeriesMap.get(primary.series_code);
      const primaryFamily = primarySeries?.family_code ? catalogFamilyMap.get(primarySeries.family_code) : null;
      if (!primarySeries || !primaryFamily) {
        return {
          products: nextProducts,
          selectionReason: {
            ...selectionReason,
            why_selected: dedupeStringList([...preservedWhySelected, options?.manualReason]),
            risk_flags: preservedRiskFlags,
            compatibility_actions: [],
          },
        };
      }

      const actions: SolutionCompatibilityAction[] = [];
      for (const rule of primaryFamily.compatibilities || []) {
        const preferredCodes = dedupeStringList(rule.preferred_series_codes || []);
        const optionalCodes = dedupeStringList(rule.optional_series_codes || []);
        const applies = compatibilityRuleApplies(rule, nextProducts, selectionReason);
        const action: SolutionCompatibilityAction = {
          source_family_code: rule.source_family_code,
          target_family_code: rule.target_family_code,
          relation_type: rule.relation_type,
          condition: rule.condition,
          applies,
          preferred_series_codes: preferredCodes,
          optional_series_codes: optionalCodes,
          covered_series_codes: [],
          added_series_codes: [],
          missing_series_codes: [],
        };

        if (!applies) {
          actions.push(action);
          continue;
        }

        const candidateCodes = [...preferredCodes];
        if (compatibilityOptionalApplies(rule, nextProducts, selectionReason)) {
          candidateCodes.push(...optionalCodes);
        }

        for (const seriesCode of dedupeStringList(candidateCodes)) {
          const supportSeries = catalogSeriesMap.get(seriesCode);
          const existingIndex = nextProducts.findIndex((item) => item.series_code === seriesCode);

          if (existingIndex >= 0) {
            if (options?.enforceMissing && supportSeries && isCompatibilityGeneratedProduct(nextProducts[existingIndex])) {
              const syncedSupport = buildCatalogProduct(supportSeries, {
                role: supportSeries.series_name,
                quantity: primary.quantity || 1,
                ratedVoltage: primary.rated_voltage || supportSeries.voltage_levels[0] || '待确认',
                ratedPowerKw: primary.rated_power_kw ?? null,
              });
              nextProducts[existingIndex] = {
                ...nextProducts[existingIndex],
                ...syncedSupport,
                config: '兼容规则补充',
                rationale: buildCompatibilityRationale(rule, primarySeries, supportSeries),
              };
            }
            action.covered_series_codes = [...(action.covered_series_codes || []), seriesCode];
            continue;
          }

          if (!supportSeries) {
            action.missing_series_codes = [...(action.missing_series_codes || []), seriesCode];
            continue;
          }

          if (!options?.enforceMissing) {
            action.missing_series_codes = [...(action.missing_series_codes || []), seriesCode];
            continue;
          }

          const nextSupport = buildCatalogProduct(supportSeries, {
            role: supportSeries.series_name,
            quantity: primary.quantity || 1,
            ratedVoltage: primary.rated_voltage || supportSeries.voltage_levels[0] || '待确认',
            ratedPowerKw: primary.rated_power_kw ?? null,
          });
          nextSupport.config = '兼容规则补充';
          nextSupport.rationale = buildCompatibilityRationale(rule, primarySeries, supportSeries);
          nextProducts.push(nextSupport);
          action.added_series_codes = [...(action.added_series_codes || []), supportSeries.code];
        }

        actions.push(action);
      }

      const compatibilityWhySelected = actions.flatMap((action) => {
        const added = action.added_series_codes || [];
        const covered = action.covered_series_codes || [];
        if (added.length > 0) {
          const verb = action.relation_type === 'requires' ? '要求补齐' : '建议补齐';
          return [`产品族兼容规则${verb} ${added.join(', ')}，已自动纳入当前方案。`];
        }
        if (covered.length > 0) {
          return [`产品族兼容规则已由当前配置覆盖 ${covered.join(', ')}。`];
        }
        return [];
      });

      const compatibilityRiskFlags = actions.flatMap((action) => {
        const missing = action.missing_series_codes || [];
        if (missing.length === 0) return [];
        const prefix = action.relation_type === 'requires' ? '缺少必需配套目录项' : '缺少推荐配套目录项';
        return [`${prefix}：${missing.join(', ')}`];
      });

      return {
        products: nextProducts,
        selectionReason: {
          ...selectionReason,
          why_selected: dedupeStringList([
            ...preservedWhySelected,
            options?.manualReason,
            ...compatibilityWhySelected,
          ]),
          risk_flags: dedupeStringList([...preservedRiskFlags, ...compatibilityRiskFlags]),
          compatibility_actions: actions,
        },
      };
    },
    [
      buildCatalogProduct,
      buildCompatibilityRationale,
      catalogFamilyMap,
      catalogSeriesMap,
      compatibilityOptionalApplies,
      compatibilityRuleApplies,
    ]
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
        setCatalogFamilies([]);
        setCatalogSeries([]);
        setCatalogMaterials([]);
        setCatalogModels([]);
        setCatalogInterfaces([]);
        setMaterialReadiness(null);
        setCatalogFamilyFilter(null);
        return;
      }

      try {
        setCatalogLoading(true);
        const readinessParams = new URLSearchParams();
        readinessParams.set('project_id', projectId);
        selectedFamilyCodeList.forEach((familyCode) => readinessParams.append('family_code', familyCode));
        const readinessEndpoint = readinessParams.toString()
          ? `/catalog/material-readiness?${readinessParams.toString()}`
          : '/catalog/material-readiness';
        const [versionsRes, familiesRes, seriesRes, materialsRes, modelsRes, interfacesRes, readinessRes] = await Promise.all([
          api.get('/catalog/versions'),
          api.get('/catalog/families', {
            params: {
              published_only: true,
              catalog_version: activeCatalogVersion || undefined,
            },
          }),
          api.get('/catalog/series', {
            params: {
              published_only: true,
              catalog_version: activeCatalogVersion || undefined,
            },
          }),
          api.get('/catalog/materials'),
          api.get('/catalog/models', {
            params: {
              published_only: true,
              catalog_version: activeCatalogVersion || undefined,
            },
          }),
          api.get('/catalog/interfaces', {
            params: {
              published_only: true,
              catalog_version: activeCatalogVersion || undefined,
            },
          }),
          api.get(readinessEndpoint),
        ]);
        setCatalogVersions(versionsRes.data || []);
        setCatalogFamilies(familiesRes.data || []);
        setCatalogSeries(seriesRes.data || []);
        setCatalogMaterials(materialsRes.data || []);
        setCatalogModels(modelsRes.data || []);
        setCatalogInterfaces(interfacesRes.data || []);
        setMaterialReadiness(readinessRes.data || null);
      } catch (error) {
        toast.error(getApiErrorMessage(error, 'Failed to load catalog explorer'));
      } finally {
        setCatalogLoading(false);
      }
    };

    void fetchCatalogExplorer();
  }, [activeCatalogVersion, projectId, selectedFamilyCodeList, solutionId]);

  const runManifestPreview = useCallback(
    async (
      manifestPath: string,
      options?: {
        sourceKind?: string | null;
        silent?: boolean;
      }
    ) => {
      const normalizedPath = manifestPath.trim();
      if (!normalizedPath) {
        toast.error('请先填写 manifest 路径');
        return;
      }

      setManifestPreviewLoading(true);
      setManifestPreviewPath(normalizedPath);
      try {
        const payload: Record<string, unknown> = {
          manifest_path: normalizedPath,
          replace_existing: manifestPreviewReplaceExisting,
        };
        if (options?.sourceKind) {
          payload.source_kind = options.sourceKind;
        }
        const res = await api.post('/catalog/materials/preview-manifest', payload);
        setManifestPreview(res.data || null);
        if (!options?.silent) {
          toast.success('Manifest preview refreshed');
        }
      } catch (error) {
        toast.error(getApiErrorMessage(error, 'Failed to preview material manifest'));
      } finally {
        setManifestPreviewLoading(false);
      }
    },
    [manifestPreviewReplaceExisting]
  );

  useEffect(() => {
    if (catalogFamilyFilter && !catalogFamilies.some((item) => item.code === catalogFamilyFilter)) {
      setCatalogFamilyFilter(null);
    }
  }, [catalogFamilies, catalogFamilyFilter]);

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

      if (index === 0) {
        const reconciled = reconcileCompatibilitySelection(nextProducts, prev.selection_reason, {
          manualReason: '已手动更新主驱动参数。',
          enforceMissing: true,
        });
        return {
          ...prev,
          selected_products: reconciled.products,
          interface_plan: {
            ...prev.interface_plan,
            io_allocation: recalculateIoAllocation(reconciled.products, prev.interface_plan.io_allocation),
          },
          selection_reason: reconciled.selectionReason,
        };
      }

      const shouldRecalculateIo = patch.quantity !== undefined;
      return {
        ...prev,
        selected_products: nextProducts,
        interface_plan: shouldRecalculateIo
          ? {
              ...prev.interface_plan,
              io_allocation: recalculateIoAllocation(nextProducts, prev.interface_plan.io_allocation),
            }
          : prev.interface_plan,
      };
    });
  };

  const removeProduct = (index: number) => {
    setSolution((prev) => {
      if (!prev || index === 0) return prev;
      const trimmedProducts = prev.selected_products.filter((_, itemIndex) => itemIndex !== index);
      const reconciled = reconcileCompatibilitySelection(trimmedProducts, prev.selection_reason, {
        manualReason: `已手动移除配套设备 ${prev.selected_products[index]?.name || prev.selected_products[index]?.series_code || ''}。`,
        enforceMissing: false,
      });
      return {
        ...prev,
        selected_products: reconciled.products,
        interface_plan: {
          ...prev.interface_plan,
          io_allocation: recalculateIoAllocation(reconciled.products, prev.interface_plan.io_allocation),
        },
        selection_reason: reconciled.selectionReason,
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
      selection_reason: snapshot.selection_reason,
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
      const reconciled = reconcileCompatibilitySelection([nextPrimary, ...prev.selected_products.slice(1)], prev.selection_reason, {
        manualReason: `已手动将主驱动切换为 ${series.series_name}。`,
        enforceMissing: true,
      });
      const nextProtocol = series.communication_protocols.includes(prev.interface_plan.dcs_protocol || '')
        ? prev.interface_plan.dcs_protocol
        : series.communication_protocols[0] || prev.interface_plan.dcs_protocol;
      return {
        ...prev,
        source_catalog_version: series.catalog_version,
        selected_products: reconciled.products,
        interface_plan: {
          ...prev.interface_plan,
          dcs_protocol: nextProtocol,
          io_allocation: recalculateIoAllocation(reconciled.products, prev.interface_plan.io_allocation),
        },
        selection_reason: {
          ...reconciled.selectionReason,
          catalog_version: series.catalog_version,
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
      const reconciled = reconcileCompatibilitySelection(nextProducts, prev.selection_reason, {
        manualReason: `已手动同步目录项 ${series.series_name}。`,
        enforceMissing: true,
      });
      return {
        ...prev,
        selected_products: reconciled.products,
        interface_plan: {
          ...prev.interface_plan,
          io_allocation: recalculateIoAllocation(reconciled.products, prev.interface_plan.io_allocation),
        },
        selection_reason: reconciled.selectionReason,
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
                      {(product.model_number || product.source_material_key) && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {product.model_number && <Badge variant="secondary">{product.model_number}</Badge>}
                          {product.source_material_key && <Badge variant="outline">{product.source_material_key}</Badge>}
                        </div>
                      )}
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
                          <th className="pb-2 pr-4 font-medium">Model</th>
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
                            <td className="py-3 pr-4">{product.model_number || '-'}</td>
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

            {snapshotCatalogModelMatches.length > 0 && (
              <div className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-foreground">Catalog Model Evidence</p>
                  <Badge variant="outline">{snapshotCatalogModelMatches.length} 条</Badge>
                </div>
                <div className="space-y-3">
                  {snapshotCatalogModelMatches.map((item) => (
                    <div
                      key={`${item.series_code}-${item.model_number}`}
                      className="rounded-md border border-border bg-[#fbfcf8] p-4"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-medium text-foreground">{item.model_number}</p>
                          <p className="mt-1 text-sm leading-6 text-muted-foreground">
                            {item.series_name || item.series_code}
                          </p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {item.rated_voltage && <Badge variant="outline">{item.rated_voltage}</Badge>}
                          {typeof item.rated_power_kw === 'number' && Number.isFinite(item.rated_power_kw) && (
                            <Badge variant="outline">{formatPower(item.rated_power_kw)}</Badge>
                          )}
                          {item.rated_current && <Badge variant="outline">{item.rated_current}</Badge>}
                          {item.source_material_key && <Badge variant="secondary">{item.source_material_key}</Badge>}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {snapshotCatalogInterfaceEntries.length > 0 && (
              <div className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-foreground">Catalog Interface Evidence</p>
                  <Badge variant="outline">{snapshotCatalogInterfaceEntries.length} 条</Badge>
                </div>
                <div className="space-y-3">
                  {snapshotCatalogInterfaceEntries.map((item, index) => (
                    <div
                      key={`${item.series_code}-${item.interface_type}-${item.protocol || 'none'}-${index}`}
                      className="rounded-md border border-border bg-[#fbfcf8] p-4"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-medium text-foreground">
                            {item.series_name || item.series_code}
                          </p>
                          <p className="mt-1 text-sm leading-6 text-muted-foreground">
                            {formatInterfaceType(item.interface_type)}
                          </p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {item.protocol && <Badge variant="outline">{item.protocol}</Badge>}
                          {item.source_material_key && <Badge variant="secondary">{item.source_material_key}</Badge>}
                        </div>
                      </div>
                      {summarizeStringList(item.signal_summary, 4).length > 0 && (
                        <div className="mt-3 space-y-1 text-sm leading-6 text-muted-foreground">
                          {summarizeStringList(item.signal_summary, 4).map((summary) => (
                            <p key={`${item.series_code}-${item.interface_type}-${summary}`}>{summary}</p>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {(solution.selection_reason.compatibility_actions || []).length > 0 && (
              <div className="space-y-3">
                <p className="text-sm font-semibold text-foreground">Compatibility Actions</p>
                <div className="space-y-3">
                  {(solution.selection_reason.compatibility_actions || []).map((action, index) => {
                    const targetFamily = catalogFamilyMap.get(action.target_family_code);
                    const status = getCompatibilityStatus(action);
                    return (
                      <div
                        key={`${action.source_family_code}-${action.target_family_code}-${action.relation_type}-${index}`}
                        className="rounded-md border border-border bg-[#fbfcf8] p-4"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={getCompatibilityVariant(action.relation_type)}>
                            {formatCompatibilityRelation(action.relation_type)}
                          </Badge>
                          <Badge variant={status.variant}>{status.label}</Badge>
                          <span className="text-sm font-medium text-foreground">
                            {targetFamily?.display_name || targetFamily?.name || action.target_family_code}
                          </span>
                        </div>

                        {action.condition && (
                          <p className="mt-2 text-sm leading-6 text-muted-foreground">{action.condition}</p>
                        )}

                        <div className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">
                          {(action.preferred_series_codes || []).length > 0 && (
                            <p>Preferred: {(action.preferred_series_codes || []).join(', ')}</p>
                          )}
                          {(action.optional_series_codes || []).length > 0 && (
                            <p>Optional: {(action.optional_series_codes || []).join(', ')}</p>
                          )}
                          {(action.covered_series_codes || []).length > 0 && (
                            <p>Covered: {(action.covered_series_codes || []).join(', ')}</p>
                          )}
                          {(action.added_series_codes || []).length > 0 && (
                            <p>Added: {(action.added_series_codes || []).join(', ')}</p>
                          )}
                          {(action.missing_series_codes || []).length > 0 && (
                            <p className="text-warning">Missing: {(action.missing_series_codes || []).join(', ')}</p>
                          )}
                          {!action.applies && <p>当前场景未触发这条兼容规则。</p>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

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

            {materialReadiness && (
              <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      {materialReadiness.gate_passed ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                      ) : (
                        <ShieldAlert className="h-4 w-4 text-amber-600" />
                      )}
                      <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Material Readiness Gate</p>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">
                      这里明确判断当前资料库是否满足进入 Phase 1 / 2 / 3 的最小门槛。当前优先按方案实际选中的产品族收口，不再只看全局口径。
                    </p>
                  </div>
                  <Badge variant={materialReadiness.gate_passed ? 'success' : 'warning'}>
                    {materialReadiness.gate_passed ? 'Gate Passed' : 'Gate Blocked'}
                  </Badge>
                </div>

                <div className="mt-4 flex flex-wrap gap-2">
                  <Badge variant="outline">{materialReadiness.available_material_count} 份 Gate Eligible 资料</Badge>
                  <Badge variant="outline">{catalogMaterials.length} 份 Registry 资料</Badge>
                  <Badge variant="outline">{syntheticMaterials.length} 份 Synthetic</Badge>
                  <Badge variant="outline">Core Manuals {materialReadiness.required_core_manual_family_count}</Badge>
                  <Badge variant={selectedFamilyCodeList.length > 0 ? 'success' : 'outline'}>
                    {selectedFamilyCodeList.length > 0 ? 'Project Scoped' : 'Default Scope'}
                  </Badge>
                  {materialReadiness.target_family_codes.map((familyCode) => (
                    <Badge key={`readiness-family-${familyCode}`} variant="secondary">
                      {catalogFamilyMap.get(familyCode)?.display_name || catalogFamilyMap.get(familyCode)?.name || familyCode}
                    </Badge>
                  ))}
                </div>

                <div className="mt-4 space-y-2">
                  {materialReadiness.checklist.map((item) => (
                    <div key={item.check_key} className="rounded-md border border-border bg-white px-3 py-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-medium text-foreground">{item.label}</p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {item.actual_count} / {item.required_count}
                            {item.matched_document_names.length > 0 ? ` · ${item.matched_document_names.length} 份已匹配资料` : ''}
                          </p>
                        </div>
                        <Badge variant={item.passed ? 'success' : 'warning'}>
                          {item.passed ? 'Ready' : 'Missing'}
                        </Badge>
                      </div>
                      {item.matched_family_codes.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {item.matched_family_codes.map((familyCode) => (
                            <Badge key={`${item.check_key}-${familyCode}`} variant="outline">
                              {catalogFamilyMap.get(familyCode)?.display_name || catalogFamilyMap.get(familyCode)?.name || familyCode}
                            </Badge>
                          ))}
                        </div>
                      )}
                      {item.matched_document_names.length > 0 && (
                        <div className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">
                          {item.matched_document_names.slice(0, 3).map((documentName) => (
                            <p key={`${item.check_key}-${documentName}`}>{documentName}</p>
                          ))}
                          {item.matched_document_names.length > 3 && (
                            <p>+ {item.matched_document_names.length - 3} 份资料</p>
                          )}
                        </div>
                      )}
                      {item.missing_detail && (
                        <p className="mt-2 text-sm leading-6 text-amber-700">{item.missing_detail}</p>
                      )}
                    </div>
                  ))}
                </div>

                <div className="mt-4 space-y-2">
                  {materialReadiness.phase_allowances.map((item) => (
                    <div key={item.phase} className="rounded-md border border-border bg-white px-3 py-3">
                      <div className="flex items-start justify-between gap-3">
                        <p className="text-sm font-medium text-foreground">{item.label}</p>
                        <Badge variant={item.allowed ? 'success' : 'warning'}>
                          {item.allowed ? 'Allowed' : 'Blocked'}
                        </Badge>
                      </div>
                      <p className="mt-2 text-sm leading-6 text-muted-foreground">{item.reason}</p>
                    </div>
                  ))}
                </div>

                {!materialReadiness.gate_passed && materialReadiness.missing_items.length > 0 && (
                  <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-3 text-sm leading-6 text-amber-800">
                    当前缺口：{materialReadiness.missing_items.join('、')}
                  </div>
                )}
              </div>
            )}

            <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <Database className="h-4 w-4 text-primary" />
                    <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Manifest Intake Preview</p>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-muted-foreground">
                    这里直接走 `preview-manifest` dry-run。客户资料一到，可以先看可导入量、重复 key、推断字段和 gate 影响，再决定是否正式入库。
                  </p>
                </div>
                {manifestPreview && (
                  <Badge variant={manifestPreview.import_blocked ? 'warning' : 'success'}>
                    {manifestPreview.import_blocked ? 'Preview Blocked' : 'Preview Ready'}
                  </Badge>
                )}
              </div>

              <div className="mt-4 space-y-3 rounded-md border border-border bg-white p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant={manifestPreviewSourceKind === 'auto' ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setManifestPreviewSourceKind('auto')}
                  >
                    Auto Detect
                  </Button>
                  <Button
                    type="button"
                    variant={manifestPreviewSourceKind === 'customer_provided' ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setManifestPreviewSourceKind('customer_provided')}
                  >
                    Force Customer
                  </Button>
                  <Button
                    type="button"
                    variant={manifestPreviewSourceKind === 'synthetic_test_only' ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setManifestPreviewSourceKind('synthetic_test_only')}
                  >
                    Force Synthetic
                  </Button>
                  <span className="text-xs leading-5 text-muted-foreground">
                    不再内置本地样本路径；直接粘贴当前要检查的 manifest 绝对路径。
                  </span>
                </div>
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
                  <Input
                    value={manifestPreviewPath}
                    onChange={(event) => {
                      setManifestPreviewPath(event.target.value);
                    }}
                    placeholder="/abs/path/to/customer-material-manifest.json"
                    className="h-10"
                  />
                  <Button
                    type="button"
                    onClick={() =>
                      void runManifestPreview(manifestPreviewPath, {
                        sourceKind: manifestPreviewSourceKind === 'auto' ? undefined : manifestPreviewSourceKind,
                      })
                    }
                    disabled={manifestPreviewLoading}
                  >
                    {manifestPreviewLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                    Run Preview
                  </Button>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant={manifestPreviewReplaceExisting ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setManifestPreviewReplaceExisting(true)}
                  >
                    Replace Existing
                  </Button>
                  <Button
                    type="button"
                    variant={!manifestPreviewReplaceExisting ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setManifestPreviewReplaceExisting(false)}
                  >
                    Skip Existing
                  </Button>
                </div>
              </div>

              {manifestPreview ? (
                <div className="mt-4 space-y-4">
                  <div className="grid gap-3 lg:grid-cols-2">
                    <div className="rounded-md border border-border bg-white p-3">
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Preview Summary</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Badge variant="outline">{manifestPreview.total_entry_count} entries</Badge>
                        <Badge variant="outline">{manifestPreview.unique_material_key_count} unique keys</Badge>
                        <Badge variant="outline">{manifestPreview.would_import_count} would import</Badge>
                        <Badge variant="outline">{manifestPreview.existing_material_count} existing</Badge>
                        <Badge variant="outline">{manifestPreview.gate_ready_material_count} gate-ready</Badge>
                        <Badge variant="outline">{manifestPreview.non_synthetic_material_count} non-synthetic</Badge>
                      </div>
                      <div className="mt-3 space-y-1 text-sm leading-6 text-muted-foreground">
                        <p className="break-all">{manifestPreview.manifest_path}</p>
                        <p>source_kind: {formatMaterialSourceKind(manifestPreview.source_kind)}</p>
                        <p>mode: {manifestPreview.replace_existing ? 'replace existing' : 'skip existing'}</p>
                      </div>
                    </div>
                    <div className="rounded-md border border-border bg-white p-3">
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Preview Diagnostics</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Badge variant={manifestPreview.duplicate_material_key_count > 0 ? 'warning' : 'success'}>
                          duplicate keys {manifestPreview.duplicate_material_key_count}
                        </Badge>
                        <Badge variant={manifestPreview.missing_source_path_count > 0 ? 'warning' : 'success'}>
                          missing path {manifestPreview.missing_source_path_count}
                        </Badge>
                        <Badge variant={manifestPreview.missing_source_file_count > 0 ? 'warning' : 'success'}>
                          missing file {manifestPreview.missing_source_file_count}
                        </Badge>
                        <Badge variant={manifestPreview.inferred_family_count > 0 ? 'warning' : 'success'}>
                          inferred family {manifestPreview.inferred_family_count}
                        </Badge>
                        <Badge variant={manifestPreview.inferred_material_type_count > 0 ? 'warning' : 'success'}>
                          inferred type {manifestPreview.inferred_material_type_count}
                        </Badge>
                      </div>
                      {manifestPreview.duplicate_material_keys.length > 0 && (
                        <div className="mt-3 space-y-1 text-xs leading-5 text-muted-foreground">
                          {manifestPreview.duplicate_material_keys.slice(0, 4).map((materialKey) => (
                            <p key={`duplicate-key-${materialKey}`}>{materialKey}</p>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="grid gap-3 lg:grid-cols-2">
                    <div className="rounded-md border border-border bg-white p-3">
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">By Source</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {manifestPreviewSourceSummary.length > 0 ? (
                          manifestPreviewSourceSummary.map(([sourceKind, count]) => (
                            <Badge key={`manifest-preview-source-${sourceKind}`} variant={getMaterialSourceVariant(sourceKind)}>
                              {formatMaterialSourceKind(sourceKind)} · {count}
                            </Badge>
                          ))
                        ) : (
                          <span className="text-sm text-muted-foreground">当前 preview 还没有 source 汇总。</span>
                        )}
                      </div>
                    </div>
                    <div className="rounded-md border border-border bg-white p-3">
                      <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Issues</p>
                      <div className="mt-3 space-y-2">
                        {manifestPreview.issues.length > 0 ? (
                          manifestPreview.issues.map((issue, index) => (
                            <div key={`${issue.issue_type}-${index}`} className="rounded-md border border-border bg-[#fbfcf8] px-3 py-2">
                              <div className="flex items-center gap-2">
                                <Badge variant={issue.severity === 'blocking' || issue.severity === 'warning' ? 'warning' : 'outline'}>
                                  {issue.severity}
                                </Badge>
                                <p className="text-sm font-medium text-foreground">{formatManifestIssueType(issue.issue_type)}</p>
                              </div>
                              <p className="mt-2 text-sm leading-6 text-muted-foreground">{issue.message}</p>
                            </div>
                          ))
                        ) : (
                          <div className="rounded-md border border-dashed border-border bg-[#fbfcf8] px-3 py-2 text-sm text-muted-foreground">
                            当前 preview 没有发现阻断或提醒项。
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="rounded-md border border-border bg-white p-3">
                    <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Entry Preview</p>
                    <div className="mt-3 space-y-3">
                      {manifestPreviewEntryPreview.length > 0 ? (
                        manifestPreviewEntryPreview.map((entry) => (
                          <div key={`preview-entry-${entry.material_key}`} className="rounded-md border border-border bg-[#fbfcf8] p-3">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <p className="text-sm font-semibold text-foreground">{entry.document_name}</p>
                                <p className="mt-1 text-xs text-muted-foreground">{entry.material_key}</p>
                              </div>
                              <Badge variant={entry.counted_toward_gate ? 'success' : 'warning'}>
                                {entry.counted_toward_gate ? 'Gate Ready' : 'Not Counted'}
                              </Badge>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2">
                              <Badge variant="outline">{formatMaterialType(entry.material_type)}</Badge>
                              <Badge variant="outline">
                                {catalogFamilyMap.get(entry.family_code || '')?.display_name ||
                                  catalogFamilyMap.get(entry.family_code || '')?.name ||
                                  entry.family_code ||
                                  'Unclassified'}
                              </Badge>
                              <Badge variant={getMaterialSourceVariant(entry.source_kind)}>
                                {formatMaterialSourceKind(entry.source_kind)}
                              </Badge>
                              <Badge variant={entry.existing_material ? 'outline' : 'success'}>
                                {entry.existing_material ? 'Existing' : 'New'}
                              </Badge>
                              <Badge variant={entry.duplicate_material_key ? 'warning' : 'outline'}>
                                {entry.duplicate_material_key ? 'Duplicate Key' : 'Unique Key'}
                              </Badge>
                            </div>
                            <div className="mt-3 space-y-1 text-xs leading-5 text-muted-foreground">
                              <p>
                                explicit fields: family={entry.explicit_family_code ? 'yes' : 'no'} / type=
                                {entry.explicit_material_type ? 'yes' : 'no'} / status=
                                {entry.explicit_availability_status ? 'yes' : 'no'}
                              </p>
                              <p>source file exists: {entry.source_path_exists === null ? '-' : entry.source_path_exists ? 'yes' : 'no'}</p>
                              {entry.source_path && <p className="break-all">{entry.source_path}</p>}
                            </div>
                            {entry.issues.length > 0 && (
                              <div className="mt-3 space-y-1 text-sm leading-6 text-amber-700">
                                {entry.issues.map((issue) => (
                                  <p key={`${entry.material_key}-${issue}`}>{issue}</p>
                                ))}
                              </div>
                            )}
                          </div>
                        ))
                      ) : (
                        <div className="rounded-md border border-dashed border-border bg-[#fbfcf8] px-3 py-2 text-sm text-muted-foreground">
                          当前 preview 还没有 entry 详情。
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="mt-4 rounded-md border border-dashed border-border bg-white px-4 py-3 text-sm text-muted-foreground">
                  还没有 preview 结果。先选择预设 manifest，或填入客户 manifest 的绝对路径后执行预检。
                </div>
              )}
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
            {catalogFamilies.length > 0 && (
              <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <GitBranch className="h-4 w-4 text-primary" />
                      <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Family Map</p>
                    </div>
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                      展示首批产品族、别名与配套关系。点击某个产品族后，目录项会按 family 聚焦，便于手动调整当前方案。
                    </p>
                  </div>
                  {catalogFamilyFilter && (
                    <Button type="button" variant="outline" size="sm" onClick={() => setCatalogFamilyFilter(null)}>
                      显示全部目录项
                    </Button>
                  )}
                </div>

                <div className="mt-4 grid gap-4 lg:grid-cols-2">
                  {catalogFamilies.map((family) => {
                    const isFiltered = catalogFamilyFilter === family.code;
                    const isInSnapshot = selectedFamilyCodes.has(family.code);
                    return (
                      <div
                        key={family.code}
                        className={`rounded-md border p-4 ${
                          isFiltered || isInSnapshot ? 'border-primary/30 bg-primary/8' : 'border-border bg-white'
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="text-sm font-semibold text-foreground">{family.display_name || family.name}</p>
                              <Badge variant={family.status === 'active' ? 'success' : 'outline'}>
                                {family.status === 'active' ? 'Active' : 'Planned'}
                              </Badge>
                              {isInSnapshot && <Badge variant="warning">In Snapshot</Badge>}
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">{family.code}</p>
                          </div>
                          <Badge variant="outline">{family.series_count} 个目录项</Badge>
                        </div>

                        <p className="mt-3 text-sm leading-6 text-muted-foreground">
                          {family.description || '当前产品族还没有补充说明。'}
                        </p>

                        <div className="mt-4 space-y-3 text-sm">
                          <div>
                            <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Aliases</p>
                            <div className="mt-2 flex flex-wrap gap-2">
                              {family.aliases.length > 0 ? (
                                family.aliases.slice(0, 6).map((alias) => (
                                  <Badge key={`${family.code}-${alias.alias}`} variant="secondary">
                                    {alias.alias}
                                  </Badge>
                                ))
                              ) : (
                                <span className="text-muted-foreground">无</span>
                              )}
                            </div>
                          </div>

                          <div>
                            <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Compatibility</p>
                            <div className="mt-2 space-y-2">
                              {family.compatibilities.length > 0 ? (
                                family.compatibilities.map((item) => {
                                  const targetFamily = catalogFamilyMap.get(item.target_family_code);
                                  return (
                                    <div
                                      key={`${family.code}-${item.target_family_code}-${item.relation_type}`}
                                      className="rounded-md border border-border bg-[#fbfcf8] px-3 py-3"
                                    >
                                      <div className="flex flex-wrap items-center gap-2">
                                        <Badge variant={getCompatibilityVariant(item.relation_type)}>
                                          {formatCompatibilityRelation(item.relation_type)}
                                        </Badge>
                                        <span className="text-sm font-medium text-foreground">
                                          {targetFamily?.display_name || targetFamily?.name || item.target_family_code}
                                        </span>
                                      </div>
                                      {(item.condition || item.description) && (
                                        <p className="mt-2 text-sm leading-6 text-muted-foreground">
                                          {[item.condition, item.description].filter(Boolean).join(' | ')}
                                        </p>
                                      )}
                                      {item.preferred_series_codes.length > 0 && (
                                        <div className="mt-2">
                                          <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Preferred Series</p>
                                          <div className="mt-2 flex flex-wrap gap-2">
                                            {item.preferred_series_codes.map((code) => (
                                              <Badge key={`${family.code}-preferred-${code}`} variant="secondary">
                                                {code}
                                              </Badge>
                                            ))}
                                          </div>
                                        </div>
                                      )}
                                      {item.optional_series_codes.length > 0 && (
                                        <div className="mt-2">
                                          <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Optional Series</p>
                                          <div className="mt-2 flex flex-wrap gap-2">
                                            {item.optional_series_codes.map((code) => (
                                              <Badge key={`${family.code}-optional-${code}`} variant="outline">
                                                {code}
                                              </Badge>
                                            ))}
                                          </div>
                                        </div>
                                      )}
                                    </div>
                                  );
                                })
                              ) : (
                                <div className="rounded-md border border-dashed border-border bg-[#fbfcf8] px-3 py-2 text-sm text-muted-foreground">
                                  当前产品族还没有补充兼容关系。
                                </div>
                              )}
                            </div>
                          </div>

                          <div className="flex flex-wrap gap-2">
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => setCatalogFamilyFilter(isFiltered ? null : family.code)}
                            >
                              {isFiltered ? '查看全部目录项' : '筛选该产品族'}
                            </Button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {catalogFamilyFilter && (
              <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-[#fbfcf8] px-4 py-3 text-sm text-muted-foreground">
                <span>当前筛选：</span>
                <Badge variant="success">
                  {catalogFamilyMap.get(catalogFamilyFilter)?.display_name ||
                    catalogFamilyMap.get(catalogFamilyFilter)?.name ||
                    catalogFamilyFilter}
                </Badge>
                <span>{filteredCatalogSeries.length} 个目录项</span>
                <span>{filteredCatalogModels.length} 个型号</span>
                <span>{filteredCatalogInterfaces.length} 个接口</span>
              </div>
            )}

            <div className="grid gap-3 lg:grid-cols-2">
              <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Model Registry</p>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">
                      当前目录已沉淀可追溯的最小型号层，可直接看到型号、额定电压/功率和样本来源键。
                    </p>
                  </div>
                  <Badge variant="outline">{filteredCatalogModels.length} 个型号</Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {filteredCatalogModels.length > 0 ? (
                    filteredCatalogModels.slice(0, 8).map((item) => (
                      <Badge key={`${item.series_code}-${item.model_number}`} variant="secondary">
                        {item.model_number}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-sm text-muted-foreground">当前筛选下还没有型号条目。</span>
                  )}
                </div>
              </div>

              <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Interface Registry</p>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">
                      通讯协议和最小 I/O/联锁骨架已经进入目录，可按系列回看接口来源。
                    </p>
                  </div>
                  <Badge variant="outline">{filteredCatalogInterfaces.length} 个接口</Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {interfaceTypeSummary.length > 0 ? (
                    interfaceTypeSummary.map(([interfaceType, count]) => (
                      <Badge key={`interface-type-${interfaceType}`} variant="secondary">
                        {formatInterfaceType(interfaceType)} · {count}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-sm text-muted-foreground">当前筛选下还没有接口条目。</span>
                  )}
                </div>
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              {filteredCatalogSeries.map((series) => {
                const seriesModels = catalogModelsBySeries.get(series.code) || [];
                const seriesInterfaces = catalogInterfacesBySeries.get(series.code) || [];
                return (
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
                      {series.family_code && (
                        <Badge variant="outline">
                          {catalogFamilyMap.get(series.family_code)?.display_name || series.family_code}
                        </Badge>
                      )}
                      {series.voltage_levels.map((item) => (
                        <Badge key={`${series.code}-${item}`} variant="outline">
                          {item}
                        </Badge>
                      ))}
                      <Badge variant="outline">Models {seriesModels.length}</Badge>
                      <Badge variant="outline">Interfaces {seriesInterfaces.length}</Badge>
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
                        <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Model Registry</p>
                        <div className="mt-2 space-y-2">
                          {seriesModels.length > 0 ? (
                            seriesModels.map((model) => (
                              <div key={`${series.code}-${model.model_number}`} className="rounded-md border border-border bg-white px-3 py-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <p className="text-sm font-medium text-foreground">{model.model_number}</p>
                                  <div className="flex flex-wrap gap-2">
                                    {model.rated_voltage && <Badge variant="outline">{model.rated_voltage}</Badge>}
                                    {typeof model.rated_power_kw === 'number' && Number.isFinite(model.rated_power_kw) && (
                                      <Badge variant="outline">{formatPower(model.rated_power_kw)}</Badge>
                                    )}
                                    {model.rated_current && <Badge variant="outline">{model.rated_current}</Badge>}
                                  </div>
                                </div>
                                {summarizeRecord(model.specs, 3).length > 0 && (
                                  <div className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">
                                    {summarizeRecord(model.specs, 3).map((item) => (
                                      <p key={`${model.model_number}-${item}`}>{item}</p>
                                    ))}
                                  </div>
                                )}
                                {model.source_material_key && (
                                  <p className="mt-2 text-xs text-muted-foreground">source: {model.source_material_key}</p>
                                )}
                              </div>
                            ))
                          ) : (
                            <div className="rounded-md border border-dashed border-border bg-white px-3 py-2 text-sm text-muted-foreground">
                              当前目录项还没有沉淀型号条目。
                            </div>
                          )}
                        </div>
                      </div>

                      <div>
                        <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Interface Registry</p>
                        <div className="mt-2 space-y-2">
                          {seriesInterfaces.length > 0 ? (
                            seriesInterfaces.map((item) => (
                              <div key={`${series.code}-${item.interface_type}-${item.sort_order}`} className="rounded-md border border-border bg-white px-3 py-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <p className="text-sm font-medium text-foreground">{formatInterfaceType(item.interface_type)}</p>
                                    {item.protocol && <Badge variant="outline">{item.protocol}</Badge>}
                                  </div>
                                  {item.source_material_key && <Badge variant="secondary">{item.source_material_key}</Badge>}
                                </div>
                                {summarizeRecord(item.signal_spec, 3).length > 0 && (
                                  <div className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">
                                    {summarizeRecord(item.signal_spec, 3).map((summary) => (
                                      <p key={`${series.code}-${item.interface_type}-${summary}`}>{summary}</p>
                                    ))}
                                  </div>
                                )}
                                {item.notes && <p className="mt-2 text-sm leading-6 text-muted-foreground">{item.notes}</p>}
                              </div>
                            ))
                          ) : (
                            <div className="rounded-md border border-dashed border-border bg-white px-3 py-2 text-sm text-muted-foreground">
                              当前目录项还没有沉淀接口条目。
                            </div>
                          )}
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
                );
              })}
            </div>

            {!catalogLoading && catalogSeries.length === 0 && (
              <div className="rounded-md border border-dashed border-border bg-[#fbfcf8] px-4 py-3 text-sm text-muted-foreground">
                当前没有可展示的发布目录数据。
              </div>
            )}

            {!catalogLoading && catalogSeries.length > 0 && filteredCatalogSeries.length === 0 && (
              <div className="rounded-md border border-dashed border-border bg-[#fbfcf8] px-4 py-3 text-sm text-muted-foreground">
                当前产品族筛选下没有目录项。
              </div>
            )}

            <div className="rounded-md border border-border bg-[#fbfcf8] p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <Database className="h-4 w-4 text-primary" />
                    <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Materials Registry</p>
                  </div>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                    这里展示 Phase 0B 的资料台账承接层。导入 manifest 之后，真实方案样本、客户手册、BOM、点表和规则资料都能按同一入口追踪。
                  </p>
                </div>
                <Badge variant="outline">{catalogMaterials.length} 份资料</Badge>
              </div>

              <div className="mt-4 grid gap-3 lg:grid-cols-2">
                <div className="rounded-md border border-border bg-white p-3">
                  <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">By Type</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {materialTypeSummary.length > 0 ? (
                      materialTypeSummary.map(([materialType, count]) => (
                        <Badge key={`material-type-${materialType}`} variant="secondary">
                          {formatMaterialType(materialType)} · {count}
                        </Badge>
                      ))
                    ) : (
                      <span className="text-sm text-muted-foreground">当前还没有导入资料台账。</span>
                    )}
                  </div>
                </div>
                <div className="rounded-md border border-border bg-white p-3">
                  <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">By Status</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {materialStatusSummary.length > 0 ? (
                      materialStatusSummary.map(([status, count]) => (
                        <Badge key={`material-status-${status}`} variant={getMaterialStatusVariant(status)}>
                          {formatMaterialStatus(status)} · {count}
                        </Badge>
                      ))
                    ) : (
                      <span className="text-sm text-muted-foreground">当前还没有导入资料台账。</span>
                    )}
                  </div>
                </div>
                <div className="rounded-md border border-border bg-white p-3 lg:col-span-2">
                  <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">By Source</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {materialSourceSummary.length > 0 ? (
                      materialSourceSummary.map(([sourceKind, count]) => (
                        <Badge key={`material-source-${sourceKind}`} variant={getMaterialSourceVariant(sourceKind)}>
                          {formatMaterialSourceKind(sourceKind)} · {count}
                        </Badge>
                      ))
                    ) : (
                      <span className="text-sm text-muted-foreground">当前还没有导入资料台账。</span>
                    )}
                  </div>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant={materialSourceFilter === 'gate_eligible' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setMaterialSourceFilter('gate_eligible')}
                >
                  只看 Gate Eligible
                </Button>
                <Button
                  type="button"
                  variant={materialSourceFilter === 'all' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setMaterialSourceFilter('all')}
                >
                  查看全部
                </Button>
                <Button
                  type="button"
                  variant={materialSourceFilter === 'synthetic_only' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setMaterialSourceFilter('synthetic_only')}
                >
                  只看 Synthetic
                </Button>
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-white px-3 py-2 text-sm text-muted-foreground">
                  <span>当前显示：</span>
                  <Badge variant="outline">{filteredCatalogMaterials.length} 份</Badge>
                  <span>真实资料默认计入 gate，synthetic 只作研发验证。</span>
                </div>
              </div>

              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                {filteredCatalogMaterials.map((material) => {
                  const family = material.family_code ? catalogFamilyMap.get(material.family_code) : null;
                  const gateEligible = isGateEligibleMaterial(material);
                  return (
                    <div key={material.material_key} className="rounded-md border border-border bg-white p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-foreground">{material.document_name}</p>
                          <p className="mt-1 text-xs text-muted-foreground">{material.material_key}</p>
                        </div>
                        <Badge variant={getMaterialStatusVariant(material.availability_status)}>
                          {formatMaterialStatus(material.availability_status)}
                        </Badge>
                      </div>

                      <div className="mt-3 flex flex-wrap gap-2">
                        <Badge variant="outline">{formatMaterialType(material.material_type)}</Badge>
                        <Badge variant="outline">{family?.display_name || family?.name || material.family_code || 'Unclassified'}</Badge>
                        <Badge variant={getMaterialSourceVariant(material.source_kind)}>
                          {formatMaterialSourceKind(material.source_kind)}
                        </Badge>
                        {material.file_format && <Badge variant="outline">{material.file_format}</Badge>}
                        {material.assigned_track && <Badge variant="outline">{material.assigned_track}</Badge>}
                        <Badge variant={gateEligible ? 'success' : 'warning'}>
                          {gateEligible ? 'Counted Toward Gate' : 'Not Counted Toward Gate'}
                        </Badge>
                      </div>

                      {(material.notes || material.source_path) && (
                        <div className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">
                          {material.notes && <p>{material.notes}</p>}
                          {material.source_path && <p className="break-all text-xs">{material.source_path}</p>}
                        </div>
                      )}

                      {material.tags.length > 0 && (
                        <div className="mt-3">
                          <p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Tags</p>
                          <div className="mt-2 flex flex-wrap gap-2">
                            {material.tags.map((tag) => (
                              <Badge key={`${material.material_key}-${tag}`} variant="secondary">
                                {tag}
                              </Badge>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {!catalogLoading && catalogMaterials.length === 0 && (
                <div className="mt-4 rounded-md border border-dashed border-border bg-white px-4 py-3 text-sm text-muted-foreground">
                  当前资料台账还是空的。先运行 materials manifest 导入脚本，再回到这里查看 family 归类和状态汇总。
                </div>
              )}

              {!catalogLoading && catalogMaterials.length > 0 && filteredCatalogMaterials.length === 0 && (
                <div className="mt-4 rounded-md border border-dashed border-border bg-white px-4 py-3 text-sm text-muted-foreground">
                  当前筛选条件下没有资料台账条目。
                </div>
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
