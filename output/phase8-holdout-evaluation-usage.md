# Phase 8 Holdout Evaluation Usage

本文件记录下一轮 Phase 8 的可运行评测入口。

## 固定评测集

评测集配置：

- `backend/data/eval/phase8_holdout_eval_set.json`

当前包含：

- `holdout_linyi_lci_near_library`
- `holdout_zhanjiang_lci_generalization`
- `sim_steel_fan_vfd_near_library`
- `sim_new_domain_generalization_placeholder`

## 单项目评测命令

在已有项目完成 outline / draft / export 后运行：

```bash
cd backend
../.venv/bin/python scripts/evaluate_holdout_outline_draft.py \
  --project-id <project_uuid> \
  --baseline-document-name "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx" \
  --output ../output/phase8-linyi-outline-draft-eval.md
```

脚本会输出：

- markdown 报告
- JSON 指标
- baseline outline override JSON

## 当前指标

脚本分开统计：

- Outline 覆盖率
- Draft 章节数量与图占位情况
- 技术章节证据准确率
- 跨章节证据污染
- 内部残留词
- 废话模板比例
- 图资产 Top-3 source-bound rate
- 错图进入正文比例
- 生成耗时
- Word 导出可用性
- Phase 8 gate 汇总状态

## 使用原则

- Outline 先独立评分。
- 如果 outline 质量不稳定，用脚本生成的 baseline outline override 重新生成 draft，再单独评分 draft。
- Draft 评分不应被 outline 错误完全掩盖。
- 图资产必须单独看 Top-3，不只看最终正文里的首图。
