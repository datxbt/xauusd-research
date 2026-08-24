[English](README.md) · **Tiếng Việt**

# xauusd-research

Nghiên cứu chiến lược giao dịch XAUUSD (vàng giao ngay) dựng trực tiếp trên dữ
liệu tick thô của Exness — khoảng 176 triệu tick trải từ 2024-01 đến 2026-07 —
kèm `tick_synth`, bộ sinh dữ liệu tick tổng hợp dùng để kiểm tra xem một kết
quả là lợi thế thật hay chỉ là may mắn của một lần rút ngẫu nhiên.

Mọi nghiên cứu ở đây đều theo cùng một quy trình, và chính quy trình mới là
điều quan trọng.

## Quy trình

```
TRAIN    2024-01 .. 2025-06   (18 tháng)  — tìm kiếm thoải mái ở đây
HOLDOUT  2025-07 .. 2026-06   (12 tháng)  — chỉ mở MỘT lần, cho một cấu hình
```

Giả thuyết phải được viết ra **trước** khi nhìn vào bất kỳ kết quả nào, kèm
theo ngưỡng mà nó buộc phải vượt qua: số lệnh tối thiểu, và khoảng tin cậy
bootstrap của P&L trung bình mỗi phiên phải nằm trọn vẹn trên mức 0. Chỉ duy
nhất ứng viên tốt nhất trên TRAIN mới được chấm điểm ngoài mẫu. Nếu không có
gì vượt ngưỡng trên TRAIN thì holdout vẫn đóng, và câu trả lời là "không".

Toàn bộ lưới tham số luôn được báo cáo, không chỉ ô thắng cuộc, để kết quả ở
cấp độ cả họ chiến lược vẫn hiện rõ thay vì chỉ thấy thành viên may mắn nhất.

Chi phí giao dịch lấy đúng spread bid/ask có sẵn trong dữ liệu tick, cộng hoa
hồng. Lệnh khớp phải trả đúng bên của sổ lệnh.

## Các chiến lược

**Phá vỡ biên độ mở phiên** ([backtest_orb.py](backtest_orb.py)) — lấy đỉnh/đáy
của R phút đầu tiên sau khi một phiên lớn mở cửa, giao dịch theo hướng phá vỡ
đầu tiên, cắt lỗ ở phía đối diện của biên độ, chốt lời theo một bội số của độ
rộng biên độ. Cơ sở của giả thuyết nằm ở vi cấu trúc thị trường: thông tin tích
tụ qua đêm khi thanh khoản mỏng, rồi được định giá lại thành một đợt bùng nổ
lệnh khi phiên chính mở cửa.

## Cấu trúc dự án

| đường dẫn | nội dung |
|---|---|
| [backtest_orb.py](backtest_orb.py) | kiểm định ORB đã đăng ký trước, lưới 18 cấu hình |
| [tickdata.py](tickdata.py) | engine dùng chung: tick → sub-bar → phiên, tính khối lượng lệnh và chỉ số |
| [session_context.py](session_context.py) | bảng đặc tính từng phiên (xu hướng/đi ngang, biến động, spread) để ghép vào lệnh |
| [optimize_time_windows.py](optimize_time_windows.py) | quét 216 khung giờ ORB (24 giờ × 3 biên độ × 3 mục tiêu), chấm trên cả hai giai đoạn |
| [regime_analysis.py](regime_analysis.py) | mỗi năm trông ra sao, đặc tính nào có tính bền, và dự báo cho những đặc tính đó |
| [optimize_for_regime.py](optimize_for_regime.py) | điều kiện hóa danh mục ORB theo biến động — đầu vào duy nhất dự báo được |
| [run_synth_backtest.py](run_synth_backtest.py) | chạy danh mục ORB 8 khung giờ trên các bản sao tổng hợp |
| [XAUUSD_SessionBreakout.mq5](XAUUSD_SessionBreakout.mq5) | EA cho MT5: danh mục 8 khung giờ, chạy thật |
| [tick_synth/](tick_synth/) | bộ sinh tick tổng hợp — xem [README riêng](tick_synth/README.md) |

