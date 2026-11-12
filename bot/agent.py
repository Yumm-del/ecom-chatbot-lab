"""对话 Agent 主循环。

v1：一次模型调用，把商品数据丢进去，直接生成回复。
v2：两段式 —— 先路由（选 Skill），再执行（只加载被选中的 Skill 正文）。

为什么 v2 要拆成两段？
1. 路由和执行是两种能力，拆开才能分别评估、分别迭代
2. 只有被选中的 Skill 正文进上下文，才是真正的渐进式披露
3. badcase 归因时能区分"是路由错了还是执行错了"
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .memory import UserMemory
from .prompts import (
    V1_SYSTEM,
    V2_EXEC_SYSTEM,
    V2_ROUTER_SYSTEM,
    render_product,
    render_skills,
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SKILLS_DIR = ROOT / "skills"

DEFAULT_MODEL = "deepseek-chat"


# ------------------------------------------------------------
# 环境与资源加载
# ------------------------------------------------------------

def _load_env() -> None:
    """读取项目根目录的 .env（如果存在），不覆盖已有的环境变量。

    这样既支持 .env 文件，也支持在 shell 里直接 export。
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_client():
    """构造 OpenAI 兼容客户端。

    DeepSeek 使用 OpenAI 的接口协议，只是换了 base_url。

    ⚠️ 为什么要传自定义 http_client：
    Windows 的系统代理写在注册表里，httpx 默认 trust_env=True 会通过
    urllib.request.getproxies() 读到它（比如 127.0.0.1:7897）。
    如果代理软件没开，就会报 "Connection refused"，但 DNS 和 TCP 其实是通的，
    排查起来非常误导。这里显式关掉代理继承。

    如果你的环境必须走代理，把下面的 False 改成 True，或改用 curl 已验证的路径。
    """
    _load_env()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY。请在项目根目录创建 .env，内容：\n"
            "DEEPSEEK_API_KEY=你的key"
        )

    import httpx
    from openai import OpenAI

    http_client = httpx.Client(trust_env=False, timeout=60.0)
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def load_products() -> dict[str, dict]:
    """加载模拟商品库，返回 {product_id: product}。"""
    data = json.loads((DATA_DIR / "products.json").read_text(encoding="utf-8"))
    return {p["id"]: p for p in data["products"]}


def load_skill_docs(names: list[str], skills_dir: Path | None = None) -> dict[str, str]:
    """只读取被选中的 Skill 正文，做到按需加载。

    参数：
        names      : Skill 名称列表
        skills_dir : 指定 Skill 根目录。传 None 用默认的 skills/。
                     用于版本对照——v2 用冻结快照，v3 用最新定义。

    注意：这里读的是 SKILL.md 去掉 frontmatter 后的正文部分。
    frontmatter（name / description）属于第一层元数据，路由阶段已用到。
    """
    base = Path(skills_dir) if skills_dir else SKILLS_DIR
    docs: dict[str, str] = {}
    for name in names:
        path = base / name / "SKILL.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            text = parts[2] if len(parts) >= 3 else text
        docs[name] = text.strip()
    return docs


# ------------------------------------------------------------
# 槽位抽取（规则式，不用模型）
# ------------------------------------------------------------

_HEIGHT_PATTERNS = [
    r"身高\s*(\d{3})\s*(?:cm|厘米|公分)?",
    r"(\d{3})\s*(?:cm|厘米|公分)\s*(?:身高)?",
    r"1[\.点](\d{2})\s*(?:m|米)?",
]

# 体重单位列表：(正则, 是否需要把"斤"换算成公斤)
#
# ⚠️ 两个踩过的坑，改这个列表前务必先看：
#
# 坑 1：「斤」的模式必须排在最前面。
#   如果先跑 `体重\s*(\d{2,3})` 这种不带单位的模式，"我体重120斤" 会被匹配成
#   "体重120"，group(0) 里根本没有"斤"字，后面就再也没机会除以 2 了。
#
# 坑 2：不能用 `"斤" in 匹配文本` 判断单位。
#   "公斤" 里面就含"斤"字，会把"60公斤"换算成 30kg——
#   而且是静默的，用户永远不知道自己的体重被砍了一半。
#   所以这里改成给每个模式配一个显式的布尔标志，不靠字符串猜测。
_WEIGHT_PATTERNS = [
    (r"(\d{2,3})\s*斤", True),                       # 120斤 = 60kg
    (r"体重\s*(\d{2,3})\s*(?:kg|公斤|千克)", False),
    (r"(\d{2,3})\s*(?:kg|公斤|千克)", False),
    (r"体重\s*(\d{2,3})", False),                    # 只说了"体重60"，按 kg 处理
]


