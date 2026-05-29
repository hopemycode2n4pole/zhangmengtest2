"""
多源 LOL 资讯抓取 + 图片提取模块
策略: 文章详情页(SSR) + 搜索发现 + RSS
"""

import os
import re
import json
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urljoin, urlparse, quote

import requests
import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# LOL 相关性关键词（中英文）
LOL_KEYWORDS = [
    "英雄联盟", "lol", "league of legends", "leagueoflegends",
    "召唤师", "峡谷", "符文", "排位", "段位",
    "新英雄", "皮肤", "赛事", "战队", "选手", "冠军",
    "世界赛", "msi", "s赛", "季中赛",
    "lpl", "lck", "lec", "lcs", "t1", "blg", "hle", "dk", "g2",
    "faker", "zeus", "oner", "gumayusi", "keria", "doran",
    "版本更新", "补丁", "patch", "平衡调整", "buff", "nerf",
    "拳头", "riot games", "大乱斗", "aram", "嚎哭深渊",
    "打野", "中单", "上单", "adc", "辅助", "jungler", "mid lane", "top lane",
    "ranked", "clash", "冠军皮肤", "worlds", "world championship",
    "demacia", "noxus", "德玛西亚", "诺克萨斯",
    "怀旧服", "uzi", "theshy", "jackeylove", "showmaker",
    "locke", "洛克", "t1冠军", "五人排位", "ranked 5",
    "海克斯", "hextech", "极地大乱斗", "howling abyss",
    "26.1", "26.2", "26.11", "26.12", "26.13",  # patch numbers
]

# 非LOL的泛游戏/体育关键词 - 用于排除
NOT_LOL_KEYWORDS = [
    "足彩", "竞彩", "法网", "网球", "f1", "nba常规赛", "cba",
    "一加手机", "turbo", "红色沙漠", "黑神话", "原神", "星铁",
    "文博日历", "木拱廊桥", "意大利市长",
    "英超", "欧冠", "世界杯预选", "fifa",
    "pubg", "apex", "valorant", "cs2", "dota2",
    "崩坏", "明日方舟", "鸣潮", "绝区零",
]

def _is_lol_relevant(text: str) -> bool:
    """判断内容是否与 LOL 相关（双重检查：必须有LOL关键词且无非LOL关键词）"""
    low = text.lower()

    # 排除明显非LOL的内容
    for nk in NOT_LOL_KEYWORDS:
        if nk in low:
            return False

    # 必须有至少一个LOL关键词
    match_count = sum(1 for kw in LOL_KEYWORDS if kw in low)
    return match_count >= 1

