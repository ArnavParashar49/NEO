"""PDF merge and file compression tools."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

from actions.file_controller import _is_safe_path, _resolve_path, _resolve_target, _unique_dest

_PDF_SETTINGS = {
    "low": "/screen",
    "medium": "/ebook",
    "high": "/printer",
}


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _collect_pdfs(params: dict) -> tuple[list[Path], str | None]:
    path = (params.get("path") or "desktop").strip()
    name = (params.get("name") or "").strip()
    files_raw = params.get("files") or params.get("names") or []

    pdfs: list[Path] = []

    if isinstance(files_raw, str):
        files_raw = [x.strip() for x in files_raw.replace(",", " ").split() if x.strip()]

    if files_raw:
        base = _resolve_path(path)
        for item in files_raw:
            target = base / item if not Path(item).is_absolute() else Path(item)
            if target.suffix.lower() == ".pdf" and target.exists():
                pdfs.append(target)
    elif name:
        target = _resolve_target(path, name)
        if target.suffix.lower() == ".pdf" and target.exists():
            pdfs.append(target)
    else:
        folder = _resolve_path(path)
        if folder.is_dir():
            pdfs = sorted(
                [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"],
                key=lambda p: p.name.lower(),
            )

    pdfs = [p for p in pdfs if _is_safe_path(p)]
    if len(pdfs) < 2:
        return [], "Need at least 2 PDF files to merge. Set path to a folder or list files/names."
    return pdfs, None


def _merge_pdfs(params: dict) -> str:
    pdfs, err = _collect_pdfs(params)
    if err:
        return f"FAILED: {err}"

    out_name = (params.get("output") or params.get("output_name") or "merged.pdf").strip()
    if not out_name.lower().endswith(".pdf"):
        out_name += ".pdf"

    dest_folder = _resolve_path(params.get("destination") or params.get("path") or "desktop")
    if not _is_safe_path(dest_folder):
        return f"Access denied: {dest_folder}"
    dest_folder.mkdir(parents=True, exist_ok=True)
    output = dest_folder / out_name
    if output.exists():
        output = _unique_dest(dest_folder, out_name)

    try:
        from pypdf import PdfWriter

        writer = PdfWriter()
        for pdf in pdfs:
            writer.append(str(pdf))
        with open(output, "wb") as f:
            writer.write(f)
        writer.close()
    except ImportError:
        try:
            from PyPDF2 import PdfMerger

            merger = PdfMerger()
            for pdf in pdfs:
                merger.append(str(pdf))
            merger.write(str(output))
            merger.close()
        except ImportError:
            return "FAILED: Install pypdf — pip install pypdf"
        except Exception as e:
            return f"FAILED: Could not merge PDFs — {e}"
    except Exception as e:
        return f"FAILED: Could not merge PDFs — {e}"

    names = ", ".join(p.name for p in pdfs[:4])
    if len(pdfs) > 4:
        names += f" + {len(pdfs) - 4} more"
    return (
        f"Merged {len(pdfs)} PDF(s) into {output.name} ({_fmt_size(output.stat().st_size)}). "
        f"Sources: {names}"
    )


def _compress_pdf_gs(src: Path, dst: Path, quality: str) -> tuple[bool, str]:
    gs = shutil.which("gs")
    if not gs:
        return False, "Ghostscript not installed"

    setting = _PDF_SETTINGS.get(quality, "/ebook")
    cmd = [
        gs,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={setting}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={dst}",
        str(src),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not dst.exists():
        return False, (r.stderr or r.stdout or "ghostscript failed").strip()[:200]
    return True, ""


def _compress_pdf_pypdf(src: Path, dst: Path) -> tuple[bool, str]:
    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(src))
        writer = PdfWriter()
        for page in reader.pages:
            page.compress_content_streams()
            writer.add_page(page)
        with open(dst, "wb") as f:
            writer.write(f)
        return True, ""
    except Exception as e:
        return False, str(e)


def _compress_pdf(params: dict) -> str:
    path = (params.get("path") or "desktop").strip()
    name = (params.get("name") or "").strip()
    if not name:
        return "NEEDS_USER: Which PDF should I compress? Set name."

    src = _resolve_target(path, name)
    if not _is_safe_path(src):
        return f"Access denied: {src}"
    if not src.exists() or src.suffix.lower() != ".pdf":
        return f"PDF not found: {name}"

    quality = (params.get("quality") or "medium").lower()
    dst = src.with_name(f"{src.stem}_compressed.pdf")
    if dst.exists():
        dst = _unique_dest(src.parent, dst.name)

    ok, err = _compress_pdf_gs(src, dst, quality)
    if not ok:
        ok, err = _compress_pdf_pypdf(src, dst)

    if not ok or not dst.exists():
        return f"FAILED: Could not compress PDF — {err}"

    before = src.stat().st_size
    after = dst.stat().st_size
    pct = int((1 - after / before) * 100) if before else 0
    return (
        f"Compressed {src.name}: {_fmt_size(before)} → {_fmt_size(after)} "
        f"({pct}% smaller). Saved as {dst.name}"
    )


def _compress_image(params: dict) -> str:
    from PIL import Image

    path = (params.get("path") or "desktop").strip()
    name = (params.get("name") or "").strip()
    if not name:
        return "NEEDS_USER: Which image should I compress? Set name."

    src = _resolve_target(path, name)
    if not _is_safe_path(src) or not src.exists():
        return f"File not found: {name}"

    quality = min(max(int(params.get("quality") or 80), 10), 95)
    ext = src.suffix.lower()
    if ext in (".png",):
        dst = src.with_name(f"{src.stem}_compressed.png")
        if dst.exists():
            dst = _unique_dest(src.parent, dst.name)
        img = Image.open(src)
        img.save(dst, optimize=True, compress_level=9)
    else:
        dst = src.with_name(f"{src.stem}_compressed.jpg")
        if dst.exists():
            dst = _unique_dest(src.parent, dst.name)
        img = Image.open(src).convert("RGB")
        img.save(dst, "JPEG", quality=quality, optimize=True)

    before = src.stat().st_size
    after = dst.stat().st_size
    return f"Compressed {src.name}: {_fmt_size(before)} → {_fmt_size(after)}. Saved as {dst.name}"


def _zip_compress(params: dict) -> str:
    path = (params.get("path") or "desktop").strip()
    name = (params.get("name") or "").strip()
    folder = _resolve_path(path)

    if name:
        target = _resolve_target(path, name)
        if target.is_file():
            zip_path = target.with_suffix(".zip")
            if zip_path.exists():
                zip_path = _unique_dest(target.parent, zip_path.name)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(target, arcname=target.name)
            return f"Created {zip_path.name} ({_fmt_size(zip_path.stat().st_size)})."
        folder = target

    if not folder.is_dir():
        return f"Not a folder: {folder}"

    out_name = (params.get("output") or f"{folder.name}.zip").strip()
    if not out_name.lower().endswith(".zip"):
        out_name += ".zip"
    zip_path = folder.parent / out_name
    if zip_path.exists():
        zip_path = _unique_dest(folder.parent, out_name)

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in folder.rglob("*"):
            if item.is_file() and not item.name.startswith("."):
                zf.write(item, arcname=item.relative_to(folder))
                count += 1

    return f"Zipped {count} file(s) from {folder.name} → {zip_path.name} ({_fmt_size(zip_path.stat().st_size)})."


def _handle_with_confirm(action_id: str, summary: str, ask: str, params: dict, run_fn) -> str:
    from actions import confirm_gate as cg

    proceed, stored, err = cg.consume_confirmed(params)
    if err:
        return err
    if proceed:
        merged = cg.merge_params(params, stored) if stored else params
        return run_fn(merged)
    return cg.needs_confirm(action_id, summary, dict(params), ask=ask)


def document_tools(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "").lower().strip()

    if player:
        player.write_log(f"[document] {action}")

    try:
        if action in ("merge_pdf", "merge_pdfs", "merge"):
            pdfs, err = _collect_pdfs(params)
            if err:
                return f"FAILED: {err}"
            summary = f"Merge {len(pdfs)} PDF(s) into one file."
            return _handle_with_confirm(
                "merge_pdf",
                summary,
                f"Should I merge {len(pdfs)} PDF files?",
                {**params, "action": "merge_pdf"},
                _merge_pdfs,
            )

        if action in ("compress_pdf", "compress"):
            path = (params.get("path") or "desktop").strip()
            name = (params.get("name") or "").strip()
            if name and name.lower().endswith(".pdf"):
                return _compress_pdf(params)
            target = _resolve_target(path, name) if name else None
            if target and target.suffix.lower() == ".pdf":
                return _compress_pdf(params)
            if name:
                ext = Path(name).suffix.lower()
                if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
                    return _compress_image(params)
            return (
                "Set name to a PDF for compress_pdf, or an image (.jpg/.png). "
                "Use action zip to compress a folder."
            )

        if action in ("compress_image", "shrink_image"):
            return _compress_image(params)

        if action in ("zip", "zip_folder", "compress_folder"):
            folder = _resolve_path(params.get("path") or "desktop")
            name = (params.get("name") or "").strip()
            if name:
                folder = _resolve_target(params.get("path") or "desktop", name)
            item_count = sum(1 for _ in folder.rglob("*") if _.is_file()) if folder.is_dir() else 1
            summary = f"Create zip archive ({item_count} file(s)) from {folder.name}."
            return _handle_with_confirm(
                "zip_folder",
                summary,
                f"Should I zip {folder.name}?",
                {**params, "action": "zip"},
                _zip_compress,
            )

        if action == "info":
            pdfs, err = _collect_pdfs(params)
            if err:
                return err
            lines = [f"Found {len(pdfs)} PDF(s):"]
            for i, p in enumerate(pdfs[:15], 1):
                lines.append(f"  {i}. {p.name} ({_fmt_size(p.stat().st_size)})")
            return "\n".join(lines)

        return (
            "Unknown action. Use: merge_pdf | compress_pdf | compress_image | zip | info. "
            "merge: path folder or files list. compress: name required."
        )

    except Exception as e:
        return f"Document tools error: {e}"
