"""Regression tests for Telegram poster discovery and candidate OCR handling."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extract = load_module("extract_codal_disclosures", "scripts/extract_codal_disclosures.py")
process = load_module("process_codal_reports", "scripts/process_codal_reports.py")


class TelegramPosterTests(unittest.TestCase):
    def test_parser_keeps_photo_url_alt_and_message_identity(self):
        html = """
        <div class="tgme_widget_message_wrap">
          <div data-post="Codal360_ir/348140"></div>
          <time datetime="2026-10-13T10:20:00+00:00"></time>
          <div class="tgme_widget_message_text">گزارش ۳ ماهه شرکت نمونه</div>
          <a href="https://codal.ir/Reports/Decision.aspx?LetterSerial=123">گزارش رسمی</a>
          <img src="/file/preview.jpg" alt="فروش و سود شرکت" width="640">
        </div>
        """
        parser = extract.TelegramParser()
        parser.feed(html)
        self.assertEqual(len(parser.posts), 1)
        post = parser.posts[0]
        self.assertEqual(post["post_id"], "348140")
        self.assertIn("گزارش ۳ ماهه", post["text"])
        self.assertEqual(post["images"][0]["url"], "/file/preview.jpg")
        self.assertEqual(post["images"][0]["alt"], "فروش و سود شرکت")
        self.assertIn("LetterSerial=123", post["links"][0]["url"])

    def test_image_only_post_is_retained_as_channel_evidence(self):
        posts = [{
            "text": "گزارش مالی شرکت نمونه",
            "images": [{"url": "https://t.me/file/preview.jpg", "alt": "پوستر مالی"}],
            "links": [],
            "channel": "Codal360_ir",
            "source_name": "تلگرام کدال۳۶۰",
            "date": "2026-10-13T10:20:00+00:00",
            "post_id": "348140",
            "permalink": "https://t.me/Codal360_ir/348140",
            "source_page": "https://t.me/s/Codal360_ir",
        }]
        rows = extract.build_channel_evidence(posts)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_attachments"][0]["alt"], "پوستر مالی")
        self.assertEqual(rows[0]["image_ocr_status"], "pending_ocr")

    def test_period_and_persian_digit_normalization(self):
        self.assertEqual(process.extract_period_months("گزارش ۹ ماهه منتهی به ۱۴۰۵/۰۶/۳۱"), 9)
        self.assertEqual(process.text_digits("فروش ۱۲۳٬۴۵۶ میلیون ریال"), "فروش 123,456 میلیون ریال")

    def test_ocr_failure_is_explicit_not_zero_score(self):
        result = process.ocr_telegram_images([{"url": "https://example.com/poster.jpg"}], limit=1)
        self.assertIn(result["status"], {
            "tesseract_unavailable", "tesseract_language_unavailable",
            "ocr_candidate_text", "ocr_failed_or_empty",
        })
        # OCR output is evidence only; it never manufactures a score by itself.
        self.assertNotIn("score", result)


if __name__ == "__main__":
    unittest.main()
