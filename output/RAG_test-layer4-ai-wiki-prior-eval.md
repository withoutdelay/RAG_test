# Layer 4 AI Wiki Prior Holdout Eval

更新时间：2026-04-20

评测范围：固定 `pilot_main` case shortlist，不改线上主链路，只比较 reusable block 检索/重排阶段在 `without_prior` 与 `with_prior` 两种模式下的差异。
评测模式：`fast_heuristic_only`

## 汇总

- 评测章节数：16
- 有 case shortlist 的章节数：16
- 有明确 equipment target 的章节数：16
- 跳过文档数：0
- `with_prior` 命中 prior 的章节数：11
- `with_prior` 命中 prior 的块数：29
- `with_prior` 累计 prior boost：3.55

| Metric | without_prior | with_prior |
| --- | ---: | ---: |
| Top1 section_type match | 9 | 9 |
| Top3 section_type hit | 10 | 10 |
| Top1 equipment_type match | 8 | 8 |
| Top3 equipment_type hit | 9 | 9 |

## Delta

- section_type 首命中排名改善：0
- section_type 首命中排名变差：0
- equipment_type 首命中排名改善：0
- equipment_type 首命中排名变差：0
- Top1 候选发生变化：1
- Top1 section_type match gained：0
- Top1 section_type match lost：0

## 明细

### 上电湛江中纸高浓磨机项目成套方案VerA.pdf / 5 变频器技术数据 > 5.1 变频器配置 > 5.1.1 变频器系统示意图

- target: `section_type=vfd_spec` / `equipment_type=vfd`
- case shortlist: 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf
- AI Wiki terms: 永磁电机变频改造方案族, 永磁, 永磁电机, 节能改造, 变频改造, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 高压变频器方案族, 高压变频器, 中压变频器, 变频装置, 变频柜, VFD
- AI Wiki products: 永磁电机变频改造方案族, 高压变频器方案族
- AI Wiki modules: 功率单元, IGBT, 变压器柜
- without_prior: top1=乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx / score=1.2 / section_match=True / equipment_match=True
- with_prior: top1=乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx / score=1.2 / section_match=True / equipment_match=True / prior_boost=0.36

### 上电湛江中纸高浓磨机项目成套方案VerA.pdf / 12 电机润滑油站系统( TBD ,由上电供应商确定) > 12.1 供货范围( TBD ,由上电供应商确定)

- target: `section_type=supply_scope` / `equipment_type=motor`
- case shortlist: 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 2025-SDQ技术方案.docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf
- AI Wiki terms: none
- AI Wiki products: 水电阻起动柜方案族
- AI Wiki modules: 晶闸管
- without_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=True / equipment_match=False
- with_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=True / equipment_match=False / prior_boost=0.33

### 上电湛江中纸高浓磨机项目成套方案VerA.pdf / 4 LCI 变频软起系统方案 > 4.3 本地控制单元 PLC 对电机辅助设备监控功能描述

- target: `section_type=communication_interface` / `equipment_type=lci`
- case shortlist: 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 2025-SDQ技术方案.docx
- AI Wiki terms: LCI 变频软起方案族, LCI, LCI 软起, 变频软起, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 永磁电机变频改造方案族, 永磁, 永磁电机, 节能改造, 变频改造, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 控制柜
- AI Wiki products: LCI 变频软起方案族, 永磁电机变频改造方案族
- AI Wiki modules: 控制柜, 晶闸管, 功率单元
- without_prior: top1=099-230101-001西安陕鼓（秦风气体）技术协议.pdf / score=1.2 / section_match=True / equipment_match=False
- with_prior: top1=099-230101-001西安陕鼓（秦风气体）技术协议.pdf / score=1.2 / section_match=True / equipment_match=False / prior_boost=0.39

### 上电湛江中纸高浓磨机项目成套方案VerA.pdf / 4 LCI 变频软起系统方案 > 4.2 启动和同步过程描述

- target: `section_type=motor_spec` / `equipment_type=lci`
- case shortlist: 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx
- AI Wiki terms: LCI 变频软起方案族, LCI, LCI 软起, 变频软起, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 晶闸管, 控制柜, 控制单元, PLC柜
- AI Wiki products: LCI 变频软起方案族
- AI Wiki modules: 晶闸管, 控制柜
- without_prior: top1=099-230101-001西安陕鼓（秦风气体）技术协议.pdf / score=1.1493 / section_match=False / equipment_match=False
- with_prior: top1=099-230101-001西安陕鼓（秦风气体）技术协议.pdf / score=1.1432 / section_match=False / equipment_match=False / prior_boost=0.0

