import time
import datetime
import re
import json
import threading
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

def parse_release_date(date_str):
    if not date_str:
        return ""
    date_str = date_str.strip().lower()
    now = datetime.datetime.now()
    
    if date_str == 'just now' or date_str == 'vừa xong':
        return now.strftime('%Y-%m-%d %H:%M:%S')

    if re.match(r'^\d{1,2}:\d{2}:\d{2}$', date_str) or re.match(r'^\d{1,2}:\d{2}$', date_str):
        return ""

    if re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$', date_str):
        return date_str
        
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return f"{date_str} 00:00:00"
        
    match_en = re.match(r'^(\d+)\s+(year|month|week|day|hour|minute|second)s?\s+ago$', date_str)
    match_vi = re.match(r'^(\d+)\s+(năm|tháng|tuần|ngày|giờ|phút|giây)\s+trước$', date_str)
    
    if match_en or match_vi:
        if match_en:
            val = int(match_en.group(1))
            unit = match_en.group(2)
        else:
            val = int(match_vi.group(1))
            unit_vi = match_vi.group(2)
            unit_map = {'năm': 'year', 'tháng': 'month', 'tuần': 'week', 'ngày': 'day', 'giờ': 'hour', 'phút': 'minute', 'giây': 'second'}
            unit = unit_map[unit_vi]
            
        if unit == 'year': dt = now - datetime.timedelta(days=val*365)
        elif unit == 'month': dt = now - datetime.timedelta(days=val*30)
        elif unit == 'week': dt = now - datetime.timedelta(weeks=val)
        elif unit == 'day': dt = now - datetime.timedelta(days=val)
        elif unit == 'hour': dt = now - datetime.timedelta(hours=val)
        elif unit == 'minute': dt = now - datetime.timedelta(minutes=val)
        elif unit == 'second': dt = now - datetime.timedelta(seconds=val)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
        
    if re.match(r'^\d{2}-\d{2}-\d{2}$', date_str):
        parts = date_str.split('-')
        year = int(parts[0])
        if year < 100: year += 2000
        return f"{year}-{parts[1]}-{parts[2]} 00:00:00"

    return date_str

