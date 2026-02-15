#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from PIL import Image
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Pillow is required. Install with: python3 -m pip install pillow"
    ) from exc


def _cache_key_from_image_url(image_url: str) -> str:
    token = urlparse(image_url).path.rstrip("/").split("/")[-1]
    return token.strip()


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return value[:120] if value else "item"


def _load_catalog(state_file: Path) -> list[dict[str, Any]]:
    if not state_file.exists():
        raise FileNotFoundError(f"state file not found: {state_file}")
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    catalog = payload.get("catalog", [])
    if not isinstance(catalog, list):
        raise ValueError("invalid state file: `catalog` must be a list")
    return catalog


def export_dataset(
    *,
    state_file: Path,
    cache_dir: Path,
    output_dir: Path,
    include_fallback: bool,
    limit: int | None,
) -> dict[str, int]:
    catalog = _load_catalog(state_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"

    seen_names: set[str] = set()
    exported = 0
    skipped_fallback = 0
    skipped_missing_cache = 0
    skipped_invalid = 0

    with manifest_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "product_id",
                "category",
                "brand",
                "product_name",
                "price",
                "product_url",
                "image_url",
                "cache_file",
                "export_file",
            ],
        )
        writer.writeheader()

        for row in catalog:
            product_id = str(row.get("product_id", "")).strip()
            category = str(row.get("category", "unknown")).strip() or "unknown"
            image_url = str(row.get("image_url", "")).strip()

            if not include_fallback and product_id.startswith("fallback-"):
                skipped_fallback += 1
                continue
            if not product_id or not image_url:
                skipped_invalid += 1
                continue

            cache_key = _cache_key_from_image_url(image_url)
            if not cache_key:
                skipped_invalid += 1
                continue

            cache_file = cache_dir / f"{cache_key}.img"
            if not cache_file.exists():
                skipped_missing_cache += 1
                continue

            category_dir = output_dir / _safe_name(category)
            category_dir.mkdir(parents=True, exist_ok=True)

            base_name = _safe_name(product_id)
            file_name = f"{base_name}.jpg"
            index = 2
            while str(category_dir / file_name) in seen_names:
                file_name = f"{base_name}_{index}.jpg"
                index += 1
            export_file = category_dir / file_name

            try:
                with Image.open(cache_file) as img:
                    rgb = img.convert("RGB")
                    rgb.save(export_file, format="JPEG", quality=92)
            except Exception:
                skipped_invalid += 1
                continue

            seen_names.add(str(export_file))
            writer.writerow(
                {
                    "product_id": product_id,
                    "category": category,
                    "brand": str(row.get("brand", "")),
                    "product_name": str(row.get("product_name", "")),
                    "price": row.get("price"),
                    "product_url": str(row.get("product_url", "")),
                    "image_url": image_url,
                    "cache_file": str(cache_file),
                    "export_file": str(export_file),
                }
            )
            exported += 1

            if limit is not None and exported >= limit:
                break

    return {
        "exported": exported,
        "skipped_fallback": skipped_fallback,
        "skipped_missing_cache": skipped_missing_cache,
        "skipped_invalid": skipped_invalid,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export crawled catalog cache images as .jpg dataset.")
    parser.add_argument("--state-file", type=Path, default=Path("data/job_state.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/assets/catalog-cache"))
    parser.add_argument("--output-dir", type=Path, default=Path("datasets/catalog-jpg"))
    parser.add_argument("--include-fallback", action="store_true", help="Include fallback-* synthetic products.")
    parser.add_argument("--limit", type=int, default=None, help="Export at most N images.")
    args = parser.parse_args()

    summary = export_dataset(
        state_file=args.state_file,
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        include_fallback=args.include_fallback,
        limit=args.limit,
    )
    print(f"output_dir: {args.output_dir}")
    print(f"exported: {summary['exported']}")
    print(f"skipped_fallback: {summary['skipped_fallback']}")
    print(f"skipped_missing_cache: {summary['skipped_missing_cache']}")
    print(f"skipped_invalid: {summary['skipped_invalid']}")
    print(f"manifest: {args.output_dir / 'manifest.csv'}")


if __name__ == "__main__":
    main()
