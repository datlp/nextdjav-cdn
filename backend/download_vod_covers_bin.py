import os
import sys
import time
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as curl_requests

# Đảm bảo UTF-8 cho Windows console
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Cấu hình danh sách các kho database và segment bin
SOURCES = {
    'vlxx': {
        'db_path': r'D:\Dat\Database\vlxx\vlxx.db',
        'table_name': 'vlxx_videos',
        'bin_path': r'D:\Dat\Database\vlxx\vlxx_covers_0001.bin',
        'bin_id': 1,
        'referer': 'https://vlxx.net/',
        'workers': 32,
        'timeout': 10
    },
    'javtiful': {
        'db_path': r'D:\Dat\Database\javtiful\javtiful.db',
        'table_name': 'javtiful_videos',
        'bin_path': r'D:\Dat\Database\javtiful\javtiful_covers_0001.bin',
        'bin_id': 1,
        'referer': 'https://javtiful.com/',
        'workers': 48,
        'timeout': 10
    },
    'missav': {
        'db_path': r'D:\Dat\Database\termux-services\missav.db',
        'table_name': 'missav_videos',
        'bin_path': r'D:\Dat\Database\termux-services\missav\missav_covers_0001.bin',
        'bin_id': 1,
        'referer': 'https://missav.ai/',
        'workers': 64,
        'timeout': 10
    }
}

class CoverDownloader:
    def __init__(self, source_key: str, config: dict):
        self.source_key = source_key
        self.config = config
        self.db_path = config['db_path']
        self.table = config['table_name']
        self.bin_path = config['bin_path']
        self.bin_id = config['bin_id']
        self.referer = config['referer']
        self.workers = config['workers']
        self.timeout = config['timeout']
        
        self.bin_lock = threading.Lock()
        self.db_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        
        self.processed = 0
        self.downloaded = 0
        self.failed = 0
        self.bytes_downloaded = 0
        self.stop_event = threading.Event()
        
        # Thread-local curl session
        self.local_storage = threading.local()

    def get_session(self):
        if not hasattr(self.local_storage, 'session'):
            self.local_storage.session = curl_requests.Session(impersonate="chrome120")
            self.local_storage.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': self.referer,
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
            })
        return self.local_storage.session

    def download_item(self, item):
        vid_id, cover_url = item
        url = cover_url.strip()
        if url.startswith('//'):
            url = 'https:' + url
        elif not url.startswith('http'):
            url = f"{self.referer.rstrip('/')}/{url.lstrip('/')}"

        session = self.get_session()
        for attempt in range(2):
            try:
                res = session.get(url, timeout=self.timeout)
                if res.status_code == 200 and len(res.content) > 100:
                    return {"id": vid_id, "data": res.content, "success": True}
                elif res.status_code == 404:
                    return {"id": vid_id, "success": False, "is_404": True}
            except Exception:
                time.sleep(0.3)
                continue
        return {"id": vid_id, "success": False, "is_404": False}

    def monitor(self, total_tasks: int):
        t0 = time.time()
        last_t = t0
        last_b = 0
        last_p = 0
        
        while not self.stop_event.is_set():
            time.sleep(1.0)
            now = time.time()
            dt = now - last_t
            if dt <= 0:
                continue
            
            with self.stats_lock:
                p = self.processed
                d = self.downloaded
                f = self.failed
                b = self.bytes_downloaded

            dp = p - last_p
            db = b - last_b
            mbps = (db * 8) / (dt * 1_000_000)
            rps = dp / dt
            pct = (p / total_tasks * 100) if total_tasks > 0 else 0
            
            last_t = now
            last_b = b
            last_p = p

            sys.stdout.write(
                f"\r\033[K[{self.source_key.upper()}] {pct:5.1f}% ({p:,}/{total_tasks:,}) | "
                f"Rate: {rps:4.0f} img/s | Throughput: {mbps:5.1f} Mbps | "
                f"OK: {d:,} | Fail: {f:,}"
            )
            sys.stdout.flush()

    def run(self, limit: int = None):
        print(f"\n🚀 Bắt đầu tải cover cho nguồn: {self.source_key.upper()}")
        print(f"📂 DB: {self.db_path}")
        print(f"📦 BIN: {self.bin_path}")
        
        # 1. Truy vấn các video chưa có trong segment bin
        conn = sqlite3.connect(self.db_path, timeout=60.0)
        cur = conn.cursor()
        query = f"SELECT id, cover FROM {self.table} WHERE cover IS NOT NULL AND cover != '' AND (cover_length IS NULL OR cover_length = 0)"
        if limit:
            query += f" LIMIT {limit}"
        cur.execute(query)
        rows = cur.fetchall()
        conn.close()

        total = len(rows)
        print(f"📊 Số lượng cover cần tải: {total:,}")
        if total == 0:
            print(f"✅ Nguồn {self.source_key} đã có đầy đủ 100% cover trong file bin!")
            return

        # Đảm bảo thư mục lưu bin tồn tại
        os.makedirs(os.path.dirname(os.path.abspath(self.bin_path)), exist_ok=True)

        # Mở luồng monitor
        self.stop_event.clear()
        mon = threading.Thread(target=self.monitor, args=(total,), daemon=True)
        mon.start()

        # Batch buffer
        batch_items = []
        BATCH_SIZE = 100

        def flush_batch():
            nonlocal batch_items
            if not batch_items:
                return
            
            # Ghi bytes vào Segment Bin file
            db_updates = []
            with self.bin_lock:
                with open(self.bin_path, "a+b") as bf:
                    pos = bf.tell()
                    for item in batch_items:
                        v_id = item['id']
                        c_bytes = item['data']
                        length = len(c_bytes)
                        offset = pos
                        bf.write(c_bytes)
                        pos += length
                        db_updates.append((self.bin_id, offset, length, v_id))

            # Cập nhật metadata vị trí vào SQLite
            with self.db_lock:
                c = sqlite3.connect(self.db_path, timeout=60.0)
                c.execute("PRAGMA journal_mode = WAL;")
                c.execute("PRAGMA synchronous = NORMAL;")
                c.executemany(f"UPDATE {self.table} SET cover_bin_id = ?, cover_offset = ?, cover_length = ? WHERE id = ?", db_updates)
                c.commit()
                c.close()

            batch_items.clear()

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self.download_item, r): r for r in rows}
            for fut in as_completed(futures):
                res = fut.result()
                with self.stats_lock:
                    self.processed += 1
                    if res.get('success'):
                        self.downloaded += 1
                        self.bytes_downloaded += len(res['data'])
                        batch_items.append(res)
                        if len(batch_items) >= BATCH_SIZE:
                            flush_batch()
                    else:
                        self.failed += 1

            # Flush còn lại
            flush_batch()

        self.stop_event.set()
        time.sleep(1.2)
        print(f"\n🎉 Hoàn thành {self.source_key.upper()}: Tải thành công {self.downloaded:,}/{total:,} | Lỗi: {self.failed:,}\n")

if __name__ == '__main__':
    target = sys.argv[1].lower() if len(sys.argv) > 1 else 'all'
    if target == 'all':
        for s_name, s_cfg in SOURCES.items():
            downloader = CoverDownloader(s_name, s_cfg)
            downloader.run()
    elif target in SOURCES:
        downloader = CoverDownloader(target, SOURCES[target])
        downloader.run()
    else:
        print(f"Lựa chọn không hợp lệ: {target}. Chọn một trong: all, vlxx, javtiful, missav")