### 上电湛江中纸高浓磨机项目成套方案VerA.pdf / 19 培训

- target: `section_type=service_support` / `equipment_type=vfd`
- case shortlist: 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx
- AI Wiki terms: 运维巡检方案族, 维保, 巡检, 检修, 维护, 赞比亚-恩多拉机场-成套设备维保巡检方案.docx, 高压变频器方案族, 高压变频器, 中压变频器, 变频装置, 变频柜, VFD
- AI Wiki products: 运维巡检方案族, 高压变频器方案族
- AI Wiki modules: 功率单元, IGBT, 冷却风机
- without_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=True / equipment_match=True
- with_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=True / equipment_match=True / prior_boost=0.39

### 上电湛江中纸高浓磨机项目成套方案VerA.pdf / 电机及软起动成套装置技术方案

- target: `section_type=starter_spec` / `equipment_type=soft_starter`
- case shortlist: 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 2025-SDQ技术方案.docx
- AI Wiki terms: 水电阻起动柜方案族, 水电阻柜, 水电阻起动柜, 2025-SDQ技术方案.docx, 高压变频器方案族, 高压变频器, 中压变频器, 变频装置, 变频柜, VFD, 中压变频器采购技术协议, 晶闸管
- AI Wiki products: 水电阻起动柜方案族, 高压变频器方案族
- AI Wiki modules: 水电阻柜, 晶闸管
- without_prior: top1=2025-SDQ技术方案.docx / score=1.2 / section_match=True / equipment_match=True
- with_prior: top1=2025-SDQ技术方案.docx / score=1.2 / section_match=True / equipment_match=True / prior_boost=0.35

### 上电湛江中纸高浓磨机项目成套方案VerA.pdf / 6 变压器技术规范 > 6.1 输入变压器技术规范

- target: `section_type=transformer_spec` / `equipment_type=transformer`
- case shortlist: 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 赞比亚-恩多拉机场-成套设备维保巡检方案.docx
- AI Wiki terms: 功率单元, 功率模块, 逆变单元, 整流单元, H桥, 变压器柜, 隔离变压器, 整流变压器, 移相变压器
- AI Wiki products: none
- AI Wiki modules: 功率单元, 变压器柜
- without_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=0.0 / section_match=False / equipment_match=False
- with_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=0.0 / section_match=False / equipment_match=False / prior_boost=0.0

### 上电湛江中纸高浓磨机项目成套方案VerA.pdf / 17 调试

- target: `section_type=commissioning_acceptance` / `equipment_type=vfd`
- case shortlist: 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx
- AI Wiki terms: 永磁电机变频改造方案族, 永磁, 永磁电机, 节能改造, 变频改造, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 高压变频器方案族, 高压变频器, 中压变频器, 变频装置, 变频柜, VFD
- AI Wiki products: 永磁电机变频改造方案族, 高压变频器方案族
- AI Wiki modules: 功率单元, IGBT, 冷却风机
- without_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=True / equipment_match=True
- with_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=True / equipment_match=True / prior_boost=0.39

### 临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx / 4 变频器技术数据 Component Technical Data > 4.1 变频器配置Converter Configuration > 4.1.5 变频器外形图 Converter Typical Outline Drawing

- target: `section_type=vfd_spec` / `equipment_type=vfd`
- case shortlist: 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf
- AI Wiki terms: 永磁电机变频改造方案族, 永磁, 永磁电机, 节能改造, 变频改造, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 高压变频器方案族, 高压变频器, 中压变频器, 变频装置, 变频柜, VFD
- AI Wiki products: 永磁电机变频改造方案族, 高压变频器方案族
- AI Wiki modules: 功率单元, IGBT, 变压器柜
- without_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=True / equipment_match=True
- with_prior: top1=乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx / score=1.2 / section_match=True / equipment_match=True / prior_boost=0.39

### 临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx / 5 输入/输出变压器技术规范Transformer Specification > 5.1 输入变压器技术规范Input Transformer Specification

- target: `section_type=transformer_spec` / `equipment_type=transformer`
- case shortlist: 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx
- AI Wiki terms: 高压变频器方案族, 高压变频器, 中压变频器, 变频装置, 变频柜, VFD, 中压变频器采购技术协议, 晶闸管, 功率单元, 功率模块, 逆变单元, 整流单元
- AI Wiki products: 高压变频器方案族
- AI Wiki modules: 晶闸管, 功率单元, 变压器柜
- without_prior: top1=乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx / score=1.2 / section_match=True / equipment_match=True
- with_prior: top1=乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx / score=1.2 / section_match=True / equipment_match=True / prior_boost=0.07

