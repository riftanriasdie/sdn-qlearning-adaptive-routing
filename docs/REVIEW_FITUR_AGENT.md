# Review Fitur Agen Monitoring KPI

File: `agent_reporter_04_kpi_aktual.py`
Kelas utama: `KPIActualAgent` — sensor pengukur kualitas link per-pasangan switch.

Dokumen ini merinci setiap fitur agen beserta fungsinya.

---

## A. Model Operasi

Setiap agen adalah **proses independen** yang dijalankan di host monitoring
`HMx` dengan 3 argumen:
```
python3 agent_reporter_04_kpi_aktual.py <my_sw> <target_sw> <target_ip>
```
Lalu masuk loop tak terbatas: **ping target → ukur KPI → tulis file → ulang**.

| Parameter | Nilai | Fungsi |
|-----------|-------|--------|
| `PING_COUNT` | 100 | Jumlah paket ping per siklus. |
| `PING_INTERVAL` | 0.01 s | Jeda antar paket (10 ms) → 1 siklus ping ≈ 1.0 dtk. |
| `LOSS_WINDOW` | 5 | Jumlah siklus diakumulasi untuk hitung loss. |

Total per siklus ≈ 1.0 dtk ping + 0.5 dtk sleep = **~1.5 dtk**. Lebih cepat dari
interval RL (2.0 dtk) → **data selalu fresh** saat controller membacanya.

---

## B. Pengukuran Tiga KPI

Output file: `/tmp/link_report_<my_sw>_<target_sw>.json` berisi
`{src_sw, dst_sw, lat, jit, loss, timestamp}`.

### B.1 Latency — rata-rata RTT per paket
```python
avg_lat = sum(rtt_values) / len(rtt_values)
```
Ping dijalankan **tanpa flag `-q`** agar setiap baris RTT per-paket tersedia,
lalu di-parse dengan regex `icmp_seq=(\d+).*?time=([0-9.]+) ms`.

**Fungsi:** latency murni rata-rata, **tanpa smoothing** → mencerminkan kondisi
sesaat link secara akurat.

### B.2 Jitter — RFC 3393
```python
diff = abs(rtt_by_seq[seq_curr] - rtt_by_seq[seq_prev])   # hanya jika berurutan
avg_jitter = mean(jitter_samples)
```
Jitter = rata-rata selisih RTT antar paket **berurutan** `|RTT[n] − RTT[n−1]|`.

**Fungsi & keunggulan:**
- Sesuai standar **RFC 3393** (lebih defensible secara akademis daripada
  `mdev` bawaan ping).
- Pasangan paket yang di antaranya **ada paket hilang dilewati** (cek
  `seq_curr == seq_prev + 1`) agar jitter tidak terdistorsi oleh gap sequence.

### B.3 Packet Loss — akumulasi mentah (resolusi tinggi)
```python
self.loss_window = deque(maxlen=LOSS_WINDOW)   # [(sent, lost), ...]
loss = total_lost / total_sent * 100
```
Loss dihitung dari **akumulasi hitungan paket mentah** lintas 5 siklus
(500 paket), bukan rata-rata persentase.

**Fungsi & keunggulan (poin penting untuk laporan):**
- Resolusi = `1 / (100 × 5)` = **0.2%**.
- Berbeda dari moving-average persen: `avg([0,0,0,0,2%]) = 0.4%` hanya
  menghaluskan (resolusi tetap kasar 2%). Akumulasi mentah
  `1 lost / 500 sent = 0.2%` benar-benar **meningkatkan resolusi**.
- Penting karena `W_LOSS=100` di controller — loss kecil pun berpengaruh besar
  pada keputusan Q-Learning, jadi pengukurannya harus presisi.

Paket hilang dideteksi dari **gap nomor `icmp_seq`** (`lost = 100 − received`).

---

## C. Penanganan Link Putus Total

```python
if result[0] is None:        # received == 0
    lat = 9999.0; jit = 0.0
    self.loss_window.append((PING_COUNT, PING_COUNT))
    loss = 100.0
```
Jika tidak ada satu pun balasan ping (semua timeout), agen melaporkan
`lat=9999`, `loss=100%`.

**Fungsi:** memberi sinyal jelas ke controller bahwa link mati. Di controller,
`lat>1000` atau `loss>20` dianggap *data stale* → reward −20 / auto-reset
(lihat fitur E.5 review controller).

---

## D. Atomic Write (Anti Race-Condition)

```python
with open(temp_filename, 'w') as f:
    json.dump(report, f); f.flush(); os.fsync(f.fileno())
os.rename(temp_filename, self.filename)
```
Tulis ke file `.tmp` dulu → `fsync` → baru `os.rename` ke nama final.

**Fungsi:** `os.rename` bersifat **atomic** di level OS. Controller yang membaca
file di thread lain **tidak akan pernah** mendapat file setengah tertulis
(JSON korup). Ini krusial karena controller membaca tiap detik sementara agen
menulis tiap ~1.5 detik tanpa koordinasi/lock.

---

## E. Isolasi dari Sinyal Terminal (Robustness)

Dua lapis perlindungan agar agen tidak ikut mati saat user menekan Ctrl+C di
Mininet CLI:

| Mekanisme | Lokasi | Fungsi |
|-----------|--------|--------|
| `start_new_session=True` | `subprocess.check_output` ping | Subprocess ping pindah ke session OS terpisah → SIGINT dari CLI tidak sampai ke ping. Lebih andal dari `preexec_fn` karena di level syscall `setsid`. |
| `signal.signal(SIGINT, SIG_IGN)` | `main()` | Proses agen mengabaikan SIGINT → terus berjalan selama topologi aktif. |

**Fungsi:** agen harus hidup sepanjang eksperimen. Jika user menghentikan `ping`
manual atau menekan Ctrl+C di CLI, agen tidak boleh ikut tumbang.

---

## F. Struktur Loop & Ketahanan Error

- `deque(maxlen=5)` — *sliding window* otomatis; siklus terlama keluar saat
  yang baru masuk, tanpa manajemen index manual.
- `try/except` di sekitar ping dan penulisan file — error sesaat (mis. ping
  gagal sekali) tidak menghentikan loop; siklus berikutnya tetap jalan.
- Validasi argumen di `main()` (`len(sys.argv) < 4` → exit).

**Fungsi:** agen tahan banting — berjalan terus meski ada gangguan sementara.

---

## Ringkasan Fitur Agen

1. **Pengukuran 3 KPI aktual**: latency (avg RTT), jitter (RFC 3393),
   loss (akumulasi mentah).
2. **Resolusi loss tinggi 0.2%** via akumulasi 500 paket (bukan rata-rata %).
3. **Jitter standar RFC 3393** dengan skip gap paket hilang.
4. **Parsing per-paket** (ping tanpa `-q`) → akurasi lebih tinggi.
5. **Deteksi link putus** → `lat=9999`, `loss=100%`.
6. **Atomic write** (`.tmp` → `fsync` → `rename`) → tidak ada file korup.
7. **Isolasi sinyal** (`start_new_session` + `SIGINT ignore`) → agen tahan Ctrl+C.
8. **Data fresh** (~1.5 dtk/siklus < 2 dtk interval RL).
9. **Tahan error** (try/except + sliding window deque).
