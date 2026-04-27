# Holdout Eval Preflight: 临沂钢铁鼓风机电机及启动装置

## 目标

使用 holdout 验证文档 `临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx` 抽取需求，作为当前系统生成新方案的基准输入；生成后与原 holdout 文档做结构、参数、证据和正文质量对比。

## 当前本地准备结果

- 验证文档：`private_samples/real_proposals/临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx`
- 角色：`holdout_eval`
- 本地评测项目：`d73b7b53-6cc3-441d-9587-411fcdd22aa9`
- 需求卡：`5b94c35f-cf7a-4f3c-9efa-8ab7582de05c`
- Evidence bundle：`f32ff3f5-e40a-4459-a15d-3e4a1dfe6153`
- Evidence quality：`0.7913`

## 抽取出的核心需求

- 项目对象：山东临沂钢铁投资集团特钢公司高炉风机电机及启动装置
- 产品线：LCI 变频软起
- 行业：钢铁
- 业务目标：为高炉鼓风机配置高压电机及 LCI 变频软起动系统，实现一套软起系统对两台高炉鼓风机电机的启动、加速、同步切换和旁路运行。
- 供电条件：10kV ±10%，50Hz ±2%，10kV 母线短路容量 Max. 500MVA
- 辅助电源：380V±10%, 3Ph+N+PE, 50±1%Hz
- 控制电源：AC220V±10%, 1Ph+N+PE, 50±1%Hz
- 启动过程：按加速转矩曲线加速至约 95% 额定转速后释放同步装置；同步装置向 SFC 发送速度和电压调整信号。
- 启动时间：总启动时间 147s，包括纯加速 112s、建磁约 5s、同步约 30s。
- 变频器型号：MEGADRIVE - LCI.SO / A1212-211N465
- 变频器额定输出功率：11970kW
- 冷却介质：Air，最大入口温度 40°C
- 直流电抗器：Ld=1.0mH, Ud/2=2205V, Id=2789A

## Evidence Retrieval 结果

Top 8 命中：

1. `宝山钢铁股份有限公司三鼓风LCI改造方案.docx` / `3 系统方案 System Solution` / score `1.0`
2. `宝山钢铁股份有限公司三鼓风LCI改造方案.docx` / `tmpxsfgnh2s.docx` / score `0.7548`
3. `宝山钢铁股份有限公司三鼓风LCI改造方案.docx` / `3 系统方案 System Solution` / score `0.6191`
4. `宝山钢铁股份有限公司三鼓风LCI改造方案.docx` / `4 变频器技术数据 Component Technical Data` / score `0.5329`
5. `宝山钢铁股份有限公司三鼓风LCI改造方案.docx` / `4 变频器技术数据 Component Technical Data` / score `0.4996`
6. `宝山钢铁股份有限公司三鼓风LCI改造方案.docx` / `8 调试` / score `0.4968`
7. `宝山钢铁股份有限公司三鼓风LCI改造方案.docx` / `2 供货范围 Scopes of supply` / score `0.4936`
8. `宝山钢铁股份有限公司三鼓风LCI改造方案.docx` / `6 提交资料` / score `0.4807`

初步判断：

- 正向：主召回来源集中在同类 LCI 钢铁场景，Top1 命中 `系统方案`，Top4/5 命中 `变频器技术数据`，方向正确。
- 风险：Top6 `调试`、Top8 `提交资料` 对主方案正文价值较低，后续生成时需要看章节级筛选是否能压住这些外围材料。
- 数据链路问题：直接通过 `/documents/upload` 重新上传 holdout docx 时，接口 300 秒未返回；说明 RFP 上传解析仍存在同步阻塞问题。此次 preflight 改用已入库的 `holdout_eval` 解析结果创建需求卡，避免重复解析。

## 下一步

若继续完整评测，需要调用当前配置的 LLM relay：

1. 基于需求卡和 evidence bundle 生成 outline。
2. 批准 outline 后生成 sections。
3. 拉取生成稿，与 holdout 原文做对比：
   - 章节覆盖率
   - 关键参数保真度
   - 复用证据命中质量
   - 错场景/噪声材料污染
   - 图表资产引用情况
   - 生成耗时与失败点
