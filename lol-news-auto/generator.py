"""
文章生成模块 - 调用 Claude API 基于抓取的资讯生成图文文章
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional

from anthropic import Anthropic

logger = logging.getLogger(__name__)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """你是一位资深的《英雄联盟》电竞记者，擅长撰写专业、有深度、图文并茂的游戏资讯文章。

写作规范：
- 每篇文章 500 字左右（中文），结构清晰，包含标题、导语、正文、小结
- 用 Markdown 格式输出，标题用 # 一级标题
- 正文中需插入图片标注：![图片说明](图片路径) —— 我会给你可用的图片列表
- 内容要有观点和分析，不只是翻译新闻
- 面向中文 LOL 玩家，语气专业但不枯燥
- 每篇文章末尾标注来源和日期
- 严禁编造不存在的比赛结果或选手数据"""


def build_article_prompt(articles: list[dict], num: int = 10) -> str:
    """将抓取的资讯整理成 prompt，指定生成 10 篇不同主题的文章"""
    # 挑选信息量最丰富的文章
    scored = []
    for a in articles:
        score = len(a.get("summary", "")) + len(a.get("images", [])) * 5 + len(a.get("title", ""))
        scored.append((score, a))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [a for _, a in scored[:40]]

    # 构建资讯摘要
    digest_parts = []
    for i, a in enumerate(top):
        imgs = a.get("images", [])[:3]
        img_lines = "\n".join([f"  图片: {img}" for img in imgs]) if imgs else "  (无图片)"
        digest_parts.append(
            f"[{i+1}] 来源:{a['source_name']}\n"
            f"  标题: {a['title']}\n"
            f"  链接: {a['url']}\n"
            f"  摘要: {a.get('summary', '')[:200]}\n"
            f"{img_lines}"
        )

    digest = "\n\n".join(digest_parts)

    return f"""以下是今天（{datetime.now().strftime('%Y年%m月%d日')}）从多个来源抓取的英雄联盟相关资讯。

请基于这些资讯，选择 10 个不同的角度/主题，撰写 10 篇图文并茂的文章。
要求：
- 每篇文章约 500 字
- 每篇文章至少插入 2-3 张来自对应资讯的图片（使用上面提供的图片路径）
- 10 篇文章覆盖不同主题（如新英雄、赛事、版本更新、皮肤、花边新闻等）
- 输出格式：用 --- 分隔每篇文章，每篇文章是完整的 Markdown

## 今日资讯素材

{digest}

## 输出要求

请生成 10 篇文章，每篇之间用单独一行 `---` 分隔。
每篇文章格式：
```
# 文章标题

![图片说明](images/子目录/文件名.jpg)

正文内容...

![图片说明](images/子目录/文件名.jpg)

更多正文...

---
```
"""


def generate_articles(articles: list[dict], num: int = 10, model: str = "claude-sonnet-4-6") -> str:
    """调用 Claude API 生成 10 篇文章的 Markdown 文本"""
    prompt = build_article_prompt(articles, num)

    logger.info(f"Calling Claude API ({model}) to generate {num} articles ...")
    logger.info(f"Prompt length: {len(prompt)} chars, based on {len(articles)} source articles")

    message = client.messages.create(
        model=model,
        max_tokens=16000,
        temperature=0.8,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract text from content blocks (handle ThinkingBlock, TextBlock, etc.)
    content = ""
    for block in message.content:
        if hasattr(block, "text"):
            content += block.text

    if not content:
        logger.error("No text content in response. Content blocks: %s", [type(b).__name__ for b in message.content])
        raise RuntimeError("Claude API returned no text content")

    usage_str = f"{message.usage.input_tokens} in / {message.usage.output_tokens} out" if hasattr(message, "usage") and message.usage else "usage n/a"
    logger.info(f"Generated {len(content)} chars, {usage_str}")
    return content


def split_articles(markdown_text: str) -> list[dict]:
    """将 Claude 返回的合并文本按 --- 拆分为独立文章"""
    raw_articles = markdown_text.split("\n---\n")
    parsed = []

    for i, raw in enumerate(raw_articles):
        raw = raw.strip().lstrip("---\n").strip()
        if not raw or len(raw) < 100:
            continue

        # 提取标题
        lines = raw.split("\n")
        title = ""
        for line in lines:
            if line.startswith("# "):
                title = line.replace("# ", "").strip()
                break

        parsed.append({
            "index": i + 1,
            "title": title or f"英雄联盟日报 #{i+1}",
            "content": raw,
        })

    return parsed


def save_articles(parsed: list[dict], output_dir: str, image_dir: str = "images") -> list[str]:
    """保存文章为 Markdown，修正图片路径并复制引用图片到文章目录"""
    os.makedirs(output_dir, exist_ok=True)
    saved = []

    # 计算图片目录相对于输出目录的路径
    import shutil
    output_abs = os.path.abspath(output_dir)
    image_abs = os.path.abspath(image_dir)

    try:
        rel_img = os.path.relpath(image_abs, output_abs)
    except ValueError:
        rel_img = "../images"

    for art in parsed:
        idx = art["index"]
        content = art["content"]

        # 修正图片路径: images/xxx/yy.jpg -> ../images/xxx/yy.jpg
        content = content.replace('](images/', f']({rel_img}/')

        # 复制引用的图片到文章目录
        import re
        img_pattern = re.compile(r'!\[.*?\]\(([^)]+)\)')
        for m in img_pattern.finditer(content):
            src = m.group(1)
            src_abs = os.path.join(image_abs, os.path.relpath(src, "images")) if src.startswith("images/") else os.path.join(output_abs, src)
            # 标准化路径
            if src.startswith(rel_img + "/"):
                actual_rel = src[len(rel_img) + 1:]
                src_abs = os.path.join(image_abs, actual_rel)
            elif not os.path.isabs(src):
                src_abs = os.path.join(output_abs, src)

            if os.path.isfile(src_abs):
                dst_dir = os.path.join(output_dir, "images")
                os.makedirs(dst_dir, exist_ok=True)
                dst = os.path.join(dst_dir, os.path.basename(src_abs))
                if not os.path.exists(dst):
                    shutil.copy2(src_abs, dst)
                # 更新路径为本地 images/
                content = content.replace(f']({src})', f'](images/{os.path.basename(src_abs)})')

        safe_title = art["title"].replace("/", "-").replace(":", "：")[:50]
        filename = f"{idx:02d}-{safe_title}.md"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            f.write(f"\n\n---\n*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 自动生成 by Claude*")

        saved.append(filepath)
        logger.info(f"Saved: {filepath}")

    return saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Generator module loaded. Use generate_articles() with scraped data.")
