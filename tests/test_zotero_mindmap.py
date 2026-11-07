import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import xml.etree.ElementTree as ET


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zotero_mindmap.py"
SPEC = importlib.util.spec_from_file_location("zotero_mindmap", MODULE_PATH)
zm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(zm)


class OutlineTests(unittest.TestCase):
    def test_headings_lists_and_paragraphs_keep_order(self):
        html = (
            "<h1>方法</h1><p>总览段落</p>"
            "<ul><li>一<ul><li>一.1</li></ul></li><li>二</li></ul>"
            "<h2>局限</h2><p>失败条件</p>"
        )
        outline = zm.html_to_outline(html)
        triples = [(entry["text"], entry["level"], entry["kind"]) for entry in outline]
        self.assertEqual(
            triples,
            [
                ("方法", 1, "heading"),
                ("总览段落", 2, "paragraph"),
                ("一", 2, "list"),
                ("一.1", 3, "list"),
                ("二", 2, "list"),
                ("局限", 2, "heading"),
                ("失败条件", 3, "paragraph"),
            ],
        )

    def test_paragraph_inside_list_item_is_not_duplicated(self):
        outline = zm.html_to_outline("<ul><li><p>唯一内容</p></li></ul>")
        self.assertEqual(
            [(entry["text"], entry["kind"]) for entry in outline],
            [("唯一内容", "list")],
        )

    def test_empty_html(self):
        self.assertEqual(zm.html_to_outline(""), [])


class TreeTests(unittest.TestCase):
    def test_paper_grouping(self):
        tree = zm.build_tree(zm.DemoClient(), "COLL0001")
        self.assertEqual(tree["title"], "四足机器人地形泛化")
        papers = [child for child in tree["children"] if child["kind"] == "paper"]
        self.assertEqual(len(papers), 2)

        paper = next(item for item in papers if "Terrain-Aware" in item["title"])
        groups = {child["title"]: child for child in paper["children"]}
        self.assertIn("核心结论", groups)
        annotation = groups["核心结论"]["children"][0]
        self.assertEqual(annotation["kind"], "annotation")
        self.assertEqual(annotation["ref"]["page"], "4")
        self.assertIn("procedural", annotation["title"])
        self.assertIn("笔记", groups)
        note_titles = [node["title"] for node in groups["笔记"]["children"]]
        self.assertIn("实验设置", note_titles)
        self.assertIn("疑点", note_titles)

    def test_color_grouping(self):
        tree = zm.build_tree(zm.DemoClient(), "COLL0001", group_by="color")
        labels = [child["title"] for child in tree["children"]]
        self.assertEqual(labels[:3], ["核心结论", "局限/冲突", "数据/结果"])
        self.assertIn("笔记", labels)
        first = tree["children"][0]
        self.assertEqual(first["children"][0]["kind"], "paper")

    def test_year_grouping_and_abstract(self):
        tree = zm.build_tree(
            zm.DemoClient(), "COLL0001", group_by="year", include_abstract=True
        )
        years = [child["title"] for child in tree["children"]]
        self.assertEqual(years, ["2024", "2023"])
        paper = tree["children"][0]["children"][0]
        self.assertTrue(paper["children"][0]["title"].startswith("摘要："))
        self.assertEqual(paper["children"][0]["kind"], "abstract")

    def test_unknown_collection_raises(self):
        with self.assertRaises(zm.ZoteroError):
            zm.build_tree(zm.DemoClient(), "MISSING")

    def test_annotation_kinds(self):
        meta = {"key": "ITEM0001"}
        comment_only = {
            "key": "ANN1",
            "data": {"annotationType": "note", "annotationComment": "一条便签"},
        }
        node = zm.annotation_node(meta, comment_only)
        self.assertIn("批注：一条便签", node["title"])

        image = {"key": "ANN2", "data": {"annotationType": "image"}}
        self.assertEqual(zm.annotation_node(meta, image)["title"], "[区域标注]")

        empty = {"key": "ANN3", "data": {"annotationType": "highlight"}}
        self.assertIsNone(zm.annotation_node(meta, empty))

    def test_collect_annotations_two_hops(self):
        notes, annotations = zm.collect_annotations(zm.DemoClient().children, "ITEM0001")
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(annotations), 2)
        self.assertEqual({item["key"] for item in annotations}, {"ANN0001", "ANN0002"})


class ExportTests(unittest.TestCase):
    def test_markdown_structure(self):
        tree = zm.build_tree(zm.DemoClient(), "COLL0001")
        text = zm.render_markdown(tree)
        self.assertTrue(text.startswith("# 四足机器人地形泛化\n"))
        self.assertIn("## [2024] Learning Terrain-Aware Locomotion", text)
        self.assertIn("### 核心结论", text)
        self.assertIn("- “The policy is trained on procedural terrain.”", text)
        self.assertIn("p.4", text)
        self.assertIn("  - 训练地形：procedural heightfield", text)
        self.assertNotIn("\n### 局限/冲突\n###", text)
        self.assertTrue(text.rstrip().endswith("-->"))

    def test_freemind_is_valid_xml_and_escapes(self):
        tree = zm.node('A & B < C "D"', "root")
        tree["children"].append(zm.node("子节点 & 值", "paper"))
        xml_text = zm.render_freemind(tree)
        root = ET.fromstring(xml_text)
        self.assertEqual(root.tag, "map")
        self.assertEqual(root.attrib["version"], "1.0.1")
        node = root.find("node")
        self.assertEqual(node.attrib["TEXT"], 'A & B < C "D"')
        self.assertEqual(node.find("node").attrib["TEXT"], "子节点 & 值")

    def test_json_export_keeps_traceable_refs(self):
        tree = zm.build_tree(zm.DemoClient(), "COLL0001")
        data = json.loads(zm.render_json(tree))
        paper = next(child for child in data["children"] if child["kind"] == "paper")
        annotation = paper["children"][0]["children"][0]
        self.assertEqual(annotation["ref"]["item"], "ITEM0001")
        self.assertEqual(annotation["ref"]["annotation"], "ANN0001")
        self.assertEqual(annotation["ref"]["page"], "4")

    def test_truncate(self):
        self.assertEqual(zm.truncate("短文本", 10), "短文本")
        result = zm.truncate("一二三四五六七八九十", 5)
        self.assertEqual(len(result), 5)
        self.assertTrue(result.endswith("…"))


class ClientTests(unittest.TestCase):
    def test_url_prefix(self):
        client = zm.ZoteroClient("http://127.0.0.1:23119/api/")
        self.assertEqual(
            client._url("items/top"),
            "http://127.0.0.1:23119/api/users/0/items/top",
        )

    def test_403_has_actionable_message(self):
        client = zm.ZoteroClient()
        error = HTTPError(
            "http://127.0.0.1:23119/api/users/0/collections",
            403,
            "Forbidden",
            None,
            io.BytesIO(b"{}"),
        )
        with patch.object(zm, "urlopen", side_effect=error):
            with self.assertRaises(zm.ZoteroError) as raised:
                client.collections()
        self.assertIn("Allow other applications", str(raised.exception))

    def test_ping_403_has_actionable_message(self):
        client = zm.ZoteroClient()
        error = HTTPError(
            "http://127.0.0.1:23119/api/",
            403,
            "Forbidden",
            None,
            io.BytesIO(b"{}"),
        )
        with patch.object(zm, "urlopen", side_effect=error):
            with self.assertRaises(zm.ZoteroError) as raised:
                client.ping()
        self.assertIn("Allow other applications", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
