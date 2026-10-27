# nextdjav-cdn: Media Server & VOD Instance Nodes

Hệ thống xem video và crawl dữ liệu đa nguồn (VOD) hiệu suất cao, tích hợp server proxy phát đa phương tiện HLS/m3u8, cơ chế phục vụ ảnh bìa **Segment Bin** không tốn RAM và giao diện web hiện đại.

---

## 📌 Tính Năng Nổi Bật Mới Nhất:
- **Phục vụ ảnh bìa Segment Bin (`*_covers_0001.bin`)**: Đọc ảnh trực tiếp qua con trỏ nhị phân `seek-and-read`, không lưu BLOB trong SQLite, tốc độ phản hồi < 1ms và không ngốn RAM.
- **Proxy Streaming Đa Nguồn M3U8 (`/api/proxy`)**: Tự động giải mã và gắn Referer linh hoạt (`playergo.top` -> `missav99.com`, `surrit.com` -> `missav.ws`), khắc phục hoàn toàn lỗi chặn phát trên trình duyệt.
- **Tự động thích ứng Schema VOD (Resilient Fallback Query)**: Kiểm tra động `PRAGMA table_info` trước khi SELECT các cột cờ crawl (`details_fetched`, `cover_fetched`), ngăn chặn hoàn toàn lỗi crash SQLite khi nguồn chưa crawl đủ metadata.
- **Tự động nạp Cấu hình Root (.env)**: Đồng bộ với `.env` tại thư mục gốc cha `termux-services`, không phụ thuộc giá trị mặc định.

---

## 🌐 Danh Sách Cổng Chuẩn Hệ Sinh Thái (Ports 5012 - 5016):
- `5010`: `nextdjav-admin` (Master Admin & Coordinator)
- `5011`: `nextdjav-gdrive` (Google Drive Streamer & Vault Decrypt)
- `5012`: `nextdjav-cdn` - Javtiful (`PORT_JAVTIFUL`)
- `5013`: `nextdjav-cdn` - MissAV (`PORT_MISSAV`)
- `5014`: `nextdjav-cdn` - VLXX (`PORT_VLXX`)
- `5015`: `nextdjav-cdn` - Sextop1 (`PORT_SEXTOP1`)
- `5016`: `nextdjav-cdn` - Javguru (`PORT_JAVGURU`)

---

## 🚀 Khởi Chạy:

Khởi chạy tập trung tự động tất cả các instance VOD theo cấu hình cổng trong `.env`:
```powershell
python start.py
```

Hoặc khởi chạy riêng lẻ từng nguồn:
```powershell
python backend/server.py -source javtiful
python backend/server.py -source missav  
python backend/server.py -source vlxx    
python backend/server.py -source sextop1 
python backend/server.py -source javguru
```
