#!/usr/bin/env python3
"""Read-only Zotero -> Markdown / FreeMind mindmap exporter.

This is the standalone prototype for the integration plan in
``docs/文献管理工具选型与集成方案.md``. It uses only the Python standard
library and the Zotero local API (read-only; Zotero 10+ also supports writes,
which this prototype does not use).

Typical use::

    python3 scripts/zotero_mindmap.py --ping
    python3 scripts/zotero_mindmap.py --collections
    python3 scripts/zotero_mindmap.py --collection ABCD1234 --out map.md
    python3 scripts/zotero_mindmap.py --collection ABCD1234 --format mm --out map.mm
    python3 scripts/zotero_mindmap.py --collection ABCD1234 --group-by color
    python3 scripts/zotero_mindmap.py --demo --out demo.md

The output is deterministic: every annotation leaf carries its item key and
page label so it can be traced back to Zotero. No LLM is involved.
"""

from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape


DEFAULT_BASE_URL = "http://127.0.0.1:23119/api"
DEFAULT_COLORS = {
    "#ffd400": "核心结论",
    "#ff6666": "局限/冲突",
    "#5fb236": "方法/实验",
    "#2ea8e5": "数据/结果",
    "#a28ae5": "背景/相关工作",
    "#e56eee": "待验证",
    "#f19837": "代码/数据",
    "#aaaaaa": "其他",
}
COLOR_ORDER = list(DEFAULT_COLORS)


