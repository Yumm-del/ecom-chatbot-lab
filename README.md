# ecom-chatbot-lab

> 东南亚电商场景下的 **AI 导购对话策略设计与评测**实验
> 以一个具体场景（售前尺码/适配咨询）为切口，走完：真实 badcase → 问题抽象 → Skill 定义 → Prompt 设计 → 评测集 → 真实运行 → 归因 → 迭代

**说明**：本项目是个人研究项目，与 Shopee / Sea 无隶属关系。商品、用户、订单数据全部为**模拟数据**，不包含任何真实平台数据，也未调用任何平台接口。

---

## 为什么做这个

电商 AI 助手有一个被公开数据反复验证的落差：

- 上海市消保委《2026 年 618 网购体验消费者调查报告》（2026-06-26，4308 份有效问卷）：**84.56% 的消费者尝试过 AI 选购，其中只有 16.06% 认为能精准匹配**。
- 印尼、巴西的公开投诉平台上，Shopee AI 客服的高频问题不是"答不上来"，而是：**答错规格、拖时间不升级、假装人类、机械无共情**。

也就是说，**模型能力已经不是瓶颈，场景拆解和边界设计才是。**

本项目想验证一个假设：

> 在同一个模型能力下，只要把「该问的先问、该查的查表、该记的记住、该转的转走」这四件事设计清楚，
> 售前尺码咨询的对话质量可以显著提升——而且这个提升可以被量化。

---

## 做了什么

| 层 | 内容 |
|---|---|
| **问题层** | 从公开 badcase 抽象出 5 类对话失败模式（见 [DESIGN.md](DESIGN.md#2-从-badcase-到问题类型)） |
| **能力层** | 5 个按 **Agent Skills 规范**（`SKILL.md` + YAML frontmatter）定义的 Skill |
| **记忆层** | 三层用户记忆模型：会话槽位 / 跨会话稳定偏好 / 负反馈历史 |
| **评测层** | 25 条场景用例 × 5 维 0–2 分 rubric，含"该转人工"与"数据缺失"的对抗用例 |
| **验证层** | v1 / v2 / v3 三版对照实验，真实调用模型，保留原始响应 |
| **迭代层** | v2 评测发现 4 处缺陷 → 只改 SKILL.md → v3 重新验证 |

---

## 目录

```
ecom-chatbot-lab/
├── DESIGN.md                 # 设计文档：badcase → 问题类型 → Skill → 评测
├── REPORT.md                 # 实验结果、迭代闭环、实验缺陷、上线缺口
├── data/
│   ├── products.json         # 模拟商品库（含结构化尺码表）
│   └── badcases.md           # 公开 badcase 来源与抽象过程
├── skills/                   # v1.1（v3 使用）
│   ├── clarify-missing-info/SKILL.md
│   ├── lookup-size-chart/SKILL.md
│   ├── recommend-size/SKILL.md
│   ├── remember-preference/SKILL.md
│   └── escalate-to-human/SKILL.md
├── skills_v2_snapshot/       # v1.0 冻结快照，保证 v2 可复现
├── bot/
│   ├── memory.py             # 三层记忆模型
│   ├── prompts.py            # v1 / v2 两套 system prompt
│   └── agent.py              # 主循环：槽位判断 → Skill 路由 → 生成
└── evals/
    ├── cases.json            # 25 条评测用例
    ├── rubric.md             # 5 维 0–2 分评分标准
    ├── run.py                # 运行器：跑对话、存原始响应与快照
    ├── judge_saved.py        # 对已存结果做 LLM 裁判初评（不重跑对话）
    ├── compare.py            # 多版本横向对比
    └── runs/                 # 每次运行的 report.md + results.json
```

---

## 怎么跑

```bash
pip install -r requirements.txt
cp .env.example .env          # 填入 DEEPSEEK_API_KEY

# 三个版本（用同一版用例）
python -m evals.run --version v1 --output evals/runs/v1
python -m evals.run --version v2 --skills-dir skills_v2_snapshot --output evals/runs/v2
python -m evals.run --version v3 --output evals/runs/v3

# 打分（对已存结果，不重跑对话）
python -m evals.judge_saved --input evals/runs/v1
python -m evals.judge_saved --input evals/runs/v2
python -m evals.judge_saved --input evals/runs/v3

# 横向对比
python -m evals.compare evals/runs/v1 evals/runs/v2 evals/runs/v3
```

---

## 结论

同一个模型（deepseek-chat），只改设计：

| 维度 | v1 基线 | v2 第一版设计 | v3 迭代后 |
|---|---|---|---|
| **总分（/10）** | **7.80** | **8.64** | **9.00** |
| 记忆使用 | 1.20 | 1.88 | **1.96** |
| 边界遵守 | 1.68 | **1.96** | **2.00** |
| 事实准确 | 1.96 | 1.88 | **2.00** |
| 澄清充分 | 1.40 | 1.56 | 1.60 |
| 表达共情 | **1.56** | 1.36 | 1.44 |

**两个关键点**：

1. **提升精准落在设计针对的维度**（记忆、边界），而不是全面普涨——这比"全面提升"更能说明因果。
2. **v2 → v3 只改了 SKILL.md，Prompt 和数据一字未动，还涨了 0.36**——说明规则层本身是可迭代的。

**唯一低于基线的是"表达共情"**：规则越细，话越像说明书。
这指向下一步方向——不是继续加规则，而是设计"什么时候把依据折叠起来说"。

完整分析、剩余问题、实验设计缺陷见 [REPORT.md](REPORT.md)。

> 本项目只验证"给定模拟商品数据下的对话行为"，**不验证**真实平台的效果、真实用户满意度或线上指标。
> 所有分数仅用于本项目内部版本间比较，不代表模型通用准确率。
