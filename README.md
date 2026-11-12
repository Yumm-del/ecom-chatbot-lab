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
| **决策层** | 测出问题之后怎么办：[PRIORITY.md](PRIORITY.md) 排优先级、[METRICS.md](METRICS.md) 映射业务指标、[COST.md](COST.md) 算成本账、[MULTILINGUAL.md](MULTILINGUAL.md) 测多语言 |

> **评分只是中间产物。** 一个只交付"分数"的评测，在评审眼里等于没做完——
> 后面四份文档才是把"哪里错了"变成"先修哪个、要花多少钱、修完怎么验证"的部分。

---

## 目录

```
ecom-chatbot-lab/
├── DESIGN.md                 # 设计文档：badcase → 问题类型 → Skill → 评测
├── REPORT.md                 # 实验结果、迭代闭环、实验缺陷、上线缺口
├── PRIORITY.md               # badcase 优先级矩阵：影响面 × 修复成本
├── METRICS.md                # 质量分 → 业务指标映射、埋点清单、A/B 设计
├── COST.md                   # 成本核算：两段式架构贵多少、怎么压回去
├── MULTILINGUAL.md           # 多语言测试：Taglish / 印尼语下的失效方式
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
├── tests/
│   └── test_slots.py         # 槽位抽取回归测试（记忆层入口，错一次污染全部对话）
└── evals/
    ├── cases.json            # 25 条评测用例
    ├── cases_multilingual.json  # 6 条 Taglish / 印尼语用例
    ├── rubric.md             # 5 维 0–2 分评分标准
    ├── annotation-guide.md   # 人工标注规范：流程、裁决判例、Kappa 一致性
    ├── run.py                # 运行器：跑对话、存原始响应与 token 用量
    ├── judge_saved.py        # 对已存结果做 LLM 裁判初评（不重跑对话）
    ├── compare.py            # 多版本横向对比
    ├── cost.py               # token 用量 → 成本，含优化杠杆的敏感性分析
    └── runs/                 # 每次运行的 report.md + results.json
```

---

## 怎么跑

```bash
pip install -r requirements.txt
cp .env.example .env          # 填入 DEEPSEEK_API_KEY

# 三个版本（用同一版用例）
# 注意 --reply-language zh：v2/v3 的冻结结果是在"永远中文"的 prompt 下跑出来的，
# 不加这个参数会用新的 auto 策略（跟随用户语言），复现不出原来的分数。
python -m evals.run --version v1 --output evals/runs/v1
python -m evals.run --version v2 --skills-dir skills_v2_snapshot --reply-language zh --output evals/runs/v2
python -m evals.run --version v3 --reply-language zh --output evals/runs/v3

# v4：v3 + 语言策略修正（默认 auto，跟随用户语言）
python -m evals.run --version v4 --judge --output evals/runs/v4

# 打分（对已存结果，不重跑对话）
python -m evals.judge_saved --input evals/runs/v1
python -m evals.judge_saved --input evals/runs/v2
python -m evals.judge_saved --input evals/runs/v3

# 横向对比
python -m evals.compare evals/runs/v1 evals/runs/v2 evals/runs/v3

# 多语言用例（Taglish / 印尼语）
python -m evals.run --version v3 --cases evals/cases_multilingual.json --judge \
    --reply-language zh --output evals/runs/multilingual/v3
python -m evals.run --version v4 --cases evals/cases_multilingual.json --judge \
    --output evals/runs/multilingual/v4

# 成本核算（token → 钱，含优化杠杆）
python -m evals.cost evals/runs/cost/v1 evals/runs/cost/v2 evals/runs/cost/v3

# 记忆层回归测试
python -m unittest discover tests
```

---

## 结论

同一个模型（deepseek-chat），只改设计：

| 维度 | v1 基线 | v2 第一版设计 | v3 迭代后 | 重复运行噪声 |
|---|---|---|---|---|
| **总分（/10）** | **7.80** | **8.64** | **9.00** | 0.08 / 0.12 |
| 记忆使用 | 1.20 | 1.88 | **1.96** | 0.08 ✅ |
| 边界遵守 | 1.68 | **1.96** | **2.00** | 0.04 ✅ |
| 事实准确 | 1.96 | 1.88 | **2.00** | 0.04 ✅ |
| 澄清充分 | 1.40 | 1.56 | 1.60 | 0.08 ❓ |
| 表达共情 | 1.56 | 1.36 | 1.44 | 0.08 ❌ 不可下结论 |

**三个关键点**：

1. **提升精准落在设计针对的维度**（记忆、边界），而不是全面普涨——这比"全面提升"更能说明因果。
2. **v2 → v3 只改了 SKILL.md，Prompt 和数据一字未动，还涨了 0.36**——说明规则层本身是可迭代的。
3. **补做了重复运行，然后撤回了自己的一条结论**：原来说"规则化伤共情"（1.56 → 1.44），
   复跑后 v1 与 v3 的区间**重叠**，这个差值就是测量误差本身。
   同样，A 类用例的噪声是 1.20，和它的提升是同一量级——**没有任何小于噪声的差值可以写进结论**。

> **分课别的结果不放在这里**：每类只有 5 条用例，一条用例波动 2 分，类别均分就动 0.4。
> 完整的分类别结果和重复运行噪声见 [REPORT.md](REPORT.md) §2.2。

完整分析、剩余问题、实验设计缺陷见 [REPORT.md](REPORT.md)。

---

## 三个"分数之外"的结论

| 结论 | 出处 |
|---|---|
| **两段式架构的成本是朴素版的 5 倍**，因为每一轮都重发整份 Skill 正文（实测占 exec 输入 85%）。加缓存 + 路由换规则能压到 2.3 倍 | [COST.md](COST.md) |
| **换一批语料，分数会骗人**：多语言集 v3 拿 9.67 分、v1 只有 8.17；但 v1 六条全部用用户的语言回复，v3 只有一条。根因是 prompt 第一行写着"请用中文回答用户"。改成参数后恢复到 4/6（v4），剩下 2 条是 Skill 的输出模板仍用中文写死——**5 维 rubric 完整地漏掉了"用户能不能看懂"** | [MULTILINGUAL.md](MULTILINGUAL.md) |
| **没有重复运行，0.1 量级的差值都不算数**：共情维度上 v1 区间 [1.52, 1.56] 与 v3 区间 [1.44, 1.52] 重叠，原来"规则化伤共情"的结论已撤回；A 类的运行间噪声（1.20）与它 v1→v3 的提升（1.60）是同一量级 | [REPORT.md](REPORT.md) §2.2 / §4 发现 4 |

> 本项目只验证"给定模拟商品数据下的对话行为"，**不验证**真实平台的效果、真实用户满意度或线上指标。
> 所有分数仅用于本项目内部版本间比较，不代表模型通用准确率。
