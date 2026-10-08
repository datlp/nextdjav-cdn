import argparse
import os
import sys
import time
import datetime
import json
import re
import sqlite3
import base64
import hmac
import hashlib
import threading
import concurrent.futures
import importlib.util
import builtins
import difflib
import signal
import queue
import unicodedata
import contextlib
from urllib.parse import urlparse, parse_qs, quote

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')



def custom_log(category, message):
    now = datetime.datetime.now()
    timestamp = now.strftime('%y%m%d-%H%M%S.') + f"{now.microsecond // 1000:03d}"
    try:
        import sys, os
        port = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a in ('--port', '-port')), None)
        if not port:
            port = os.environ.get('PORT', '??')
    except:
        port = "??"
    print(f"[{timestamp}] [Port:{port}] [{category}] {message}", flush=True)

# Đưa custom_log vào builtins để các file source-*.py gọi được mà không cần import builtins
builtins.custom_log = custom_log

try:
    from curl_cffi import requests as curl_requests
    from bs4 import BeautifulSoup
    from flask import Flask, request, jsonify, Response, make_response, send_from_directory
    import psutil
    custom_log("System", "✔️ Kiểm tra đủ package cơ bản...")
except ImportError as e:
    print(f"Lỗi: Không tìm thấy thư viện bắt buộc. Chi tiết: {e}")
    print("Vui lòng cài đặt các thư viện cần thiết bằng lệnh sau:")
    print("pip install curl_cffi beautifulsoup4 flask")
    custom_log("System", f"❌ Không tìm thấy thư viện bắt buộc. Chi tiết: {e}")
    custom_log("System", "⚠️ Vui lòng cài đặt bằng lệnh: pip install curl_cffi beautifulsoup4 flask psutil")
    sys.exit(1)
#  
# Monkey patch curl_cffi.requests Session to enforce secure DNS (1.1.1.1 / 8.8.8.8)
from curl_cffi import curl
original_Session = curl_requests.Session

class PatchedSession(original_Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if hasattr(self, 'curl') and self.curl:
            try:
                # Dùng DNS over HTTPS (DoH) qua Cloudflare 1.1.1.1 (hoặc Google 8.8.8.8) thay vì DNS hệ thống
                self.curl.setopt(curl.CurlOpt.DOH_URL, b'https://cloudflare-dns.com/dns-query')
            except Exception:
                try:
                    self.curl.setopt(curl.CurlOpt.DOH_URL, b'https://dns.google/dns-query')
                except Exception:
                    pass

curl_requests.Session = PatchedSession

#  
try:
    import spacy
except ImportError:
    spacy = None
try:
    import underthesea
except ImportError:
    underthesea = None

try:
    import nltk
except ImportError:
    nltk = None

global_last_request_time = time.time()
CLIENT_IDLE_TIMEOUT = 5
DB_FLUSH_INTERVAL = 5 # Khoảng thời gian (giây) định kỳ ghi buffer xuống DB

memory_lock = threading.Lock()
db_lock = threading.Lock()
db_buffer = {
    'videos': {},
    'video_urls': {},
    'media': {}
}
downloading_media = set()

JWT_SECRET = "javtiful-player-secret-key-2026"

def create_jwt(payload):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(',', ':')).encode()).decode().rstrip('=')
    payload_enc = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    signature = base64.urlsafe_b64encode(hmac.new(JWT_SECRET.encode(), f"{header}.{payload_enc}".encode(), hashlib.sha256).digest()).decode().rstrip('=')
    return f"{header}.{payload_enc}.{signature}"

def verify_jwt(token):
    try:
        header, payload_enc, signature = token.split('.')
        expected_sig = base64.urlsafe_b64encode(hmac.new(JWT_SECRET.encode(), f"{header}.{payload_enc}".encode(), hashlib.sha256).digest()).decode().rstrip('=')
        if hmac.compare_digest(signature, expected_sig):
            payload_padded = payload_enc + '=' * (-len(payload_enc) % 4)
            return json.loads(base64.urlsafe_b64decode(payload_padded.encode()).decode())
    except Exception:
        pass
    return None

def extract_clean_keywords_bulletproof(text):
    if not text:
        return []
    text = re.sub(r'[^\w\s]', ' ', text.lower())
    words = text.split()
    
    if nltk:
        try:
            try:
                nltk.data.find('corpora/stopwords')
            except LookupError:
                nltk.download('stopwords', quiet=True)
            from nltk.corpus import stopwords
            stop_words = set(stopwords.words('english'))
            words = [w for w in words if w not in stop_words and not w.isdigit() and len(w) > 2]
            return list(dict.fromkeys(words))
        except Exception:
            pass
            
    # Fallback nếu không có nltk hoặc nltk bị lỗi
    basic_stopwords = {'the', 'is', 'are', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'this', 'that'}
    words = [w for w in words if w not in basic_stopwords and not w.isdigit() and len(w) > 2]
    return list(dict.fromkeys(words))

def extract_clean_keywords_viet_eng(text):
    if not text:
        return []
    text = re.sub(r'[^\w\s]', ' ', text.lower())
    words = text.split()
    
    stop_words = {'the', 'is', 'are', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'this', 'that'}
    vi_stopwords = {'và', 'của', 'các', 'có', 'được', 'cho', 'trong', 'đã', 'một', 'với', 'những', 'là', 'như', 'hay', 'đang', 'nhưng', 'tại', 'để', 'từ', 'khi', 'làm', 'đến', 'sự', 'này', 'ra', 'phải', 'người', 'về', 'sau', 'rằng', 'chỉ', 'cũng', 'nhiều', 'việc', 'hơn', 'mới', 'vì', 'nếu', 'lại', 'rất', 'còn', 'bởi', 'thì', 'lên', 'đi', 'nào', 'sẽ', 'đó', 'thể', 'theo', 'mình', 'qua', 'phim', 'sex', 'jav', 'vietsub', 'không', 'che', 'hd', 'vlxx', 'full', 'bản', 'đẹp', 'nhất'}
    stop_words.update(vi_stopwords)
    
    if nltk:
        try:
            try:
                nltk.data.find('corpora/stopwords')
            except LookupError:
                nltk.download('stopwords', quiet=True)
            from nltk.corpus import stopwords
            stop_words.update(stopwords.words('english'))
            try:
                stop_words.update(stopwords.words('vietnamese'))
            except Exception:
                pass
        except Exception:
            pass
            
    words = [w for w in words if w not in stop_words and not w.isdigit() and len(w) > 2]
    return list(dict.fromkeys(words))


VIDEOS_TABLE = "javtiful_videos"

@contextlib.contextmanager
def sqlite_timeout(conn, timeout=2.0):
    start = time.time()
    def handler():
        if time.time() - start > timeout:
            return 1
        return 0
    conn.set_progress_handler(handler, 10000)
    try:
        yield
    finally:
        conn.set_progress_handler(None, 0)

def flush_db_buffer(db_conn):
    with memory_lock:
        videos_to_save = db_buffer['videos'].copy()
        urls_to_save = db_buffer['video_urls'].copy()
        media_to_save = db_buffer['media'].copy()
        
        db_buffer['videos'].clear()
        db_buffer['video_urls'].clear()
        db_buffer['media'].clear()
        
    if not any([videos_to_save, urls_to_save, media_to_save]):
        return
        
    try:
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute("BEGIN TRANSACTION;")
            
            for vid_id, vid in videos_to_save.items():
                cursor.execute(f'''
                    INSERT INTO {VIDEOS_TABLE} (id, title, cover, added_at, release_date, dvd)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        cover = excluded.cover,
                        added_at = excluded.added_at,
                        release_date = excluded.release_date,
                        dvd = excluded.dvd
                ''', (vid['id'], vid['title'], vid['cover'], vid['added_at'], vid.get('release_date', ''), vid.get('dvd', '')))
                
            for vid_id, url in urls_to_save.items():
                cursor.execute(f"UPDATE {VIDEOS_TABLE} SET url = ? WHERE id = ?", (url, vid_id))
                
            for media_id, m in media_to_save.items():
                cursor.execute("INSERT OR REPLACE INTO media (id, data, content_type) VALUES (?, ?, ?)", (media_id, m['data'], m['content_type']))
                
            db_conn.commit()
            
        details_ids = ",".join(urls_to_save.keys())
        custom_log("System", f"✔️ Buffer ghi xuống DB: {len(videos_to_save)} videos | {len(urls_to_save)} details: {details_ids}")
    except Exception as e:
        custom_log("System", f"❌ Lỗi khi ghi DB: {e}")
        try:
            with db_lock:
                db_conn.rollback()
        except:
            pass
        with memory_lock:
            for vid_id, vid in videos_to_save.items():
                if vid_id not in db_buffer['videos']: db_buffer['videos'][vid_id] = vid
            for vid_id, url in urls_to_save.items():
                if vid_id not in db_buffer['video_urls']: db_buffer['video_urls'][vid_id] = url
            for media_id, m in media_to_save.items():
                if media_id not in db_buffer['media']: db_buffer['media'][media_id] = m

def background_db_worker(db_conn):
    while True:
        time.sleep(DB_FLUSH_INTERVAL)
        flush_db_buffer(db_conn)

