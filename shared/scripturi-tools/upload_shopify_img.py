import csv
import mimetypes
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import requests

# Use core.stores if available, fallback to manual CSV
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from core.stores import get_store as _core_get_store
except ImportError:
    _core_get_store = None

STORES_CSV = "stores.csv"
INPUT_FOLDER = "images_compressed"
API_VERSION = "2026-04"
TIMEOUT = 90
PRODUCTS_PER_PAGE = 100
DELETE_EXISTING_PRODUCT_IMAGES = False

GRAPHQL_PRODUCTS_QUERY = """
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
        variants(first: 100) {
          nodes {
            id
            sku
          }
        }
        media(first: 100) {
          nodes {
            id
            mediaContentType
          }
        }
      }
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

def normalize_shop_domain(shop_value: str) -> str:
    shop_value = (shop_value or "").strip()
    return shop_value.replace("https://", "").replace("http://", "").strip("/")

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

def load_store_by_prefix(prefix: str, csv_path: str = STORES_CSV):
    """Load store by prefix — uses core.stores if available."""
    if _core_get_store is not None:
        store = _core_get_store(prefix)
        if store:
            return store
        raise ValueError(f"Prefixul {prefix} nu a fost găsit")

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Nu exista fisierul {csv_path}")
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("prefix") or "").strip().upper() == prefix.upper():
                shop = normalize_shop_domain(row.get("shop", ""))
                token = (row.get("token") or "").strip()
                if not shop or not token:
                    raise ValueError(f"Magazinul {prefix} are shop sau token lipsa in stores.csv")
                return {"prefix": prefix.upper(), "shop": shop, "token": token}
    raise ValueError(f"Prefixul {prefix} nu a fost gasit in {csv_path}")

def graphql_request(shop: str, token: str, query: str, variables: dict):
    url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
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
        data = graphql_request(shop, token, GRAPHQL_PRODUCTS_QUERY, variables)
        products = data["data"]["products"]

        for edge in products["edges"]:
            yield edge["node"]

        if not products["pageInfo"]["hasNextPage"]:
            break

        after = products["pageInfo"]["endCursor"]

def build_sku_to_product_map(shop: str, token: str):
    sku_map = {}
    duplicate_skus = set()

    for product in iter_all_products(shop, token):
        product_id = product["id"]
        product_title = product.get("title") or ""
        media_ids = [
            m["id"]
            for m in (product.get("media", {}).get("nodes", []) or [])
            if m.get("mediaContentType") == "IMAGE" and m.get("id")
        ]

        for variant in product.get("variants", {}).get("nodes", []) or []:
            clean_sku = sanitize_sku(variant.get("sku") or "")
            if not clean_sku:
                continue

            entry = {
                "product_id": product_id,
                "product_title": product_title,
                "media_ids": media_ids,
            }

            if clean_sku in sku_map:
                duplicate_skus.add(clean_sku)
            else:
                sku_map[clean_sku] = entry

    return sku_map, duplicate_skus

def list_local_images(folder: str):
    if not os.path.exists(folder):
        raise FileNotFoundError(f"Nu exista folderul {folder}")

    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    pattern = re.compile(r"^(?P<sku>.+)_(?P<idx>\d+)(?P<ext>\.[A-Za-z0-9]+)$")
    grouped = defaultdict(list)

    for filename in os.listdir(folder):
        full_path = os.path.join(folder, filename)
        if not os.path.isfile(full_path):
            continue

        match = pattern.match(filename)
        if not match:
            continue

        ext = match.group("ext").lower()
        if ext not in valid_exts:
            continue

        sku = sanitize_sku(match.group("sku"))
        if not sku:
            continue

        idx = int(match.group("idx"))

        grouped[sku].append({
            "filename": filename,
            "path": full_path,
            "index": idx,
            "ext": ext,
        })

    for sku in grouped:
        grouped[sku].sort(key=lambda x: x["index"])

    return grouped

def guess_mime_type(file_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or "application/octet-stream"

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

def upload_binary_to_staged_target(staged_target: dict, file_path: str):
    url = staged_target["url"]
    params = staged_target.get("parameters") or []
    form_data = {p["name"]: p["value"] for p in params}
    mime_type = guess_mime_type(file_path)

    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, mime_type)}
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

def delete_existing_product_images(shop: str, token: str, product_id: str, media_ids: list):
    if not media_ids:
        return

    variables = {
        "mediaIds": media_ids,
        "productId": product_id,
    }

    data = graphql_request(shop, token, GRAPHQL_PRODUCT_DELETE_MEDIA, variables)
    payload = data["data"]["productDeleteMedia"]

    media_user_errors = payload.get("mediaUserErrors") or []
    if media_user_errors:
        raise RuntimeError(f"productDeleteMedia mediaUserErrors: {media_user_errors}")

def main():
    if len(sys.argv) < 2:
        print("Utilizare: python upload_shopify_img.py CARP")
        sys.exit(1)

    prefix = sys.argv[1].strip().upper()
    store = load_store_by_prefix(prefix)
    shop = store["shop"]
    token = store["token"]

    local_images = list_local_images(INPUT_FOLDER)
    if not local_images:
        print("Nu am gasit imagini valide in images_compressed.")
        sys.exit(0)

    sku_map, duplicate_skus = build_sku_to_product_map(shop, token)

    uploaded_files = 0
    matched_skus = 0
    skipped_skus = 0

    for sku, files in sorted(local_images.items()):
        if not sku:
            skipped_skus += 1
            continue

        if sku in duplicate_skus:
            print(f"[SKIP] SKU duplicat in Shopify: {sku}")
            skipped_skus += 1
            continue

        product_info = sku_map.get(sku)
        if not product_info:
            print(f"[SKIP] SKU negasit in Shopify: {sku}")
            skipped_skus += 1
            continue

        product_id = product_info["product_id"]
        product_title = product_info["product_title"]
        existing_media_ids = product_info["media_ids"]

        print(f"[PRODUCT] {sku} -> {product_title}")

        try:
            if DELETE_EXISTING_PRODUCT_IMAGES and existing_media_ids:
                delete_existing_product_images(shop, token, product_id, existing_media_ids)
                print(f"  - imagini vechi sterse: {len(existing_media_ids)}")

            media_inputs = []

            for item in files:
                filename = item["filename"]
                file_path = item["path"]
                mime_type = guess_mime_type(file_path)

                staged_target = create_staged_target(shop, token, filename, mime_type)
                upload_binary_to_staged_target(staged_target, file_path)

                media_inputs.append({
                    "alt": sku,
                    "mediaContentType": "IMAGE",
                    "originalSource": staged_target["resourceUrl"],
                })

                print(f"  [UPLOADED] {filename}")

            attach_media_to_product(shop, token, product_id, media_inputs)
            uploaded_files += len(files)
            matched_skus += 1
            print(f"  [OK] atasate {len(files)} imagini")

        except Exception as e:
            print(f"  [ERR] {sku}: {e}")

    print("-" * 60)
    print(f"SKU-uri locale gasite: {len(local_images)}")
    print(f"SKU-uri potrivite si procesate: {matched_skus}")
    print(f"SKU-uri sarite: {skipped_skus}")
    print(f"Imagini incarcate: {uploaded_files}")

if __name__ == "__main__":
    main()