class ZoteroError(RuntimeError):
    """A user-facing Zotero connection or API error."""


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def truncate(text: str, limit: int) -> str:
    text = normalize_space(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def data_of(obj: dict) -> dict:
    return obj.get("data") or {}


def item_key(obj: dict) -> str:
    return obj.get("key") or data_of(obj).get("key") or ""


class _OutlineParser(HTMLParser):
    """Convert Zotero note HTML into a flat ``(level, text, kind)`` outline.

    Headings become levels 1-6. List items become one level below the most
    recent heading, plus their nesting depth. Paragraphs are only used when
    the note has no headings or lists, which keeps mindmaps from filling up
    with prose.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._buf: list[str] = []
        self._mode: tuple[str, int] | None = None
        self._section_level = 1
        self._list_depth = 0
        self._in_li = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._mode = ("heading", int(tag[1]))
        elif tag == "li":
            self._flush()
            self._in_li += 1
            self._mode = ("list", self._list_depth)
        elif tag in ("ul", "ol"):
            self._flush()
            self._list_depth += 1
        elif tag == "p":
            if self._in_li:
                return
            self._flush()
            self._mode = ("paragraph", 0)
        elif tag == "br":
            self._buf.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.lower() == "br":
            self._buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if re.fullmatch(r"h[1-6]", tag) or tag in ("li", "p"):
            self._flush()
            self._mode = None
            if tag == "li":
                self._in_li = max(0, self._in_li - 1)
        elif tag in ("ul", "ol"):
            self._flush()
            self._list_depth = max(0, self._list_depth - 1)

    def handle_data(self, data: str) -> None:
        self._buf.append(data)

    def _flush(self) -> None:
        text = normalize_space("".join(self._buf))
        self._buf.clear()
        if not text or not self._mode:
            return
        kind, value = self._mode
        if kind == "heading":
            self._section_level = value
            self.items.append({"level": value, "text": text, "kind": "heading"})
        elif kind == "list":
            level = min(6, self._section_level + max(1, value))
            self.items.append({"level": level, "text": text, "kind": "list"})
        elif kind == "paragraph":
            level = min(6, self._section_level + 1)
            self.items.append({"level": level, "text": text, "kind": "paragraph"})

    def result(self) -> list[dict]:
        self.close()
        return self.items


def html_to_outline(html: str) -> list[dict]:
    parser = _OutlineParser()
    parser.feed(html or "")
    return parser.result()


def strip_html(html: str) -> str:
    return normalize_space(re.sub(r"<[^>]+>", " ", html or ""))


def annotation_page(data: dict) -> str:
    label = data.get("annotationPageLabel")
    if label:
        return str(label)
    position = data.get("annotationPosition")
    if isinstance(position, str):
        try:
            position = json.loads(position)
        except (TypeError, ValueError):
            position = {}
    if isinstance(position, dict) and isinstance(position.get("pageIndex"), int):
        return str(position["pageIndex"] + 1)
    return ""


def annotation_sort(data: dict) -> tuple[int, str]:
    page = annotation_page(data)
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        page_number = 10**6
    return page_number, str(data.get("annotationSortIndex") or "")


def color_label(color: str | None, labels: dict[str, str] | None = None) -> str:
    labels = labels or DEFAULT_COLORS
    key = (color or "").lower()
    return labels.get(key, "未分类" if not color else color)


def color_rank(label: str, labels: dict[str, str] | None = None) -> tuple[int, str]:
    labels = labels or DEFAULT_COLORS
    names = list(labels.values())
    return (names.index(label) if label in names else len(names), label)


def node(title: str, kind: str, ref: dict | None = None) -> dict:
    return {"title": title, "kind": kind, "ref": ref or {}, "children": []}


def item_meta(obj: dict) -> dict:
    data = data_of(obj)
    creators = []
    for creator in data.get("creators") or []:
        name = creator.get("name")
        if not name:
            name = " ".join(
                part
                for part in (creator.get("firstName"), creator.get("lastName"))
                if part
            )
        if name:
            creators.append(name)
    year = ""
    for field in ("date", "dateAdded"):
        match = re.search(r"(?:19|20)\d{2}", str(data.get(field) or ""))
        if match:
            year = match.group(0)
            break
    tags = [
        tag.get("tag")
        for tag in data.get("tags") or []
        if isinstance(tag, dict) and tag.get("tag")
    ]
    return {
        "key": item_key(obj),
        "title": normalize_space(data.get("title") or "未命名条目"),
        "year": year,
        "creators": creators,
        "abstract": normalize_space(data.get("abstractNote") or ""),
        "doi": normalize_space(data.get("DOI") or ""),
        "url": normalize_space(data.get("url") or ""),
        "tags": tags,
        "collections": data.get("collections") or [],
    }


def paper_title(meta: dict) -> str:
    prefix = f"[{meta['year']}] " if meta["year"] else "[年份未知] "
    title = prefix + meta["title"]
    if meta["creators"]:
        first = meta["creators"][0]
        suffix = " et al." if len(meta["creators"]) > 1 else ""
        title += f" — {first}{suffix}"
    return title


def annotation_node(meta: dict, annotation: dict) -> dict | None:
    data = data_of(annotation)
    text = normalize_space(data.get("annotationText") or "")
    comment = normalize_space(data.get("annotationComment") or "")
    kind = (data.get("annotationType") or "highlight").lower()
    placeholder = False
    if not text and not comment:
        if kind == "image":
            text, placeholder = "[区域标注]", True
        elif kind == "ink":
            text, placeholder = "[手绘标注]", True
        else:
            return None
    parts = []
    if text:
        parts.append(text if placeholder else f"“{truncate(text, 180)}”")
    if comment:
        parts.append(f"批注：{truncate(comment, 120)}")
    page = annotation_page(data)
    if page:
        parts.append(f"p.{page}")
    return node(
        " — ".join(parts),
        "annotation",
        {
            "item": meta["key"],
            "annotation": item_key(annotation),
            "page": page,
            "color": data.get("annotationColor") or "",
        },
    )


def note_outline_nodes(meta: dict, note: dict) -> list[dict]:
    data = data_of(note)
    ref = {"item": meta["key"], "note": item_key(note)}
    outline = html_to_outline(data.get("note") or "")
    if not outline:
        text = strip_html(data.get("note") or "")
        return [node(f"笔记：{truncate(text, 180)}", "note", ref)] if text else []
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for entry in outline:
        current = node(truncate(entry["text"], 220), "note", ref)
        while stack and stack[-1][0] >= entry["level"]:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(current)
        else:
            roots.append(current)
        stack.append((entry["level"], current))
    return roots


def collect_annotations(children_fn, item_key_value: str) -> tuple[list[dict], list[dict]]:
    """Return (notes, annotations) for a top-level item.

    Annotations are children of attachments (item -> attachment -> annotation),
    not direct children of the paper. Direct annotation children are also
    accepted for robustness.
    """
    children = children_fn(item_key_value)
    notes = [child for child in children if data_of(child).get("itemType") == "note"]
    annotations: list[dict] = [
        child for child in children if data_of(child).get("itemType") == "annotation"
    ]
    seen = {item_key(annotation) for annotation in annotations}
    for child in children:
        if data_of(child).get("itemType") != "attachment":
            continue
        for grandchild in children_fn(item_key(child)):
            if data_of(grandchild).get("itemType") != "annotation":
                continue
            key = item_key(grandchild)
            if key and key in seen:
                continue
            seen.add(key)
            annotations.append(grandchild)
    return notes, annotations


def annotation_groups(
    meta: dict,
    annotations: list[dict],
    labels: dict[str, str] | None = None,
) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for annotation in annotations:
        label = color_label(data_of(annotation).get("annotationColor"), labels)
        groups.setdefault(label, []).append(annotation)
    result = []
    for label in sorted(groups, key=lambda name: color_rank(name, labels)):
        children = []
        for annotation in sorted(groups[label], key=lambda a: annotation_sort(data_of(a))):
            child = annotation_node(meta, annotation)
            if child:
                children.append(child)
        if children:
            group = node(label, "annotation-group", {"item": meta["key"]})
            group["children"] = children
            result.append(group)
    return result


def paper_node(
    meta: dict,
    notes: list[dict],
    annotations: list[dict],
    include_abstract: bool,
    labels: dict[str, str] | None = None,
) -> dict:
    paper = node(
        paper_title(meta),
        "paper",
        {"item": meta["key"], "doi": meta["doi"], "url": meta["url"]},
    )
    if include_abstract and meta["abstract"]:
        paper["children"].append(
            node(f"摘要：{truncate(meta['abstract'], 300)}", "abstract", {"item": meta["key"]})
        )
    paper["children"].extend(annotation_groups(meta, annotations, labels))
    note_children: list[dict] = []
    for note in notes:
        note_children.extend(note_outline_nodes(meta, note))
    if note_children:
        group = node("笔记", "note-group", {"item": meta["key"]})
        group["children"] = note_children
        paper["children"].append(group)
    return paper


def build_tree(
    client,
    collection_key: str | None = None,
    group_by: str = "paper",
    include_abstract: bool = False,
    labels: dict[str, str] | None = None,
) -> dict:
    collection = client.collection(collection_key) if collection_key else None
    root_title = (
        normalize_space(data_of(collection).get("name"))
        if collection
        else "Zotero 文献库"
    )
    root = node(root_title, "root", {"collection": collection_key} if collection_key else {})

    prepared: list[tuple[dict, list[dict], list[dict]]] = []
    for obj in client.items(collection_key):
        meta = item_meta(obj)
        if not meta["key"]:
            continue
        notes, annotations = collect_annotations(client.children, meta["key"])
        prepared.append((meta, notes, annotations))

    if not prepared:
        root["children"].append(node("（当前范围没有条目）", "empty"))
        return root

    if group_by == "color":
        groups: dict[str, list[tuple[dict, list[dict]]]] = {}
        notes_group = node("笔记", "note-group")
        for meta, notes, annotations in prepared:
            for annotation in annotations:
                label = color_label(data_of(annotation).get("annotationColor"), labels)
                groups.setdefault(label, []).append((meta, annotation))
            for note in notes:
                note_children = note_outline_nodes(meta, note)
                if note_children:
                    paper = node(paper_title(meta), "paper", {"item": meta["key"]})
                    paper["children"] = note_children
                    notes_group["children"].append(paper)
        for label in sorted(groups, key=lambda name: color_rank(name, labels)):
            colour_node = node(label, "color-group")
            papers: dict[str, dict] = {}
            for meta, annotation in groups[label]:
                paper = papers.get(meta["key"])
                if paper is None:
                    paper = node(paper_title(meta), "paper", {"item": meta["key"]})
                    papers[meta["key"]] = paper
                    colour_node["children"].append(paper)
                child = annotation_node(meta, annotation)
                if child:
                    paper["children"].append(child)
            if colour_node["children"]:
                root["children"].append(colour_node)
        if notes_group["children"]:
            root["children"].append(notes_group)
        return root

    if group_by == "year":
        years: dict[str, list[tuple[dict, list[dict], list[dict]]]] = {}
        for meta, notes, annotations in prepared:
            years.setdefault(meta["year"] or "年份未知", []).append((meta, notes, annotations))
        for year in sorted(years, reverse=True):
            year_node = node(year, "year-group")
            for meta, notes, annotations in sorted(
                years[year], key=lambda row: row[0]["title"].casefold()
            ):
                year_node["children"].append(
                    paper_node(meta, notes, annotations, include_abstract, labels)
                )
            root["children"].append(year_node)
        return root

    # Default: paper -> annotation colour groups -> notes.
    def paper_sort_key(row):
        meta = row[0]
        try:
            year = -int(meta["year"])
        except (TypeError, ValueError):
            year = 0
        return year, meta["title"].casefold()

    for meta, notes, annotations in sorted(prepared, key=paper_sort_key):
        root["children"].append(
            paper_node(meta, notes, annotations, include_abstract, labels)
        )
    return root


def count_nodes(tree: dict) -> int:
    return 1 + sum(count_nodes(child) for child in tree["children"])


def render_markdown(tree: dict) -> str:
    lines = [f"# {normalize_space(tree['title'])}", ""]

    def walk(current: dict, depth: int) -> None:
        for child in current["children"]:
            title = normalize_space(child["title"])
            if depth <= 2:
                if lines and lines[-1].lstrip().startswith("- "):
                    lines.append("")
                if depth == 1:
                    lines.extend([f"## {title}", ""])
                else:
                    lines.extend([f"### {title}", ""])
            else:
                lines.append(f"{'  ' * (depth - 3)}- {title}")
            walk(child, depth + 1)

    walk(tree, 1)
    if lines and lines[-1].lstrip().startswith("- "):
        lines.append("")
    lines.extend(
        [
            f"<!-- generated by zotero_mindmap.py; nodes: {count_nodes(tree)} -->",
            "",
        ]
    )
    return "\n".join(lines)


def render_freemind(tree: dict) -> str:
    def esc(value: str) -> str:
        return escape(str(value), {'"': "&quot;"})

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<map version="1.0.1">']

    def walk(current: dict, indent: str) -> None:
        lines.append(f'{indent}<node TEXT="{esc(current["title"])}">')
        for child in current["children"]:
            walk(child, indent + "  ")
        lines.append(f"{indent}</node>")

    walk(tree, "  ")
    lines.append("</map>")
    return "\n".join(lines) + "\n"


def render_json(tree: dict) -> str:
    return json.dumps(tree, ensure_ascii=False, indent=2) + "\n"


class ZoteroClient:
    """Minimal read-only client for the Zotero local API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        library: str = "users/0",
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.library = library.strip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{self.library}/{path.lstrip('/')}"

    def _friendly_error(self, error: HTTPError, url: str) -> "ZoteroError":
        detail = ""
        try:
            detail = error.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if error.code == 403:
            return ZoteroError(
                "Zotero 本地 API 返回 403：请在 Zotero 设置 → 高级中勾选 "
                "“Allow other applications on this computer to communicate "
                "with Zotero”。"
            )
        if error.code == 404:
            return ZoteroError(f"Zotero 对象不存在：{url}")
        return ZoteroError(f"Zotero API 返回 HTTP {error.code}：{detail or url}")

    def _request(self, url: str, raw: bool = False):
        request = Request(
            url,
            headers={
                "Accept": "text/plain" if raw else "application/json",
                "Zotero-API-Version": "3",
                "User-Agent": "SearchTools-Zotero-Mindmap/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                if raw:
                    return body, dict(response.headers)
                return json.loads(body), dict(response.headers)
        except HTTPError as error:
            raise self._friendly_error(error, url) from error
        except URLError as error:
            raise ZoteroError(
                "无法连接 Zotero 本地 API。请确认 Zotero 正在运行，并且本地 API 已开启。"
            ) from error
        except TimeoutError as error:
            raise ZoteroError("连接 Zotero 本地 API 超时。") from error

    def ping(self) -> dict:
        request = Request(
            self.base_url + "/",
            headers={
                "Zotero-API-Version": "3",
                "User-Agent": "SearchTools-Zotero-Mindmap/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return dict(response.headers)
        except HTTPError as error:
            raise self._friendly_error(error, self.base_url + "/") from error
        except URLError as error:
            raise ZoteroError(
                "无法连接 Zotero 本地 API。请确认 Zotero 正在运行，并且本地 API 已开启。"
            ) from error

    def collections(self) -> list[dict]:
        data, _ = self._request(self._url("collections"))
        return data

    def collection(self, key: str) -> dict:
        data, _ = self._request(self._url(f"collections/{key}"))
        return data

    def items(self, collection_key: str | None = None) -> list[dict]:
        if collection_key:
            data, _ = self._request(self._url(f"collections/{collection_key}/items/top"))
        else:
            data, _ = self._request(self._url("items/top"))
        return data

    def children(self, key: str) -> list[dict]:
        data, _ = self._request(self._url(f"items/{key}/children"))
        return data

    def file_url(self, attachment_key: str) -> str:
        body, _ = self._request(self._url(f"items/{attachment_key}/file/view/url"), raw=True)
        return body.strip()


class DemoClient:
    """A small offline fixture so the renderer can be tried without Zotero."""

    def __init__(self) -> None:
        self._collection = {
            "key": "COLL0001",
            "data": {"key": "COLL0001", "name": "四足机器人地形泛化", "parentCollection": False},
        }
        self._items = [
            {
                "key": "ITEM0001",
                "data": {
                    "key": "ITEM0001",
                    "itemType": "journalArticle",
                    "title": "Learning Terrain-Aware Locomotion",
                    "date": "2024",
                    "creators": [
                        {"firstName": "Ada", "lastName": "Smith"},
                        {"firstName": "Bo", "lastName": "Li"},
                    ],
                    "abstractNote": "A terrain-aware policy trained on procedural heightfields.",
                    "DOI": "10.0000/demo.1",
                    "url": "https://example.org/paper1",
                    "tags": [{"tag": "locomotion"}, {"tag": "terrain"}],
                    "collections": ["COLL0001"],
                },
            },
            {
                "key": "ITEM0002",
                "data": {
                    "key": "ITEM0002",
                    "itemType": "conferencePaper",
                    "title": "Robust Quadruped Gait over Gaps",
                    "date": "2023-05",
                    "creators": [{"firstName": "Chen", "lastName": "Wang"}],
                    "abstractNote": "A study of gap crossing with proprioceptive sensing.",
                    "DOI": "10.0000/demo.2",
                    "url": "https://example.org/paper2",
                    "tags": [{"tag": "gait"}, {"tag": "gap"}],
                    "collections": ["COLL0001"],
                },
            },
        ]
        self._children = {
            "ITEM0001": [
                {
                    "key": "ATT0001",
                    "data": {
                        "key": "ATT0001",
                        "itemType": "attachment",
                        "parentItem": "ITEM0001",
                        "contentType": "application/pdf",
                        "filename": "paper1.pdf",
                    },
                },
                {
                    "key": "NOTE0001",
                    "data": {
                        "key": "NOTE0001",
                        "itemType": "note",
                        "parentItem": "ITEM0001",
                        "note": (
                            "<h2>实验设置</h2>"
                            "<ul><li>训练地形：procedural heightfield</li>"
                            "<li>测试地形：stairs、slopes、gaps</li></ul>"
                            "<h2>疑点</h2><p>没有报告可变形地形上的失败率。</p>"
                        ),
                    },
                },
            ],
            "ATT0001": [
                {
                    "key": "ANN0001",
                    "data": {
                        "key": "ANN0001",
                        "itemType": "annotation",
                        "annotationType": "highlight",
                        "annotationText": "The policy is trained on procedural terrain.",
                        "annotationComment": "需要核对训练地形与测试地形的分布差异。",
                        "annotationColor": "#ffd400",
                        "annotationPageLabel": "4",
                        "annotationSortIndex": "00004|000215",
                        "tags": [{"tag": "训练设置"}],
                    },
                },
                {
                    "key": "ANN0002",
                    "data": {
                        "key": "ANN0002",
                        "itemType": "annotation",
                        "annotationType": "highlight",
                        "annotationText": "Performance degrades on deformable terrain.",
                        "annotationComment": "",
                        "annotationColor": "#ff6666",
                        "annotationPageLabel": "9",
                        "annotationSortIndex": "00009|000120",
                        "tags": [],
                    },
                },
            ],
            "ITEM0002": [
                {
                    "key": "ATT0002",
                    "data": {
                        "key": "ATT0002",
                        "itemType": "attachment",
                        "parentItem": "ITEM0002",
                        "contentType": "application/pdf",
                        "filename": "paper2.pdf",
                    },
                }
            ],
            "ATT0002": [
                {
                    "key": "ANN0003",
                    "data": {
                        "key": "ANN0003",
                        "itemType": "annotation",
                        "annotationType": "highlight",
                        "annotationText": "A 0.35 m gap was crossed in 80% of trials.",
                        "annotationComment": "样本量未说明。",
                        "annotationColor": "#2ea8e5",
                        "annotationPageLabel": "6",
                        "annotationSortIndex": "00006|000044",
                        "tags": [],
                    },
                }
            ],
        }

    def collection(self, key: str) -> dict:
        if key != "COLL0001":
            raise ZoteroError(f"演示数据中没有 Collection：{key}")
        return self._collection

    def collections(self) -> list[dict]:
        return [self._collection]

    def items(self, collection_key: str | None = None) -> list[dict]:
        return self._items

    def children(self, key: str) -> list[dict]:
        return self._children.get(key, [])

    def file_url(self, attachment_key: str) -> str:
        return f"file:///demo/{attachment_key}.pdf"


def print_collections(client) -> None:
    rows = client.collections()
    for row in sorted(rows, key=lambda r: (data_of(r).get("name") or "").casefold()):
        data = data_of(row)
        parent = data.get("parentCollection") or ""
        suffix = f"  (父级: {parent})" if parent else ""
        print(f"{item_key(row)}  {data.get('name') or '未命名'}{suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 Zotero 本地 API 导出只读思维导图（Markdown / FreeMind）。"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Zotero 本地 API 地址")
    parser.add_argument("--library", default="users/0", help="库前缀，默认 users/0")
    parser.add_argument("--collection", help="Collection key；--demo 时默认 COLL0001")
    parser.add_argument("--group-by", choices=("paper", "color", "year"), default="paper")
    parser.add_argument("--format", dest="fmt", choices=("md", "mm", "json"), default="md")
    parser.add_argument("--out", default="-", help="输出文件；- 表示标准输出")
    parser.add_argument("--include-abstract", action="store_true")
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据，不连接 Zotero")
    parser.add_argument("--ping", action="store_true", help="只探测 Zotero 本地 API")
    parser.add_argument("--collections", action="store_true", help="只列出 Collection")
    args = parser.parse_args(argv)

    client = DemoClient() if args.demo else ZoteroClient(args.base_url, args.library)

    if args.ping:
        if args.demo:
            print("demo mode: Zotero not contacted")
            return 0
        headers = client.ping()
        for name in (
            "X-Zotero-Version",
            "X-Zotero-Connector-API-Version",
            "Zotero-API-Version",
            "Zotero-Schema-Version",
            "Zotero-Server-ID",
        ):
            value = headers.get(name)
            if value:
                print(f"{name}: {value}")
        return 0

    if args.collections:
        print_collections(client)
        return 0

    collection_key = args.collection or ("COLL0001" if args.demo else None)
    if not collection_key:
        parser.error("请用 --collection 指定 Collection key，或使用 --demo")

    tree = build_tree(
        client,
        collection_key=collection_key,
        group_by=args.group_by,
        include_abstract=args.include_abstract,
    )
    output = (
        render_markdown(tree)
        if args.fmt == "md"
        else render_freemind(tree)
        if args.fmt == "mm"
        else render_json(tree)
    )

    if args.out == "-":
        sys.stdout.write(output)
    else:
        path = Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
        print(f"已写入 {path}（{count_nodes(tree)} 个节点）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ZoteroError as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(2)
