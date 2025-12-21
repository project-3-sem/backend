import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from texts.models import Text

try:
    from docx import Document
except ImportError as e:
    raise ImportError("python-docx is required. Install it with: python -m pip install python-docx") from e


class Command(BaseCommand):
    help = "Import texts from a .docx file grouped by difficulty headings."

    def add_arguments(self, parser):
        parser.add_argument("--path", type=str, required=True, help="Path to тексты.docx")
        parser.add_argument("--clear", action="store_true", help="Delete all existing texts before importing.")

    def handle(self, *args, **options):
        docx_path = Path(options["path"]).expanduser()

        if not docx_path.exists():
            raise CommandError(f"File not found: {docx_path}")

        if options["clear"]:
            Text.objects.all().delete()
            self.stdout.write(self.style.WARNING("Cleared existing texts."))

        doc = Document(str(docx_path))

        def detect_difficulty(s: str):
            t = s.strip().lower()
            if "легк" in t:
                return "easy"
            if "средн" in t:
                return "medium"
            if "сложн" in t:
                return "hard"
            return None

        title_re = re.compile(r"^\s*\d+\.\s+(?P<title>.+?)\s*$")

        current_diff = None
        current_title = None
        body_parts = []
        created = 0

        def flush():
            nonlocal current_title, body_parts, created
            if current_title and current_diff and body_parts:
                Text.objects.create(
                    title=current_title.strip(),
                    body="\n\n".join(body_parts).strip(),
                    difficulty=current_diff,
                )
                created += 1
            current_title = None
            body_parts = []

        for p in doc.paragraphs:
            line = (p.text or "").strip()
            if not line:
                continue

            maybe_diff = detect_difficulty(line)
            if maybe_diff:
                flush()
                current_diff = maybe_diff
                continue

            m = title_re.match(line)
            if m:
                flush()
                current_title = m.group("title")
                continue

            body_parts.append(line)

        flush()
        self.stdout.write(self.style.SUCCESS(f"Import finished. Created: {created} texts."))