Kết quả nằm trong `orb_results/`, `regime_results/`, `market_context/`
và `spread_stats/`, được commit vào repo như hồ sơ nghiên cứu.

## Kết luận từ regime_analysis

Kết quả này chi phối mọi thứ phía sau, nên cần đặt ngay từ đầu:

| dự báo được (AR1 0.60–0.87) | không dự báo được (AR1 ≤ 0.12) |
|---|---|
| biến động thực tế, biên độ, spread, số tick | hướng đi, độ xu hướng |

Vì vậy mức biến động là thứ duy nhất hợp lệ để điều kiện hóa khối lượng vào
lệnh hay việc chọn khung giờ. EA tính khối lượng nghịch đảo với biến động thực
tế gần nhất cũng chính vì lý do này. Bất cứ thứ gì điều kiện hóa theo *hướng*
được dự báo đều chỉ là khớp nhiễu.

## tick_synth

Một lịch sử chỉ là một lần rút ngẫu nhiên, và phép bootstrap ở cấp phiên trong
các script này chỉ lấy mẫu lại các phiên — nó không thể lấy mẫu lại chính chuỗi
tick. `tick_synth` tạo ra file tick tổng hợp đúng định dạng Exness và đúng cấu
trúc thư mục, nên mọi backtest ở đây đọc được mà không cần sửa gì:

```bash
python backtest_orb.py --data-dir tick_synth/output/rep00 --outdir orb_results/rep00
```

Hai cách dùng. **Các lịch sử thay thế** (khối trọn ngày) cho thấy khoảng phân
tán của kết quả mà một lợi thế lẽ ra có thể gặp phải. **Chuỗi tick đối chứng**
(khối ngắn) vẫn giữ nguyên chi phí, nhịp tick và biến động thật, nhưng phá bỏ
cấu trúc xu hướng kéo dài nhiều giờ — một lợi thế kiểu phá vỡ lẽ ra phải gần
như biến mất ở đó. Nếu nó không biến mất thì P&L đang đến từ một nguyên nhân
khác với cơ chế đã nêu.

Phương pháp đầy đủ, số liệu kiểm định và các bẫy cần tránh:
[tick_synth/README.md](tick_synth/README.md).

## Dữ liệu

Dữ liệu tick **không** nằm trong repo này — đó là khoảng 19 GB dữ liệu từ nhà
cung cấp, và các chuỗi tick sinh ra cùng bộ nhớ đệm chiếm thêm khoảng 64 GB.
Tất cả đều đã được gitignore. Cấu trúc mong đợi:

```
Monthly_Tick_Data/<YYYY>/Exness_XAUUSD_Raw_Spread_<YYYY>_<MM>/*.csv
```

```
"Exness","Symbol","Timestamp","Bid","Ask"
"exness","XAUUSD_Raw_Spread","2025-03-02 23:05:00.071Z",2873.618,2873.655
```

Các lần chạy tổng hợp được tái tạo lại chứ không lưu trữ: mỗi thư mục kết quả
đều có `manifest.json` ghi lại phương pháp, seed, khoảng dữ liệu nguồn và toàn
bộ tham số, nên một chuỗi tick đã xóa có thể tạo lại giống hệt từng byte.

## Yêu cầu

Python 3.11 trở lên, `numpy`, `pandas`, `scipy`, `matplotlib`. EA cần
MetaTrader 5 — hãy đọc phần đầu file trước khi chạy, `InpGMTOffsetHours` phải
khớp với đồng hồ máy chủ của sàn, nếu không EA sẽ giao dịch hoàn toàn sai giờ.

## Một lưu ý cần nhớ

Chuỗi tick tổng hợp dùng để đo độ phân tán và độ bền của kết quả. Chúng không
dùng để tìm ra lợi thế, và tuyệt đối không được tối ưu tham số trên chúng.