def extract_slots(message: str) -> dict:
    """从用户原话里抽取身高、体重。

    为什么用规则而不用模型？
    因为这一步要写进长期记忆，错一次会污染后续所有对话。
    规则虽然覆盖不全，但**不会编**——这正是记忆层最需要的性质。

    返回：{"height_cm": 165} 这类字典，抽不到就不放键。
    """
    slots: dict[str, float] = {}

    for pat in _HEIGHT_PATTERNS:
        m = re.search(pat, message)
        if m:
            raw = m.group(1)
            # "1.65" / "1点65" 这种写法要还原成 165
            if len(raw) == 2 and ("1" in message[:2] or "." in m.group(0) or "点" in m.group(0)):
                value = float(f"1.{raw}") * 100
            else:
                value = float(raw)
            if 120 <= value <= 220:
                slots["height_cm"] = round(value)
                break

    for pat, is_jin in _WEIGHT_PATTERNS:
        m = re.search(pat, message)
        if not m:
            continue
        value = float(m.group(1))
        if is_jin:
            value = value / 2  # 1 斤 = 0.5 kg
        if 30 <= value <= 150:
            slots["weight_kg"] = round(value, 1)
            break

    return slots


def detect_fit_feedback(message: str) -> str | None:
    """识别用户对已有尺码的反馈，用于写入 L3。

    只认明确的三个信号，避免把"还行"这类模糊表达当成结论。
    """
    if re.search(r"太紧|偏紧|勒|紧了", message):
        return "too_tight"
    if re.search(r"太松|偏大|大了|宽松得", message):
        return "too_loose"
    if re.search(r"刚刚好|正好|很合身", message):
        return "just_right"
    return None


# ------------------------------------------------------------
# 模型调用
# ------------------------------------------------------------

class UsageMeter:
    """token 用量累加器。

    **目的**：把每次模型调用的 token 消耗记录下来，支撑成本核算。

    **为什么不用全局变量**：全局变量在并发下会串数据，而且测试时无法隔离。
    这里做成一个可以随意 new 的小对象，谁需要谁创建，用完就丢。

    **为什么要分 stage**：v2/v3 有两段调用（路由 + 执行）。
    只看总数不知道钱花在哪一段，就没法做"路由换小模型"这类优化决策。
    """

    def __init__(self) -> None:
        self.prompt_tokens = 0        # 输入 token（system + 历史 + 用户消息）
        self.completion_tokens = 0    # 输出 token（模型生成的回复）
        self.total_tokens = 0
        self.calls = 0                # 调用次数，乘价格时要用
        self.by_stage: dict[str, dict] = {}

    def add(self, usage, stage: str = "other") -> None:
        """记录一次调用的用量。

        参数：
            usage : OpenAI SDK 返回的 usage 对象（可能是 None）
            stage : "router" / "exec" / "judge" 等，用于拆分成本

        为什么要 getattr 兜底：
        部分 OpenAI 兼容服务不返回 usage 字段，直接 .prompt_tokens 会 AttributeError。
        """
        if usage is None:
            return
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        total = getattr(usage, "total_tokens", 0) or (prompt + completion)

        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.calls += 1

        bucket = self.by_stage.setdefault(
            stage, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        )
        bucket["prompt_tokens"] += prompt
        bucket["completion_tokens"] += completion
        bucket["calls"] += 1

    def to_dict(self) -> dict:
        """导出成可直接塞进 JSON 的字典。"""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
            "by_stage": self.by_stage,
        }


