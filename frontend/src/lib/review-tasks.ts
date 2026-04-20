import { ReviewTask, ReviewTaskPayload, ValidationIssue } from '@/lib/types';

export type ReviewTaskActionStatus = 'resolved' | 'rejected';

export interface ReviewTaskSuggestedBlock {
  key: string;
  title: string;
  kind: 'paragraph' | 'table' | 'list';
  markdown: string;
}

export interface ReviewTaskRevisionBrief {
  checklist: string[];
  contentAnchors: string[];
  resolutionTemplate: string;
  suggestedBlocks: ReviewTaskSuggestedBlock[];
}

export interface SectionReviewBatchBrief {
  taskCodes: string[];
  checklist: string[];
  contentAnchors: string[];
  resolutionTemplate: string;
  suggestedBlocks: ReviewTaskSuggestedBlock[];
}

export interface ReviewTaskPlaceholderSuggestion {
  key: string;
  label: string;
  value: string;
  placeholders: string[];
  matchTerms?: string[];
}

interface SelectedProductSummaryItem {
  role: string;
  name: string;
  quantity: string;
}

export function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function toStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item).trim()).filter(Boolean) : [];
}

export function toRecordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item) => item && typeof item === 'object' && !Array.isArray(item)) as Array<Record<string, unknown>>
    : [];
}

export function issueFromTaskPayload(payload: ReviewTaskPayload): ValidationIssue {
  return {
    code: String(payload.code || ''),
    message: String(payload.message || ''),
    level: typeof payload.level === 'string' ? payload.level : undefined,
    section_id: typeof payload.section_id === 'string' ? payload.section_id : undefined,
    section_title: typeof payload.section_title === 'string' ? payload.section_title : undefined,
    suggested_action: typeof payload.suggested_action === 'string' ? payload.suggested_action : undefined,
    details: toRecord(payload.details),
  };
}

export function buildReviewResolutionPayload(
  task: ReviewTask,
  note: string,
  status: ReviewTaskActionStatus,
): unknown {
  const trimmedNote = note.trim();
  const issue = issueFromTaskPayload(task.payload || {});
  const basePayload = {
    source: 'review_task_ui',
    task_type: task.task_type,
    code: issue.code || undefined,
    section_id: issue.section_id || undefined,
    suggested_action: issue.suggested_action || undefined,
    action: status === 'resolved' ? 'section_updated' : 'needs_follow_up',
    note: trimmedNote,
  };

  if (task.task_type === 'param_conflict') {
    return {
      ...basePayload,
      action: status === 'resolved' ? 'accepted_value' : 'needs_clarification',
      value: trimmedNote,
    };
  }

  return basePayload;
}

function compactStrings(values: unknown[]): string[] {
  const seen = new Set<string>();
  const items: string[] = [];
  for (const value of values) {
    const normalized = String(value || '').trim();
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    items.push(normalized);
  }
  return items;
}

function parseSelectedProductsSummary(value: unknown): SelectedProductSummaryItem[] {
  const normalized = String(value ?? '').trim();
  if (!normalized) {
    return [];
  }

  return compactStrings(normalized.split(/[；;]+/))
    .map((entry) => {
      const separatorIndex = entry.search(/[：:]/);
      const role = separatorIndex >= 0 ? entry.slice(0, separatorIndex).trim() : '';
      const detail = separatorIndex >= 0 ? entry.slice(separatorIndex + 1).trim() : entry.trim();
      const quantityMatch = detail.match(/\s*[xX×]\s*([0-9]+(?:\.[0-9]+)?(?:\s*[台套个组面项]?)?)\s*$/);
      const quantity = quantityMatch?.[1]?.trim() || '1';
      const name = (quantityMatch ? detail.slice(0, quantityMatch.index) : detail).trim();
      if (!name) {
        return null;
      }
      return {
        role: role || '设备',
        name,
        quantity,
      };
    })
    .filter((item): item is SelectedProductSummaryItem => Boolean(item));
}

function formatIoAllocation(value: unknown): string {
  const io = toRecord(value);
  const items = ['DI', 'DO', 'AI', 'AO']
    .map((key) => {
      const count = io[key];
      if (count === null || count === undefined || count === '') {
        return '';
      }
      return `${key} ${count}`;
    })
    .filter(Boolean);
  return items.join(' / ');
}