class Scraper:
    def __init__(self, db_conn, db_lock, memory_lock, db_buffer, table_name="javtiful_videos", domain=None):
        self.db_conn = db_conn
        self.db_lock = db_lock
        self.memory_lock = memory_lock
        self.db_buffer = db_buffer
        self.table_name = table_name
        self.domain = domain if domain else "javtiful.com"
        self.session = curl_requests.Session(impersonate="chrome120")
        self.sync_lock = threading.Lock()
        self.referer = f"https://{self.domain}/"
        self.source_name = "Javtiful"
        self.url_cache = {}  # {vid_id: (url, expiry_ts)}

    def update_sync_tasks_from_menu(self):
        custom_log(self.source_name, f"Khởi tạo sync_tasks cho {self.source_name}...")
        tasks = [
            f"https://{self.domain}/category/chinese-av?page={{page}}",
            f"https://{self.domain}/censored?page={{page}}",
            f"https://{self.domain}/uncensored?page={{page}}",
            f"https://{self.domain}/reducing-mosaic?page={{page}}",
            f"https://{self.domain}/videos?page={{page}}"
        ]
        with self.db_lock:
            cursor = self.db_conn.cursor()
            for url in tasks:
                cursor.execute("INSERT OR IGNORE INTO sync_tasks (url_pattern) VALUES (?)", (url,))
            self.db_conn.commit()

    def sync_list_page(self, url_pattern, page):
        with self.sync_lock:
            return self._sync_list_page(url_pattern, page)
            
    def _sync_list_page(self, url_pattern, page):
        url = url_pattern.replace("{page}", str(page))
        if page == 1 and "?page=1" in url:
            url = url.replace("?page=1", "")
            
        custom_log(self.source_name, f"⏳ Syncing {url}")
        try:
            res = self.session.get(url, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            videos = []
            
            total_pages = 0
            for a in soup.select('a.front-pagination-link'):
                try:
                    num = int(a.text.strip())
                    if num > total_pages:
                        total_pages = num
                except:
                    pass

            items = soup.select('article.front-video-card')
            now = int(time.time())
            for idx, item in enumerate(items):
                a_tag = item.select_one('a')
                if not a_tag: continue
                href = a_tag.get('href', '')
                if '/video/' not in href: continue
                
                parts = href.split('/video/')
                if len(parts) < 2: continue
                vid_id = parts[1]
                
                img_tag = a_tag.select_one('img')
                cover = img_tag.get('data-front-lazy-src') or img_tag.get('src') if img_tag else ''
                if cover and cover.startswith('/'):
                    cover = f"https://{self.domain}" + cover
                
                title_tag = item.select_one('a.front-video-title')
                title = title_tag.text.strip() if title_tag else vid_id
                dvd = title.split(' ')[0] if title else ''
                
                release_date = ""
                meta_div = item.select_one('.front-video-meta')
                if meta_div:
                    spans = meta_div.select('span')
                    if len(spans) >= 3:
                        release_date = spans[2].text.strip()
                
                release_date = parse_release_date(release_date)
                
                if vid_id and cover:
                    pseudo_time = now - (page * 10000) - idx
                    added_at_dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(pseudo_time))
                    videos.append((vid_id, title, cover, added_at_dt, release_date, dvd))
                    
            with self.db_lock:
                cursor = self.db_conn.cursor()
                new_count = 0
                for vid in videos:
                    vid_id = vid[0]
                    cursor.execute(f"SELECT id FROM {self.table_name} WHERE id = ?", (vid_id,))
                    if not cursor.fetchone():
                        with self.memory_lock:
                            if vid_id not in self.db_buffer['videos']:
                                new_count += 1
                    
                with self.memory_lock:
                    for vid in videos:
                        vid_id = vid[0]
                        self.db_buffer['videos'][vid_id] = {
                            'id': vid[0], 'title': vid[1], 'cover': vid[2], 'added_at': vid[3], 'release_date': vid[4], 'dvd': vid[5]
                        }
            custom_log(self.source_name, f"{self.source_name} {page} {len(videos)} video{'s' if len(videos) != 1 else ''}")
            return new_count, len(videos), total_pages
        except Exception as e:
            custom_log(self.source_name, f"❌ Sync failed: {e}")
            return 0, -1, 0

    def get_video_url(self, vid_id, force_refresh=False):
        now_ts = time.time()
        if not force_refresh:
            cached = self.url_cache.get(vid_id)
            if cached and cached[1] > now_ts:
                return cached[0]
            
        url = f"https://{self.domain}/video/{vid_id}"
        custom_log(self.source_name, f"⏳ Fetching video URL for {vid_id}")
        try:
            res = self.session.get(url, timeout=15)
            mp4_url = None
            match = re.search(r'<script[^>]*id=["\']frontWatchConfig["\'][^>]*>\s*(.*?)\s*</script>', res.text, re.DOTALL)
            if match:
                config = json.loads(match.group(1))
                sources = config.get('playerSources', [])
                if sources: mp4_url = sources[0]['src']
            if not mp4_url:
                soup = BeautifulSoup(res.text, 'html.parser')
                source = soup.select_one('video source')
                if source and source.get('src'): mp4_url = source.get('src')
            if mp4_url:
                # Fast-stream URL Javtiful có token hết hạn, lưu RAM cache 3 giờ
                self.url_cache[vid_id] = (mp4_url, now_ts + 10800)
                return mp4_url
            return None
        except Exception as e:
            custom_log(self.source_name, f"❌ Failed to get video URL for {vid_id}: {e}")
            return None

    def sync_video_details(self, vid_id):
        with self.sync_lock:
            url = f"https://{self.domain}/video/{vid_id}"
            try:
                res = self.session.get(url, timeout=15)
                if res.status_code != 200:
                    with self.db_lock:
                        cursor = self.db_conn.cursor()
                        cursor.execute(f"UPDATE {self.table_name} SET details_fetched = -1 WHERE id = ?", (vid_id,))
                        self.db_conn.commit()
                    return False
                soup = BeautifulSoup(res.text, 'html.parser')
                actress_arr, genre_arr, maker, details, release_date = [], [], "", "", ""
                for block in soup.select('.front-watch-detail'):
                    strong = block.select_one('strong')
                    if not strong: continue
                    label = strong.text.strip().lower()
                    if 'actress' in label:
                        actress_arr = [a.text.strip() for a in block.select('.front-watch-actor-card span') if a.text.strip()]
                    elif 'tags' in label or 'categories' in label:
                        genre_arr = [t.text.strip() for t in block.select('a') if t.text.strip() and t.text.strip() not in genre_arr]
                    elif 'channel' in label:
                        a_tag = block.select_one('a')
                        if a_tag: maker = a_tag.text.strip()
                    elif 'added on' in label or 'date' in label:
                        time_tag = block.select_one('time')
                        if time_tag and time_tag.get('datetime'): release_date = time_tag.get('datetime').split('T')[0]
                release_date = parse_release_date(release_date)
                desc_meta = soup.find('meta', {'name': 'description'})
                if desc_meta: details = desc_meta.get('content', '')
                actress_str = ", ".join(actress_arr)
                genre_str = ", ".join(genre_arr)
                with self.db_lock:
                    cursor = self.db_conn.cursor()
                    if release_date:
                        cursor.execute(f'''UPDATE {self.table_name} SET actress = ?, genre = ?, maker = ?, details = ?, release_date = ?, details_fetched = 1 WHERE id = ?''', (actress_str, genre_str, maker, details, release_date, vid_id))
                    else:
                        cursor.execute(f'''UPDATE {self.table_name} SET actress = ?, genre = ?, maker = ?, details = ?, details_fetched = 1 WHERE id = ?''', (actress_str, genre_str, maker, details, vid_id))
                    self.db_conn.commit()
                custom_log(self.source_name, f"{self.source_name} {vid_id} {len(actress_arr)} actress{'es' if len(actress_arr) != 1 else ''}, {len(genre_arr)} genre{'s' if len(genre_arr) != 1 else ''}, {maker}")
                return True
            except Exception as e:
                custom_log(self.source_name, f"❌ Lỗi lấy chi tiết video {vid_id}: {e}")
                with self.db_lock:
                    cursor = self.db_conn.cursor()
                    cursor.execute(f"UPDATE {self.table_name} SET details_fetched = -1 WHERE id = ?", (vid_id,))
                    self.db_conn.commit()
                return False