### 临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx / 1 Surge Arrestor

- target: `section_type=protection_interlock` / `equipment_type=motor`
- case shortlist: 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf
- AI Wiki terms: 永磁电机变频改造方案族, 永磁, 永磁电机, 节能改造, 变频改造, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 运维巡检方案族, 维保, 巡检, 检修, 维护, 赞比亚-恩多拉机场-成套设备维保巡检方案.docx
- AI Wiki products: 永磁电机变频改造方案族, 运维巡检方案族
- AI Wiki modules: 晶闸管, IGBT, 功率单元
- without_prior: top1=099-230101-001西安陕鼓（秦风气体）技术协议.pdf / score=1.2 / section_match=False / equipment_match=False
- with_prior: top1=099-230101-001西安陕鼓（秦风气体）技术协议.pdf / score=1.2 / section_match=False / equipment_match=False / prior_boost=0.0

### 临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx / 7 电机控制盘Motor Control Panel

- target: `section_type=commercial_manual_only` / `equipment_type=motor`
- case shortlist: 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 2025-SDQ技术方案.docx
- AI Wiki terms: LCI 变频软起方案族, LCI, LCI 软起, 变频软起, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 晶闸管, 控制柜, 控制单元, PLC柜
- AI Wiki products: LCI 变频软起方案族
- AI Wiki modules: 晶闸管, 控制柜
- without_prior: top1=099-230101-001西安陕鼓（秦风气体）技术协议.pdf / score=1.1624 / section_match=False / equipment_match=False
- with_prior: top1=099-230101-001西安陕鼓（秦风气体）技术协议.pdf / score=1.2 / section_match=False / equipment_match=False / prior_boost=0.0

### 临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx / 2 供货范围 Scopes of supply

- target: `section_type=supply_scope` / `equipment_type=lci`
- case shortlist: 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf
- AI Wiki terms: none
- AI Wiki products: 高压变频器方案族, LCI 变频软起方案族
- AI Wiki modules: 晶闸管, 水电阻柜, 冷却风机
- without_prior: top1=乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx / score=1.2 / section_match=False / equipment_match=False
- with_prior: top1=乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx / score=1.2 / section_match=False / equipment_match=False / prior_boost=0.1

### 临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx / 11 调试

- target: `section_type=commissioning_acceptance` / `equipment_type=vfd`
- case shortlist: 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx
- AI Wiki terms: 永磁电机变频改造方案族, 永磁, 永磁电机, 节能改造, 变频改造, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 高压变频器方案族, 高压变频器, 中压变频器, 变频装置, 变频柜, VFD
- AI Wiki products: 永磁电机变频改造方案族, 高压变频器方案族
- AI Wiki modules: 功率单元, IGBT, 冷却风机
- without_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=True / equipment_match=True
- with_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=True / equipment_match=True / prior_boost=0.39

### 临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx / 3 系统方案System Solution > 3.2 启动和同步过程描述 Description of Start and Sychronization

- target: `section_type=motor_spec` / `equipment_type=soft_starter`
- case shortlist: 099-230101-001西安陕鼓（秦风气体）技术协议.pdf, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx
- AI Wiki terms: 晶闸管
- AI Wiki products: none
- AI Wiki modules: 晶闸管
- without_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=False / equipment_match=False
- with_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=False / equipment_match=False / prior_boost=0.0

### 临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx / 1 工厂设计环境 Plant design data > 1.1 自然环境 Environment

- target: `section_type=site_conditions` / `equipment_type=vfd`
- case shortlist: 宝山钢铁股份有限公司三鼓风LCI改造方案.docx, 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx, 099-230101-001西安陕鼓（秦风气体）技术协议.pdf
- AI Wiki terms: 高压变频器方案族, 高压变频器, 中压变频器, 变频装置, 变频柜, VFD, 中压变频器采购技术协议, LCI 变频软起方案族, LCI, LCI 软起, 变频软起, 宝山钢铁股份有限公司三鼓风LCI改造方案.docx
- AI Wiki products: 高压变频器方案族, LCI 变频软起方案族
- AI Wiki modules: 功率单元, IGBT, 冷却风机
- without_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=False / equipment_match=True
- with_prior: top1=宝山钢铁股份有限公司三鼓风LCI改造方案.docx / score=1.2 / section_match=False / equipment_match=True / prior_boost=0.39
