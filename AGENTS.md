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

### 🌐 Cổng tiêu chuẩn hệ thống (Standard Ecosystem Ports):
- `3010`: `nextdjav-admin` (Admin server / Watchdog & Crawler)
- `3011`: `nextdjav-gdrive` (Dedicated GDrive OnePlayer)
- `3012`: `nextdjav-cdn` - Javtiful (`javtiful.com`)
- `3013`: `nextdjav-cdn` - MissAV (`missav.ws`)
- `3014`: `nextdjav-cdn` - VLXX (`vlxx.phd`)
- `3015`: `nextdjav-cdn` - Sextop1 (`sextop1.spa`)

---

## ⚡ Chuẩn Đọc Ảnh Bìa Segment Bin & Proxy Playback:
1. **Phục vụ ảnh bìa qua Segment Bin (`*_covers_0001.bin`)**:
   - Mọi server crawl-vod (`3012 - 3015`) đọc trực tiếp ảnh từ file `.bin` bằng con trỏ `cover_offset` và `cover_length`.
   - Giữ RAM luôn tối ưu dưới 200MB, tốc độ phản hồi ảnh < 1ms.
2. **Bộ giải mã URL Video (`/api/video/<path:code>`)**:
   - Hỗ trợ ID dạng đường dẫn (ví dụ: `ban-tinh-8-lan-vao-lon-sep-nu-xinh-dam/3142` của VLXX).
   - Tự động bóc tách m3u8 và mã token động cho trình phát.
3. **Proxy Streaming (`/api/proxy`)**:
   - Gắn dynamic Referer thích hợp cho từng domain CDN (MissAV: `playergo.top` dùng `missav99.com`, `surrit.com` dùng `missav.ws`).
