# Crawl VOD - Video On Demand System

Hệ thống xem video và crawl dữ liệu đa nguồn (VOD) hiệu suất cao, tích hợp server proxy phát đa phương tiện HLS/m3u8, cơ chế phục vụ ảnh bìa **Segment Bin** không tốn RAM và giao diện web hiện đại.

---

## 📌 Tính Năng Nổi Bật Mới Nhất:
- **Phục vụ ảnh bìa Segment Bin (`*_covers_0001.bin`)**: Đọc ảnh trực tiếp qua con trỏ nhị phân `seek-and-read`, không lưu BLOB trong SQLite, tốc độ phản hồi < 1ms và không ngốn RAM.
- **Proxy Streaming Đa Nguồn M3U8 (`/api/proxy`)**: Tự động giải mã và gắn Referer linh hoạt (`playergo.top` -> `missav99.com`, `surrit.com` -> `missav.ws`), khắc phục hoàn toàn lỗi chặn phát trên trình duyệt.
- **Phân giải URL Video thông minh (`/api/video/<path:code>`)**: Hỗ trợ đầy đủ các ID dạng path chứa dấu gạch chéo `/` (như VLXX), tìm kiếm chính xác theo cả DVD code và tiêu đề.
- **Tương thích hoàn toàn Termux Android & Windows**: Đệm bộ nhớ SQLite WAL, tối ưu cho thiết bị di động.

---

## 🌐 Danh Sách Cổng Chuẩn Hệ Sinh Thái:
- `3010`: `nextdjav-admin` (Admin server / Watchdog & Crawler)
- `3011`: `nextdjav` (Dedicated GDrive OnePlayer)
- `3012`: `crawl-vod` - Javtiful (`javtiful.com`)
- `3013`: `crawl-vod` - MissAV (`missav.ws`)
- `3014`: `crawl-vod` - VLXX (`vlxx.phd`)
- `3015`: `crawl-vod` - Sextop1 (`sextop1.spa`)

---

## 🚀 Khởi Chạy:

### 💻 Trên Windows (PowerShell):
```powershell
python backend/server.py -source javtiful
python backend/server.py -source missav  
python backend/server.py -source vlxx    
python backend/server.py -source sextop1 
```

### 📱 Trên Android (Termux):
```bash
python /sdcard/Projects/crawl-vod/backend/server.py -source javtiful &
python /sdcard/Projects/crawl-vod/backend/server.py -source missav   &
python /sdcard/Projects/crawl-vod/backend/server.py -source vlxx     &
python /sdcard/Projects/crawl-vod/backend/server.py -source sextop1  &
```
