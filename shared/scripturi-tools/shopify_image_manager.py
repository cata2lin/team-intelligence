#!/usr/bin/env python3
"""
shopify_image_manager.py — Redenumire + Compresie poze Shopify

Modes:
  rename   — Setează alt text pe imagini din titlul produsului (SEO-friendly)
  compress — Download → comprimare → re-upload (cu ștergere original)
  both     — Rename + Compress

Usage:
  python shopify_image_manager.py --store CARP --mode both --max-size 1080 --quality 85
  python shopify_image_manager.py --store CARP --mode rename --dry-run
"""

import argparse
import io
import mimetypes
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

try:
    import pillow_avif  # noqa: F401
except ImportError:
    pass

# Core imports
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from core.stores import get_store as _core_get_store
except ImportError:
    _core_get_store = None

API_VERSION = "2026-04"
TIMEOUT = 90
PRODUCTS_PER_PAGE = 100

# ─── GraphQL Queries ──────────────────────────────────────────

GRAPHQL_PRODUCTS_WITH_IMAGES = """
query GetProducts($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        title
        handle
        variants(first: 100) {
          nodes {
            sku
          }
        }
        media(first: 100) {
          nodes {
            id
            mediaContentType
            alt
            ... on MediaImage {
              image {
                url
                width
                height
              }
            }
          }
        }
      }
    }
  }
}
"""