def _fetch_page(url: str, timeout: int = 20) -> Optional[requests.Response]:
    """安全抓取页面，自动处理编码"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return None
        # 强制 UTF-8（中文站通常为 utf-8 或 gbk）
        if r.encoding and "iso-8859" in r.encoding.lower():
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        logger.debug(f"Fetch failed [{url[:80]}]: {e}")
        return None

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")

# ===================== 图片处理 =====================

def extract_image_urls(html: str, base_url: str) -> list[str]:
    """从 HTML 中提取有效图片 URL"""
    soup = BeautifulSoup(html, "lxml")
    seen = set()
    urls = []

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if not src:
            continue
        full = urljoin(base_url, src)

        # 过滤小图标和无关图
        low = full.lower()
        skip_words = ["icon", "logo", "avatar", "emoji", "favicon", "50x50", "32x32",
                       "qr_code", "barcode", "pixel", "1x1", "tracking", "beacon",
                       "weixin", "wechat", "share", "appdownload"]
        if any(k in low for k in skip_words):
            continue

        if full in seen:
            continue
        seen.add(full)
        urls.append(full)

    return urls


def download_image(url: str, subdir: str = "") -> Optional[str]:
    """下载图片到本地，返回相对路径。失败返回 None"""
    try:
        safe_subdir = re.sub(r"[^a-zA-Z0-9_\-一-鿿]", "_", subdir) if subdir else "general"
        target_dir = os.path.join(IMAGE_DIR, safe_subdir)
        os.makedirs(target_dir, exist_ok=True)

        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()

        # 只保留 >= 10KB 的图（过滤缩略图）
        if len(r.content) < 10240:
            return None

        ct = r.headers.get("Content-Type", "")
        ext = ".jpg"
        if "png" in ct:
            ext = ".png"
        elif "webp" in ct:
            ext = ".webp"
        elif "gif" in ct:
            ext = ".gif"

        name = hashlib.md5(url.encode()).hexdigest()[:12] + ext
        path = os.path.join(target_dir, name)
        with open(path, "wb") as f:
            f.write(r.content)

        return os.path.join("images", safe_subdir, name)
    except Exception as e:
        logger.debug(f"Image download failed [{url[:80]}]: {e}")
        return None


# ===================== 文章内容提取 =====================

def extract_article_content(html: str, url: str) -> dict:
    """从文章详情页 HTML 提取标题、正文、图片"""
    soup = BeautifulSoup(html, "lxml")

    # 提取标题
    title = ""
    for sel in ["h1", ".post_title", ".article-title", "title", "[class*=title]"]:
        t = soup.select_one(sel)
        if t and len(t.get_text(strip=True)) > 3:
            title = t.get_text(strip=True)
            break
    if not title:
        title = soup.title.get_text(strip=True) if soup.title else ""

    # 提取正文
    body_selectors = [
        ".post_body", ".article-body", "#article", ".article-content",
        ".post-content", ".article_text", "[class*=article-body]",
        "[class*=post_content]", ".content", "#endText",
    ]
    body_text = ""
    body_html = ""
    for sel in body_selectors:
        elem = soup.select_one(sel)
        if elem and len(elem.get_text(strip=True)) > 100:
            body_text = elem.get_text(separator="\n", strip=True)
            body_html = str(elem)
            break

    # 如果没找到，用整页文本（取最长段落）
    if not body_text:
        paragraphs = [p.get_text(strip=True) for p in soup.find_all(["p", "div"]) if len(p.get_text(strip=True)) > 50]
        body_text = "\n".join(paragraphs[:20])

    # 提取图片
    img_urls = extract_image_urls(body_html or html, url)

    return {
        "title": title,
        "url": url,
        "body_text": body_text[:3000],
        "raw_images": img_urls[:10],
    }


# ===================== 各源抓取逻辑 =====================

def _fetch_article(url: str, source_name: str, subdir: str, max_imgs: int = 6) -> Optional[dict]:
    """通用文章抓取：下载页面 -> 提取内容 -> 下载图片 -> 相关性过滤"""
    resp = _fetch_page(url)
    if not resp:
        return None

    info = extract_article_content(resp.text, url)
    if not info["title"] or len(info["body_text"]) < 80:
        return None

    # LOL 相关性检查
    combined = info["title"] + " " + info["body_text"][:500]
    if not _is_lol_relevant(combined):
        logger.debug(f"  Not LOL-relevant: {info['title'][:60]}")
        return None

    # 下载图片
    local_imgs = []
    for img_url in info["raw_images"][:max_imgs]:
        local = download_image(img_url, subdir=subdir)
        if local:
            local_imgs.append(local)

    info["images"] = local_imgs
    info["source_name"] = source_name
    info["summary"] = info["body_text"][:300]
    return info


def scrape_163_articles(max_count: int = 15) -> list[dict]:
    """从网易游戏频道抓取 LOL 相关文章"""
    results = []
    source_pages = [
        "https://dy.163.com/tag/%E8%8B%B1%E9%9B%84%E8%81%94%E7%9B%9F/",  # 英雄联盟标签页
    ]

    for page_url in source_pages:
        resp = _fetch_page(page_url, timeout=15)
        if not resp:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        links = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not href.startswith("http"):
                href = urljoin(page_url, href)
            if href in seen:
                continue
            seen.add(href)

            text = a.get_text(strip=True)
            is_article = any(k in href for k in ["/article/", "/dy/article/", "/a/", "/news/"])
            is_lol = _is_lol_relevant(text + " " + href)
            if is_article and is_lol and len(text) > 8:
                links.append((text, href))

        logger.info(f"  163 found {len(links)} LOL-related links from {page_url}")

        for title, link in links[:max_count]:
            article = _fetch_article(link, "网易", "163", max_imgs=5)
            if article:
                if not article["title"] or len(article["title"]) < 3:
                    article["title"] = title
                results.append(article)

    return results


def scrape_bing_news(query: str = "英雄联盟", max_count: int = 20) -> list[dict]:
    """通过 Bing News 搜索发现文章，然后抓取详情页"""
    results = []
    encoded = quote(query)
    search_urls = [
        f"https://www.bing.com/news/search?q={encoded}&qft=interval%3d%227%22&FORM=YFNR",
        f"https://www.bing.com/news/search?q={encoded}+LOL",
    ]

    for surl in search_urls:
        resp = _fetch_page(surl, timeout=15)
        if not resp or len(resp.text) < 5000:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        links = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if href.startswith("http") and "bing.com" not in href and len(text) > 10:
                if href not in seen:
                    seen.add(href)
                    links.append((text, href))

        logger.info(f"  Bing found {len(links)} article links")

        for title, link in links[:max_count]:
            article = _fetch_article(link, "Bing News", "bing", max_imgs=5)
            if article:
                if not article["title"] or len(article["title"]) < 3:
                    article["title"] = title
                results.append(article)

    return results


def scrape_rss_feeds() -> list[dict]:
    """抓取可用的 RSS 源"""
    feeds = [
        ("Dot Esports", "https://dotesports.com/league-of-legends/feed"),
        ("Esports.gg", "https://esports.gg/feed/"),
        ("Inven Global", "https://www.invenglobal.com/lol/rss"),
    ]
    results = []

    for name, url in feeds:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            feed = feedparser.parse(r.text)
            logger.info(f"  RSS {name}: {len(feed.entries)} entries")

            for entry in feed.entries[:10]:
                link = entry.get("link", "")
                if not link:
                    continue

                article = _fetch_article(link, name, "rss", max_imgs=5)
                if article:
                    if not article["title"]:
                        article["title"] = entry.get("title", "")
                    article["summary"] = entry.get("summary", article.get("body_text", "")[:300])
                    results.append(article)

        except Exception as e:
            logger.warning(f"RSS {name} failed: {e}")

    return results


def scrape_specific_articles() -> list[dict]:
    """抓取已知 SSR 友好的文章 URL - 成功率最高的方法"""
    known_urls = [
        # 网易 - 手游NBA联动
        ("https://www.163.com/dy/article/KTVLVNPL0511CPVM.html", "网易"),
        # 直播吧 - HLE vs DK
        ("https://news.zhibo8.com/game/2026-05-26/6a153d421c158native.htm", "直播吧"),
        # Inven Global - 新英雄洛克
        ("https://www.invenglobal.com/lol/articles/22272/riot-games-announces-lol-2026-season-2-update-new-assassin-roke-revealed", "Inven"),
        # 直播吧 - T1皮肤官宣
        ("https://wap.zhibo8.com/news/web/other/2026-05-28/6a1791e9e45dfnative.htm", "直播吧"),
        # 17173 - 开发者更新
        ("https://news.17173.com/content/05282026/114420039.shtml", "17173"),
        # esports.gg - Locke
        ("https://esports.gg/news/league-of-legends/league-of-legends-locke-release-date-and-abilities/", "esports.gg"),
        # gameRiv - T1 skins
        ("https://gameriv.com/lol-t1-worlds-2025-skins-confirmed-full-champion-list-revealed/", "GameRiv"),
        # TweakTown - 26.11 patch
        ("https://www.tweaktown.com/news/111843/league-of-legends-patch-26-11-shakes-up-support-role-nerfs-smolder-teemo-and-deathfire-touch/index.html", "TweakTown"),
        # 163 - LPL假赛辟谣
        ("http://360game.360.cn/article/content?id=69febab924bebef8bc529127", "360游戏"),
        # 游侠网 - Locke介绍
        ("https://gl.ali213.net/html/2026-4/1763683.html", "游侠网"),
        # sheepe sports - dev update
        ("https://www.sheepesports.com/de/lol/articles/t1-worlds-skins-coming-and-ranked-5v5-announced-in-the-last-dev-update/en", "SheepEsports"),
        # escapist - patch 26.6
        ("https://www.escapistmagazine.com/news-league-of-legends-patch-26-6/", "Escapist"),
        # u.gg - patch 26.11
        ("https://u.gg/lol/news/patch-26-11-official-notes-league-of-legends", "U.GG"),
    ]

    results = []
    for url, source_name in known_urls:
        logger.info(f"  Fetching: {url[:80]}...")
        domain = urlparse(url).netloc.replace(".", "_")
        article = _fetch_article(url, source_name, domain, max_imgs=8)
        if article:
            results.append(article)
            logger.info(f"    OK: {article['title'][:60]}, {len(article['images'])} images")
        else:
            logger.info(f"    Skip (not accessible or not LOL-relevant)")

    return results


# ===================== 主入口 =====================

def scrape_all() -> list[dict]:
    """聚合所有源"""
    all_articles = []

    # 策略1: 已知文章 URL（最可靠）
    logger.info("=== Strategy 1: Known article URLs ===")
    try:
        known = scrape_specific_articles()
        all_articles.extend(known)
        logger.info(f"  Got {len(known)} articles")
    except Exception as e:
        logger.error(f"Strategy 1 failed: {e}")

    # 策略2: 网易163
    logger.info("=== Strategy 2: 163.com ===")
    try:
        from163 = scrape_163_articles(max_count=10)
        all_articles.extend(from163)
        logger.info(f"  Got {len(from163)} articles")
    except Exception as e:
        logger.error(f"Strategy 2 failed: {e}")

    # 策略3: RSS 源
    logger.info("=== Strategy 3: RSS feeds ===")
    try:
        rss = scrape_rss_feeds()
        all_articles.extend(rss)
        logger.info(f"  Got {len(rss)} articles")
    except Exception as e:
        logger.error(f"Strategy 3 failed: {e}")

    # 策略4: Bing News (可能受限于网络)
    logger.info("=== Strategy 4: Bing News ===")
    try:
        bing = scrape_bing_news(max_count=15)
        all_articles.extend(bing)
        logger.info(f"  Got {len(bing)} articles")
    except Exception as e:
        logger.error(f"Strategy 4 failed: {e}")

    # 去重
    seen_urls = set()
    deduped = []
    for a in all_articles:
        url = a.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped.append(a)

    logger.info(f"=== Total: {len(deduped)} unique articles ===")
    return deduped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    articles = scrape_all()
    print(f"\nFinal: {len(articles)} articles")
    for i, a in enumerate(articles[:5]):
        print(f"  [{i+1}] {a['title'][:80]}")
        print(f"       Source: {a['source_name']}, Images: {len(a['images'])}, Chars: {len(a['body_text'])}")