def chat(
    client,
    messages: list[dict],
    temperature: float = 0.0,
    meter: UsageMeter | None = None,
    stage: str = "other",
) -> str:
    """单次模型调用。

    温度固定为 0，是为了让评测结果尽量可复现。
    即使如此，模型输出仍可能波动——这种波动本身就是需要记录的现象。

    参数：
        meter : 传入 UsageMeter 就会记录本次用量；传 None 时行为与旧版完全一致。
        stage : 这次调用属于哪一段（router / exec / judge），用于成本拆分。

    返回：
        模型回复的纯文本（空回复返回空字符串）。
    """
    resp = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=800,
    )

    # 用量记录放在取内容之前，保证即使回复为空也照常记账
    if meter is not None:
        meter.add(getattr(resp, "usage", None), stage=stage)

    return resp.choices[0].message.content or ""


def _parse_json(text: str) -> dict:
    """从模型输出里抠出 JSON。

    模型经常会加 ```json 围栏或前后缀解释，所以不能直接 json.loads。
    """
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


# ------------------------------------------------------------
# 两个版本的单轮对话
# ------------------------------------------------------------

def v1_turn(
    client,
    user_message: str,
    product: dict | None,
    history: list[dict],
    meter: UsageMeter | None = None,
) -> dict:
    """朴素版本：一次调用，无路由、无记忆、无 Skill。"""
    system = V1_SYSTEM.format(products_text=render_product(product))
    messages = [{"role": "system", "content": system}, *history]
    messages.append({"role": "user", "content": user_message})

    # v1 只有一段调用，stage 统一记 exec（和 v2 的执行段可比）
    reply = chat(client, messages, meter=meter, stage="exec")
    return {"reply": reply, "route": None, "skills": [], "escalate": None, "memory_writes": []}


def v2_turn(
    client,
    user_message: str,
    product: dict | None,
    memory: UserMemory,
    history: list[dict],
    skills_dir: Path | None = None,
    meter: UsageMeter | None = None,
) -> dict:
    """完整版本：先写记忆 → 再路由 → 只加载选中 Skill → 生成回复。

    meter 会分别记录 router 和 exec 两段的用量——这是成本核算的关键，
    因为"能不能把路由换成小模型"取决于路由段占了多大比例。
    """
    # --- 步骤 1：把用户本轮提供的信息写进记忆 ---
    memory_writes: list[str] = []
    notices: list[str] = []

    for key, value in extract_slots(user_message).items():
        conflict = memory.write(key, value, layer="L1", source="user_stated")
        memory_writes.append(f"L1.{key}={value}")
        if conflict:
            notices.append(conflict["message"])

    feedback = detect_fit_feedback(user_message)
    if feedback and product:
        memory.note_fit_feedback(
            category=product.get("category", "unknown"),
            brand=product.get("brand", "unknown"),
            size=str(memory.known_slots().get("usual_size", "unknown")),
            feedback=feedback,
        )
        memory_writes.append(f"L3.feedback={feedback}")

    # --- 步骤 2：路由 ---
    router_prompt = V2_ROUTER_SYSTEM.format(
        memory_text=memory.render_for_prompt(),
        product_text=render_product(product),
    )
    route_raw = chat(
        client,
        [
            {"role": "system", "content": router_prompt},
            {"role": "user", "content": user_message},
        ],
        meter=meter,
        stage="router",
    )
    route = _parse_json(route_raw)

    skills = route.get("skills") or []
    if not isinstance(skills, list):
        skills = []

    # 兜底：路由失败时按最保守的路径走（先查表 + 先澄清）
    if not skills:
        skills = ["clarify-missing-info", "lookup-size-chart"]
        route["_fallback"] = True

    # --- 步骤 3：只加载被选中的 Skill 正文 ---
    skill_docs = load_skill_docs(skills, skills_dir=skills_dir)

    # --- 步骤 4：生成回复 ---
    exec_prompt = V2_EXEC_SYSTEM.format(
        memory_text=memory.render_for_prompt(),
        product_text=render_product(product),
        skills_text=render_skills(skill_docs),
    )

    if notices:
        exec_prompt += "\n\n【需要告知用户的记忆更新】\n" + "\n".join(notices)

    messages = [{"role": "system", "content": exec_prompt}, *history]
    messages.append({"role": "user", "content": user_message})

    reply = chat(client, messages, meter=meter, stage="exec")

    return {
        "reply": reply,
        "route": route,
        "skills": sorted(skill_docs.keys()),
        "escalate": bool(route.get("escalate")) or ("escalate-to-human" in skills),
        "memory_writes": memory_writes,
    }
