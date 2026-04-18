from __future__ import annotations

import unittest

from app.services.llm.prompts.holistic import build_holistic_prompts


class HolisticPromptTests(unittest.TestCase):
    def test_build_holistic_prompts_uses_structured_contract_and_preserves_assets(self) -> None:
        system_prompt, user_prompt = build_holistic_prompts(
            global_params={
                "project_name": "某钢铁集团高炉鼓风机电机及 LCI 变频软起动系统改造项目",
                "voltage_level": "10kV",
                "quantity": "1套系统服务2台同步电机",
            },
            all_sections_markdown=(
                "## 4 LCI 变频软起动系统总体方案\n\n"
                "本章说明 LCI/SFC 软启动系统总体方案。\n\n"
                "[[ASSET:FIGURE:asset-001]]\n\n"
                "## 5 启动过程与同步切换控制方案\n\n"
                "系统在接近额定转速时完成同步切换，并与 DCS、PLC 协同。\n"
            ),
        )

        self.assertIn("<editing_contract>", system_prompt)
        self.assertIn("<global_params>", system_prompt)
        self.assertIn("不要输出 XML 标签", system_prompt)
        self.assertIn("<holistic_finalize_request>", user_prompt)
        self.assertIn("<section_manifest>", user_prompt)
        self.assertIn("4 LCI 变频软起动系统总体方案", user_prompt)
        self.assertIn("<term_unification_hints>", user_prompt)
        self.assertIn("LCI/SFC 变频软起动系统", user_prompt)
        self.assertIn("<asset_placeholder_inventory>", user_prompt)
        self.assertIn("[[ASSET:FIGURE:asset-001]]", user_prompt)
        self.assertNotIn("用 HTML 注释标注修改处", system_prompt)
        self.assertIn("不要输出解释说明、审查报告、修改记录或 HTML 注释", system_prompt)


if __name__ == "__main__":
    unittest.main()
