import unittest

from inbound_crm import PackingSlipCRMReader, PackingSlipReadError, map_table_rows


class ScriptedReader(PackingSlipCRMReader):
    def __init__(self, pages, total_pages=None, progress=None):
        super().__init__(session=None, progress=progress)
        self.pages = pages
        self.position = 1
        self.visited = []
        self.visible_buttons = [1, 3, 5, 7]
        self.scripted_total_pages = total_pages or len(pages)

    def _go_to_first_page(self):
        self.position = 1

    def _current_page_number(self):
        return self.position

    def _total_pages(self):
        return self.scripted_total_pages

    def _read_current_page(self):
        self.visited.append(self.position)
        return self.pages[self.position - 1]

    def _has_next_page(self):
        return self.position < len(self.pages)

    def _advance_to_page(self, expected_page):
        self.position = expected_page

    def _wait_for_page(self, expected_page, previous_fingerprint):
        return None


def detail_row(page, serial=None):
    return {
        "page": page,
        "row_index": 1,
        "order_number": "210524",
        "product_code": "916000024",
        "description": "中央净水机",
        "expected_quantity": "8",
        "serial": serial or f"SN{page:08d}",
    }


class InboundCRMTest(unittest.TestCase):
    def test_maps_shuffled_headers_and_filters_noise_rows(self):
        headers = ["条码", "物料描述", "订单号", "应发数量", "物料编码"]
        rows = [
            ["SN00000001", "中央净水机", "210524", "1", "916000024"],
            headers,
            ["", "合计", "", "1", ""],
            ["", "", "", "", ""],
        ]

        mapped = map_table_rows(headers, rows, page_number=3)

        self.assertEqual(
            mapped,
            [
                {
                    "page": 3,
                    "row_index": 1,
                    "order_number": "210524",
                    "product_code": "916000024",
                    "description": "中央净水机",
                    "expected_quantity": "1",
                    "serial": "SN00000001",
                }
            ],
        )

    def test_requires_product_code_and_serial_headers(self):
        with self.assertRaisesRegex(PackingSlipReadError, "物料编码.*条码"):
            map_table_rows(["订单号", "物料描述"], [["210524", "中央净水机"]], 1)

    def test_reads_all_eight_pages_in_strict_consecutive_order(self):
        updates = []
        reader = ScriptedReader(
            [[detail_row(page)] for page in range(1, 9)],
            progress=updates.append,
        )

        result = reader._read_all_pages()

        self.assertEqual(reader.visited, [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(
            [entry["page"] for entry in result["page_counts"]],
            [1, 2, 3, 4, 5, 6, 7, 8],
        )
        self.assertEqual([row["page"] for row in result["rows"]], list(range(1, 9)))
        self.assertEqual(updates[-1], {"page": 8, "row_count": 1})

    def test_rejects_page_jump(self):
        class JumpingReader(ScriptedReader):
            def _advance_to_page(self, expected_page):
                self.position = 3

        reader = JumpingReader([[detail_row(1)], [detail_row(2)], [detail_row(3)]])

        with self.assertRaisesRegex(PackingSlipReadError, "期望第 2 页，实际第 3 页"):
            reader._read_all_pages()

    def test_rejects_repeated_page_content(self):
        repeated = detail_row(1, serial="SN00000001")
        reader = ScriptedReader([[repeated], [{**repeated, "page": 2}]])

        with self.assertRaisesRegex(PackingSlipReadError, "页面重复"):
            reader._read_all_pages()

    def test_rejects_unidentifiable_current_page(self):
        class UnknownPageReader(ScriptedReader):
            def _current_page_number(self):
                return None

        reader = UnknownPageReader([[detail_row(1)]])

        with self.assertRaisesRegex(PackingSlipReadError, "无法识别当前页码"):
            reader._read_all_pages()

    def test_does_not_fall_back_when_aria_current_page_is_ambiguous(self):
        class PageElement:
            def __init__(self, number):
                self.number = str(number)

            def is_visible(self):
                return True

            def get_attribute(self, name):
                return None

            def inner_text(self):
                return self.number

        class Scope:
            def query_selector_all(self, selector):
                if selector == "[aria-current='page']":
                    return [PageElement(1), PageElement(2)]
                if ".active" in selector:
                    return [PageElement(1)]
                return []

        class Session:
            page = Scope()
            context = None

        reader = PackingSlipCRMReader(Session())

        self.assertIsNone(reader._current_page_number())

    def test_rejects_next_page_becoming_unavailable_before_total(self):
        class PrematureEndReader(ScriptedReader):
            def _has_next_page(self):
                return self.position < 7

        reader = PrematureEndReader(
            [[detail_row(page)] for page in range(1, 9)],
            total_pages=8,
        )

        with self.assertRaisesRegex(PackingSlipReadError, "未读完总页数"):
            reader._read_all_pages()

    def test_rejects_unknown_total_when_next_control_state_cannot_be_read(self):
        class DetachedControl:
            def get_attribute(self, name):
                raise RuntimeError("control was detached")

        class UnknownTotalReader(PackingSlipCRMReader):
            def __init__(self):
                super().__init__(session=None)

            def _go_to_first_page(self):
                return None

            def _current_page_number(self):
                return 1

            def _total_pages(self):
                return None

            def _read_current_page(self):
                return [detail_row(1)]

            def _next_controls(self):
                return [DetachedControl()]

        reader = UnknownTotalReader()

        with self.assertRaisesRegex(PackingSlipReadError, "无法识别下一页状态"):
            reader._read_all_pages()


if __name__ == "__main__":
    unittest.main()
