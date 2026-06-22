import os
import sqlite3
from contextlib import contextmanager

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "etransport.db")


def _ensure_column(cursor, table: str, column: str, ddl: str) -> None:
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)

    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tariff_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                full_display TEXT NOT NULL,
                source TEXT DEFAULT 'smartbill',
                is_active BOOLEAN DEFAULT 1
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS product_tariff_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name_normalized TEXT NOT NULL UNIQUE,
                hs_code_raw TEXT,
                matched_tariff_code TEXT NOT NULL,
                matched_tariff_label TEXT,
                confidence REAL,
                source TEXT DEFAULT 'manual',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etransport_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uit_code TEXT UNIQUE,
                operation_type TEXT,
                operation_type_code TEXT,
                transport_date TEXT,
                supplier_name TEXT,
                supplier_country TEXT,
                supplier_code TEXT,
                carrier_name TEXT,
                carrier_vat TEXT,
                vehicle_no TEXT,
                trailer_no TEXT,
                customs_office_code TEXT,
                ptf_code TEXT,
                dest_county TEXT,
                dest_city TEXT,
                dest_street TEXT,
                dest_postal TEXT,
                dest_number TEXT,
                document_number TEXT,
                document_date TEXT,
                source_file TEXT,
                file_hash TEXT UNIQUE,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etransport_product_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                product_name_raw TEXT,
                product_name_normalized TEXT,
                hs_code_raw TEXT,
                nc_code_8 TEXT,
                tariff_code_final TEXT,
                tariff_label_final TEXT,
                quantity REAL,
                uom TEXT,
                net_weight REAL,
                gross_weight REAL,
                value_ron REAL,
                purpose_code TEXT,
                source TEXT DEFAULT 'xml_import',
                confidence REAL DEFAULT 100.0,
                was_manual_override BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (document_id) REFERENCES etransport_documents(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS carrier_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                carrier_name TEXT NOT NULL,
                carrier_name_normalized TEXT NOT NULL,
                carrier_vat TEXT NOT NULL,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                times_seen INTEGER DEFAULT 1,
                source_document_id INTEGER,
                UNIQUE(carrier_name_normalized, carrier_vat)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS product_tariff_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name_normalized TEXT NOT NULL,
                hs_code_raw TEXT,
                nc_code_8 TEXT,
                tariff_code TEXT NOT NULL,
                tariff_label TEXT,
                status TEXT DEFAULT 'suggested',
                source TEXT DEFAULT 'historical',
                confidence REAL DEFAULT 0.0,
                confirmed_at TIMESTAMP,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(product_name_normalized, tariff_code)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hs_codes_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hs_code TEXT NOT NULL UNIQUE,
                hs_digits TEXT NOT NULL,
                hs_length INTEGER NOT NULL,
                chapter2 TEXT,
                heading4 TEXT,
                subheading6 TEXT,
                hs8_candidate TEXT,
                description TEXT NOT NULL,
                source_file TEXT,
                source_type TEXT DEFAULT 'pdf_import',
                source_version TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hs_code_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hs_code_raw TEXT,
                hs_code_raw_digits TEXT NOT NULL,
                hs6 TEXT,
                hs8_candidate TEXT,
                product_name_normalized TEXT DEFAULT '',
                nc8_code TEXT NOT NULL,
                tariff_label TEXT,
                status TEXT DEFAULT 'suggested',
                source TEXT DEFAULT 'historical',
                confidence REAL DEFAULT 0.0,
                supplier_name TEXT,
                first_seen_document TEXT,
                last_seen_document TEXT,
                times_seen INTEGER DEFAULT 1,
                confirmed_at TIMESTAMP,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(hs_code_raw_digits, product_name_normalized, nc8_code)
            )
        ''')

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hs_catalog_digits ON hs_codes_catalog(hs_digits)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hs_catalog_hs8 ON hs_codes_catalog(hs8_candidate)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hs_memory_digits ON hs_code_memory(hs_code_raw_digits)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hs_memory_hs8 ON hs_code_memory(hs8_candidate)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hs_memory_status ON hs_code_memory(status)")
        # Perf: historical_tariff_matcher cauta per-linie de produs in istoric (name/hs/nc8 + JOIN pe document_id)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_eph_name ON etransport_product_history(product_name_normalized)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_eph_hs ON etransport_product_history(hs_code_raw)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_eph_nc8 ON etransport_product_history(nc_code_8)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_eph_docid ON etransport_product_history(document_id)")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etransport_review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT,
                document_date TEXT,
                source_line_no INTEGER,
                supplier TEXT,
                product_name_raw TEXT,
                product_name_export TEXT,
                hs_code_raw TEXT,
                hs6 TEXT,
                nc8_candidate TEXT,
                suggested_code TEXT,
                suggested_label TEXT,
                method TEXT,
                source TEXT,
                confidence REAL,
                reason TEXT,
                is_strong BOOLEAN,
                historical_match_found BOOLEAN,
                openai_suggested_code TEXT,
                openai_rationale TEXT,
                openai_confidence REAL,
                openai_run_at TIMESTAMP,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(invoice_number, source_line_no, product_name_raw)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etransport_app_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etransport_rejected_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name_normalized TEXT NOT NULL,
                hs_code_raw TEXT,
                rejected_code TEXT NOT NULL,
                reason TEXT,
                source_document TEXT,
                rejected_by TEXT DEFAULT 'user',
                rejected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(product_name_normalized, rejected_code)
            )
        ''')


        _ensure_column(cursor, "etrtransport_product_history".replace("tr", "tr"), "hs_code_raw", "hs_code_raw TEXT") if False else None
        # Keep the migration section explicit and safe.
        _ensure_column(cursor, "etransport_product_history", "hs_code_raw", "hs_code_raw TEXT")
        _ensure_column(cursor, "etransport_product_history", "nc_code_8", "nc_code_8 TEXT")
        _ensure_column(cursor, "etransport_product_history", "tariff_code_final", "tariff_code_final TEXT")
        _ensure_column(cursor, "etransport_product_history", "tariff_label_final", "tariff_label_final TEXT")

        conn.commit()


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def get_app_setting(key: str, default: str = None) -> str:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM etransport_app_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row['value'] if row else default

def set_app_setting(key: str, value: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO etransport_app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        ''', (key, value))
        conn.commit()

init_db()
