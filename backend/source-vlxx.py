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
    def __init__(self, db_conn, db_lock, memory_lock, db_buffer, table_name="vlxx_videos", domain=None):
        self.db_conn = db_conn
        self.db_lock = db_lock
        self.memory_lock = memory_lock
        self.db_buffer = db_buffer
        self.table_name = table_name
        self.domain = domain if domain else "vlxx.moi"
        self.session = curl_requests.Session(impersonate="chrome120")
        self.sync_lock = threading.Lock()
        self.referer = f"https://{self.domain}/"
        self.source_name = "VLXX"
        
        with self.db_lock:
            try:
                self.db_conn.execute('''
                    CREATE TABLE IF NOT EXISTS configs (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
                self.db_conn.execute('''
                    CREATE TABLE IF NOT EXISTS play_configs (
                        video_id TEXT PRIMARY KEY,
                        jwplayer_key TEXT,
                        server TEXT,
                        extra_data TEXT
                    )
                ''')
                self.db_conn.commit()
            except:
                pass
                
        threading.Thread(target=self.warm_up, daemon=True).start()

    def load_session(self):
        with self.db_lock:
            cursor = self.db_conn.cursor()
            try:
                cursor.execute("SELECT value FROM configs WHERE key = ?", (f"{self.table_name}_cookies",))
                row = cursor.fetchone()
                if row and row[0]:
                    cookies = json.loads(row[0])
                    for k, v in cookies.items():
                        self.session.cookies.set(k, v, domain=f".{self.domain.replace('www.', '')}")
                    custom_log(self.source_name, f"Đã load session từ database.")
            except Exception:
                pass

    def save_session(self):
        cookies = self.session.cookies.get_dict()
        with self.db_lock:
            cursor = self.db_conn.cursor()
            try:
                cursor.execute("INSERT OR REPLACE INTO configs (key, value) VALUES (?, ?)", (f"{self.table_name}_cookies", json.dumps(cookies)))
                self.db_conn.commit()
            except Exception:
                pass

    def warm_up(self):
        self.load_session()
        fail_count = 0
        while True:
            try:
                custom_log(self.source_name, "Warming up session... ⏳")
                res = self.session.get(f"https://{self.domain}/", timeout=15)
                if res.status_code in [200, 301, 302, 403, 404]:
                    custom_log(self.source_name, "Session is warm. ✔️")
                    fail_count = 0
                    self.save_session()
                else:
                    custom_log(self.source_name, f"Session warm-up unexpected status: {res.status_code} ⚠️")
                    fail_count += 1
            except Exception as e:
                custom_log(self.source_name, f"Session warm-up failed: {e} ❌")
                fail_count += 1
                
            if fail_count >= 3:
                custom_log(self.source_name, "Re-initializing session to bypass Cloudflare... 🔄")
                self.session = curl_requests.Session(impersonate="chrome120")
                fail_count = 0
                
            time.sleep(180)

    def update_sync_tasks_from_menu(self):
        custom_log(self.source_name, f"Khởi tạo sync_tasks cho {self.source_name}...")
        tasks = [
            f"https://{self.domain}/new/{{page}}/", 
            f"https://{self.domain}/jav/{{page}}/",
            f"https://{self.domain}/vietsub/{{page}}/",
            f"https://{self.domain}/khong-che/{{page}}/",
            f"https://{self.domain}/chau-au/{{page}}/"
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
        if page == 1:
            url = url.replace("new/1/", "")
            url = url.replace("1/", "")
            
        custom_log(self.source_name, f"⏳ Syncing {url}")
        try:
            res = self.session.get(url, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            videos = []
            total_pages = 0
            
            nav_links = soup.select('.pagenavi a[data-page]')
            for a in nav_links:
                try:
                    p = int(a.get('data-page'))
                    if p > total_pages: total_pages = p
                except: pass
            if total_pages == 0: total_pages = 2000
            
            items = soup.select('.video-item') 
            now = int(time.time())
            for idx, item in enumerate(items):
                a_tag = item.select_one('a')
                if not a_tag: continue
                
                href = a_tag.get('href', '').strip('/')
                if not href.startswith('video/'): continue
                
                vid_id = href[len('video/'):] 
                title = a_tag.get('title', '')
                dvd = title.split(' ')[0] if title else ''
                
                img_tag = a_tag.select_one('img')
                cover = img_tag.get('data-original') or img_tag.get('src') if img_tag else ''
                if cover and cover.startswith('/'): cover = f"https://{self.domain}" + cover
                    
                release_date = ""
                
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
        if not force_refresh:
            with self.memory_lock:
                url_in_buffer = self.db_buffer['video_urls'].get(vid_id)
            if url_in_buffer: return url_in_buffer
            
            with self.db_lock:
                cursor = self.db_conn.cursor()
                cursor.execute(f"SELECT url FROM {self.table_name} WHERE id = ? AND url IS NOT NULL", (vid_id,))
                row = cursor.fetchone()
                if row and row[0]: return row[0]
        
        numeric_id = vid_id.split('/')[-1]
        if not numeric_id.isdigit():
            return None

        custom_log(self.source_name, f"⏳ Fetching video URL for {vid_id}")

        servers_to_try = [2, 1, 3, 4]
        if force_refresh:
            try:
                with self.db_lock:
                    cursor = self.db_conn.cursor()
                    cursor.execute("SELECT server FROM play_configs WHERE video_id = ?", (vid_id,))
                    row = cursor.fetchone()
                    if row and row[0]:
                        last_server = int(row[0])
                        if last_server in servers_to_try:
                            servers_to_try.remove(last_server)
                            servers_to_try.append(last_server)
            except Exception:
                pass

        fail_count = 0
        while fail_count < 3:
            try:
                ajax_url = f"https://{self.domain}/ajax.php"
                referer_url = f"https://{self.domain}/video/{vid_id}/"
                mp4_url = None
                
                for server_num in servers_to_try:
                    payload = {
                        'vlxx_server': '1',
                        'id': numeric_id,
                        'server': str(server_num)
                    }
                    
                    headers = {
                        'Referer': referer_url,
                        'X-Requested-With': 'XMLHttpRequest',
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
                    }
                    
                    res = self.session.post(ajax_url, data=payload, headers=headers, timeout=15)
                    
                    if res.status_code == 200 and res.text.strip():
                        try:
                            json_data = json.loads(res.text.strip())
                        except Exception:
                            json_data = {}
                        player_html = json_data.get('player')
                        if player_html:
                            soup = BeautifulSoup(player_html, 'html.parser')
                            iframe = soup.find('iframe')
                            if iframe and iframe.get('src'):
                                iframe_url = iframe['src']
                                if iframe_url.startswith('//'): iframe_url = 'https:' + iframe_url
                                
                                embed_res = self.session.get(iframe_url, headers={'Referer': referer_url}, timeout=15)
                                if embed_res.status_code == 200:
                                    unpacked_text = ""
                                    
                                    p_match = re.search(r"return p}\('(.*?)',\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\.split\('\|'\)", embed_res.text)
                                    if p_match:
                                        try:
                                            p_str, a_radix_str, c_count_str, k_words_str = p_match.groups()
                                            p_str = p_str.replace("\\'", "'")
                                            a_radix = int(a_radix_str)
                                            c_count = int(c_count_str)
                                            k_words = k_words_str.split('|')
                                            
                                            def e_base(num, b):
                                                if num == 0: return "0"
                                                chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                                                res_str = ""
                                                while num > 0:
                                                    res_str = chars[num % b] + res_str
                                                    num //= b
                                                return res_str
                                            
                                            for i in range(c_count - 1, -1, -1):
                                                if i < len(k_words) and k_words[i]:
                                                    word = e_base(i, a_radix)
                                                    p_str = re.sub(r'\b' + word + r'\b', k_words[i], p_str)
                                            unpacked_text = p_str
                                        except Exception: pass

                                    search_text = unpacked_text if unpacked_text else embed_res.text
                                    match = re.search(r'["\']?file["\']?\s*:\s*["\'](https?://[^"\']+)["\']', search_text) or \
                                            re.search(r'["\'](https?://[^"\']+\.(?:m3u8|mp4|vl)[^"\']*)["\']', search_text)
                                    if match: 
                                        mp4_url = match.group(1)
                                        
                                        jw_key = ""
                                        key_match = re.search(r'jwplayer\.key\s*=\s*["\']([^"\']+)["\']', embed_res.text)
                                        if key_match:
                                            jw_key = key_match.group(1)
                                            mp4_url = f"{mp4_url}#jwkey={jw_key}"
                                            
                                        with self.db_lock:
                                            cursor = self.db_conn.cursor()
                                            cursor.execute("INSERT OR REPLACE INTO play_configs (video_id, jwplayer_key, server) VALUES (?, ?, ?)", (vid_id, jw_key, str(server_num)))
                                            self.db_conn.commit()
                                            
                                        break 
                                        
                if mp4_url:
                    with self.db_lock:
                        cursor = self.db_conn.cursor()
                        cursor.execute(f"UPDATE {self.table_name} SET url = ? WHERE id = ?", (mp4_url, vid_id))
                        self.db_conn.commit()
                    with self.memory_lock:
                        self.db_buffer['video_urls'][vid_id] = mp4_url
                    return mp4_url
                else:
                    custom_log(self.source_name, f"⚠️ Không tìm thấy link video trong player.")
                    
            except Exception as e:
                custom_log(self.source_name, f"❌ Lỗi lấy link (lần {fail_count + 1}): {e}")
                fail_count += 1
                if fail_count < 3:
                    self.session = curl_requests.Session(impersonate="chrome120")
                    self.load_session()
                    time.sleep(1)
                    
        return None

    def sync_video_details(self, vid_id):
        with self.sync_lock:
            url = f"https://{self.domain}/video/{vid_id}/"
            try:
                res = self.session.get(url, timeout=15)
                if res.status_code != 200:
                    with self.db_lock:
                        cursor = self.db_conn.cursor()
                        cursor.execute(f"UPDATE {self.table_name} SET details_fetched = -1 WHERE id = ?", (vid_id,))
                        self.db_conn.commit()
                    return False
                    
                custom_log(self.source_name, f"🔍 Đang bóc tách dữ liệu chi tiết cho video: {vid_id}...")

                soup = BeautifulSoup(res.text, 'html.parser')
                
                details_parts = []
                for sel in ['.video-tags', '.actress-tag', '.video-description']:
                    tag = soup.select_one(sel)
                    if tag:
                        clean_text = re.sub(r'\s+', ' ', tag.text).strip()
                        if clean_text:
                            details_parts.append(clean_text)
                details = " | ".join(details_parts)
                
                actress_arr = [a.text.strip() for a in soup.select('div.actress-tag a') if a.text.strip()]
                genre_arr = [a.text.strip() for a in soup.select('div.category-tag a') if a.text.strip()]
                
                code_tag = soup.select_one('span.video-code')
                dvd = code_tag.text.strip() if code_tag else ""
                
                actress_str = ", ".join(actress_arr)
                genre_str = ", ".join(genre_arr)
                
                with self.db_lock:
                    cursor = self.db_conn.cursor()
                    if dvd:
                        cursor.execute(f'''UPDATE {self.table_name} SET actress = ?, genre = ?, details = ?, dvd = ?, details_fetched = 1 WHERE id = ?''', (actress_str, genre_str, details, dvd, vid_id))
                    else:
                        cursor.execute(f'''UPDATE {self.table_name} SET actress = ?, genre = ?, details = ?, details_fetched = 1 WHERE id = ?''', (actress_str, genre_str, details, vid_id))
                    self.db_conn.commit()
                    
                custom_log(self.source_name, f"✔️ Bóc tách xong {vid_id}: [💃 {len(actress_arr)} actress | 🎭 {len(genre_arr)} genre | 📝 {'Có' if details else 'Không'} details | 📀 {dvd if dvd else 'Trống'}]")
                return True
            except Exception as e:
                custom_log(self.source_name, f"❌ Lỗi lấy chi tiết video {vid_id}: {e}")
                with self.db_lock:
                    cursor = self.db_conn.cursor()
                    cursor.execute(f"UPDATE {self.table_name} SET details_fetched = -1 WHERE id = ?", (vid_id,))
                    self.db_conn.commit()
                return False
                
    def clean_keywords(self, text):
        if not text:
            return []
        text = re.sub(r'[^\w\s]', ' ', text.lower())
        words = text.split()
        
        try:
            import nltk
            try:
                nltk.data.find('corpora/stopwords')
            except LookupError:
                nltk.download('stopwords', quiet=True)
            from nltk.corpus import stopwords
            stop_words = set(stopwords.words('english'))
            try:
                stop_words.update(stopwords.words('vietnamese'))
            except Exception:
                pass
        except ImportError:
            stop_words = {'the', 'is', 'are', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'this', 'that'}

        vi_stopwords = {'và', 'của', 'các', 'có', 'được', 'cho', 'trong', 'đã', 'một', 'với', 'những', 'là', 'như', 'hay', 'đang', 'nhưng', 'tại', 'để', 'từ', 'khi', 'làm', 'đến', 'sự', 'này', 'ra', 'phải', 'người', 'về', 'sau', 'rằng', 'chỉ', 'cũng', 'nhiều', 'việc', 'hơn', 'mới', 'vì', 'nếu', 'lại', 'rất', 'còn', 'bởi', 'thì', 'lên', 'đi', 'nào', 'sẽ', 'đó', 'thể', 'theo', 'mình', 'qua', 'phim', 'sex', 'jav', 'vietsub', 'không', 'che', 'hd', 'vlxx', 'full', 'bản', 'đẹp', 'nhất'}
        stop_words.update(vi_stopwords)
        
        words = [w for w in words if w not in stop_words and not w.isdigit() and len(w) > 2]
        return list(dict.fromkeys(words))