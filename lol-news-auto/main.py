#!/usr/bin/env python3
"""
英雄联盟每日资讯自动生成器
用法:
  python main.py                    # 抓取 + 生成 10 篇文章
  python main.py --scrape-only      # 仅抓取
  python main.py --generate-only    # 仅生成（使用上次抓取结果）
  python main.py --num 5            # 生成 5 篇
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from scraper import scrape_all
from generator import generate_articles, split_articles, save_articles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "run.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


def check_api_key():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("sk-ant-your-key"):
        logger.error("ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill in your key.")
        sys.exit(1)


def scrape_and_save(cache_path: Path) -> list[dict]:
    logger.info("=" * 50)
    logger.info("Phase 1: Scraping news sources ...")
    articles = scrape_all()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

    logger.info(f"Cached {len(articles)} articles to {cache_path}")
    return articles


def generate_and_save(articles: list[dict], output_dir: Path, num: int):
    logger.info("=" * 50)
    logger.info(f"Phase 2: Generating {num} articles via Claude API ...")

    markdown_text = generate_articles(articles, num=num)
    parsed = split_articles(markdown_text)

    today = datetime.now().strftime("%Y-%m-%d")
    day_dir = output_dir / today
    image_dir = output_dir / "images"
    saved = save_articles(parsed, str(day_dir), str(image_dir))

    # 写索引
    index_path = day_dir / "INDEX.md"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(f"# 英雄联盟日报 - {today}\n\n")
        f.write(f"共 {len(saved)} 篇文章，基于 {len(articles)} 条资讯生成。\n\n")
        for i, art in enumerate(parsed):
            f.write(f"{i+1}. [{art['title']}]({Path(saved[i]).name})\n")

    logger.info(f"Done! {len(saved)} articles saved to {day_dir}")
    logger.info(f"Index: {index_path}")

    return saved


def main():
    parser = argparse.ArgumentParser(description="LOL Daily News Generator")
    parser.add_argument("--scrape-only", action="store_true")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--num", type=int, default=10, help="Number of articles (default: 10)")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-6")
    args = parser.parse_args()

    cache_path = ROOT / "output" / "cache" / "latest_articles.json"
    today = datetime.now().strftime("%Y-%m-%d")
    output_dir = ROOT / "output"

    if args.generate_only:
        check_api_key()
        if not cache_path.exists():
            logger.error(f"No cache found at {cache_path}. Run without --generate-only first.")
            sys.exit(1)
        with open(cache_path, "r", encoding="utf-8") as f:
            articles = json.load(f)
        logger.info(f"Loaded {len(articles)} cached articles")
    else:
        articles = scrape_and_save(cache_path)

    if args.scrape_only:
        logger.info("Scrape-only mode. Done.")
        return

    check_api_key()
    generate_and_save(articles, output_dir, args.num)


if __name__ == "__main__":
    main()