def get_db_connection(db_path, limit_buffer='200M', source_module=None):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or '.', exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL;')
    
    # Thiết lập cache_size và mmap_size cho SQLite để tránh Android Termux kill process do tốn RAM
    if limit_buffer and limit_buffer != '0':
        limit_str = str(limit_buffer).strip().upper()
        kb = 2000
        try:
            if limit_str.endswith('G'): kb = int(float(limit_str[:-1]) * 1024 * 1024)
            elif limit_str.endswith('M'): kb = int(float(limit_str[:-1]) * 1024)
            elif limit_str.endswith('K'): kb = int(float(limit_str[:-1]))
            elif limit_str.isdigit(): kb = int(limit_str) // 1024
            
            if kb > 0:
                conn.execute(f'PRAGMA cache_size=-{kb};')
                conn.execute(f'PRAGMA mmap_size={kb * 1024};')
        except Exception as e:
            custom_log("System", f"⚠️ Lỗi khi set limit buffer: {e}")
            
    if source_module and hasattr(source_module, 'setup_db'):
        source_module.setup_db(conn, VIDEOS_TABLE)
    else:
        # Fallback sử dụng bảng mặc định nếu plugin không tự định nghĩa
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS {VIDEOS_TABLE} (
                id TEXT PRIMARY KEY,
                title TEXT,
                cover TEXT,
                url TEXT,
                added_at TEXT,
                release_date TEXT,
                actress TEXT,
                genre TEXT,
                maker TEXT,
                details TEXT,
                dvd TEXT,
                details_fetched INTEGER DEFAULT 0
            )
        ''')
        try:
            conn.execute(f"ALTER TABLE {VIDEOS_TABLE} ADD COLUMN dvd TEXT")
        except sqlite3.OperationalError:
            pass
            
        cursor = conn.cursor()
        try:
            cursor.execute(f"UPDATE {VIDEOS_TABLE} SET dvd = substr(title, 1, instr(title || ' ', ' ') - 1) WHERE dvd IS NULL OR dvd = ''")
        except Exception as e:
            custom_log("System", f"⚠️ Bỏ qua update dvd do lỗi DB: {e}")
        
        try:
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{VIDEOS_TABLE}_details_fetched ON {VIDEOS_TABLE}(details_fetched, added_at ASC)')
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{VIDEOS_TABLE}_search_actress ON {VIDEOS_TABLE}(actress)')
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{VIDEOS_TABLE}_search_genre ON {VIDEOS_TABLE}(genre)')
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{VIDEOS_TABLE}_search_maker ON {VIDEOS_TABLE}(maker)')
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{VIDEOS_TABLE}_search_details ON {VIDEOS_TABLE}(details)')
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{VIDEOS_TABLE}_search_title ON {VIDEOS_TABLE}(title)')
        except Exception as e:
            custom_log("System", f"⚠️ Bỏ qua tạo index do lỗi DB: {e}")

        try:
            cursor.execute(f"PRAGMA table_info({VIDEOS_TABLE}_fts)")
            fts_cols = [row[1] for row in cursor.fetchall()]
            if 'dvd' not in fts_cols:
                cursor.execute(f"DROP TABLE IF EXISTS {VIDEOS_TABLE}_fts")
                cursor.execute(f"DROP TRIGGER IF EXISTS {VIDEOS_TABLE}_ai")
                cursor.execute(f"DROP TRIGGER IF EXISTS {VIDEOS_TABLE}_ad")
                cursor.execute(f"DROP TRIGGER IF EXISTS {VIDEOS_TABLE}_au")

            conn.execute(f'''
                CREATE VIRTUAL TABLE IF NOT EXISTS {VIDEOS_TABLE}_fts USING fts5(
                    title, actress, genre, maker, details, dvd,
                    content='{VIDEOS_TABLE}', content_rowid='rowid'
                )
            ''')
            for trigger_sql in [
                f"CREATE TRIGGER IF NOT EXISTS {VIDEOS_TABLE}_ai AFTER INSERT ON {VIDEOS_TABLE} BEGIN INSERT INTO {VIDEOS_TABLE}_fts(rowid, title, actress, genre, maker, details, dvd) VALUES (new.rowid, new.title, new.actress, new.genre, new.maker, new.details, new.dvd); END;",
                f"CREATE TRIGGER IF NOT EXISTS {VIDEOS_TABLE}_ad AFTER DELETE ON {VIDEOS_TABLE} BEGIN INSERT INTO {VIDEOS_TABLE}_fts({VIDEOS_TABLE}_fts, rowid, title, actress, genre, maker, details, dvd) VALUES ('delete', old.rowid, old.title, old.actress, old.genre, old.maker, old.details, old.dvd); END;",
                f"CREATE TRIGGER IF NOT EXISTS {VIDEOS_TABLE}_au AFTER UPDATE ON {VIDEOS_TABLE} BEGIN INSERT INTO {VIDEOS_TABLE}_fts({VIDEOS_TABLE}_fts, rowid, title, actress, genre, maker, details, dvd) VALUES ('delete', old.rowid, old.title, old.actress, old.genre, old.maker, old.details, old.dvd); INSERT INTO {VIDEOS_TABLE}_fts(rowid, title, actress, genre, maker, details, dvd) VALUES (new.rowid, new.title, new.actress, new.genre, new.maker, new.details, new.dvd); END;"
            ]:
                conn.execute(trigger_sql)
                
            cursor.execute(f"SELECT COUNT(*) FROM {VIDEOS_TABLE}_fts")
            if cursor.fetchone()[0] == 0:
                custom_log("System", "⏳ Backfilling FTS index...")
                cursor.execute(f'''
                    INSERT INTO {VIDEOS_TABLE}_fts(rowid, title, actress, genre, maker, details, dvd)
                    SELECT rowid, title, actress, genre, maker, details, dvd FROM {VIDEOS_TABLE}
                ''')
        except Exception as e:
            custom_log("System", f"⚠️ Bỏ qua FTS setup do lỗi DB: {e}")

    conn.execute('''
        CREATE TABLE IF NOT EXISTS configs (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS media (
            id TEXT PRIMARY KEY,
            data BLOB,
            content_type TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS identities (
            username TEXT PRIMARY KEY,
            email TEXT UNIQUE,
            password TEXT,
            session_id TEXT,
            otp TEXT,
            otp_expire INTEGER,
            created_at INTEGER,
            verified INTEGER DEFAULT 0
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            username TEXT,
            session_id TEXT PRIMARY KEY
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            username TEXT,
            video_id TEXT,
            added_at TEXT,
            PRIMARY KEY (username, video_id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS history (
            username TEXT,
            video_id TEXT,
            watch_count INTEGER DEFAULT 1,
            last_watched INTEGER,
            PRIMARY KEY (username, video_id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sync_tasks (
            url_pattern TEXT PRIMARY KEY,
            current_page INTEGER DEFAULT 1,
            total_pages INTEGER DEFAULT 2000,
            last_fetched INTEGER DEFAULT 0,
            is_completed INTEGER DEFAULT 0
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS history_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            watched_at INTEGER
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_history_logs_time ON history_logs(watched_at)')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            username TEXT,
            keyword TEXT,
            searched_at INTEGER,
            PRIMARY KEY (username, keyword)
        )
    ''')
    conn.commit()
    return conn

NEXTDJAV_DB_PATH = None

def get_nextdjav_conn():
    """Lấy kết nối tới nextdjav.db để kiểm tra video trên GDrive và quản lý queue tải lên."""
    global NEXTDJAV_DB_PATH
    if not NEXTDJAV_DB_PATH:
        candidates = [
            getattr(app_args, 'nextdjav_db', None) if app_args else None,
        ]
        for c in candidates:
            if c and os.path.exists(c):
                NEXTDJAV_DB_PATH = c
                break
        if not NEXTDJAV_DB_PATH:
            NEXTDJAV_DB_PATH = os.environ.get("WINDOWS_DB") if os.name == "nt" else os.environ.get("TERMUX_DB")

    try:
        os.makedirs(os.path.dirname(os.path.abspath(NEXTDJAV_DB_PATH)) or '.', exist_ok=True)
        c = sqlite3.connect(NEXTDJAV_DB_PATH, check_same_thread=False, timeout=5.0)
        c.execute('PRAGMA journal_mode=WAL;')
        c.execute('PRAGMA busy_timeout=5000;')
        # Tự động đảm bảo bảng media_upload_queue tồn tại
        c.execute('''
            CREATE TABLE IF NOT EXISTS media_upload_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id TEXT UNIQUE NOT NULL,
                thread_id TEXT,
                source TEXT NOT NULL,
                title TEXT,
                file_name TEXT NOT NULL,
                original_url TEXT NOT NULL,
                poster_url TEXT,
                target_folder TEXT DEFAULT '/NextDJAV/Videos',
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                claimed_by TEXT,
                claimed_at DATETIME,
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_media_upload_queue_status ON media_upload_queue(status, created_at)')
        c.commit()
        return c
    except Exception as e:
        custom_log("System", f"⚠️ Không thể kết nối nextdjav.db ({NEXTDJAV_DB_PATH}): {e}")
        return None

def get_nextdjav_status_maps(codes):
    """Truy vấn nhanh tập hợp codes để xác định hasGDrive và inDownloadQueue."""
    has_gdrive_set = set()
    in_queue_set = set()
    if not codes:
        return has_gdrive_set, in_queue_set

    clean_codes = [c.strip().upper() for c in codes if c and c.strip()]
    if not clean_codes:
        return has_gdrive_set, in_queue_set

    conn = get_nextdjav_conn()
    if not conn:
        return has_gdrive_set, in_queue_set

    try:
        placeholders = ','.join('?' for _ in clean_codes)
        cur = conn.cursor()
        
        # 1. Kiểm tra video đã có trên GDrive
        try:
            cur.execute(f"SELECT upper(code) FROM drive_videos WHERE upper(code) IN ({placeholders})", clean_codes)
            for r in cur.fetchall():
                if r[0]: has_gdrive_set.add(r[0].strip().upper())
        except Exception:
            pass

        # 2. Kiểm tra video đang trong hàng đợi tải (pending / claimed / uploading)
        try:
            cur.execute(f"SELECT upper(media_id) FROM media_upload_queue WHERE status IN ('pending', 'claimed', 'uploading') AND upper(media_id) IN ({placeholders})", clean_codes)
            for r in cur.fetchall():
                if r[0]: in_queue_set.add(r[0].strip().upper())
        except Exception:
            pass

        conn.close()
    except Exception as e:
        custom_log("System", f"⚠️ Lỗi tra cứu status nextdjav: {e}")

    return has_gdrive_set, in_queue_set

def remove_accents(input_str):
    s1 = unicodedata.normalize('NFD', input_str)
    return re.sub(r'[\u0300-\u036f]', '', s1).lower()

tags_cache = []
def load_tags_cache_if_needed(cursor):
    global tags_cache
    if not tags_cache:
        try:
            cursor.execute("SELECT keyword, type, count FROM tags_summary")
            for r in cursor.fetchall():
                kw = r[0]
                if not kw: continue
                low = kw.lower()
                low_no_accents = remove_accents(low)
                sorted_low = " ".join(sorted(low_no_accents.split()))
                tags_cache.append((kw, r[1], r[2], low, low_no_accents, sorted_low))
        except Exception as e:
            custom_log("System", f"❌ Load tags cache error: {e}")

def rebuild_tags_fts(db_conn):
    try:
        custom_log("System", "⏳ Đang tổng hợp dữ liệu actress, genre, maker...")
        cursor = db_conn.cursor()
        cursor.execute(f"SELECT title, actress, genre, maker FROM {VIDEOS_TABLE}")
        rows = cursor.fetchall()
        
        actress_counts = {}
        genre_counts = {}
        maker_counts = {}
        
        for row in rows:
            title = row[0]
            actress = row[1]
            genre = row[2]
            maker = row[3]
            
            if actress:
                for a in actress.split(','):
                    a = a.strip()
                    if a: actress_counts[a] = actress_counts.get(a, 0) + 1
            if genre:
                for g in genre.split(','):
                    g = g.strip()
                    if g: genre_counts[g] = genre_counts.get(g, 0) + 1
            if maker:
                for m in maker.split(','):
                    m = m.strip()
                    if m: maker_counts[m] = maker_counts.get(m, 0) + 1
                    
        cursor.execute("DROP TABLE IF EXISTS tags_summary")
        cursor.execute("DROP TABLE IF EXISTS tags_fts")
        
        cursor.execute("CREATE TABLE tags_summary (keyword TEXT, type TEXT, count INTEGER)")
        cursor.execute("CREATE VIRTUAL TABLE tags_fts USING fts5(keyword, type UNINDEXED, count UNINDEXED, content='tags_summary', content_rowid='rowid')")
        
        data = []
        for k, c in actress_counts.items(): data.append((k, 'actress', c))
        for k, c in genre_counts.items(): data.append((k, 'genre', c))
        for k, c in maker_counts.items(): data.append((k, 'maker', c))
        
        cursor.executemany("INSERT INTO tags_summary (keyword, type, count) VALUES (?, ?, ?)", data)
        cursor.execute("INSERT INTO tags_fts(tags_fts) VALUES('rebuild')")
        db_conn.commit()
        global tags_cache
        tags_cache = []
        custom_log("System", "✔️ Hoàn tất tổng hợp tags.")
    except Exception as e:
        custom_log("System", f"⚠️ Bỏ qua rebuild_tags_fts do SQLite không hỗ trợ FTS5 hoặc lỗi: {e}")

class BackgroundScanner(threading.Thread):
    def __init__(self, scraper, upgrade_all=False, news_threads=0, detail_threads=0, videos_threads=0):
        super().__init__(daemon=True)
        self.scraper = scraper
        self.upgrade_all = upgrade_all
        self.news_threads = news_threads
        self.detail_threads = detail_threads
        self.videos_threads = videos_threads
        self.news_queue = queue.Queue()

    def run(self):
        self.scraper.update_sync_tasks_from_menu()
        
        if self.upgrade_all:
            domain_base = self.scraper.domain.split('.')[0].lower()
            source_name = getattr(self.scraper, 'source_name', '').lower()
            with db_lock:
                cursor = self.scraper.db_conn.cursor()
                cursor.execute("UPDATE sync_tasks SET current_page = 1, is_completed = 0")
                cursor.execute("UPDATE sync_tasks SET current_page = 1, is_completed = 0 WHERE LOWER(url_pattern) LIKE ? OR LOWER(url_pattern) LIKE ?", (f"%{domain_base}%", f"%{source_name}%"))
                self.scraper.db_conn.commit()
                
        if self.news_threads > 0:
            threading.Thread(target=self.news_dispatcher_loop, daemon=True).start()
        
        for i in range(self.news_threads):
            threading.Thread(target=self.news_scan_worker, args=(i+1,), daemon=True).start()
            
        for i in range(self.detail_threads):
            threading.Thread(target=self.details_scan_worker, args=(i+1,), daemon=True).start()
            
        for i in range(self.videos_threads):
            threading.Thread(target=self.backlog_scan_worker, args=(i+1,), daemon=True).start()
            
        while True:
            time.sleep(3600)

    def news_dispatcher_loop(self):
        while True:
            try:
                domain_base = self.scraper.domain.split('.')[0].lower()
                source_name = getattr(self.scraper, 'source_name', '').lower()
                with db_lock:
                    cursor = self.scraper.db_conn.cursor()
                    cursor.execute("SELECT url_pattern FROM sync_tasks ORDER BY CASE WHEN url_pattern LIKE '%chinese-av%' THEN 0 ELSE 1 END")
                    cursor.execute("SELECT url_pattern FROM sync_tasks WHERE LOWER(url_pattern) LIKE ? OR LOWER(url_pattern) LIKE ? ORDER BY CASE WHEN url_pattern LIKE '%chinese-av%' THEN 0 ELSE 1 END", (f"%{domain_base}%", f"%{source_name}%"))
                    tasks = cursor.fetchall()
                
                if self.news_queue.empty():
                    for task in tasks:
                        self.news_queue.put(task[0])
            except Exception as e:
                custom_log("System", f"❌ Lỗi dispatcher video mới: {e}")
                
            time.sleep(3600)

    def news_scan_worker(self, thread_num):
        global global_last_request_time
        source_name = getattr(self.scraper, 'source_name', 'System')
        while True:
            try:
                url_pattern = self.news_queue.get(timeout=5)
            except queue.Empty:
                continue
                
            page = 1
            while True:
                if time.time() - global_last_request_time < CLIENT_IDLE_TIMEOUT:
                    time.sleep(2)
                    continue
                    
                try:
                    custom_log(source_name, f"⏳[News Thread {thread_num}] {url_pattern} page {page}...")
                    new_inserted, found, _ = self.scraper.sync_list_page(url_pattern, page)
                    if found == -1:
                        time.sleep(5)
                        break
                    custom_log(source_name, f"✔️[News Thread {thread_num}] {url_pattern} page {page} - {new_inserted} new, {found} found")
                    if new_inserted > 0 and found > 0:
                        page += 1
                        time.sleep(1)
                    else:
                        custom_log(source_name, f"⏳[News Thread {thread_num}] Done Đang chuyển giao cho tác vụ khác...")
                        break
                except Exception as e:
                    custom_log("System", f"❌ Lỗi kiểm tra video mới: {e}")
                    break
            self.news_queue.task_done()
            
    def details_scan_worker(self, thread_num):
        global global_last_request_time
        source_name = getattr(self.scraper, 'source_name', 'System')
        while True:
            time.sleep(0.5)
            try:
                if time.time() - global_last_request_time < CLIENT_IDLE_TIMEOUT:
                    continue
                    
                with db_lock:
                    cursor = self.scraper.db_conn.cursor()
                    cursor.execute(f"SELECT id FROM {VIDEOS_TABLE} WHERE details_fetched = 0 ORDER BY added_at ASC LIMIT 1")
                    row = cursor.fetchone()
                    if row:
                        vid_id = row[0]
                        cursor.execute(f"UPDATE {VIDEOS_TABLE} SET details_fetched = -2 WHERE id = ?", (vid_id,))
                        self.scraper.db_conn.commit()
                    
                if not row:
                    time.sleep(300)
                    continue
                    
                custom_log(source_name, f"⏳[Detail Thread {thread_num}] {vid_id}")
                success = self.scraper.sync_video_details(vid_id)
                custom_log(source_name, f"✔️[Detail Thread {thread_num}] {vid_id} - {'Success' if success else 'Failed'}")
            except Exception as e:
                custom_log("System", f"❌ Lỗi quét chi tiết: {e}")
                time.sleep(5)

    def backlog_scan_worker(self, thread_num):
        global global_last_request_time
        source_name = getattr(self.scraper, 'source_name', 'System')
        domain_base = self.scraper.domain.split('.')[0].lower()
        src_lower = source_name.lower()
        while True:
            time.sleep(1)
            try:
                if time.time() - global_last_request_time < CLIENT_IDLE_TIMEOUT:
                    continue
                    
                task_to_run = None
                with db_lock:
                    cursor = self.scraper.db_conn.cursor()
                    cursor.execute("SELECT url_pattern, current_page, total_pages FROM sync_tasks WHERE is_completed = 0 ORDER BY CASE WHEN url_pattern LIKE '%chinese-av%' THEN 0 ELSE 1 END LIMIT 1")
                    cursor.execute("SELECT url_pattern, current_page, total_pages FROM sync_tasks WHERE is_completed = 0 AND (LOWER(url_pattern) LIKE ? OR LOWER(url_pattern) LIKE ?) ORDER BY CASE WHEN url_pattern LIKE '%chinese-av%' THEN 0 ELSE 1 END LIMIT 1", (f"%{domain_base}%", f"%{src_lower}%"))
                    row = cursor.fetchone()
                    if row:
                        url_pattern, current_page, total_pages = row
                        if current_page > total_pages or current_page > 2000:
                            cursor.execute("UPDATE sync_tasks SET is_completed = 1 WHERE url_pattern = ?", (url_pattern,))
                            self.scraper.db_conn.commit()
                        else:
                            cursor.execute("UPDATE sync_tasks SET current_page = current_page + 1 WHERE url_pattern = ?", (url_pattern,))
                            self.scraper.db_conn.commit()
                            task_to_run = (url_pattern, current_page, total_pages)
                    
                if not task_to_run:
                    time.sleep(60)
                    continue
                    
                url_pattern, current_page, total_pages = task_to_run
                
                custom_log(source_name, f"⏳[Videos Thread {thread_num}] {url_pattern} page {current_page}/{total_pages}")
                new_inserted, found, extracted_total = self.scraper.sync_list_page(url_pattern, current_page)
                
                if found == -1:
                    with db_lock:
                        cursor = self.scraper.db_conn.cursor()
                        cursor.execute("UPDATE sync_tasks SET current_page = current_page - 1 WHERE url_pattern = ? AND current_page > 1", (url_pattern,))
                        self.scraper.db_conn.commit()
                    time.sleep(5)
                    continue
                    
                custom_log(source_name, f"✔️[Videos Thread {thread_num}] {url_pattern} page {current_page}/{total_pages} - {new_inserted} new, {found} found")

                with db_lock:
                    cursor = self.scraper.db_conn.cursor()
                    new_total = extracted_total if extracted_total > 0 else total_pages
                    if found == 0 or current_page >= new_total or current_page >= 2000:
                        cursor.execute("UPDATE sync_tasks SET is_completed = 1, total_pages = ?, last_fetched = ? WHERE url_pattern = ?", 
                                       (new_total, int(time.time()), url_pattern))
                    else:
                        cursor.execute("UPDATE sync_tasks SET total_pages = ?, last_fetched = ? WHERE url_pattern = ?", 
                                       (new_total, int(time.time()), url_pattern))
                    self.scraper.db_conn.commit()
                
            except Exception as e:
                custom_log("System", f"❌ Lỗi backlog scanner: {e}")
                time.sleep(5)

_base_dir = os.path.dirname(os.path.abspath(__file__))
_frontend_dir = os.path.abspath(os.path.join(_base_dir, '..', 'frontend'))
_static_dir = os.path.join(_frontend_dir, 'static')

app = Flask(__name__, static_folder=_static_dir, static_url_path='/static')
app.config['JSON_AS_ASCII'] = False
import logging

import socket

import logging
from werkzeug.serving import WSGIRequestHandler
import datetime
def custom_log_request(self, code='-', size='-'):
    if logging.getLogger('werkzeug').disabled:
        return
    now = datetime.datetime.now()
    timestamp = now.strftime('%y%m%d-%H%M%S.') + f"{now.microsecond // 1000:03d}"
    port = getattr(self.server, 'server_port', 'PORT')
    print(f"[{timestamp}] [Port:{port}] {self.address_string()} - {self.requestline} {code}")
WSGIRequestHandler.log_request = custom_log_request

def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
LAN_IP = get_lan_ip()

log = logging.getLogger('werkzeug')
log.disabled = True

scraper_instance = None
db_conn_instance = None
app_args = None

@app.before_request
def handle_options():
    global global_last_request_time
    global_last_request_time = time.time()
    if request.method == 'OPTIONS':
        resp = Response(status=204)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = request.headers.get('Access-Control-Request-Headers', '*')
        return resp

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    ip = request.remote_addr
    method = request.method
    code = response.status_code
    url = request.full_path.rstrip('?')
    custom_log("API", f"{ip} {method} {code} {url}")
    return response

def get_identifier():
    auth_header = request.headers.get('Authorization', '')
    username = None
    if auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        jwt_payload = verify_jwt(token)
        if jwt_payload and 'username' in jwt_payload:
            username = jwt_payload['username']
            
    session_id = request.headers.get('Session-Id', '') or request.args.get('session_id', '')
    if not username and session_id:
        with db_lock:
            cursor = db_conn_instance.cursor()
            cursor.execute("SELECT username FROM user_sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                username = row[0]
    return username if username else session_id

@app.route('/api/identity/me', methods=['GET'])
def identity_me():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    token = auth_header.split(' ')[1]
    jwt_payload = verify_jwt(token)
    if not jwt_payload or 'username' not in jwt_payload:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT username, email, verified FROM identities WHERE username = ?", (jwt_payload['username'],))
        row = cursor.fetchone()
    if row:
        return jsonify({"success": True, "username": row[0], "email": row[1], "verified": bool(row[2])})
    return jsonify({"success": False, "error": "User not found"})

@app.route('/api/counts', methods=['GET'])
def get_counts():
    search_key = request.args.get('search_key', '').strip()
    identifier = get_identifier()
    related_vid_id = request.args.get('video_id', '').strip()
    
    try:
        with db_lock:
            with sqlite_timeout(db_conn_instance, 2.0):
                cursor = db_conn_instance.cursor()
                search_where_v = ""
                search_params = []
                from_main = f"{VIDEOS_TABLE} v"
                
                if search_key:
                    match_field = re.match(r'^(actress|genre|maker|title|dvd)\s*:\s*(.*)$', search_key, re.IGNORECASE)
                    if match_field:
                        field = match_field.group(1).lower()
                        val = match_field.group(2).strip()
                        raw_parts = [p.strip() for p in val.split(',') if p.strip()]
                        field_terms = []
                        for part in raw_parts:
                            safe_val = ' '.join([f'"{w}"*' for w in part.replace('"', '').split()])
                            if safe_val:
                                field_terms.append(f"{field} : ({safe_val})")
                        if field_terms:
                            from_main = f"{VIDEOS_TABLE}_fts JOIN {VIDEOS_TABLE} v ON v.rowid = {VIDEOS_TABLE}_fts.rowid"
                            search_where_v = f"{VIDEOS_TABLE}_fts MATCH ?"
                            search_params.append(' OR '.join(field_terms))
                    else:
                        raw_parts = [p.strip() for p in search_key.split(',') if p.strip()]
                        key_terms = []
                        for part in raw_parts:
                            safe_k = ' '.join([f'"{w}"*' for w in part.replace('"', '').split()])
                            if safe_k:
                                key_terms.append(f"({safe_k})")
                        if key_terms:
                            from_main = f"{VIDEOS_TABLE}_fts JOIN {VIDEOS_TABLE} v ON v.rowid = {VIDEOS_TABLE}_fts.rowid"
                            search_where_v = f"{VIDEOS_TABLE}_fts MATCH ?"
                            search_params.append(' OR '.join(key_terms))
        
                where_all = ("WHERE " + search_where_v) if search_where_v else ""
                cursor.execute(f"SELECT COUNT(*) FROM {from_main} {where_all}", search_params)
                count_all = cursor.fetchone()[0]
                
                count_fav = 0
                count_recent = 0
                count_unwatched = 0
                if identifier:
                    where_fav = "WHERE f.username = ?" + (f" AND {search_where_v}" if search_where_v else "")
                    cursor.execute(f"SELECT COUNT(*) FROM {from_main} JOIN favorites f ON f.video_id = v.id {where_fav}", [identifier] + search_params)
                    count_fav = cursor.fetchone()[0]
                    
                    where_hist = "WHERE h.username = ?" + (f" AND {search_where_v}" if search_where_v else "")
                    cursor.execute(f"SELECT COUNT(*) FROM {from_main} JOIN history h ON h.video_id = v.id {where_hist}", [identifier] + search_params)
                    count_recent = cursor.fetchone()[0]

                    where_unwatched = "WHERE h.video_id IS NULL" + (f" AND {search_where_v}" if search_where_v else "")
                    cursor.execute(f"SELECT COUNT(*) FROM {from_main} LEFT JOIN history h ON h.video_id = v.id AND h.username = ? {where_unwatched}", [identifier] + search_params)
                    count_unwatched = cursor.fetchone()[0]
                else:
                    count_unwatched = count_all
                
                where_glob = ("WHERE " + search_where_v) if search_where_v else ""
                cursor.execute(f"SELECT COUNT(DISTINCT h.video_id) FROM {from_main} JOIN history h ON h.video_id = v.id {where_glob}", search_params)
                count_global = cursor.fetchone()[0]
                
                count_related = 0
                if related_vid_id:
                    cursor.execute(f"SELECT title, actress, genre, maker FROM {VIDEOS_TABLE} WHERE id = ?", (related_vid_id,))
                    related_row = cursor.fetchone()
                    if related_row:
                        r_title, r_actress, r_genre, r_maker = related_row
                        r_keywords = extract_clean_keywords_viet_eng(r_title) if r_title else []
                        r_query_parts = []
                        if r_actress:
                            r_actresses = [a.strip() for a in r_actress.split(',')]
                            r_query_parts.append(' OR '.join([f'actress : "{a}"' for a in r_actresses if a]))
                        if r_genre:
                            r_genres = [g.strip() for g in r_genre.split(',')]
                            r_query_parts.append(' OR '.join([f'genre : "{g}"' for g in r_genres if g]))
                        if r_maker:
                            r_query_parts.append(f'maker : "{r_maker}"')
                        if r_keywords:
                            r_kw_str = ' OR '.join([f'"{k}"*' for k in r_keywords[:5]])
                            r_query_parts.append(f'title : ({r_kw_str})')
                        
                        r_fts_query = ' OR '.join([p for p in r_query_parts if p])
                        if r_fts_query:
                            cursor.execute(f"SELECT COUNT(*) FROM {VIDEOS_TABLE}_fts JOIN {VIDEOS_TABLE} v ON v.rowid = {VIDEOS_TABLE}_fts.rowid WHERE {VIDEOS_TABLE}_fts MATCH ? AND v.id != ?", (r_fts_query, related_vid_id))
                            count_related = cursor.fetchone()[0]
        
        return jsonify({
            "all": count_all,
            "favorites": count_fav,
            "recent": count_recent,
            "frequent": count_recent,
            "global_frequent": count_global,
            "unwatched": count_unwatched,
            "related": count_related,
            "trending_day": 0,
            "trending_month": 0
        })
    except sqlite3.OperationalError as e:
        if 'interrupted' in str(e).lower():
            custom_log("API", "⚠️ Query timeout trong /api/counts (>2s). Bỏ qua.")
            return jsonify({"all": 0, "favorites": 0, "recent": 0, "frequent": 0, "global_frequent": 0, "unwatched": 0, "related": 0, "trending_day": 0, "trending_month": 0})
        raise


@app.route('/api/videos', methods=['GET'])
def get_videos():
    # Nhận diện tham số tương thích cả search_key lẫn q/search từ OnePlayer
    search_key = (request.args.get('search_key', '') or request.args.get('q', '') or request.args.get('search', '')).strip()
    
    actress_param = (request.args.get('actress', '') or request.args.get('actresses', '')).strip()
    genre_param = (request.args.get('genre', '') or request.args.get('genres', '')).strip()
    studio_param = (request.args.get('studio', '') or request.args.get('studios', '') or request.args.get('maker', '')).strip()

    tab = request.args.get('tab', 'all')
    per_page = int(request.args.get('limit', 24) or 24)
    if 'offset' in request.args and request.args.get('offset'):
        offset = int(request.args.get('offset', 0))
        page = (offset // max(1, per_page)) + 1
    else:
        page = int(request.args.get('page', 1))
        offset = (page - 1) * per_page
        
    identifier = get_identifier()

    try:
        with db_lock:
            with sqlite_timeout(db_conn_instance, 2.0):
                cursor = db_conn_instance.cursor()
                where_clauses = []
                params = []
                from_clause = f"{VIDEOS_TABLE} v"

                # Đảm bảo CHỈ lấy những video đã cào hoàn tất
                dummy_uuid = "bc21a4fe-5e9b-4936-a844-b3e5f04c4cdc"
                # Kiểm tra xem bảng hiện tại có cột surrit_id không (chỉ missav mới có)
                cursor.execute(f"PRAGMA table_info({VIDEOS_TABLE})")
                col_names = [col[1] for col in cursor.fetchall()]
                
                cond_parts = [
                    "(v.details_fetched = 1 OR v.cover_fetched = 1)",
                    f"(v.url IS NOT NULL AND v.url != '' AND v.url NOT LIKE '%{dummy_uuid}%' AND v.url != 'failed')"
                ]
                if "surrit_id" in col_names:
                    cond_parts.append("(v.surrit_id IS NOT NULL AND v.surrit_id != '' AND v.surrit_id NOT IN ('404', 'failed', 'no_surrit'))")
                
                where_clauses.append(f"({' OR '.join(cond_parts)})")
                
                if tab == 'favorites':
                    if not identifier:
                        return jsonify({"items": [], "total": 0, "page": page})
                    from_clause = f"{VIDEOS_TABLE} v JOIN favorites f ON v.id = f.video_id"
                    where_clauses.append("f.username = ?")
                    params.append(identifier)
                elif tab in ['recent', 'frequent']:
                    if not identifier:
                        return jsonify({"items": [], "total": 0, "page": page})
                    from_clause = f"{VIDEOS_TABLE} v JOIN history h ON v.id = h.video_id"
                    where_clauses.append("h.username = ?")
                    params.append(identifier)
                elif tab == 'global_frequent':
                    from_clause = f"{VIDEOS_TABLE} v JOIN (SELECT video_id, SUM(watch_count) as total_watches FROM history GROUP BY video_id) h ON v.id = h.video_id"
                elif tab == 'unwatched':
                    if identifier:
                        from_clause = f"{VIDEOS_TABLE} v LEFT JOIN history h ON v.id = h.video_id AND h.username = ?"
                        where_clauses.append("h.video_id IS NULL")
                        params.append(identifier)
                    else:
                        from_clause = f"{VIDEOS_TABLE} v"
                elif tab == 'trending_day':
                    day_ago = int(time.time()) - 86400
                    from_clause = f"{VIDEOS_TABLE} v JOIN (SELECT video_id, COUNT(*) as c FROM history_logs WHERE watched_at > ? GROUP BY video_id) h ON v.id = h.video_id"
                    params.append(day_ago)
                elif tab == 'trending_month':
                    month_ago = int(time.time()) - 30*86400
                    from_clause = f"{VIDEOS_TABLE} v JOIN (SELECT video_id, COUNT(*) as c FROM history_logs WHERE watched_at > ? GROUP BY video_id) h ON v.id = h.video_id"
                    params.append(month_ago)
        
                fts_terms = []
                
                if tab == 'related':
                    related_vid_id = request.args.get('video_id', '').strip()
                    if not related_vid_id and identifier:
                        cursor.execute("SELECT video_id FROM history WHERE username = ? ORDER BY last_watched DESC LIMIT 1", (identifier,))
                        row = cursor.fetchone()
                        if row:
                            related_vid_id = row[0]
                    
                    if not related_vid_id:
                        return jsonify({"items": [], "total": 0, "page": page})
                        
                    cursor.execute(f"SELECT title, actress, genre, maker FROM {VIDEOS_TABLE} WHERE id = ?", (related_vid_id,))
                    row = cursor.fetchone()
                    if not row:
                        return jsonify({"items": [], "total": 0, "page": page})
                        
                    title, actress, genre, maker = row
                    
                    keywords = extract_clean_keywords_viet_eng(title) if title else []
                    query_parts = []
                    if actress:
                        actresses = [a.strip() for a in actress.split(',')]
                        query_parts.append(' OR '.join([f'actress : "{a}"' for a in actresses if a]))
                    if genre:
                        genres = [g.strip() for g in genre.split(',')]
                        query_parts.append(' OR '.join([f'genre : "{g}"' for g in genres if g]))
                    if maker:
                        query_parts.append(f'maker : "{maker}"')
                    if keywords:
                        kw_str = ' OR '.join([f'"{k}"*' for k in keywords[:5]])
                        query_parts.append(f'title : ({kw_str})')
                        
                    fts_query = ' OR '.join([p for p in query_parts if p])
                    if not fts_query:
                        return jsonify({"items": [], "total": 0, "page": page})
                    
                    fts_terms.append(f"({fts_query})")
                    where_clauses.append("v.id != ?")
                    params.append(related_vid_id)
        
                safe_key = ""
                # Xử lý các bộ lọc thực thể độc lập (actress, genre, maker/studio)
                if actress_param:
                    act_parts = [p.strip() for p in actress_param.split(',') if p.strip()]
                    act_terms = []
                    for part in act_parts:
                        safe_val = ' '.join([f'"{w}"*' for w in part.replace('"', '').split()])
                        if safe_val:
                            act_terms.append(f'actress : ({safe_val})')
                    if act_terms:
                        fts_terms.append(f"({' OR '.join(act_terms)})")
                        safe_key = "actress"

                if genre_param:
                    gen_parts = [p.strip() for p in genre_param.split(',') if p.strip()]
                    gen_terms = []
                    for part in gen_parts:
                        safe_val = ' '.join([f'"{w}"*' for w in part.replace('"', '').split()])
                        if safe_val:
                            gen_terms.append(f'genre : ({safe_val})')
                    if gen_terms:
                        fts_terms.append(f"({' OR '.join(gen_terms)})")
                        safe_key = "genre"

                if studio_param:
                    st_parts = [p.strip() for p in studio_param.split(',') if p.strip()]
                    st_terms = []
                    for part in st_parts:
                        safe_val = ' '.join([f'"{w}"*' for w in part.replace('"', '').split()])
                        if safe_val:
                            st_terms.append(f'maker : ({safe_val})')
                    if st_terms:
                        fts_terms.append(f"({' OR '.join(st_terms)})")
                        safe_key = "maker"

                # Xử lý từ khóa tìm kiếm tự do (search_key hoặc q)
                if search_key:
                    match_field = re.match(r'^(actress|genre|maker|studio|title|dvd)\s*:\s*(.*)$', search_key, re.IGNORECASE)
                    if match_field:
                        field = match_field.group(1).lower()
                        if field == 'studio':
                            field = 'maker'
                        val = match_field.group(2).strip()
                        raw_parts = [p.strip() for p in val.split(',') if p.strip()]
                        field_terms = []
                        for part in raw_parts:
                            safe_val = ' '.join([f'"{w}"*' for w in part.replace('"', '').split()])
                            if safe_val:
                                field_terms.append(f"{field} : ({safe_val})")
                        if field_terms:
                            fts_or_clause = ' OR '.join(field_terms)
                            fts_terms.append(f"({fts_or_clause})")
                            safe_key = fts_or_clause
                    else:
                        # Từ khóa chung: tìm kiếm theo cụm hoặc theo từ
                        raw_parts = [p.strip() for p in search_key.split(',') if p.strip()]
                        key_terms = []
                        for part in raw_parts:
                            words = [w for w in part.replace('"', '').split() if w]
                            if words:
                                safe_k = ' '.join([f'"{w}"*' for w in words])
                                key_terms.append(f"({safe_k})")
                        if key_terms:
                            fts_or_clause = ' OR '.join(key_terms)
                            fts_terms.append(f"({fts_or_clause})")
                            safe_key = fts_or_clause
                            
                if fts_terms:
                    from_clause += f" JOIN {VIDEOS_TABLE}_fts ON v.rowid = {VIDEOS_TABLE}_fts.rowid"
                    where_clauses.append(f"{VIDEOS_TABLE}_fts MATCH ?")
                    params.append(" AND ".join(fts_terms))
                        
                where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
                
                if tab == 'global_frequent' and not search_key:
                    cursor.execute("SELECT COUNT(DISTINCT video_id) FROM history")
                    total = cursor.fetchone()[0]
                else:
                    cursor.execute(f"SELECT COUNT(*) FROM {from_clause} {where_sql}", params)
                    total = cursor.fetchone()[0]
                    
                if tab == 'favorites':
                    order_clause = "ORDER BY v.release_date DESC, f.added_at DESC"
                elif tab == 'recent':
                    order_clause = "ORDER BY h.last_watched DESC, v.release_date DESC"
                elif tab == 'frequent':
                    order_clause = "ORDER BY h.watch_count DESC, v.release_date DESC"
                elif tab == 'global_frequent':
                    order_clause = "ORDER BY h.total_watches DESC, v.release_date DESC"
                elif tab in ['trending_day', 'trending_month']:
                    order_clause = "ORDER BY h.c DESC, v.release_date DESC"
                elif tab == 'related':
                    order_clause = f"ORDER BY bm25({VIDEOS_TABLE}_fts, 5.0, 10.0, 2.0, 1.0, 0.5) ASC, v.release_date DESC, v.added_at DESC"
                elif safe_key:
                    order_clause = f"ORDER BY v.release_date DESC, bm25({VIDEOS_TABLE}_fts, 5.0, 10.0, 2.0, 1.0, 0.5) ASC, v.added_at DESC"
                else:
                    order_clause = "ORDER BY v.release_date DESC, v.added_at DESC"
                    
                query = f"SELECT v.id, v.title, v.cover, v.url, v.release_date, v.actress, v.genre, v.maker, v.details, v.dvd FROM {from_clause} {where_sql} {order_clause} LIMIT ? OFFSET ?"
                cursor.execute(query, params + [per_page, offset])

                rows = cursor.fetchall()
                
        # Thu thập toàn bộ codes / dvds để tra cứu trạng thái GDrive và Upload Queue
        query_codes = []
        for row in rows:
            code_candidate = (row[9] or '').strip().upper()
            if not code_candidate:
                # Thử trích xuất code từ title nếu chưa có dvd
                m_c = re.search(r'([A-Za-z0-9]+-[0-9]+)', row[1] or '')
                code_candidate = m_c.group(1).upper() if m_c else (row[0] or '').strip().upper()
            query_codes.append(code_candidate)

        has_gdrive_set, in_queue_set = get_nextdjav_status_maps(query_codes)

        videos = []
        for idx, row in enumerate(rows):
            code_val = query_codes[idx]
            has_gd = code_val in has_gdrive_set
            in_q = code_val in in_queue_set

            dummy_uuid = "bc21a4fe-5e9b-4936-a844-b3e5f04c4cdc"
            stream_val = row[3] if (row[3] and dummy_uuid not in row[3] and 'failed' not in row[3]) else None
            if stream_val and (stream_val.startswith('http://') or stream_val.startswith('https://')):
                if '.m3u8' in stream_val or '.vl' in stream_val:
                    stream_play_url = f"/api/proxy?url={quote(stream_val)}"
                else:
                    stream_play_url = stream_val
            else:
                stream_play_url = stream_val or f"/api/video_url?id={row[0]}"

            vid_item = {
                "id": row[0],
                "code": code_val,
                "name": code_val,
                "title": row[1],
                "cover": f"/api/media?id={row[0]}",
                "cover_url": f"/api/media?id={row[0]}",
                "coverUrl": f"/api/media?id={row[0]}",
                "poster_url": f"/api/media?id={row[0]}",
                "url": stream_val or "",
                "stream_url": stream_play_url,
                "release_date": row[4] if len(row) > 4 else '',
                "releaseDate": row[4] if len(row) > 4 else '',
                "actress": row[5] if len(row) > 5 else '',
                "actresses": [a.strip() for a in row[5].split(',') if a.strip()] if (len(row) > 5 and row[5]) else [],
                "genre": row[6] if len(row) > 6 else '',
                "genres": [g.strip() for g in row[6].split(',') if g.strip()] if (len(row) > 6 and row[6]) else [],
                "maker": row[7] if len(row) > 7 else '',
                "studio": row[7] if len(row) > 7 else '',
                "details": row[8] if len(row) > 8 else '',
                "dvd": row[9] if len(row) > 9 else '',
                "source": getattr(scraper_instance, 'source_name', app_args.source),
                "domain": getattr(scraper_instance, 'domain', ''),
                "hasGDrive": has_gd,
                "has_gdrive": has_gd,
                "inDownloadQueue": in_q,
                "in_download_queue": in_q,
                "in_queue": in_q
            }
            videos.append(vid_item)

        total_pages = (total + per_page - 1) // max(1, per_page) if total > 0 else 1
        return jsonify({
            "success": True,
            "items": videos,
            "videos": videos,
            "entries": videos,
            "total": total,
            "totalPages": total_pages,
            "page": page
        })
    except sqlite3.OperationalError as e:
        if 'interrupted' in str(e).lower():
            custom_log("API", "⚠️ Query timeout trong /api/videos (>2s). Bỏ qua.")
            return jsonify({"items": [], "total": 0, "page": page})
        raise

@app.route('/api/related', methods=['GET'])
def get_related():
    vid_id = request.args.get('id', '')
    if not vid_id:
        return jsonify({"items": []})
        
    try:
        with db_lock:
            with sqlite_timeout(db_conn_instance, 2.0):
                cursor = db_conn_instance.cursor()
                cursor.execute(f"SELECT title, actress, genre, maker FROM {VIDEOS_TABLE} WHERE id = ?", (vid_id,))
                row = cursor.fetchone()
                
                if not row:
                    return jsonify({"items": []})
                    
                title, actress, genre, maker = row
                
                if hasattr(scraper_instance, 'clean_keywords'):
                    keywords = scraper_instance.clean_keywords(title) if title else []
                else:
                    keywords = extract_clean_keywords_bulletproof(title) if title else []
                        
                query_parts = []
                if actress:
                    actresses = [a.strip() for a in actress.split(',')]
                    query_parts.append(' OR '.join([f'actress : "{a}"' for a in actresses if a]))
                if genre:
                    genres = [g.strip() for g in genre.split(',')]
                    query_parts.append(' OR '.join([f'genre : "{g}"' for g in genres if g]))
                if maker:
                    query_parts.append(f'maker : "{maker}"')
                    
                if keywords:
                    kw_str = ' OR '.join([f'"{k}"*' for k in keywords[:5]])
                    query_parts.append(f'title : ({kw_str})')
                    
                fts_query = ' OR '.join([p for p in query_parts if p])
                
                if not fts_query:
                    return jsonify({"items": []})
                    
                sql = f'''
                    SELECT v.id, v.title, v.cover, v.url, v.release_date, v.actress, v.genre, v.maker, v.details, v.dvd
                    FROM {VIDEOS_TABLE}_fts
                    JOIN {VIDEOS_TABLE} v ON v.rowid = {VIDEOS_TABLE}_fts.rowid
                    WHERE {VIDEOS_TABLE}_fts MATCH ? AND v.id != ?
                    ORDER BY bm25({VIDEOS_TABLE}_fts, 5.0, 10.0, 2.0, 1.0, 0.5) ASC, v.release_date DESC
                    LIMIT 12
                '''
                cursor.execute(sql, (fts_query, vid_id))
                rows = cursor.fetchall()
                
        videos = []
        for row in rows:
            videos.append({
                "id": row[0],
                "title": row[1],
                "cover": f"/api/media?id={row[0]}",
                "url": row[3],
                "release_date": row[4] if row[4] else '',
                "actress": row[5] if row[5] else '',
                "genre": row[6] if row[6] else '',
                "maker": row[7] if row[7] else '',
                "details": row[8] if row[8] else '',
                "dvd": row[9] if len(row) > 9 and row[9] else '',
                "source": getattr(scraper_instance, 'source_name', app_args.source),
                "domain": getattr(scraper_instance, 'domain', '')
            })
        return jsonify({"items": videos})
    except sqlite3.OperationalError as e:
        if 'interrupted' in str(e).lower():
            custom_log("API", "⚠️ Query timeout trong /api/related (>2s). Bỏ qua.")
            return jsonify({"items": []})
        raise

@app.route('/api/media', methods=['GET'])
def get_media():
    vid_id = request.args.get('id', '')
    if not vid_id:
        return Response(status=404)
    
    with memory_lock:
        media_in_buffer = db_buffer['media'].get(vid_id)
    if media_in_buffer:
        return Response(media_in_buffer['data'], mimetype=media_in_buffer['content_type'] or 'image/jpeg', headers={'Cache-Control': 'public, max-age=31536000'})
        
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT data, content_type FROM media WHERE id = ?", (vid_id,))
        row = cursor.fetchone()
        
    if row:
        return Response(row[0], mimetype=row[1] or 'image/jpeg', headers={'Cache-Control': 'public, max-age=31536000'})
    else:
        # 1. Tra cứu trực tiếp từ Segment Bin file (cover_bin_id, cover_offset, cover_length)
        try:
            with db_lock:
                cursor = db_conn_instance.cursor()
                cursor.execute(f"SELECT cover_bin_id, cover_offset, cover_length FROM {VIDEOS_TABLE} WHERE id = ? OR upper(dvd) = ?", (vid_id, vid_id.upper()))
                bin_row = cursor.fetchone()
            if bin_row and bin_row[0] is not None and bin_row[1] is not None and bin_row[2] and bin_row[2] > 0:
                bin_id, offset, length = bin_row[0], bin_row[1], bin_row[2]
                db_dir = os.path.dirname(os.path.abspath(app_args.sqlite3)) if app_args and app_args.sqlite3 else '.'
                source_name = getattr(scraper_instance, 'source_name', app_args.source if app_args else 'missav').lower()
                
                vault_dir = os.environ.get("VAULT_ROOT") or os.environ.get("WINDOWS_BIN_DIR") if os.name == 'nt' else os.environ.get("TERMUX_BIN_DIR")
                if vault_dir is None: vault_dir = ""
                # Tìm file bin tương ứng (ưu tiên trong Vault trước, sau đó fallback về db_dir)
                bin_patterns = [
                    os.path.join(vault_dir, f"{source_name}_covers_{bin_id:04d}.bin"),
                    os.path.join(vault_dir, f"{source_name}_posters_{bin_id:04d}.bin"),
                    os.path.join(vault_dir, source_name, f"{source_name}_covers_{bin_id:04d}.bin"),
                    os.path.join(db_dir, f"{source_name}_covers_{bin_id:04d}.bin"),
                    os.path.join(db_dir, f"{source_name}_posters_{bin_id:04d}.bin"),
                    os.path.join(db_dir, f"{source_name}_covers_{bin_id}.bin"),
                    os.path.join(db_dir, f"covers_{bin_id:04d}.bin")
                ]
                for bp in bin_patterns:
                    if os.path.exists(bp):
                        with open(bp, 'rb') as bf:
                            bf.seek(offset)
                            bin_data = bf.read(length)
                            if len(bin_data) == length:
                                return Response(bin_data, mimetype='image/jpeg', headers={'Cache-Control': 'public, max-age=31536000'})
        except Exception as e:
            custom_log("API", f"⚠️ Lỗi đọc cover từ Segment Bin cho {vid_id}: {e}")

        # 2. Fallback: tải từ cover URL gốc nếu chưa có trong bin
        cover_url = None
        with db_lock:
            cursor = db_conn_instance.cursor()
            cursor.execute(f"SELECT cover FROM {VIDEOS_TABLE} WHERE id = ? OR upper(dvd) = ?", (vid_id, vid_id.upper()))
            vrow = cursor.fetchone()
            
        if vrow and vrow[0]:
            cover_url = vrow[0]
            if cover_url.startswith('//'):
                cover_url = 'https:' + cover_url
        
        if cover_url:
            if vid_id in downloading_media:
                for _ in range(100):
                    time.sleep(0.1)
                    if vid_id not in downloading_media:
                        break
                
                with memory_lock:
                    media_in_buffer = db_buffer['media'].get(vid_id)
                if media_in_buffer:
                    return Response(media_in_buffer['data'], mimetype=media_in_buffer['content_type'] or 'image/jpeg', headers={'Cache-Control': 'public, max-age=31536000'})
            
            downloading_media.add(vid_id)
            try:
                res = scraper_instance.session.get(cover_url, headers={"Referer": getattr(scraper_instance, 'referer', '')}, timeout=app_args.parsed_timeout)
                if res.status_code == 200:
                    content_type = res.headers.get('Content-Type', 'image/jpeg')
                    with memory_lock:
                        db_buffer['media'][vid_id] = {
                            'data': res.content,
                            'content_type': content_type
                        }
                    return Response(res.content, mimetype=content_type, headers={'Cache-Control': 'public, max-age=31536000'})
            except Exception as e:
                pass
            finally:
                downloading_media.discard(vid_id)
                
        return Response(status=404)

@app.route('/api/proxy', methods=['GET'])
def proxy_video():
    target_url = request.args.get('url', '')
    if not target_url:
        return Response(status=400)
    target_url = target_url.split('#')[0]
    client_range = request.headers.get('Range')
    
    # Thiết lập Referer phù hợp theo CDN đích
    parsed_target = urlparse(target_url)
    ref = getattr(scraper_instance, 'referer', '')
    if 'playergo.top' in parsed_target.netloc or 'sixyik.com' in parsed_target.netloc:
        ref = 'https://missav99.com/'
    elif 'surrit.com' in parsed_target.netloc:
        ref = 'https://missav.ws/'
    elif 'qooglevideo.com' in parsed_target.netloc or 'googleusercontent.com' in parsed_target.netloc:
        ref = f"https://{getattr(scraper_instance, 'domain', 'vlxx.phd')}/"
    elif 'youtubepro.me' in parsed_target.netloc:
        ref = f"https://{getattr(scraper_instance, 'domain', 'sextop1.buzz')}/"
    headers = {
        "Referer": ref,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
        
    try:
        is_m3u8 = target_url.split('?')[0].endswith('.m3u8') or target_url.split('?')[0].endswith('.vl')
        
        if is_m3u8:
            res = scraper_instance.session.get(target_url, headers=headers, timeout=app_args.parsed_timeout)
            content = res.text
            base_url = target_url.rsplit('/', 1)[0] + '/'
            
            if target_url.split('?')[0].endswith('playlist.m3u8'):
                lines = content.splitlines()
                best_info = ""
                best_url = ""
                max_val = -1
                for i in range(len(lines)):
                    if lines[i].startswith('#EXT-X-STREAM-INF'):
                        match = re.search(r'RESOLUTION=\d+x(\d+)', lines[i])
                        val = int(match.group(1)) if match else 0
                        if val == 0:
                            match_bw = re.search(r'BANDWIDTH=(\d+)', lines[i])
                            val = int(match_bw.group(1)) if match_bw else 0
                        if val >= max_val and i + 1 < len(lines):
                            max_val = val
                            best_info = lines[i]
                            best_url = lines[i+1].strip()
                if best_url:
                    content = f"#EXTM3U\n{best_info}\n{best_url}"

            new_content = []
            for line in content.splitlines():
                if line.startswith('#') or not line.strip():
                    new_content.append(line)
                else:
                    if line.startswith('http'):
                        new_url = line
                    elif line.startswith('/'):
                        parsed_base = urlparse(target_url)
                        new_url = f"{parsed_base.scheme}://{parsed_base.netloc}{line}"
                    else:
                        new_url = base_url + line
                    new_content.append(f"/api/proxy?url={quote(new_url)}")
            body = '\n'.join(new_content).encode('utf-8')
            
            resp_headers = {k: v for k, v in res.headers.items() if k.lower() not in ['content-encoding', 'transfer-encoding', 'content-length', 'connection', 'access-control-allow-origin']}
            resp_headers['Access-Control-Allow-Origin'] = '*'
            resp_headers['Content-Length'] = str(len(body))
            resp_headers['Content-Type'] = 'application/x-mpegURL'
            return Response(body, status=res.status_code, headers=resp_headers)

        # Bước 1: Request 2 byte đầu tiên để lấy Content-Length và kiểm tra HTTP Range
        head_req = scraper_instance.session.get(target_url, headers=dict(headers, Range="bytes=0-1"), timeout=app_args.parsed_timeout)
        
        total_size = 0
        is_range_supported = False
        
        if head_req.status_code == 206:
            cr = head_req.headers.get('Content-Range', '')
            match = re.search(r'/(\d+)', cr)
            if match:
                total_size = int(match.group(1))
                is_range_supported = True
                
        # Nếu Server không hỗ trợ tải nhiều luồng / không trả về size, dùng Proxy 1 luồng cơ bản
        if not is_range_supported or total_size == 0 or total_size < 5 * 1024 * 1024 or target_url.split('?')[0].endswith('.ts'):
            if client_range:
                headers['Range'] = client_range
            res = scraper_instance.session.get(target_url, headers=headers, timeout=app_args.parsed_timeout, stream=True)
            resp_headers = {k: v for k, v in res.headers.items() if k.lower() not in ['content-encoding', 'transfer-encoding', 'connection', 'access-control-allow-origin']}
            resp_headers['Access-Control-Allow-Origin'] = '*'
            
            def generate_fallback():
                try:
                    for chunk in res.iter_content(chunk_size=app_args.chunk_size_bytes):
                        if chunk:
                            yield chunk
                            global global_last_request_time
                            global_last_request_time = time.time()
                except GeneratorExit:
                    pass
                finally:
                    res.close()
            return Response(generate_fallback(), status=res.status_code, headers=resp_headers)
            
        # Bước 2: Proxy Đa luồng (Hoạt động giống IDM để tăng tốc stream)
        start = 0
        end = total_size - 1
        
        if client_range:
            match = re.match(r'bytes=(\d+)-(\d*)', client_range)
            if match:
                start = int(match.group(1))
                if match.group(2): end = int(match.group(2))
                    
        if start > end or start >= total_size:
            return Response(status=416, headers={'Content-Range': f'bytes */{total_size}'})
        if end >= total_size:
            end = total_size - 1
            
        chunk_size = app_args.chunk_size_bytes
        ranges_to_fetch = []
        curr = start
        while curr <= end:
            next_curr = min(curr + chunk_size - 1, end)
            ranges_to_fetch.append((curr, next_curr))
            curr = next_curr + 1
            
        resp_headers = {
            'Content-Type': head_req.headers.get('Content-Type', 'video/mp4'),
            'Accept-Ranges': 'bytes',
            'Content-Length': str(end - start + 1),
            'Access-Control-Allow-Origin': '*'
        }
        status_code = 200
        if client_range:
            status_code = 206
            resp_headers['Content-Range'] = f'bytes {start}-{end}/{total_size}'
            
        abort_event = threading.Event()
            
        def fetch_range(r):
            for _ in range(3): # Thử tối đa 3 lần nếu có lỗi tải khối này
                if abort_event.is_set():
                    return None
                try:
                    # Dùng stream=True để ngắt kết nối lập tức (I/O Blocking fix) khi client hủy
                    req_hdrs = dict(headers)
                    req_hdrs["Range"] = f"bytes={r[0]}-{r[1]}"
                    res = scraper_instance.session.get(target_url, headers=req_hdrs, timeout=app_args.parsed_timeout, stream=True)
                    if res.status_code in (200, 206):
                        data = bytearray()
                        for chunk in res.iter_content(chunk_size=app_args.chunk_size_bytes): 
                            if abort_event.is_set():
                                res.close()
                                return None
                            if chunk:
                                data.extend(chunk)
                        return bytes(data)
                except Exception:
                    time.sleep(1)
            return None
            
        def generate_multithread():
            global global_last_request_time
            max_workers = app_args.proxy_threads
            window_size = app_args.max_keepalive
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
            try:
                futures = {}
                submit_idx = 0
                
                def submit_next():
                    nonlocal submit_idx
                    if submit_idx < len(ranges_to_fetch) and not abort_event.is_set():
                        futures[submit_idx] = executor.submit(fetch_range, ranges_to_fetch[submit_idx])
                        submit_idx += 1
                        
                # Khởi tạo nạp trước các khối vào hàng đợi
                for _ in range(window_size):
                    submit_next()
                    
                yield_idx = 0
                while yield_idx < len(ranges_to_fetch):
                    f = futures.pop(yield_idx)
                    data = f.result()
                    if data:
                        for i in range(0, len(data), app_args.chunk_size_bytes):
                            yield data[i:i+app_args.chunk_size_bytes]
                            global_last_request_time = time.time()
                        
                        # Ngay khi 1 khối đã yield xong, nạp ngay khối mới để luồng nào xong việc có thể lấy chạy tiếp
                        submit_next()
                        yield_idx += 1
                    else:
                        break # Ngắt quá trình stream nếu gặp block lỗi nặng hoặc client abort
            except GeneratorExit:
                abort_event.set()
            finally:
                abort_event.set()
                if sys.version_info >= (3, 9): executor.shutdown(wait=False, cancel_futures=True)
                else: executor.shutdown(wait=False)
                
        return Response(generate_multithread(), status=status_code, headers=resp_headers)
    except Exception as e:
        custom_log("System", f"❌ Error fetching {target_url}: {e}")
        return Response(status=500)

@app.route('/api/sync', methods=['GET'])
def sync_api():
    domain_base = scraper_instance.domain.split('.')[0].lower()
    source_name = getattr(scraper_instance, 'source_name', '').lower()
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT url_pattern FROM sync_tasks")
        cursor.execute("SELECT url_pattern FROM sync_tasks WHERE LOWER(url_pattern) LIKE ? OR LOWER(url_pattern) LIKE ?", (f"%{domain_base}%", f"%{source_name}%"))
        tasks = cursor.fetchall()
    for task in tasks:
        scraper_instance.sync_list_page(task[0], 1)
    return jsonify({"success": True})

@app.route('/api/video_url', methods=['GET'])
def video_url_api():
    vid_id = request.args.get('id', '')
    force_refresh = request.args.get('refresh', '0').lower() in ['1', 'true', 'yes']
    url = scraper_instance.get_video_url(vid_id, force_refresh=force_refresh)
    if url:
        play_config = {}
        try:
            with db_lock:
                cursor = db_conn_instance.cursor()
                cursor.execute("SELECT jwplayer_key, server, extra_data FROM play_configs WHERE video_id = ?", (vid_id,))
                row = cursor.fetchone()
                if row:
                    play_config = {
                        "jwplayer_key": row[0] if row[0] else "",
                        "server": row[1] if row[1] else "",
                        "extra_data": row[2] if row[2] else ""
                    }
        except Exception:
            pass
        return jsonify({"success": True, "url": url, "play_config": play_config})
    return jsonify({"success": False, "error": "Cannot extract URL"})

@app.route('/api/video_details', methods=['GET'])
def video_details_api():
    vid_id = request.args.get('id', '')
    if not vid_id:
        return jsonify({"success": False, "error": "Missing id"})
    
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute(f"SELECT id, title, cover, url, release_date, actress, genre, maker, details, dvd FROM {VIDEOS_TABLE} WHERE id = ?", (vid_id,))
        row = cursor.fetchone()
        
    if row:
        code_val = (row[9] or '').strip().upper()
        if not code_val:
            m_c = re.search(r'([A-Za-z0-9]+-[0-9]+)', row[1] or '')
            code_val = m_c.group(1).upper() if m_c else (row[0] or '').strip().upper()

        has_gdrive_set, in_queue_set = get_nextdjav_status_maps([code_val])
        has_gd = code_val in has_gdrive_set
        in_q = code_val in in_queue_set

        dummy_uuid = "bc21a4fe-5e9b-4936-a844-b3e5f04c4cdc"
        stream_val = row[3] if (row[3] and dummy_uuid not in row[3] and 'failed' not in row[3]) else None
        
        # Nếu là link m3u8 ngoại vi (surrit, cdn...) thì proxy qua server để bypass CORS và Referer 403
        if stream_val and (stream_val.startswith('http://') or stream_val.startswith('https://')):
            if '.m3u8' in stream_val or '.vl' in stream_val:
                stream_play_url = f"/api/proxy?url={quote(stream_val)}"
            else:
                stream_play_url = stream_val
        else:
            stream_play_url = stream_val or f"/api/video_url?id={row[0]}"

        vid_data = {
            "id": row[0],
            "code": code_val,
            "name": code_val,
            "title": row[1],
            "cover": f"/api/media?id={row[0]}",
            "cover_url": f"/api/media?id={row[0]}",
            "coverUrl": f"/api/media?id={row[0]}",
            "poster_url": f"/api/media?id={row[0]}",
            "url": stream_val or "",
            "stream_url": stream_play_url,
            "release_date": row[4] if row[4] else '',
            "releaseDate": row[4] if row[4] else '',
            "actress": row[5] if row[5] else '',
            "actresses": [a.strip() for a in row[5].split(',') if a.strip()] if row[5] else [],
            "genre": row[6] if row[6] else '',
            "genres": [g.strip() for g in row[6].split(',') if g.strip()] if row[6] else [],
            "maker": row[7] if row[7] else '',
            "studio": row[7] if row[7] else '',
            "details": row[8] if row[8] else '',
            "dvd": row[9] if row[9] else '',
            "source": getattr(scraper_instance, 'source_name', app_args.source),
            "domain": getattr(scraper_instance, 'domain', ''),
            "hasGDrive": has_gd,
            "has_gdrive": has_gd,
            "inDownloadQueue": in_q,
            "in_download_queue": in_q,
            "in_queue": in_q
        }
        return jsonify({
            "success": True,
            "data": vid_data,
            "video": vid_data,
            "sources": [{"id": "crawl", "name": getattr(scraper_instance, 'source_name', app_args.source).capitalize(), "stream_url": vid_data["stream_url"], "default": True}]
        })
    return jsonify({"success": False, "error": "Not found"})

@app.route('/api/video/<path:code>', methods=['GET'])
def api_oneplayer_video_detail(code):
    """Tương thích OnePlayer Drawer/Detail request /api/video/:code."""
    code_clean = code.strip().upper()
    for ext in (".TS", ".MP4", ".MKV", ".DATTS", ".M3U8", ".JSON"):
        if code_clean.endswith(ext):
            code_clean = code_clean[:-len(ext)]
            break

    with db_lock:
        cursor = db_conn_instance.cursor()
        code_low = code_clean.lower()
        cursor.execute(f"""
            SELECT id, title, cover, url, release_date, actress, genre, maker, details, dvd 
            FROM {VIDEOS_TABLE} 
            WHERE id = ? OR upper(id) = ? OR dvd = ? OR upper(dvd) = ? OR upper(title) LIKE ? 
            LIMIT 1
        """, (code_low, code_clean, code_low, code_clean, f"%{code_clean}%"))
        row = cursor.fetchone()

    if not row:
        return jsonify({"success": False, "error": "Video not found"}), 404

    code_val = (row[9] or '').strip().upper() or code_clean
    has_gdrive_set, in_queue_set = get_nextdjav_status_maps([code_val])
    has_gd = code_val in has_gdrive_set
    in_q = code_val in in_queue_set

    # Tự động nạp stream URL nếu chưa có sẵn hoặc nếu URL cũ là dummy UUID
    dummy_uuid = "bc21a4fe-5e9b-4936-a844-b3e5f04c4cdc"
    stream_url = row[3] if (row[3] and dummy_uuid not in row[3] and 'failed' not in row[3]) else None
    if not stream_url:
        try:
            stream_url = scraper_instance.get_video_url(row[0])
        except Exception:
            pass

    if stream_url and (stream_url.startswith('http://') or stream_url.startswith('https://')):
        if '.m3u8' in stream_url or '.vl' in stream_url:
            stream_play_url = f"/api/proxy?url={quote(stream_url)}"
        else:
            stream_play_url = stream_url
    else:
        stream_play_url = stream_url or f"/api/video_url?id={row[0]}"

    vid_data = {
        "id": row[0],
        "code": code_val,
        "name": code_val,
        "title": row[1] or code_val,
        "cover": f"/api/media?id={row[0]}",
        "cover_url": f"/api/media?id={row[0]}",
        "coverUrl": f"/api/media?id={row[0]}",
        "poster_url": f"/api/media?id={row[0]}",
        "url": stream_url or "",
        "stream_url": stream_play_url,
        "release_date": row[4] if row[4] else '',
        "releaseDate": row[4] if row[4] else '',
        "actress": row[5] if row[5] else '',
        "actresses": [a.strip() for a in row[5].split(',') if a.strip()] if row[5] else [],
        "genre": row[6] if row[6] else '',
        "genres": [g.strip() for g in row[6].split(',') if g.strip()] if row[6] else [],
        "maker": row[7] if row[7] else '',
        "studio": row[7] if row[7] else '',
        "details": row[8] if row[8] else '',
        "dvd": row[9] if row[9] else '',
        "hasGDrive": has_gd,
        "has_gdrive": has_gd,
        "inDownloadQueue": in_q,
        "in_download_queue": in_q,
        "in_queue": in_q,
        "source": getattr(scraper_instance, 'source_name', app_args.source),
        "domain": getattr(scraper_instance, 'domain', '')
    }
    return jsonify({
        "success": True,
        "video": vid_data,
        "sources": [{"id": "crawl", "name": getattr(scraper_instance, 'source_name', app_args.source).capitalize(), "stream_url": vid_data["stream_url"], "default": True}]
    })

@app.route('/api/search_suggestions', methods=['GET'])
def search_suggestions():
    q = request.args.get('q', '').strip().lower()
    tab = request.args.get('tab', 'all')
    page = int(request.args.get('page', 1))
    identifier = get_identifier()
    suggestions = []
    
    per_page = 30
    offset = (page - 1) * per_page

    try:
        with db_lock:
            with sqlite_timeout(db_conn_instance, 2.0):
                cursor = db_conn_instance.cursor()
                
                if tab in ['actress', 'genre', 'maker']:
                    if not q:
                        cursor.execute("SELECT keyword FROM tags_summary WHERE type=? ORDER BY count DESC LIMIT ? OFFSET ?", (tab, per_page, offset))
                        for r in cursor.fetchall():
                            suggestions.append({"text": r[0], "type": tab})
                    else:
                        words = q.replace('"', '').split()
                        if not words:
                            return jsonify({"success": True, "suggestions": suggestions})
                        safe_key_and = ' AND '.join([f'"{w}"*' for w in words])
                        try:
                            cursor.execute('''
                                SELECT keyword
                                FROM tags_fts
                                WHERE tags_fts MATCH ? AND type=?
                                ORDER BY count DESC
                                LIMIT ? OFFSET ?
                            ''', (safe_key_and, tab, per_page, offset))
                            tag_rows = cursor.fetchall()
                        except Exception as e:
                            custom_log("System", f"❌ FTS tags search error: {e}")
                            tag_rows = []

                        if len(tag_rows) < 15 and page == 1:
                            load_tags_cache_if_needed(cursor)
                            seen_tags = set([r[0] for r in tag_rows])
                            q_low = q.lower()
                            q_no_accents = remove_accents(q_low)
                            q_sorted = " ".join(sorted(q_no_accents.split()))
                            q_words = q_no_accents.split()
                            
                            fuzzy_matches = []
                            for kw, t, c, low, low_no_accents, sorted_low in tags_cache:
                                if t != tab or kw in seen_tags:
                                    continue
                                if q_no_accents in low_no_accents:
                                    fuzzy_matches.append((1.0, c, kw, t))
                                elif all(w in low_no_accents for w in q_words):
                                    fuzzy_matches.append((0.95, c, kw, t))
                                else:
                                    matcher = difflib.SequenceMatcher(None, q_sorted, sorted_low)
                                    if matcher.quick_ratio() >= 0.75:
                                        score = matcher.ratio()
                                        if score >= 0.75:
                                            fuzzy_matches.append((score, c, kw, t))
                                            
                            fuzzy_matches.sort(key=lambda x: (x[0], x[1]), reverse=True)
                            for score, c, kw, t in fuzzy_matches[:per_page - len(tag_rows)]:
                                tag_rows.append((kw,))
                                seen_tags.add(kw)

                        for r in tag_rows:
                            suggestions.append({"text": r[0], "type": tab})

                    return jsonify({"success": True, "suggestions": suggestions})

                if page > 1:
                    return jsonify({"success": True, "suggestions": []})

                if identifier:
                    if not q:
                        cursor.execute("SELECT keyword FROM search_history WHERE username = ? ORDER BY searched_at DESC LIMIT 10", (identifier,))
                    else:
                        cursor.execute("SELECT keyword FROM search_history WHERE username = ? AND keyword LIKE ? ORDER BY searched_at DESC LIMIT 10", (identifier, f'%{q}%'))
                    for r in cursor.fetchall():
                        suggestions.append({"text": r[0], "type": "history"})
                    
                if not q:
                    if identifier:
                        try:
                            cursor.execute(f'''
                                SELECT v.title, v.id, v.cover
                                FROM history h
                                JOIN {VIDEOS_TABLE} v ON h.video_id = v.id
                                WHERE h.username = ?
                                ORDER BY h.last_watched DESC
                                LIMIT 5
                            ''', (identifier,))
                            for row in cursor.fetchall():
                                t = row[0].strip()
                                if t:
                                    suggestions.append({
                                        "text": t[:80] + ("..." if len(t)>80 else ""),
                                        "type": "watch_history",
                                        "id": row[1],
                                        "cover": f"/api/media?id={row[1]}"
                                    })
                        except Exception as e:
                            custom_log("System", f"❌ Lỗi gợi ý lịch sử xem (q rỗng): {e}")
        
                    cursor.execute("SELECT keyword FROM tags_summary WHERE type='actress' ORDER BY count DESC LIMIT 10")
                    for r in cursor.fetchall(): suggestions.append({"text": r[0], "type": "actress"})
                    
                    cursor.execute("SELECT keyword FROM tags_summary WHERE type='genre' ORDER BY count DESC LIMIT 10")
                    for r in cursor.fetchall(): suggestions.append({"text": r[0], "type": "genre"})
                    
                    cursor.execute("SELECT keyword FROM tags_summary WHERE type='maker' ORDER BY count DESC LIMIT 10")
                    for r in cursor.fetchall(): suggestions.append({"text": r[0], "type": "maker"})
                    
                    return jsonify({"success": True, "suggestions": suggestions})
                
                else:
                    raw_parts = [p.strip() for p in q.split(',') if p.strip()]
                    or_parts = []
                    and_parts = []
                    for part in raw_parts:
                        w_list = part.replace('"', '').split()
                        if w_list:
                            and_parts.append('(' + ' AND '.join([f'"{w}"*' for w in w_list]) + ')')
                            or_parts.append('(' + ' OR '.join([f'"{w}"*' for w in w_list]) + ')')
                    
                    if not or_parts:
                        return jsonify({"success": True, "suggestions": suggestions})
                        
                    safe_key_and = ' OR '.join(and_parts)
                    safe_key_or = ' OR '.join(or_parts)
                    
                    try:
                        cursor.execute('''
                            SELECT keyword, type, count
                            FROM tags_fts
                            WHERE tags_fts MATCH ?
                            ORDER BY count DESC
                            LIMIT 30
                        ''', (safe_key_and,))
                        tag_rows = cursor.fetchall()
                    except Exception as e:
                        custom_log("System", f"❌ FTS tags search error: {e}")
                        tag_rows = []
                        
                    # Fuzzy Search (Tìm kiếm mờ) bổ sung nếu FTS không trả về đủ kết quả
                    if len(tag_rows) < 15:
                        load_tags_cache_if_needed(cursor)
                        seen_tags = set([r[0] for r in tag_rows])
                        q_low = q.lower()
                        q_no_accents = remove_accents(q_low)
                        q_sorted = " ".join(sorted(q_no_accents.split()))
                        q_words = q_no_accents.split()
                        
                        fuzzy_matches = []
                        for kw, t, c, low, low_no_accents, sorted_low in tags_cache:
                            if kw in seen_tags:
                                continue
                            
                            if q_no_accents in low_no_accents:
                                fuzzy_matches.append((1.0, c, kw, t))
                            elif all(w in low_no_accents for w in q_words):
                                fuzzy_matches.append((0.95, c, kw, t))
                            else:
                                matcher = difflib.SequenceMatcher(None, q_sorted, sorted_low)
                                if matcher.quick_ratio() >= 0.75:
                                    score = matcher.ratio()
                                    if score >= 0.75:
                                        fuzzy_matches.append((score, c, kw, t))
                                        
                        fuzzy_matches.sort(key=lambda x: (x[0], x[1]), reverse=True)
                        for score, c, kw, t in fuzzy_matches[:15]:
                            tag_rows.append((kw, t, c))
                            seen_tags.add(kw)
        
                    match_actress = []
                    match_genre = []
                    match_maker = []
                    
                    for row in tag_rows:
                        k, t, c = row
                        if t == 'actress': match_actress.append({"text": k, "count": c})
                        elif t == 'genre': match_genre.append({"text": k, "count": c})
                        elif t == 'maker': match_maker.append({"text": k, "count": c})
        
                    match_watch_history = []
                    if identifier:
                        try:
                            if safe_key_or:
                                cursor.execute(f'''
                                    SELECT v.title, v.id, v.cover
                                    FROM {VIDEOS_TABLE}_fts fts
                                    JOIN {VIDEOS_TABLE} v ON v.rowid = fts.rowid
                                    JOIN history h ON h.video_id = v.id
                                    WHERE h.username = ? AND fts.{VIDEOS_TABLE}_fts MATCH ?
                                    ORDER BY bm25(fts, 5.0, 10.0, 2.0, 1.0, 0.5) ASC, h.last_watched DESC
                                    LIMIT 5
                                ''', (identifier, f"({safe_key_or})"))
                                for row in cursor.fetchall():
                                    t = row[0].strip()
                                    if t:
                                        match_watch_history.append({
                                            "text": t[:80] + ("..." if len(t)>80 else ""),
                                            "type": "watch_history",
                                            "id": row[1],
                                            "cover": f"/api/media?id={row[1]}"
                                        })
                        except Exception as e:
                            custom_log("System", f"❌ Lỗi FTS lịch sử xem: {e}")
                        
                    match_title = []
                    try:
                        cursor.execute(f'''
                            SELECT v.title, v.id, v.cover
                            FROM {VIDEOS_TABLE}_fts
                            JOIN {VIDEOS_TABLE} v ON v.rowid = {VIDEOS_TABLE}_fts.rowid
                            WHERE {VIDEOS_TABLE}_fts MATCH ?
                            ORDER BY bm25({VIDEOS_TABLE}_fts, 5.0, 10.0, 2.0, 1.0, 0.5) ASC LIMIT 10
                        ''', (f"title : ({safe_key_or})",))
                        for row in cursor.fetchall():
                            t = row[0].strip()
                            if t:
                                match_title.append({
                                    "text": t[:80] + ("..." if len(t)>80 else ""), 
                                    "type": "title", 
                                    "id": row[1],
                                    "cover": f"/api/media?id={row[1]}"
                                })
                    except Exception as e:
                        custom_log("System", f"❌ FTS title search error: {e}")
                        
                    history_ids = {item['id'] for item in match_watch_history}
                    unique_match_title = [item for item in match_title if item['id'] not in history_ids]
        
                chips = []
                for a in match_actress[:5]: chips.append({"text": a["text"], "type": "actress"})
                for g in match_genre[:5]: chips.append({"text": g["text"], "type": "genre"})
                for m in match_maker[:5]: chips.append({"text": m["text"], "type": "maker"})
                    
                for c in chips[:15]:
                    suggestions.append(c)
                    
                for wh in match_watch_history:
                    suggestions.append(wh)
        
                for t in unique_match_title:
                    suggestions.append(t)
                    
                return jsonify({"success": True, "suggestions": suggestions})
    except sqlite3.OperationalError as e:
        if 'interrupted' in str(e).lower():
            custom_log("API", "⚠️ Query timeout trong /api/search_suggestions (>2s). Bỏ qua.")
            return jsonify({"success": True, "suggestions": []})
        raise

# =====================================================================
# ONEPLAYER COMPATIBILITY & GDRIVE QUEUE ENDPOINTS
# =====================================================================

@app.route('/api/queue', methods=['GET', 'POST', 'DELETE'])
def api_oneplayer_queue():
    """Quản lý hàng đợi tải lên Google Drive (Add Queue / Remove Queue / List Queue)."""
    conn = get_nextdjav_conn()
    if not conn:
        return jsonify({"success": False, "error": "Không thể kết nối cơ sở dữ liệu nextdjav.db"}), 500

    try:
        cur = conn.cursor()
        if request.method == 'GET':
            cur.execute("""
                SELECT media_id, title, source, status, file_name, created_at, error_message 
                FROM media_upload_queue 
                ORDER BY id DESC LIMIT 100
            """)
            items = []
            for r in cur.fetchall():
                items.append({
                    "media_id": r[0],
                    "title": r[1],
                    "source": r[2],
                    "status": r[3],
                    "file_name": r[4],
                    "created_at": r[5],
                    "error_message": r[6]
                })
            conn.close()
            return jsonify({"success": True, "items": items, "total": len(items)})

        payload = request.get_json(silent=True) or {}
        code = (payload.get('code') or payload.get('media_id') or request.args.get('code') or '').strip().upper()
        if not code:
            conn.close()
            return jsonify({"success": False, "error": "Thiếu mã code video"}), 400

        # Chuẩn hóa mã code bỏ phần mở rộng nếu có
        for ext in (".TS", ".MP4", ".MKV", ".DATTS", ".M3U8"):
            if code.endswith(ext):
                code = code[:-len(ext)]
                break

        if request.method == 'DELETE':
            cur.execute("""
                DELETE FROM media_upload_queue 
                WHERE upper(media_id) = ? 
                   OR upper(media_id) LIKE ? 
                   OR upper(file_name) LIKE ?
            """, (code, f"%{code}%", f"{code}.%"))
            conn.commit()
            conn.close()
            custom_log("Queue", f"🗑️ Đã xóa {code} khỏi media_upload_queue")
            return jsonify({"success": True, "message": f"Đã xóa {code} khỏi hàng đợi tải"})

        if request.method == 'POST':
            # Tìm thông tin video trong DB hiện tại của crawl-vod
            vrow = None
            if db_conn_instance:
                with db_lock:
                    c_vod = db_conn_instance.cursor()
                    c_vod.execute(f"SELECT id, title, cover, url, release_date, dvd FROM {VIDEOS_TABLE} WHERE upper(dvd) = ? OR upper(id) = ? OR upper(title) LIKE ? LIMIT 1", (code, code.lower(), f"%{code}%"))
                    vrow = c_vod.fetchone()

            vid_id = vrow[0] if vrow else code.lower()
            title = vrow[1] if vrow else code
            cover = vrow[2] if vrow else ""
            stream_url = vrow[3] if vrow else ""

            # Nếu chưa có stream url, thử lấy thông qua scraper
            if not stream_url and scraper_instance:
                try:
                    stream_url = scraper_instance.get_video_url(vid_id)
                except Exception:
                    pass

            if not stream_url:
                stream_url = f"https://{getattr(scraper_instance, 'domain', 'example.com')}/video/{vid_id}"

            file_name = f"{code}.mp4"
            source_name = getattr(scraper_instance, 'source_name', None) or (getattr(app_args, 'source', None) if app_args else 'javtiful')

            cur.execute("""
                INSERT INTO media_upload_queue 
                (media_id, source, title, file_name, original_url, poster_url, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
                ON CONFLICT(media_id) DO UPDATE SET
                    source = excluded.source,
                    title = excluded.title,
                    file_name = excluded.file_name,
                    original_url = excluded.original_url,
                    poster_url = excluded.poster_url,
                    status = 'pending',
                    retry_count = 0,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
            """, (code, source_name, title, file_name, stream_url, cover))
            conn.commit()
            conn.close()
            custom_log("Queue", f"📥 Đã thêm {code} vào media_upload_queue ({file_name})")
            return jsonify({"success": True, "message": f"Đã thêm {code} vào hàng đợi tải lên Google Drive", "code": code})

    except Exception as e:
        custom_log("Queue", f"❌ Lỗi xử lý /api/queue: {e}")
        try: conn.close()
        except Exception: pass
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/boxes', methods=['GET'])
def api_oneplayer_boxes():
    """Tương thích OnePlayer Boxes/chuyên mục."""
    source_label = getattr(scraper_instance, 'source_name', app_args.source).capitalize()
    return jsonify({
        "success": True,
        "boxes": [
            {"id": "all", "name": f"Tất cả ({source_label})", "thread_count": 1, "video_thread_count": 1},
            {"id": "gdrive", "name": "Đã trên Google Drive", "thread_count": 1, "video_thread_count": 1},
            {"id": "queue", "name": "Đang đợi tải (Queue)", "thread_count": 1, "video_thread_count": 1}
        ]
    })

@app.route('/api/categories/stats', methods=['GET'])
def api_oneplayer_categories_stats():
    """Thống kê chuyên mục cho Drawer OnePlayer."""
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {VIDEOS_TABLE}")
        total = cursor.fetchone()[0]

    # Đếm số lượng video gdrive & queue từ nextdjav.db
    gdrive_cnt = 0
    queue_cnt = 0
    conn = get_nextdjav_conn()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM drive_videos")
            gdrive_cnt = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM media_upload_queue WHERE status IN ('pending', 'claimed', 'uploading')")
            queue_cnt = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass

    return jsonify({
        "success": True,
        "counts": {
            "all": total,
            "gdrive": gdrive_cnt,
            "queue": queue_cnt,
            "actresses": 0,
            "genres": 0,
            "studios": 0
        }
    })

@app.route('/api/adjacent_video', methods=['GET'])
def api_oneplayer_adjacent_video():
    """Lấy video kế tiếp hoặc trước đó cho vuốt chuyển video trên OnePlayer."""
    code = request.args.get("code", "").strip().upper()
    direction = request.args.get("direction", "next").strip().lower()
    for ext in (".TS", ".MP4", ".MKV", ".DATTS", ".M3U8", ".JSON"):
        if code.endswith(ext):
            code = code[:-len(ext)]
            break

    sort_asc = request.args.get("sort_asc", "false").lower() == "true"

    with db_lock:
        cursor = db_conn_instance.cursor()
        # Điều kiện CHỈ lấy video đã cào xong hợp lệ
        dummy_uuid = "bc21a4fe-5e9b-4936-a844-b3e5f04c4cdc"
        cursor.execute(f"PRAGMA table_info({VIDEOS_TABLE})")
        col_names = [col[1] for col in cursor.fetchall()]
        
        cond_parts = [
            "(details_fetched = 1 OR cover_fetched = 1)",
            f"(url IS NOT NULL AND url != '' AND url NOT LIKE '%{dummy_uuid}%' AND url != 'failed')"
        ]
        if "surrit_id" in col_names:
            cond_parts.append("(surrit_id IS NOT NULL AND surrit_id != '' AND surrit_id NOT IN ('404', 'failed', 'no_surrit'))")
            
        crawled_cond = f"({' OR '.join(cond_parts)})"

        # Tìm rowid và release_date hiện tại
        cursor.execute(f"SELECT rowid, id, dvd, title, cover, url, release_date FROM {VIDEOS_TABLE} WHERE upper(dvd) = ? OR upper(id) = ? OR upper(title) LIKE ? LIMIT 1", (code, code.lower(), f"%{code}%"))
        cur_row = cursor.fetchone()

        adj = None
        if cur_row:
            cur_rowid = cur_row[0]
            cur_rel = cur_row[6] or ""
            # Khớp theo thứ tự sắp xếp của Explorer (mặc định release_date DESC, rowid DESC)
            if direction == "next":
                if sort_asc:
                    cursor.execute(f"SELECT id, dvd, title, cover, url, release_date FROM {VIDEOS_TABLE} WHERE {crawled_cond} AND (release_date > ? OR (release_date = ? AND rowid > ?)) ORDER BY release_date ASC, rowid ASC LIMIT 1", (cur_rel, cur_rel, cur_rowid))
                else:
                    cursor.execute(f"SELECT id, dvd, title, cover, url, release_date FROM {VIDEOS_TABLE} WHERE {crawled_cond} AND (release_date < ? OR (release_date = ? AND rowid < ?)) ORDER BY release_date DESC, rowid DESC LIMIT 1", (cur_rel, cur_rel, cur_rowid))
                adj = cursor.fetchone()
            else:
                if sort_asc:
                    cursor.execute(f"SELECT id, dvd, title, cover, url, release_date FROM {VIDEOS_TABLE} WHERE {crawled_cond} AND (release_date < ? OR (release_date = ? AND rowid < ?)) ORDER BY release_date DESC, rowid DESC LIMIT 1", (cur_rel, cur_rel, cur_rowid))
                else:
                    cursor.execute(f"SELECT id, dvd, title, cover, url, release_date FROM {VIDEOS_TABLE} WHERE {crawled_cond} AND (release_date > ? OR (release_date = ? AND rowid > ?)) ORDER BY release_date ASC, rowid ASC LIMIT 1", (cur_rel, cur_rel, cur_rowid))
                adj = cursor.fetchone()

        if not adj:
            order_clause = "ORDER BY release_date DESC, rowid DESC" if not sort_asc else "ORDER BY release_date ASC, rowid ASC"
            cursor.execute(f"SELECT id, dvd, title, cover, url, release_date FROM {VIDEOS_TABLE} WHERE {crawled_cond} {order_clause} LIMIT 1")
            adj = cursor.fetchone()

    if not adj:
        return jsonify({"success": False, "has_adjacent": False})

    adj_code = (adj[1] or '').strip().upper() or adj[0]
    raw_stream_url = adj[4] or f"/api/video_url?id={adj[0]}"
    if raw_stream_url and (raw_stream_url.startswith('http://') or raw_stream_url.startswith('https://')):
        if '.m3u8' in raw_stream_url or '.vl' in raw_stream_url:
            stream_url = f"/api/proxy?url={quote(raw_stream_url)}"
        else:
            stream_url = raw_stream_url
    else:
        stream_url = raw_stream_url

    poster_url = f"/api/media?id={adj[0]}"

    has_gdrive_set, in_queue_set = get_nextdjav_status_maps([adj_code])

    vid_obj = {
        "id": adj[0],
        "code": adj_code,
        "name": adj_code,
        "title": adj[2] or adj_code,
        "release_date": adj[5] or "",
        "stream_url": stream_url,
        "poster_url": poster_url,
        "cover_url": poster_url,
        "coverUrl": poster_url,
        "hasGDrive": adj_code in has_gdrive_set,
        "inDownloadQueue": adj_code in in_queue_set
    }

    return jsonify({
        "success": True,
        "has_adjacent": True,
        "adjacent_code": adj_code,
        "video": vid_obj
    })

@app.route('/api/gdrive/<path:code>', methods=['GET'])
def api_oneplayer_gdrive_info(code):
    """Thông tin video trên Google Drive cho Drawer chi tiết GDrive."""
    code_clean = code.strip().upper()
    for ext in (".TS", ".MP4", ".MKV", ".DATTS", ".M3U8"):
        if code_clean.endswith(ext):
            code_clean = code_clean[:-len(ext)]
            break

    conn = get_nextdjav_conn()
    if not conn:
        return jsonify({"success": False, "error": "Cannot connect to nextdjav.db"}), 500

    try:
        cur = conn.cursor()
        cur.execute("SELECT code, title, file_id, drive, filename, file_size FROM drive_videos WHERE upper(code) = ?", (code_clean,))
        r = cur.fetchone()
        conn.close()
        if r:
            return jsonify({
                "success": True,
                "source": {
                    "code": r[0],
                    "title": r[1],
                    "gdriveFileId": r[2],
                    "drive": r[3],
                    "driveName": r[3],
                    "filename": r[4],
                    "fileSize": r[5]
                }
            })
        return jsonify({"success": False, "message": "Chưa có trên Google Drive"}), 404
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/search_history', methods=['POST', 'DELETE'])
@app.route('/api/history/search', methods=['GET', 'POST', 'DELETE'])
def manage_search_history():
    identifier = get_identifier() or "default_user"
    
    if request.method == 'GET':
        limit = min(int(request.args.get('limit', 30)), 100)
        items = []
        with db_lock:
            cursor = db_conn_instance.cursor()
            cursor.execute("SELECT keyword, count(*) FROM search_history WHERE username = ? GROUP BY keyword ORDER BY max(searched_at) DESC LIMIT ?", (identifier, limit))
            for r in cursor.fetchall():
                items.append({"query": r[0], "count": r[1]})
            if not items:
                cursor.execute("SELECT keyword, 1 FROM search_history ORDER BY searched_at DESC LIMIT ?", (limit,))
                for r in cursor.fetchall():
                    items.append({"query": r[0], "count": 1})
        return jsonify({"success": True, "history": items, "items": items})

    payload = request.get_json(silent=True) or {}
    keyword = (payload.get('keyword') or payload.get('query') or request.args.get('keyword') or request.args.get('query') or '').strip()
    
    if not keyword and request.method == 'POST':
        return jsonify({"success": False, "error": "Missing keyword"}), 400
        
    if request.method == 'POST':
        now_ts = int(time.time())
        with db_lock:
            cursor = db_conn_instance.cursor()
            cursor.execute("INSERT OR REPLACE INTO search_history (username, keyword, searched_at) VALUES (?, ?, ?)", (identifier, keyword, now_ts))
            cursor.execute("DELETE FROM search_history WHERE username = ? AND keyword IN (SELECT keyword FROM search_history WHERE username = ? ORDER BY searched_at DESC LIMIT -1 OFFSET 30)", (identifier, identifier))
            db_conn_instance.commit()
        return jsonify({"success": True})
        
    elif request.method == 'DELETE':
        with db_lock:
            cursor = db_conn_instance.cursor()
            if keyword:
                cursor.execute("DELETE FROM search_history WHERE username = ? AND keyword = ?", (identifier, keyword))
            else:
                cursor.execute("DELETE FROM search_history WHERE username = ?", (identifier,))
            db_conn_instance.commit()
        return jsonify({"success": True})

@app.route('/api/history/view', methods=['GET', 'POST'])
def api_oneplayer_history_view():
    """Lấy danh sách video đã xem gần nhất để hiển thị gợi ý tìm kiếm."""
    identifier = get_identifier() or "default_user"
    limit = min(int(request.args.get('limit', 6)), 50)
    items = []
    with db_lock:
        cursor = db_conn_instance.cursor()
        try:
            cursor.execute(f'''
                SELECT v.id, v.title, v.cover, v.actress, v.genre, v.maker, v.dvd, v.release_date
                FROM history h
                JOIN {VIDEOS_TABLE} v ON h.video_id = v.id
                WHERE h.username = ?
                ORDER BY h.last_watched DESC
                LIMIT ?
            ''', (identifier, limit))
            for r in cursor.fetchall():
                code = (r[6] or '').strip().upper() or r[0]
                items.append({
                    "id": r[0],
                    "code": code,
                    "title": r[1] or code,
                    "cover_url": f"/api/media?id={r[0]}",
                    "actress": r[3] or "",
                    "genres": r[4] or "",
                    "maker": r[5] or "",
                    "studio": r[5] or "",
                    "release_date": r[7] or ""
                })
        except Exception as e:
            custom_log("API", f"⚠️ Error /api/history/view: {e}")
            
    # Nếu chưa có lịch sử cá nhân, lấy video ngẫu nhiên/mới nhất để không bị trống gợi ý
    if not items:
        with db_lock:
            cursor = db_conn_instance.cursor()
            cursor.execute(f"SELECT id, title, cover, actress, genre, maker, dvd, release_date FROM {VIDEOS_TABLE} WHERE (details_fetched = 1 OR cover_fetched = 1) ORDER BY release_date DESC LIMIT ?", (limit,))
            for r in cursor.fetchall():
                code = (r[6] or '').strip().upper() or r[0]
                items.append({
                    "id": r[0],
                    "code": code,
                    "title": r[1] or code,
                    "cover_url": f"/api/media?id={r[0]}",
                    "actress": r[3] or "",
                    "genres": r[4] or "",
                    "maker": r[5] or "",
                    "studio": r[5] or "",
                    "release_date": r[7] or ""
                })
                
    return jsonify({"success": True, "history": items, "items": items})

@app.route('/api/search/suggest_words', methods=['GET'])
def api_oneplayer_suggest_words():
    """Gợi ý từ khóa trực tiếp cho Search Drawer (Diễn viên, Thể loại, Studio, Từ khóa)."""
    q = (request.args.get('q', '') or request.args.get('search_key', '')).strip().lower()
    limit = min(int(request.args.get('limit', 10)), 30)
    words = []
    
    if not q:
        return jsonify({"success": True, "words": []})
        
    try:
        q_no_accents = remove_accents(q)
        with db_lock:
            cursor = db_conn_instance.cursor()
            load_tags_cache_if_needed(cursor)
            
            # 1. Tìm trong tags_cache hỗ trợ cả có dấu và không dấu
            seen_words = set()
            for kw, t, count, low, low_no_accents, sorted_low in tags_cache:
                if q in low or (q_no_accents and q_no_accents in low_no_accents):
                    if kw.lower() not in seen_words:
                        seen_words.add(kw.lower())
                        words.append({"word": kw, "type": t})
                        if len(words) >= limit:
                            break
                            
            # 2. Tìm thêm mã phim (dvd / id) hoặc từ khóa từ tiêu đề
            if len(words) < limit:
                cursor.execute(f"SELECT dvd, title FROM {VIDEOS_TABLE} WHERE upper(dvd) LIKE ? OR upper(title) LIKE ? LIMIT ?", (f"%{q.upper()}%", f"%{q.upper()}%", (limit - len(words)) * 2))
                for r in cursor.fetchall():
                    val = (r[0] or '').strip()
                    if not val:
                        m = re.search(r'([A-Za-z0-9]+-[0-9]+)', r[1] or '')
                        val = m.group(1).upper() if m else ''
                    if not val and r[1]:
                        # Nếu không có dvd/code (như VLXX / SexTop1), trích xuất tiêu đề ngắn gọn
                        val = r[1].strip()[:40]
                    if val and val.lower() not in seen_words:
                        seen_words.add(val.lower())
                        words.append({"word": val, "type": "general"})
                        if len(words) >= limit:
                            break
    except Exception as e:
        custom_log("API", f"⚠️ Error /api/search/suggest_words: {e}")
        
    return jsonify({"success": True, "words": words})


@app.route('/api/identity/check', methods=['POST'])
def identity_check():
    payload = request.get_json(silent=True) or {}
    query = payload.get('query', '').strip()
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT username FROM identities WHERE username = ? OR email = ?", (query, query))
        row = cursor.fetchone()
    return jsonify({"exists": bool(row)})

@app.route('/api/identity/send_otp', methods=['POST'])
def identity_send_otp():
    payload = request.get_json(silent=True) or {}
    query = payload.get('query', '').strip()
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT email FROM identities WHERE username = ? OR email = ?", (query, query))
        row = cursor.fetchone()
    if not row or not row[0]:
        return jsonify({"success": False, "error": "Tài khoản không tồn tại hoặc chưa liên kết email."})
    
    user_email = row[0]
    import random
    otp = str(random.randint(100000, 999999))
    expire = int(time.time()) + 300
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("UPDATE identities SET otp = ?, otp_expire = ? WHERE email = ?", (otp, expire, user_email))
        db_conn_instance.commit()
    
    args_email = getattr(app_args, 'email', None)
    args_email_pass = getattr(app_args, 'emailPass', None)
        
    if not args_email or not args_email_pass:
        return jsonify({"success": False, "error": "Server chưa cấu hình email."})
        
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(f"Mã OTP đăng nhập của bạn là: {otp}\nCó hiệu lực trong 5 phút.")
        msg['Subject'] = 'Mã OTP Định Danh Javtiful Player'
        msg['From'] = args_email
        msg['To'] = user_email
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(args_email, args_email_pass.strip())
        server.send_message(msg)
        server.quit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/identity/register', methods=['POST'])
def identity_register():
    payload = request.get_json(silent=True) or {}
    username = payload.get('username', '').strip()
    password = payload.get('password', '')
    session_id = payload.get('session_id', '') or request.headers.get('Session-Id', '')
    
    if not username or not password:
        return jsonify({"success": False, "error": "Thiếu thông tin."})
        
    with db_lock:
        cursor = db_conn_instance.cursor()
                
        try:
            cursor.execute("INSERT INTO identities (username, email, password, session_id, created_at, verified) VALUES (?, ?, ?, ?, ?, ?)", 
                           (username, None, password, session_id, int(time.time()), 0))
            if session_id:
                cursor.execute("INSERT OR REPLACE INTO user_sessions (username, session_id) VALUES (?, ?)", (username, session_id))
                cursor.execute("UPDATE OR IGNORE history SET username = ? WHERE username = ?", (username, session_id))
                cursor.execute("DELETE FROM history WHERE username = ?", (session_id,))
                cursor.execute("UPDATE OR IGNORE favorites SET username = ? WHERE username = ?", (username, session_id))
                cursor.execute("DELETE FROM favorites WHERE username = ?", (session_id,))
            db_conn_instance.commit()
            token = create_jwt({"username": username})
            return jsonify({"success": True, "username": username, "token": token})
        except sqlite3.IntegrityError:
            return jsonify({"success": False, "error": "Username đã tồn tại."})

@app.route('/api/identity/login', methods=['POST'])
def identity_login():
    payload = request.get_json(silent=True) or {}
    query = payload.get('query', '').strip()
    password = payload.get('password', '')
    otp = payload.get('otp', '')
    session_id = payload.get('session_id', '') or request.headers.get('Session-Id', '')
    
    with db_lock:
        cursor = db_conn_instance.cursor()
        if password:
            cursor.execute("SELECT username FROM identities WHERE (username = ? OR email = ?) AND password = ?", (query, query, password))
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE identities SET session_id = ? WHERE username = ?", (session_id, row[0]))
                if session_id:
                    cursor.execute("INSERT OR REPLACE INTO user_sessions (username, session_id) VALUES (?, ?)", (row[0], session_id))
                    cursor.execute("UPDATE OR IGNORE history SET username = ? WHERE username = ?", (row[0], session_id))
                    cursor.execute("DELETE FROM history WHERE username = ?", (session_id,))
                    cursor.execute("UPDATE OR IGNORE favorites SET username = ? WHERE username = ?", (row[0], session_id))
                    cursor.execute("DELETE FROM favorites WHERE username = ?", (session_id,))
                db_conn_instance.commit()
                token = create_jwt({"username": row[0]})
                return jsonify({"success": True, "username": row[0], "token": token})
            else:
                return jsonify({"success": False, "error": "Sai thông tin đăng nhập."})
        elif otp:
            cursor.execute("SELECT username, otp_expire FROM identities WHERE (username = ? OR email = ?) AND otp = ?", (query, query, otp))
            row = cursor.fetchone()
            if row:
                if row[1] < int(time.time()):
                    return jsonify({"success": False, "error": "OTP đã hết hạn."})
                else:
                    cursor.execute("UPDATE identities SET session_id = ?, otp = NULL, verified = 1 WHERE username = ?", (session_id, row[0]))
                    if session_id:
                        cursor.execute("INSERT OR REPLACE INTO user_sessions (username, session_id) VALUES (?, ?)", (row[0], session_id))
                        cursor.execute("UPDATE OR IGNORE history SET username = ? WHERE username = ?", (row[0], session_id))
                        cursor.execute("DELETE FROM history WHERE username = ?", (session_id,))
                        cursor.execute("UPDATE OR IGNORE favorites SET username = ? WHERE username = ?", (row[0], session_id))
                        cursor.execute("DELETE FROM favorites WHERE username = ?", (session_id,))
                    db_conn_instance.commit()
                    token = create_jwt({"username": row[0]})
                    return jsonify({"success": True, "username": row[0], "token": token})
            else:
                return jsonify({"success": False, "error": "OTP không hợp lệ."})
        else:
            return jsonify({"success": False, "error": "Thiếu password hoặc OTP."})

@app.route('/api/identity/verify_email', methods=['POST'])
def identity_verify_email():
    payload = request.get_json(silent=True) or {}
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    token = auth_header.split(' ')[1]
    jwt_payload = verify_jwt(token)
    if not jwt_payload or 'username' not in jwt_payload:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    username = jwt_payload['username']
    otp = payload.get('otp', '')
    
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT otp_expire FROM identities WHERE username = ? AND otp = ?", (username, otp))
        row = cursor.fetchone()
        if row:
            if row[0] < int(time.time()):
                return jsonify({"success": False, "error": "OTP đã hết hạn."})
            else:
                cursor.execute("UPDATE identities SET otp = NULL, verified = 1 WHERE username = ?", (username,))
                db_conn_instance.commit()
                return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "OTP không hợp lệ."})

@app.route('/api/identity/update_profile', methods=['POST'])
def identity_update_profile():
    payload = request.get_json(silent=True) or {}
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    token = auth_header.split(' ')[1]
    jwt_payload = verify_jwt(token)
    if not jwt_payload or 'username' not in jwt_payload:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
        
    current_username = jwt_payload['username']
    new_password = payload.get('password', '')
    new_email = payload.get('email', '').strip()
    
    with db_lock:
        cursor = db_conn_instance.cursor()
        try:
            if new_email:
                cursor.execute("SELECT username FROM identities WHERE email = ? AND username != ?", (new_email, current_username))
                if cursor.fetchone():
                    return jsonify({"success": False, "error": "Email đã được sử dụng."})
                    
                cursor.execute("SELECT email FROM identities WHERE username = ?", (current_username,))
                row = cursor.fetchone()
                if not row or row[0] != new_email:
                    cursor.execute("UPDATE identities SET email = ?, verified = 0 WHERE username = ?", (new_email, current_username))
                    
            if new_password:
                cursor.execute("SELECT verified FROM identities WHERE username = ?", (current_username,))
                v_row = cursor.fetchone()
                if not v_row or v_row[0] != 1:
                    return jsonify({"success": False, "error": "Cần xác thực email để đổi mật khẩu."})
                cursor.execute("UPDATE identities SET password = ? WHERE username = ?", (new_password, current_username))
            db_conn_instance.commit()
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

@app.route('/api/identity/reset_password', methods=['POST'])
def identity_reset_password():
    payload = request.get_json(silent=True) or {}
    query = payload.get('query', '').strip()
    otp = payload.get('otp', '')
    new_password = payload.get('new_password', '')
    
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT username, otp_expire FROM identities WHERE (username = ? OR email = ?) AND otp = ?", (query, query, otp))
        row = cursor.fetchone()
        if row:
            if row[1] < int(time.time()):
                return jsonify({"success": False, "error": "OTP đã hết hạn."})
            else:
                cursor.execute("UPDATE identities SET password = ?, otp = NULL, verified = 1 WHERE username = ?", (new_password, row[0]))
                db_conn_instance.commit()
                return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "OTP không hợp lệ."})

@app.route('/api/favorites/toggle', methods=['POST'])
def favorites_toggle():
    payload = request.get_json(silent=True) or {}
    identifier = get_identifier()
    if not identifier:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
        
    video_id = payload.get('video_id')
    action = payload.get('action', 'toggle')
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT 1 FROM favorites WHERE username = ? AND video_id = ?", (identifier, video_id))
        exists = cursor.fetchone()
        if exists:
            if action == 'add':
                return jsonify({"success": True, "added": True})
            cursor.execute("DELETE FROM favorites WHERE username = ? AND video_id = ?", (identifier, video_id))
            db_conn_instance.commit()
            return jsonify({"success": True, "added": False})
        else:
            if action == 'remove':
                return jsonify({"success": True, "added": False})
            now_dt = time.strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("INSERT INTO favorites (username, video_id, added_at) VALUES (?, ?, ?)", (identifier, video_id, now_dt))
            db_conn_instance.commit()
            return jsonify({"success": True, "added": True})

@app.route('/api/favorites/status', methods=['GET'])
def favorites_status():
    identifier = get_identifier()
    if not identifier:
        return jsonify({"is_favorited": False})
        
    video_id = request.args.get('video_id')
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT 1 FROM favorites WHERE username = ? AND video_id = ?", (identifier, video_id))
        exists = cursor.fetchone()
    return jsonify({"is_favorited": bool(exists)})

@app.route('/api/history/record', methods=['POST'])
def history_record():
    payload = request.get_json(silent=True) or {}
    identifier = get_identifier()
    if not identifier:
        return jsonify({"success": False, "error": "No identifier provided"}), 400
        
    video_id = payload.get('video_id')
    now_ts = int(time.time())
    with db_lock:
        cursor = db_conn_instance.cursor()
        cursor.execute("SELECT watch_count FROM history WHERE username = ? AND video_id = ?", (identifier, video_id))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE history SET watch_count = ?, last_watched = ? WHERE username = ? AND video_id = ?", (row[0] + 1, now_ts, identifier, video_id))
        else:
            cursor.execute("INSERT INTO history (username, video_id, watch_count, last_watched) VALUES (?, ?, ?, ?)", (identifier, video_id, 1, now_ts))
            
        cursor.execute("INSERT INTO history_logs (video_id, watched_at) VALUES (?, ?)", (video_id, now_ts))
        db_conn_instance.commit()
        return jsonify({"success": True})

@app.route('/api/video/<path:code>/mark_error', methods=['POST'])
def api_mark_error(code):
    data = request.get_json(silent=True) or {}
    custom_log("OnePlayer", f"⚠️ Client báo lỗi video [{code}]: {data.get('reason', '')}")
    return jsonify({"success": True})

@app.route('/api/history/view', methods=['POST'])
def api_history_view():
    return jsonify({"success": True})

@app.route('/api/user/interaction', methods=['POST'])
def api_user_interaction():
    return jsonify({"success": True})

@app.route('/favicon.ico')
def serve_favicon():
    frontend_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend'))
    src = getattr(app_args, 'source', 'missav').lower()
    specific_ico = f"favicon_{src}.ico"
    specific_path = os.path.join(frontend_dir, 'static', specific_ico)
    if os.path.exists(specific_path):
        return send_from_directory(os.path.join(frontend_dir, 'static'), specific_ico, mimetype='image/x-icon')
    return send_from_directory(os.path.join(frontend_dir, 'static'), 'favicon.ico', mimetype='image/x-icon')

@app.route('/manifest.json')
def serve_manifest():
    frontend_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend'))
    return send_from_directory(os.path.join(frontend_dir, 'static'), 'manifest.json', mimetype='application/manifest+json')

@app.route('/<name>.png')
def serve_pwa_icons(name):
    if name in ('icon-192', 'icon-512', 'apple-icon'):
        frontend_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend'))
        return send_from_directory(os.path.join(frontend_dir, 'static'), f"{name}.png", mimetype='image/png')
    return "Not Found", 404

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_html(path):
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        frontend_dir = os.path.abspath(os.path.join(base_dir, '..', 'frontend'))

        if path:
            file_path = os.path.join(frontend_dir, path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                return send_from_directory(frontend_dir, path)

        html_path = os.path.join(frontend_dir, 'index.html')

        with open(html_path, 'rb') as f:
            content = f.read()

        src = getattr(app_args, 'source', 'vod').lower()
        if app_args and hasattr(app_args, 'source'):
            new_title = f"{src.upper()} - Video On Demand"
            content = re.sub(b'<title>.*?</title>', f'<title>{new_title}</title>'.encode('utf-8'), content, count=1, flags=re.IGNORECASE)

        # Gắn favicon riêng biệt cho từng port / source
        favicon_url = f"/static/favicon_{src}.ico"
        content = re.sub(
            rb'<link[^>]*rel=["\'](?:shortcut )?icon["\'][^>]*>',
            f'<link rel="icon" href="{favicon_url}" type="image/x-icon">'.encode('utf-8'),
            content,
            count=1,
            flags=re.IGNORECASE
        )

        return Response(content, mimetype='text/html; charset=utf-8')
    except Exception as e:
        return Response(f"HTML not found: index.html ({e})", status=404)

def migrate_old_database(db_conn, old_db_path):
    if not os.path.exists(old_db_path):
        custom_log("System", f"⚠️ Không tìm thấy file database cũ tại {old_db_path}")
        return
# 
    custom_log("System", f"⏳ Đang gắn (attach) database cũ từ {old_db_path}...")
    try:
        cursor = db_conn.cursor()
        cursor.execute("ATTACH DATABASE ? AS old_db", (old_db_path,))
        
        tables_to_sync = [
            VIDEOS_TABLE, 'media', 'identities', 'user_sessions', 
            'favorites', 'history', 'sync_tasks', 'history_logs', 'search_history'
        ]
        
        for table in tables_to_sync:
            custom_log("System", f"⏳ Đang đồng bộ bảng: {table}...")
            try:
                cursor.execute(f"PRAGMA table_info({table})")
                new_cols = [row[1] for row in cursor.fetchall()]
                
                cursor.execute(f"PRAGMA old_db.table_info({table})")
                old_cols = [row[1] for row in cursor.fetchall()]
                
                if not old_cols:
                    custom_log("System", f"⚠️ Bảng {table} không tồn tại trong DB cũ, bỏ qua.")
                    continue
                    
                common_cols = [col for col in new_cols if col in old_cols]
                cols_str = ", ".join(common_cols)
                
                cursor.execute(f"INSERT OR IGNORE INTO {table} ({cols_str}) SELECT {cols_str} FROM old_db.{table}")
                db_conn.commit()
                custom_log("System", f"✔️ Đã đồng bộ bảng {table}.")
            except Exception as e:
                custom_log("System", f"❌ Lỗi khi đồng bộ bảng {table}: {e}")
        
        cursor.execute("DETACH DATABASE old_db")
        custom_log("System", "✔️ Hoàn tất đồng bộ dữ liệu từ database cũ!")
    except Exception as e:
        custom_log("System", f"❌ Lỗi trong quá trình migration: {e}")

def start_reloader(force_watch: bool = False):
    # Nếu không có cờ watch rõ ràng thì bỏ qua trên Termux để tránh loop execv ngoài ý muốn
    if not force_watch and ("TERMUX_VERSION" in os.environ or os.path.exists("/data/data/com.termux") or os.environ.get("NO_RELOAD")):
        return
    import glob
    def get_mtimes():
        base_dir = os.path.dirname(os.path.abspath(__file__)) or '.'
        files = [__file__] + glob.glob(os.path.join(base_dir, 'source-*.py'))
        mtimes = {}
        for f in files:
            try: mtimes[f] = os.path.getmtime(f)
            except OSError: pass
        return mtimes

    def reloader_thread():
        mtimes = get_mtimes()
        while True:
            time.sleep(2)
            if get_mtimes() != mtimes:
                custom_log("System", "⚠️ Phát hiện thay đổi code, tự động khởi động lại...")
                os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=reloader_thread, daemon=True).start()

def load_source_module(source_name):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, f"source-{source_name}.py")
    if not os.path.exists(file_path):
        custom_log("System", f"❌ Lỗi: Không tìm thấy file {file_path}")
        sys.exit(1)
    
    spec = importlib.util.spec_from_file_location(f"source_{source_name}", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def system_monitor_worker():
    try: import psutil
    except ImportError: return
    while True:
        try:
            cpu = psutil.cpu_percent(interval=None)
            process = psutil.Process(os.getpid())
            ram_mb = process.memory_info().rss / 1048576.0
            nltk_mb = sys.getsizeof(sys.modules.get('nltk')) / 1048576.0 if 'nltk' in sys.modules else 0.0
            custom_log("System", f"✔️RAM chiếm dụng: {ram_mb:.2f}M | NLTK: {nltk_mb:.2f}M | CPU: {cpu}%")
        except Exception: pass
        time.sleep(5)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-sqlite3', type=str, default=None, help="Path to the SQLite3 database file")
    parser.add_argument('-upgrade-all', action='store_true', help="Start scanning from page 1 instead of backlog")
    parser.add_argument('-emailPass', type=str, default="szywozapustydcuw", help="App password for email")
    parser.add_argument('-email', type=str, default="infor.dkeeps@gmail.com", help="Email to send OTP from")
    parser.add_argument('-old-sqlite3', type=str, default="", help="Path to an old SQLite3 database to migrate data from")
    parser.add_argument('-limit-bufer', '-limit-buffer', type=str, default='200M', dest='limit_buffer', help="Limit memory buffer size to avoid Termux killing the process")
    parser.add_argument('-source', type=str, default='javtiful', help="Nguồn crawl dữ liệu (ví dụ: javtiful, missav)")
    parser.add_argument('-news-threads', type=int, default=0, help="Số luồng quét video mới (mặc định 0)")
    parser.add_argument('-detail-threads', type=int, default=0, help="Số luồng lấy chi tiết video (mặc định 0)")
    parser.add_argument('-videos-threads', type=int, default=0, help="Số luồng quét video backlog (mặc định 0)")
    parser.add_argument('-domain', type=str, default=None, help="Tên miền (domain) cho scraper")
    parser.add_argument('-chunk_size', type=str, default='128KB', help="Kích thước chunk proxy (e.g. 128KB)")
    parser.add_argument('-max_connections', type=int, default=30, help="Số luồng connections tối đa")
    parser.add_argument('-proxy-threads', type=int, default=8, help="Số luồng tải file (Proxy đa luồng)")
    parser.add_argument('-max_keepalive', type=int, default=10, help="Số khối buffer keepalive tối đa trong RAM")
    parser.add_argument('-timeout', type=str, default="connect=3.0,read=None", help="Cấu hình timeout proxy")
    parser.add_argument('-nextdjav-db', type=str, default=None, help="Đường dẫn đến file nextdjav.db (để quản lý upload queue và cờ GDrive)")
    parser.add_argument('-w', '--watch', action='store_true', help="Kích hoạt Watchdog auto-restart khi sửa code")
    
    args = parser.parse_args()
    
    env_port = os.environ.get("PORT")
    if not env_port:
        print("[!] LỖI: Vui lòng cấu hình bắt buộc PORT trong file .env")
        sys.exit(1)
    args.port = int(env_port)

    if args.sqlite3 is None:
        if os.name == 'nt':
            cand = f"D:\\Dat\\Database\\{args.source}\\{args.source}.db"
            if os.path.exists(cand):
                args.sqlite3 = cand
            else:
                args.sqlite3 = f"D:\\Database\\{args.source}.db"
        else:
            cand = None
            if os.path.exists(cand):
                args.sqlite3 = cand
            else:
                args.sqlite3 = None
            
    chunk_str = args.chunk_size.upper().replace('B', '')
    if chunk_str.endswith('M'):
        args.chunk_size_bytes = int(float(chunk_str[:-1]) * 1024 * 1024)
    elif chunk_str.endswith('K'):
        args.chunk_size_bytes = int(float(chunk_str[:-1]) * 1024)
    else:
        args.chunk_size_bytes = int(chunk_str)

    connect_timeout = 8.0
    read_timeout = None
    if args.timeout:
        parts = args.timeout.split(',')
        for p in parts:
            if p.startswith('connect='):
                val = p.split('=')[1]
                connect_timeout = float(val) if val.lower() != 'none' else None
            elif p.startswith('read='):
                val = p.split('=')[1]
                read_timeout = float(val) if val.lower() != 'none' else None
    if not read_timeout:
        read_timeout = 60.0
    if connect_timeout and connect_timeout < 5.0:
        connect_timeout = 8.0
    args.parsed_timeout = (connect_timeout, read_timeout)
    
    global db_conn_instance, scraper_instance, app_args, VIDEOS_TABLE
    app_args = args
    VIDEOS_TABLE = f"{args.source}_videos"
    
    # Xóa cache trong bộ nhớ khi khởi động hoặc tải lại để đảm bảo không có dữ liệu cũ
    global tags_cache
    with memory_lock:
        db_buffer['videos'].clear()
        db_buffer['video_urls'].clear()
        db_buffer['media'].clear()
    downloading_media.clear()
    tags_cache = []
    custom_log("System", "✔️ Đã xóa cache trong bộ nhớ khi khởi động.")

    start_reloader(force_watch=getattr(args, 'watch', False))
    threading.Thread(target=system_monitor_worker, daemon=True).start()
    
    source_module = load_source_module(args.source)
    db_conn_instance = get_db_connection(args.sqlite3, args.limit_buffer, source_module)
    if args.old_sqlite3:
        migrate_old_database(db_conn_instance, args.old_sqlite3)
        
    rebuild_tags_fts(db_conn_instance)
    
    # Resolved domain logic:
    # 1. args.domain (từ command line / start.sh) luôn là MỚI NHẤT và ưu tiên số 1
    # 2. Check configs table in DB
    # 3. Fallback to scraper's default
    resolved_domain = None
    if args.domain:
        resolved_domain = args.domain.lower().replace("www.", "")
        custom_log("System", f"ℹ️ Lấy domain bắt buộc từ start.sh (command line): {resolved_domain}")
        with db_lock:
            try:
                cursor = db_conn_instance.cursor()
                cursor.execute("SELECT value FROM configs WHERE key = 'domain'")
                row = cursor.fetchone()
                old_db_domain = row[0].lower().replace("www.", "") if row and row[0] else ""
                
                cursor.execute("INSERT OR REPLACE INTO configs (key, value) VALUES ('domain', ?)", (resolved_domain,))
                if old_db_domain and old_db_domain != resolved_domain:
                    cursor.execute("UPDATE sync_tasks SET url_pattern = replace(url_pattern, ?, ?)", (old_db_domain, resolved_domain))
                    cursor.execute("UPDATE sync_tasks SET url_pattern = replace(url_pattern, ?, ?)", (f"www.{old_db_domain}", resolved_domain))
                db_conn_instance.commit()
                custom_log("System", f"✔️ Đã đồng bộ domain {resolved_domain} vào database.")
            except Exception as e:
                custom_log("System", f"⚠️ Lỗi lưu domain vào DB: {e}")
    else:
        with db_lock:
            try:
                cursor = db_conn_instance.cursor()
                cursor.execute("SELECT value FROM configs WHERE key = 'domain'")
                row = cursor.fetchone()
                if row and row[0]:
                    resolved_domain = row[0]
                    custom_log("System", f"ℹ️ Lấy domain từ database: {resolved_domain}")
            except Exception as e:
                custom_log("System", f"⚠️ Lỗi đọc domain từ DB: {e}")
                
        if not resolved_domain:
            try:
                temp_scraper = source_module.Scraper(db_conn_instance, db_lock, memory_lock, db_buffer, VIDEOS_TABLE, domain=None)
                resolved_domain = temp_scraper.domain
            except Exception:
                resolved_domain = None
            custom_log("System", f"ℹ️ Lấy domain mặc định của source: {resolved_domain}")
            
            if resolved_domain:
                with db_lock:
                    try:
                        cursor = db_conn_instance.cursor()
                        cursor.execute("INSERT OR REPLACE INTO configs (key, value) VALUES ('domain', ?)", (resolved_domain,))
                        db_conn_instance.commit()
                        custom_log("System", f"✔️ Đã lưu domain {resolved_domain} vào database.")
                    except Exception as e:
                        custom_log("System", f"⚠️ Lỗi lưu domain vào DB: {e}")
                        
    scraper_instance = source_module.Scraper(db_conn_instance, db_lock, memory_lock, db_buffer, VIDEOS_TABLE, domain=resolved_domain)
    threading.Thread(target=background_db_worker, args=(db_conn_instance,), daemon=True).start()
    scanner = BackgroundScanner(
        scraper_instance, 
        upgrade_all=args.upgrade_all,
        news_threads=args.news_threads,
        detail_threads=args.detail_threads,
        videos_threads=args.videos_threads
    )
    scanner.start()
    custom_log("System", f"✔️ {args.source.capitalize()} Player worker started at http://localhost:{args.port}")
        
    def graceful_exit(sig, frame):
        custom_log("System", "⚠️ Nhận tín hiệu dừng (Ctrl+C), đang lưu dữ liệu an toàn...")
        if db_conn_instance:
            flush_db_buffer(db_conn_instance)
            try: db_conn_instance.close()
            except Exception: pass
        custom_log("System", "✔️ Đã thoát an toàn.")
        sys.exit(0)
        
    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)

    try:

        app.run(host='0.0.0.0', port=args.port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        graceful_exit(None, None)

if __name__ == '__main__':
    main()
