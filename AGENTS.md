# Quy tắc Phát Triển & Vận Hành Crawl-VOD (BẮT BUỘC TIẾNG VIỆT)

## 🛑 Quy tắc tự động hóa Git:
- **KHÔNG TỰ ĐỘNG** thực hiện lệnh `git commit` hoặc `git push` lên GitHub sau khi chỉnh sửa code hoặc hoàn thành công việc.
- Chỉ thực hiện `git commit` hoặc `git push` khi người dùng **yêu cầu trực tiếp**.

---

## 📝 Quy tắc Git Commit Message (BẮT BUỘC TIẾNG VIỆT)

TẤT CẢ các Git commit message PHẢI ĐƯỢC VIẾT HOÀN TOÀN BẰNG **TIẾNG VIỆT** theo định dạng Conventional Commits.

### Format:
`<type>(<scope>): <mô tả tổng quan bằng tiếng Việt>`

`<dòng trống>`
`- Frontend:`
`  + <chi tiết task frontend bằng tiếng Việt>`
`- Backend:`
`  + <chi tiết task backend bằng tiếng Việt>`

---

## 📍 Danh Sách Cổng (Ports) & Khởi Chạy Chuẩn Toàn Hệ Sinh Thái:

### 🌐 Cổng tiêu chuẩn hệ thống (Standard Ecosystem Ports 5012 - 5016):
- `5010`: `nextdjav-admin` (Master Coordinator & Crawler)
- `5011`: `nextdjav-gdrive` (Dedicated GDrive OnePlayer)
- `5012`: `nextdjav-cdn` - Javtiful (`PORT_JAVTIFUL`)
- `5013`: `nextdjav-cdn` - MissAV (`PORT_MISSAV`)
- `5014`: `nextdjav-cdn` - VLXX (`PORT_VLXX`)
- `5015`: `nextdjav-cdn` - Sextop1 (`PORT_SEXTOP1`)
- `5016`: `nextdjav-cdn` - Javguru (`PORT_JAVGURU`)

---

## ⚡ Chuẩn Đọc Ảnh Bìa Segment Bin & Proxy Playback:
1. **Phục vụ ảnh bìa qua Segment Bin (`*_covers_0001.bin`)**:
   - Mọi server crawl-vod (`5012 - 5016`) đọc trực tiếp ảnh từ file `.bin` bằng con trỏ `cover_offset` và `cover_length`.
   - Giữ RAM luôn tối ưu dưới 200MB, tốc độ phản hồi ảnh < 1ms.
2. **Bộ giải mã URL Video (`/api/video/<path:code>`)**:
   - Hỗ trợ ID dạng đường dẫn (ví dụ: `ban-tinh-8-lan-vao-lon-sep-nu-xinh-dam/3142` của VLXX).
   - Tự động bóc tách m3u8 và mã token động cho trình phát.
3. **Proxy Streaming (`/api/proxy`)**:
   - Gắn dynamic Referer thích hợp cho từng domain CDN (MissAV: `playergo.top` dùng `missav99.com`, `surrit.com` dùng `missav.ws`).
4. **Resilient Schema & Fallback Query (BẮT BUỘC)**:
   - Khi truy vấn VOD fallback, phải dùng `PRAGMA table_info` kiểm tra động các cột cờ crawl (`details_fetched`, `cover_fetched`) trước khi đưa vào mệnh đề `SELECT / WHERE` để tránh crash DB.
   - Luôn nạp file `.env` từ thư mục gốc cha `termux-services`.
5. **Ngữ Cảnh Lịch Sử & Milestones (Context Storage)**:
   - Toàn bộ lịch sử phát triển, quyết định kiến trúc và bugfix của repo này được lưu tại:
     `D:\Dat\Database\chat-history/nextdjav-cdn/history.db` (chế độ SQLite WAL).
   - Agent nạp context kỹ thuật từ bảng `milestones` với version active `v2.1-vault-and-mesh`.
