from typing import List, Dict, Any
from etransport.db import get_db_connection
from datetime import datetime

def save_to_review_queue(product_list: List[dict]):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        for item in product_list:
            cursor.execute('''
                INSERT INTO etransport_review_queue (
                    invoice_number,
                    document_date,
                    source_line_no,
                    supplier,
                    product_name_raw,
                    product_name_export,
                    hs_code_raw,
                    hs6,
                    nc8_candidate,
                    suggested_code,
                    suggested_label,
                    method,
                    source,
                    confidence,
                    reason,
                    is_strong,
                    historical_match_found
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(invoice_number, source_line_no, product_name_raw) 
                DO UPDATE SET
                    suggested_code = excluded.suggested_code,
                    suggested_label = excluded.suggested_label,
                    method = excluded.method,
                    source = excluded.source,
                    confidence = excluded.confidence,
                    reason = excluded.reason,
                    is_strong = excluded.is_strong,
                    historical_match_found = excluded.historical_match_found,
                    updated_at = CURRENT_TIMESTAMP
            ''', (
                item.get('invoice_number'),
                item.get('document_date'),
                item.get('source_line_no'),
                item.get('supplier'),
                item.get('product_name_raw'),
                item.get('product_name_export'),
                item.get('hs_code_raw'),
                item.get('hs6'),
                item.get('nc8_candidate'),
                item.get('suggested_code'),
                item.get('suggested_label'),
                item.get('method'),
                item.get('source'),
                item.get('confidence'),
                item.get('reason'),
                item.get('is_strong'),
                item.get('historical_match_found'),
            ))
        conn.commit()

def get_app_setting(key: str, default: str = None) -> str:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM etransport_app_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row['value'] if row else default

def set_app_setting(key: str, value: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # insert or replace using upsert
        cursor.execute('''
            INSERT INTO etransport_app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        ''', (key, value))
        conn.commit()