# Mutation: update file name + alt text
GRAPHQL_UPDATE_FILE = """
mutation fileUpdate($input: [FileUpdateInput!]!) {
  fileUpdate(files: $input) {
    files {
      id
      alt
      ... on MediaImage {
        image { url }
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

GRAPHQL_STAGED_UPLOADS_CREATE = """
mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets {
      url
      resourceUrl
      parameters {
        name
        value
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

GRAPHQL_PRODUCT_UPDATE_MEDIA = """
mutation UpdateProductWithNewMedia($product: ProductUpdateInput!, $media: [CreateMediaInput!]) {
  productUpdate(product: $product, media: $media) {
    product {
      id
      title
    }
    userErrors {
      field
      message
    }
  }
}
"""

GRAPHQL_PRODUCT_DELETE_MEDIA = """
mutation productDeleteMedia($mediaIds: [ID!]!, $productId: ID!) {
  productDeleteMedia(mediaIds: $mediaIds, productId: $productId) {
    deletedMediaIds
    deletedProductImageIds
    mediaUserErrors {
      field
      message
    }
  }
}
"""


# ─── Helpers ──────────────────────────────────────────────────

def log(msg: str):
    print(msg, flush=True)


def sanitize_sku(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace(",", "").replace("'", "").replace('"', "").replace("&", "and")
    text = re.sub(r"[\s/\\|:;]+", "_", text)
    text = re.sub(r"[^A-Za-z0-9._-]", "", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return text


def slugify_title(title: str) -> str:
    """Convert product title to SEO-friendly slug for alt text and filenames.

    Example: 'Ceas Automatic Seiko Presage — Ediție Limitată'
          -> 'ceas-automatic-seiko-presage-editie-limitata'
    """
    text = (title or "").strip()
    if not text:
        return ""
    # Normalize unicode, remove diacritics
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Lowercase
    text = text.lower()
    # Replace common separators with hyphens
    text = text.replace("&", "and")
    text = re.sub(r"[\s_/\\|:;,.'\"]+", "-", text)
    # Remove non-alphanumeric (keep hyphens)
    text = re.sub(r"[^a-z0-9-]", "", text)
    # Collapse multiple hyphens
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1048576:
        return f"{b / 1024:.1f} KB"
    return f"{b / 1048576:.1f} MB"


def graphql_request(shop: str, token: str, query: str, variables: dict):
    url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    response = requests.post(
        url, headers=headers,
        json={"query": query, "variables": variables},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if "errors" in data and data["errors"]:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data


def iter_all_products(shop: str, token: str):
    after = None
    while True:
        variables = {"first": PRODUCTS_PER_PAGE, "after": after}
        data = graphql_request(shop, token, GRAPHQL_PRODUCTS_WITH_IMAGES, variables)
        products = data["data"]["products"]
        for edge in products["edges"]:
            yield edge["node"]
        if not products["pageInfo"]["hasNextPage"]:
            break
        after = products["pageInfo"]["endCursor"]


def choose_base_sku(product_node: dict) -> str:
    variants = product_node.get("variants", {}).get("nodes", []) or []
    for variant in variants:
        sku = sanitize_sku(variant.get("sku") or "")
        if sku:
            return sku
    return ""


def get_extension_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    _, ext = os.path.splitext(path)
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}:
        return ext
    return ".jpg"


def download_image(url: str) -> bytes:
    """Download image and return raw bytes."""
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content


def compress_image(image_bytes: bytes, max_size: int, quality: int) -> tuple:
    """Compress and resize image. Returns (compressed_bytes, orig_w, orig_h, new_w, new_h)."""
    img = Image.open(io.BytesIO(image_bytes))
    orig_w, orig_h = img.size

    # Convert to RGB for JPEG output
    if img.mode in ("RGBA", "LA", "P"):
        if img.mode == "P":
            img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA"):
            bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Resize if needed
    width, height = img.size
    if width > max_size or height > max_size:
        if width < height:
            new_width = max_size
            new_height = int((max_size / width) * height)
        else:
            new_height = max_size
            new_width = int((max_size / height) * width)
        img = img.resize((new_width, new_height), Image.LANCZOS)

    new_w, new_h = img.size

    # Save to bytes
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    compressed_bytes = buf.getvalue()

    return compressed_bytes, orig_w, orig_h, new_w, new_h


def guess_mime_type(filename: str) -> str:
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "image/jpeg"


def create_staged_target(shop: str, token: str, filename: str, mime_type: str):
    variables = {
        "input": [{
            "filename": filename,
            "mimeType": mime_type,
            "httpMethod": "POST",
            "resource": "PRODUCT_IMAGE",
        }]
    }
    data = graphql_request(shop, token, GRAPHQL_STAGED_UPLOADS_CREATE, variables)
    payload = data["data"]["stagedUploadsCreate"]
    user_errors = payload.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"stagedUploadsCreate userErrors: {user_errors}")
    targets = payload.get("stagedTargets") or []
    if not targets:
        raise RuntimeError("Nu am primit staged target")
    return targets[0]


def upload_bytes_to_staged_target(staged_target: dict, file_bytes: bytes, filename: str):
    url = staged_target["url"]
    params = staged_target.get("parameters") or []
    form_data = {p["name"]: p["value"] for p in params}
    mime_type = guess_mime_type(filename)
    files = {"file": (filename, io.BytesIO(file_bytes), mime_type)}
    response = requests.post(url, data=form_data, files=files, timeout=TIMEOUT)
    response.raise_for_status()


def attach_media_to_product(shop: str, token: str, product_id: str, media_inputs: list):
    variables = {
        "product": {"id": product_id},
        "media": media_inputs,
    }
    data = graphql_request(shop, token, GRAPHQL_PRODUCT_UPDATE_MEDIA, variables)
    payload = data["data"]["productUpdate"]
    user_errors = payload.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"productUpdate userErrors: {user_errors}")


def delete_product_media(shop: str, token: str, product_id: str, media_ids: list):
    if not media_ids:
        return
    variables = {
        "mediaIds": media_ids,
        "productId": product_id,
    }
    data = graphql_request(shop, token, GRAPHQL_PRODUCT_DELETE_MEDIA, variables)
    payload = data["data"]["productDeleteMedia"]
    errors = payload.get("mediaUserErrors") or []
    if errors:
        raise RuntimeError(f"productDeleteMedia errors: {errors}")


def rename_image(shop: str, token: str, media_id: str, new_filename: str, new_alt: str):
    """Update filename + alt text on a file/media via fileUpdate mutation."""
    file_input = {
        "id": media_id,
        "alt": new_alt,
        "filename": new_filename,
    }
    variables = {"input": [file_input]}
    data = graphql_request(shop, token, GRAPHQL_UPDATE_FILE, variables)
    payload = data["data"]["fileUpdate"]
    user_errors = payload.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"fileUpdate userErrors: {user_errors}")


# ─── Main Logic ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Shopify Image Manager — Rename & Compress")
    parser.add_argument("--store", required=True, help="Store prefix (e.g. CARP)")
    parser.add_argument("--mode", choices=["rename", "compress", "both"], default="both",
                        help="Mode: rename (alt text), compress (download+resize+reupload), both")
    parser.add_argument("--max-size", type=int, default=1080, help="Max dimension in px (for compress)")
    parser.add_argument("--quality", type=int, default=85, help="JPEG quality 1-100 (for compress)")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be done")

    args = parser.parse_args()
    prefix = args.store.strip().upper()
    mode = args.mode
    max_size = args.max_size
    quality = args.quality
    dry_run = args.dry_run

    # Load store
    if _core_get_store:
        store = _core_get_store(prefix)
    else:
        store = None
    if not store:
        log(f"❌ Magazin '{prefix}' negăsit!")
        sys.exit(1)

    shop = store["shop"]
    token = store["token"]
    if not shop or not token:
        log(f"❌ Magazin '{prefix}' fără shop sau token!")
        sys.exit(1)

    do_rename = mode in ("rename", "both")
    do_compress = mode in ("compress", "both")

    mode_label = {"rename": "Redenumire (alt text)", "compress": "Compresie + Re-upload", "both": "Redenumire + Compresie"}
    log(f"🏪 Magazin: {prefix} ({shop})")
    log(f"🔧 Mod: {mode_label[mode]}")
    if do_compress:
        log(f"📐 Max rezoluție: {max_size}px")
        log(f"🎨 Calitate JPEG: {quality}")
    if dry_run:
        log(f"⚠️  DRY RUN — nu se fac modificări!")
    log("")

    # Stats
    total_products = 0
    skipped_no_sku = 0
    renamed_count = 0
    compressed_count = 0
    total_orig_bytes = 0
    total_new_bytes = 0
    errors = 0

    # Process
    for product in iter_all_products(shop, token):
        total_products += 1
        base_sku = choose_base_sku(product)
        title = product.get("title", "?")

        if not base_sku:
            skipped_no_sku += 1
            log(f"[SKIP] Fără SKU: {title}")
            continue

        media_nodes = product.get("media", {}).get("nodes", []) or []
        image_nodes = [m for m in media_nodes if m.get("mediaContentType") == "IMAGE"]

        if not image_nodes:
            continue

        product_id = product["id"]
        title_slug = slugify_title(title)
        # Fallback to SKU if title is empty/unusable
        if not title_slug:
            title_slug = base_sku.lower().replace("_", "-")

        log(f"─── {base_sku} │ {title} │ {len(image_nodes)} poze ───")
        log(f"    slug: {title_slug}")

        # ── RENAME ──
        if do_rename:
            for idx, media in enumerate(image_nodes, 1):
                media_id = media.get("id")
                old_alt = media.get("alt") or ""
                image_info = media.get("image") or {}
                image_url = image_info.get("url", "")

                # Shopify requires the original extension in the filename
                orig_ext = get_extension_from_url(image_url)  # e.g. ".png", ".jpg", ".gif"
                new_filename = f"{title_slug}-{idx}{orig_ext}"
                # Alt text = titlu lizibil cu index
                new_alt = f"{title} {idx}"

                if dry_run:
                    log(f"  ✏️  [DRY] NAME: \"{new_filename}\"")
                    log(f"          ALT:  \"{new_alt}\"")
                    renamed_count += 1
                else:
                    try:
                        rename_image(shop, token, media_id, new_filename, new_alt)
                        log(f"  ✏️  NAME: \"{new_filename}\"")
                        log(f"      ALT:  \"{new_alt}\"")
                        renamed_count += 1
                    except Exception as e:
                        log(f"  ❌ Rename error ({new_filename}): {e}")
                        errors += 1

                # Rate limiting
                time.sleep(0.15)

        # ── COMPRESS ──
        if do_compress:
            # Collect images to process
            images_to_upload = []
            media_ids_to_delete = []

            for idx, media in enumerate(image_nodes, 1):
                media_id = media.get("id")
                image_info = media.get("image") or {}
                image_url = image_info.get("url")
                orig_w = image_info.get("width") or 0
                orig_h = image_info.get("height") or 0

                if not image_url:
                    continue

                # Skip GIFs — preserve animation
                orig_ext = get_extension_from_url(image_url)
                if orig_ext == ".gif":
                    log(f"  🎞️  {title_slug}-{idx}{orig_ext} — GIF, skip compresie (animație)")
                    continue

                # Check if already small enough
                if orig_w and orig_h and orig_w <= max_size and orig_h <= max_size:
                    log(f"  📐 {title_slug}-{idx} — deja {orig_w}x{orig_h}, skip compresie")
                    continue

                try:
                    # Download
                    raw_bytes = download_image(image_url)
                    orig_size = len(raw_bytes)
                    total_orig_bytes += orig_size

                    # Compress
                    compressed_bytes, ow, oh, nw, nh = compress_image(raw_bytes, max_size, quality)
                    new_size = len(compressed_bytes)
                    total_new_bytes += new_size

                    saved = orig_size - new_size
                    pct = (saved / orig_size * 100) if orig_size > 0 else 0

                    new_filename = f"{title_slug}-{idx}.jpg"
                    new_alt = f"{title_slug}-{idx}"

                    if dry_run:
                        log(f"  📦 [DRY] COMPRESS: {new_filename} │ "
                            f"{format_bytes(orig_size)} → {format_bytes(new_size)} │ "
                            f"{ow}x{oh} → {nw}x{nh} │ Salvat: {pct:.1f}%")
                        compressed_count += 1
                    else:
                        # Stage upload
                        staged = create_staged_target(shop, token, new_filename, "image/jpeg")
                        upload_bytes_to_staged_target(staged, compressed_bytes, new_filename)

                        images_to_upload.append({
                            "alt": new_alt,
                            "mediaContentType": "IMAGE",
                            "originalSource": staged["resourceUrl"],
                        })
                        media_ids_to_delete.append(media_id)

                        log(f"  📦 COMPRESSED: {new_filename} │ "
                            f"{format_bytes(orig_size)} → {format_bytes(new_size)} │ "
                            f"{ow}x{oh} → {nw}x{nh} │ Salvat: {pct:.1f}%")
                        compressed_count += 1

                except Exception as e:
                    log(f"  ❌ Compress error ({title_slug}-{idx}): {e}")
                    errors += 1

                time.sleep(0.2)

            # Attach new images and delete old ones
            if not dry_run and images_to_upload:
                try:
                    # First delete old images
                    if media_ids_to_delete:
                        delete_product_media(shop, token, product_id, media_ids_to_delete)
                        log(f"  🗑️  Șterse {len(media_ids_to_delete)} imagini vechi")
                        time.sleep(0.3)

                    # Then attach new ones
                    attach_media_to_product(shop, token, product_id, images_to_upload)
                    log(f"  ✅ Atașate {len(images_to_upload)} imagini comprimate")

                except Exception as e:
                    log(f"  ❌ Upload/Delete error ({base_sku}): {e}")
                    errors += 1

        log("")

    # ── SUMMARY ──
    log("═" * 60)
    log("📊 RAPORT FINAL")
    log("═" * 60)
    log(f"  🏪 Magazin:              {prefix}")
    log(f"  📦 Produse procesate:     {total_products}")
    log(f"  ⚠️  Fără SKU (skip):      {skipped_no_sku}")
    log("")

    if do_rename:
        log(f"  ✏️  Poze redenumite:      {renamed_count}")

    if do_compress:
        log(f"  📦 Poze comprimate:      {compressed_count}")
        if total_orig_bytes > 0:
            total_saved = total_orig_bytes - total_new_bytes
            total_pct = (total_saved / total_orig_bytes * 100)
            log(f"  💾 Size original:        {format_bytes(total_orig_bytes)}")
            log(f"  💾 Size comprimat:       {format_bytes(total_new_bytes)}")
            log(f"  🎉 Total salvat:         {format_bytes(total_saved)} ({total_pct:.1f}%)")

    if errors > 0:
        log(f"\n  ❌ Erori:                {errors}")

    if dry_run:
        log(f"\n  ⚠️  DRY RUN — nu s-a modificat nimic!")

    log("")


if __name__ == "__main__":
    main()