function toMarkdownCell(value: unknown, fallback = '待补充'): string {
  const normalized = String(value ?? '')
    .trim()
    .replace(/\|/g, '/')
    .replace(/\r?\n+/g, ' ');
  return normalized || fallback;
}

function toDisplayToken(value: unknown): string {
  const normalized = String(value ?? '')
    .trim()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ');
  if (!normalized) {
    return '';
  }
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function buildMarkdownTable(headers: string[], rows: string[][]): string {
  const safeHeaders = headers.map((header) => toMarkdownCell(header));
  const safeRows = rows.map((row) => row.map((cell) => toMarkdownCell(cell)));
  return [
    `| ${safeHeaders.join(' | ')} |`,
    `| ${safeHeaders.map(() => '---').join(' | ')} |`,
    ...safeRows.map((row) => `| ${row.join(' | ')} |`),
  ].join('\n');
}

function buildLeadWithTable(lead: string, headers: string[], rows: string[][]): string {
  return [lead.trim(), '', buildMarkdownTable(headers, rows)].join('\n');
}

function compatibilityRelationHint(relationType: unknown): string {
  const normalized = String(relationType || '').trim().toLowerCase();
  if (normalized === 'requires') {
    return '建议写明为必配项，并说明联动条件或边界。';
  }
  if (normalized === 'recommended') {
    return '建议写明为推荐配套项，并说明适用场景。';
  }
  if (normalized === 'optional') {
    return '建议写明为可选项，并注明触发条件。';
  }
  if (normalized === 'conflicts_with') {
    return '建议补充冲突限制或替代方案说明。';
  }
  return '建议补充配套关系、触发条件和实施边界。';
}

function dedupeSuggestedBlocks(blocks: ReviewTaskSuggestedBlock[]): ReviewTaskSuggestedBlock[] {
  const seen = new Set<string>();
  const items: ReviewTaskSuggestedBlock[] = [];
  for (const block of blocks) {
    const key = `${block.key}|${block.title}|${block.markdown.trim()}`;
    if (!block.markdown.trim() || seen.has(key)) {
      continue;
    }
    seen.add(key);
    items.push(block);
  }
  return items;
}

function dedupePlaceholderSuggestions(suggestions: ReviewTaskPlaceholderSuggestion[]): ReviewTaskPlaceholderSuggestion[] {
  const seen = new Set<string>();
  const items: ReviewTaskPlaceholderSuggestion[] = [];
  for (const suggestion of suggestions) {
    const key = `${suggestion.label}|${suggestion.value}|${suggestion.placeholders.join('|')}`;
    if (!suggestion.value.trim() || suggestion.placeholders.length === 0 || seen.has(key)) {
      continue;
    }
    seen.add(key);
    items.push(suggestion);
  }
  return items;
}

function normalizeSuggestionValue(value: unknown): string {
  if (Array.isArray(value)) {
    return compactStrings(value).join('、');
  }
  if (value && typeof value === 'object') {
    const record = toRecord(value);
    const ioAllocation = formatIoAllocation(record);
    if (ioAllocation) {
      return ioAllocation;
    }
    return compactStrings(Object.values(record)).join(' / ');
  }
  return String(value ?? '').trim();
}

function placeholderDefinitionForField(fieldName: string): { label: string; placeholders: string[] } | null {
  const normalized = fieldName.trim().toLowerCase();
  if (!normalized) {
    return null;
  }
  switch (normalized) {
    case 'primary_product':
    case 'product_name':
    case 'device_name':
    case 'equipment_name':
      return { label: '设备名称', placeholders: ['待补充主设备名称', '待补充设备'] };
    case 'model_number':
    case 'model':
    case 'config':
    case 'configuration':
      return { label: '型号/配置', placeholders: ['待补充型号', '待补充型号/配置'] };
    case 'voltage_level':
    case 'rated_voltage':
      return { label: '电压等级', placeholders: ['待补充电压等级'] };
    case 'power_rating':
    case 'rated_power':
    case 'total_power':
      return { label: '容量/功率', placeholders: ['待补充容量/功率'] };
    case 'quantity':
      return { label: '数量', placeholders: ['待补充数量'] };
    case 'protocol':
    case 'dcs_protocol':
      return { label: '协议', placeholders: ['待补充协议'] };
    case 'io_allocation':
      return { label: 'IO 能力', placeholders: ['待补充IO能力'] };
    case 'catalog_interface_signals':
    case 'interface_signals':
    case 'interface_signal':
      return { label: '接口边界', placeholders: ['待补充接口边界'] };
    case 'target_family_code':
    case 'family_code':
    case 'primary_product_family':
      return { label: '配套产品族', placeholders: ['待补充产品族'] };
    case 'missing_items':
    case 'missing_series_codes':
      return { label: '缺失项', placeholders: ['待补充缺失项'] };
    default:
      return null;
  }
}

function pushPlaceholderSuggestion(
  suggestions: ReviewTaskPlaceholderSuggestion[],
  options: {
    key: string;
    fieldName: string;
    value: unknown;
    label?: string;
    placeholders?: string[];
    matchTerms?: unknown[];
  },
): void {
  const normalizedValue = normalizeSuggestionValue(options.value);
  if (!normalizedValue) {
    return;
  }
  const definition =
    options.placeholders && options.placeholders.length > 0
      ? {
          label: options.label || options.fieldName,
          placeholders: compactStrings(options.placeholders),
        }
      : placeholderDefinitionForField(options.fieldName);
  if (!definition || definition.placeholders.length === 0) {
    return;
  }
  suggestions.push({
    key: options.key,
    label: options.label || definition.label,
    value: normalizedValue,
    placeholders: definition.placeholders,
    matchTerms: compactStrings(options.matchTerms || []),
  });
}

export function buildReviewTaskRevisionBrief(task: ReviewTask): ReviewTaskRevisionBrief {
  const issue = issueFromTaskPayload(task.payload || {});
  const details = toRecord(issue.details);
  const checklist = compactStrings([
    issue.suggested_action,
  ]);
  const contentAnchors: string[] = [];
  const suggestedBlocks: ReviewTaskSuggestedBlock[] = [];

  switch (issue.code) {
    case 'VAL011': {
      const primaryProduct = String(details.expected_primary_product || '').trim();
      const voltageLevel = String(details.expected_voltage_level || '').trim();
      const powerRating = String(details.expected_power_rating || '').trim();
      const modelNumber = String(details.expected_model_number || '').trim();
      if (primaryProduct) checklist.push(`在正文中明确主设备名称：${primaryProduct}`);
      if (voltageLevel) checklist.push(`在正文中写明电压等级：${voltageLevel}`);
      if (powerRating) checklist.push(`在正文中写明容量或功率：${powerRating}`);
      if (modelNumber) checklist.push(`在正文中写明型号：${modelNumber}`);
      contentAnchors.push(...compactStrings([primaryProduct, voltageLevel, powerRating, modelNumber]));
      const sentenceParts = compactStrings([
        primaryProduct ? `本方案主设备采用 ${primaryProduct}` : '',
        modelNumber ? `型号为 ${modelNumber}` : '',
        voltageLevel ? `适配 ${voltageLevel} 电压等级` : '',
        powerRating ? `额定容量/功率为 ${powerRating}` : '',
      ]);
      if (sentenceParts.length > 0) {
        suggestedBlocks.push({
          key: 'val011-parameter-summary',
          title: '主设备关键参数骨架',
          kind: 'table',
          markdown: buildLeadWithTable(
            '本节主设备关键参数建议按下表补齐：',
            ['参数项', '推荐表述', '待确认项'],
            [
              ['主设备名称', primaryProduct || '待补充主设备名称', '与方案快照中的主设备命名保持一致'],
              ['型号', modelNumber || '待补充型号', '确认是否需要与商务清单完全一致'],
              ['电压等级', voltageLevel || '待补充电压等级', '建议补充额定电压或系统电压等级'],
              ['容量/功率', powerRating || '待补充容量/功率', '建议写明额定容量或额定功率'],
            ],
          ),
        });
      }
      break;
    }
    case 'VAL012': {
      const protocol = String(details.expected_protocol || '').trim();
      const ioAllocation = formatIoAllocation(details.expected_io_allocation);
      const interfaceSignals = toStringArray(details.expected_catalog_interface_signals);
      if (protocol) checklist.push(`补充 DCS 通讯协议：${protocol}`);
      if (ioAllocation) checklist.push(`补充接口点表或 IO 能力：${ioAllocation}`);
      if (interfaceSignals.length > 0) checklist.push(`至少补入一条接口边界或信号描述`);
      contentAnchors.push(...compactStrings([protocol, ioAllocation, ...interfaceSignals]));
      suggestedBlocks.push({
        key: 'val012-interface-table',
        title: '接口协议与 IO 骨架',
        kind: 'table',
        markdown: buildLeadWithTable(
          '本节接口协议与 IO 边界建议按下表补齐：',
          ['检查项', '推荐表述/当前快照', '待确认项'],
          [
            ['DCS 通讯协议', protocol || '待补充协议', '确认最终协议名称、主备链路和通讯介质'],
            ['IO 能力', ioAllocation || '待补充IO能力', '确认最终点表、预留量和信号分配'],
            [
              '关键接口/信号边界',
              interfaceSignals.slice(0, 6).map((item) => toDisplayToken(item)).join('、') || '待补充接口边界',
              '确认信号方向、接入对象、责任边界或第三方接口',
            ],
          ],
        ),
      });
      break;
    }
    case 'VAL013': {
      const missingProducts = toStringArray(details.missing_products);
      const expectedProducts = toStringArray(details.expected_products);
      const hasTable = Boolean(details.markdown_table_present);
      const missingProductSet = new Set(missingProducts);
      if (missingProducts.length > 0) checklist.push(`补齐缺失设备：${missingProducts.join('、')}`);
      if (!hasTable) checklist.push('将供货范围改为表格呈现，至少包含设备、型号/配置、数量、备注');
      contentAnchors.push(
        ...compactStrings([
          ...missingProducts,
          ...expectedProducts,
          !hasTable ? '| 序号 | 设备 | 型号/配置 | 数量 | 备注 |' : '',
        ]),
      );
      if (!hasTable || expectedProducts.length > 0) {
        const tableRows = (expectedProducts.length > 0 ? expectedProducts : missingProducts).map((product, index) => [
          String(index + 1),
          product,
          '待补充型号/配置',
          '待补充数量',
          missingProductSet.has(product) ? '当前章节未覆盖，需补齐' : '建议与方案快照或商务清单保持一致',
        ]);
        suggestedBlocks.push({
          key: 'val013-supply-table',
          title: '供货范围表格骨架',
          kind: 'table',
          markdown: buildLeadWithTable(
            '本节供货范围建议按下表补齐，型号、数量和边界以最终技术协议为准：',
            ['序号', '设备名称', '推荐配置/型号表述', '数量', '备注'],
            tableRows.length > 0
              ? tableRows
              : [['1', '待补充设备', '待补充型号/配置', '待补充数量', '与方案快照保持一致']],
          ),
        });
      }
      break;
    }
    case 'VAL014': {
      const uncoveredActions = toRecordArray(details.uncovered_actions);
      const riskFlags = toStringArray(details.risk_flags);
      for (const action of uncoveredActions) {
        const targetFamilyCode = String(action.target_family_code || '').trim();
        const condition = String(action.condition || '').trim();
        const missingSeries = toStringArray(action.missing_series_codes);
        checklist.push(
          compactStrings([
            targetFamilyCode ? `说明配套产品族：${targetFamilyCode}` : '',
            condition ? `写明触发条件：${condition}` : '',
            missingSeries.length > 0 ? `提示缺失配套项：${missingSeries.join('、')}` : '',
          ]).join('；'),
        );
        contentAnchors.push(...compactStrings([targetFamilyCode, condition, ...missingSeries]));
      }
      if (riskFlags.length > 0) {
        checklist.push(`补入风险提示：${riskFlags.join('、')}`);
        contentAnchors.push(...riskFlags);
      }
      if (uncoveredActions.length > 0 || riskFlags.length > 0) {
        const tableRows = uncoveredActions.map((action) => {
          const targetFamilyCode = String(action.target_family_code || '').trim();
          const condition = String(action.condition || '').trim();
          const missingSeries = toStringArray(action.missing_series_codes);
          return [
            condition || '待补充触发条件',
            toDisplayToken(targetFamilyCode) || '待补充产品族',
            missingSeries.map((item) => toDisplayToken(item)).join('、') || '待补充',
            compatibilityRelationHint(action.relation_type),
          ];
        });
        const riskBlock = riskFlags.length > 0
          ? [
              '',
              '风险提示建议同步写明：',
              ...riskFlags.map((item) => `- ${item}`),
            ].join('\n')
          : '';
        suggestedBlocks.push({
          key: 'val014-compatibility-note',
          title: '兼容规则说明骨架',
          kind: 'table',
          markdown: `${buildLeadWithTable(
            '本节配套兼容规则建议按下表补齐：',
            ['触发条件', '配套产品族', '当前缺失项', '建议表述'],
            tableRows.length > 0
              ? tableRows
              : [['待补充触发条件', '待补充产品族', '待补充缺失项', '建议补充配套关系、触发条件和实施边界。']],
          )}${riskBlock}`,
        });
      }
      break;
    }
    case 'VAL108': {
      const qualitySummary = String(details.quality_gate_summary || '').trim();
      const qualityIssues = compactStrings(
        (Array.isArray(details.quality_gate_issues) ? details.quality_gate_issues : []).map((item) =>
          typeof item === 'object' && item ? (item as Record<string, unknown>).code : item
        ),
      );
      if (qualitySummary) checklist.push(`优先修正自动质检摘要指出的问题：${qualitySummary}`);
      if (qualityIssues.length > 0) checklist.push(`逐项消化质量问题：${qualityIssues.join('、')}`);
      contentAnchors.push(...qualityIssues);
      suggestedBlocks.push({
        key: 'val108-quality-skeleton',
        title: '章节重写骨架',
        kind: 'list',
        markdown: [
          '建议按以下顺序重写本节：',
          '',
          '1. 结论句：先写当前项目的明确结论，不保留内部提示语。',
          '2. 参数句：补齐当前项目的关键参数、设备名称、型号或接口边界。',
          '3. 边界句：补齐对外交付所需的风险、限制、切换条件或实施说明。',
        ].join('\n'),
      });
      break;
    }
    default: {
      const missingFields = toStringArray(details.missing_fields);
      const missingItems = toStringArray(details.missing_items);
      const missingProducts = toStringArray(details.missing_products);
      if (missingFields.length > 0) checklist.push(`补齐缺失字段：${missingFields.join('、')}`);
      if (missingItems.length > 0) checklist.push(`补齐缺失项：${missingItems.join('、')}`);
      if (missingProducts.length > 0) checklist.push(`补齐缺失设备：${missingProducts.join('、')}`);
      contentAnchors.push(...compactStrings([...missingFields, ...missingItems, ...missingProducts]));
      break;
    }
  }

  const normalizedChecklist = compactStrings(checklist).filter(Boolean);
  const normalizedAnchors = compactStrings(contentAnchors).slice(0, 8);
  const normalizedSuggestedBlocks = dedupeSuggestedBlocks(suggestedBlocks);
  const resolutionTemplate = [
    issue.code ? `处理项：${issue.code}` : `处理项：${task.task_type}`,
    issue.section_title ? `章节：${issue.section_title}` : '',
    normalizedChecklist.length > 0 ? `已处理内容：${normalizedChecklist.join('；')}` : '',
    normalizedAnchors.length > 0 ? `已补充锚点：${normalizedAnchors.join('；')}` : '',
    '结果：已按当前章节修订并完成自检，等待重新校验。',
  ]
    .filter(Boolean)
    .join('\n');

  return {
    checklist: normalizedChecklist,
    contentAnchors: normalizedAnchors,
    resolutionTemplate,
    suggestedBlocks: normalizedSuggestedBlocks,
  };
}

export function buildSectionReviewBatchBrief(tasks: ReviewTask[]): SectionReviewBatchBrief {
  const briefs = tasks.map((task) => buildReviewTaskRevisionBrief(task));
  const issues = tasks.map((task) => issueFromTaskPayload(task.payload || {}));
  const taskCodes = compactStrings(
    tasks.map((task, index) => issues[index]?.code || task.task_type)
  );
  const checklist = compactStrings([
    ...issues.map((issue) => issue.suggested_action || ''),
    ...briefs.flatMap((brief) => brief.checklist),
  ]);
  const contentAnchors = compactStrings(briefs.flatMap((brief) => brief.contentAnchors)).slice(0, 10);
  const suggestedBlocks = dedupeSuggestedBlocks(briefs.flatMap((brief) => brief.suggestedBlocks));
  const sectionTitles = compactStrings(issues.map((issue) => issue.section_title || ''));
  const resolutionTemplate = [
    sectionTitles.length > 0 ? `章节：${sectionTitles.join(' / ')}` : '',
    taskCodes.length > 0 ? `处理项：${taskCodes.join('、')}` : '',
    checklist.length > 0 ? `本轮统一修订：${checklist.join('；')}` : '',
    contentAnchors.length > 0 ? `补充锚点：${contentAnchors.join('；')}` : '',
    '结果：本章节已按合并修订清单统一调整并完成自检，待重新校验。',
  ]
    .filter(Boolean)
    .join('\n');

  return {
    taskCodes,
    checklist,
    contentAnchors,
    resolutionTemplate,
    suggestedBlocks,
  };
}

export function buildSectionPlaceholderSuggestions(
  tasks: ReviewTask[],
  globalParamSnapshot?: Record<string, unknown>,
): ReviewTaskPlaceholderSuggestion[] {
  const suggestions: ReviewTaskPlaceholderSuggestion[] = [];
  const globalParams = toRecord(globalParamSnapshot);
  const selectedProducts = parseSelectedProductsSummary(globalParams.selected_products);

  pushPlaceholderSuggestion(suggestions, {
    key: 'global-primary-product',
    fieldName: 'primary_product',
    value: globalParams.primary_product,
    label: '主设备名称',
    matchTerms: [globalParams.primary_product],
  });
  pushPlaceholderSuggestion(suggestions, {
    key: 'global-primary-model',
    fieldName: 'model_number',
    value: globalParams.primary_model_number,
    label: '型号/配置',
    matchTerms: [globalParams.primary_product, globalParams.primary_product_family],
  });
  pushPlaceholderSuggestion(suggestions, {
    key: 'global-voltage-level',
    fieldName: 'voltage_level',
    value: globalParams.voltage_level,
    label: '电压等级',
    matchTerms: [globalParams.primary_product],
  });
  pushPlaceholderSuggestion(suggestions, {
    key: 'global-power-rating',
    fieldName: 'power_rating',
    value: globalParams.power_rating || globalParams.total_power,
    label: '容量/功率',
    matchTerms: [globalParams.primary_product],
  });
  pushPlaceholderSuggestion(suggestions, {
    key: 'global-quantity',
    fieldName: 'quantity',
    value: globalParams.quantity,
    label: '数量',
    matchTerms: [globalParams.selected_products],
  });
  pushPlaceholderSuggestion(suggestions, {
    key: 'global-dcs-protocol',
    fieldName: 'dcs_protocol',
    value: globalParams.dcs_protocol,
    label: '协议',
    matchTerms: [globalParams.catalog_interface_summary],
  });
  selectedProducts.forEach((product, productIndex) => {
    pushPlaceholderSuggestion(suggestions, {
      key: `selected-product-${productIndex}-name`,
      fieldName: 'device_name',
      value: product.name,
      label: `设备名称（${product.role}）`,
      matchTerms: [product.role, product.name],
    });
    pushPlaceholderSuggestion(suggestions, {
      key: `selected-product-${productIndex}-quantity`,
      fieldName: 'quantity',
      value: product.quantity,
      label: `数量（${product.role}）`,
      matchTerms: [product.role, product.name],
    });
  });

  tasks.forEach((task, taskIndex) => {
    const issue = issueFromTaskPayload(task.payload || {});
    const details = toRecord(issue.details);

    pushPlaceholderSuggestion(suggestions, {
      key: `task-${taskIndex}-primary-product`,
      fieldName: 'primary_product',
      value: details.expected_primary_product,
      label: '主设备名称',
      matchTerms: [details.expected_primary_product],
    });
    pushPlaceholderSuggestion(suggestions, {
      key: `task-${taskIndex}-model-number`,
      fieldName: 'model_number',
      value: details.expected_model_number,
      label: '型号/配置',
      matchTerms: [details.expected_primary_product, details.expected_model_number],
    });
    pushPlaceholderSuggestion(suggestions, {
      key: `task-${taskIndex}-voltage-level`,
      fieldName: 'voltage_level',
      value: details.expected_voltage_level,
      label: '电压等级',
      matchTerms: [details.expected_primary_product],
    });
    pushPlaceholderSuggestion(suggestions, {
      key: `task-${taskIndex}-power-rating`,
      fieldName: 'power_rating',
      value: details.expected_power_rating,
      label: '容量/功率',
      matchTerms: [details.expected_primary_product],
    });
    pushPlaceholderSuggestion(suggestions, {
      key: `task-${taskIndex}-protocol`,
      fieldName: 'protocol',
      value: details.expected_protocol,
      label: '协议',
      matchTerms: [details.expected_protocol],
    });
    pushPlaceholderSuggestion(suggestions, {
      key: `task-${taskIndex}-io-allocation`,
      fieldName: 'io_allocation',
      value: details.expected_io_allocation,
      label: 'IO 能力',
      matchTerms: [details.expected_protocol],
    });
    pushPlaceholderSuggestion(suggestions, {
      key: `task-${taskIndex}-interface-signals`,
      fieldName: 'catalog_interface_signals',
      value: toStringArray(details.expected_catalog_interface_signals).slice(0, 6),
      label: '接口边界',
      matchTerms: toStringArray(details.expected_catalog_interface_signals),
    });

    toStringArray(details.missing_products).forEach((item, itemIndex) =>
      pushPlaceholderSuggestion(suggestions, {
        key: `task-${taskIndex}-missing-product-${itemIndex}`,
        fieldName: 'device_name',
        value: item,
        label: '设备名称',
        matchTerms: [item],
      }),
    );
    toStringArray(details.expected_products).forEach((item, itemIndex) =>
      pushPlaceholderSuggestion(suggestions, {
        key: `task-${taskIndex}-expected-product-${itemIndex}`,
        fieldName: 'device_name',
        value: item,
        label: '设备名称',
        matchTerms: [item],
      }),
    );

    toRecordArray(details.uncovered_actions).forEach((action, actionIndex) => {
      pushPlaceholderSuggestion(suggestions, {
        key: `task-${taskIndex}-compatibility-condition-${actionIndex}`,
        fieldName: 'condition',
        value: action.condition,
        label: '触发条件',
        placeholders: ['待补充触发条件'],
        matchTerms: [action.target_family_code, action.condition],
      });
      pushPlaceholderSuggestion(suggestions, {
        key: `task-${taskIndex}-compatibility-family-${actionIndex}`,
        fieldName: 'target_family_code',
        value: toDisplayToken(action.target_family_code),
        label: '配套产品族',
        matchTerms: [action.target_family_code, action.condition],
      });
      pushPlaceholderSuggestion(suggestions, {
        key: `task-${taskIndex}-compatibility-missing-${actionIndex}`,
        fieldName: 'missing_series_codes',
        value: toStringArray(action.missing_series_codes),
        label: '缺失项',
        matchTerms: [action.target_family_code, action.condition, ...toStringArray(action.missing_series_codes)],
      });
    });

    const expectedValues = toRecord(details.expected_values);
    Object.entries(expectedValues).forEach(([fieldName, value]) =>
      pushPlaceholderSuggestion(suggestions, {
        key: `task-${taskIndex}-expected-value-${fieldName}`,
        fieldName,
        value,
        matchTerms: [details.expected_primary_product, details.expected_protocol],
      }),
    );
  });

  return dedupePlaceholderSuggestions(suggestions);
}
