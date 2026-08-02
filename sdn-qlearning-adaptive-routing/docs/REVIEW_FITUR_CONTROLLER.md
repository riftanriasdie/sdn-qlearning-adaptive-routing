# Review Fitur Controller Ryu

File: `perc_ta_ryu_final_06_optimasirute.py`
Kelas utama: `TAMainController` (Ryu app) + `FlowRuleManager` + `TopologyWebAPI`

Dokumen ini merinci **setiap fitur** controller beserta **fungsinya** dan
**di mana** ia diimplementasikan.

---

## A. Arsitektur & Model Threading

Controller berjalan sebagai aplikasi Ryu yang memunculkan **5 thread hijau
(green thread / `hub.spawn`)** yang berjalan paralel non-blocking:

| Thread | Fungsi |
|--------|--------|
| `_startup_logic` | Fase inisialisasi: tunggu topologi & KPI, warmup, set `rl_ready`. |
| `_monitor_reader_loop` | Membaca `link_report_*.json` → isi `self.link_stats`. |
| `_topology_discovery_loop` | Deteksi link UP/DOWN via LLDP, update graf `self.net`. |
| `_rl_background_worker` | Siklus Q-Learning + push forward/reverse rule (tiap 2 dtk). |
| `_log_q_table_loop` | Cetak tabel Q-routing & active path tiap 5 dtk. |

**Fungsi:** memisahkan tugas yang berbeda kecepatan (telemetri 1 dtk, RL 2 dtk,
topologi 2 dtk, logging 5 dtk) agar tidak saling memblokir.

**Struktur data inti:**
- `self.net` (`nx.DiGraph`) — graf topologi switch berarah, atribut `port`.
- `self.q_table` — `{(dpid, 0): {port: q_value}}`. State = `(dpid, 0)`.
- `self.link_stats` — `{(src, dst): {lat, jit, loss, timestamp}}`.
- `self.datapaths` — `{dpid: datapath}` koneksi OpenFlow aktif.
- `self.greedy_path_cache` — aksi terpilih per switch (untuk hysteresis).

---

## B. FlowRuleManager — Manajemen Siklus Hidup Flow Rule

Kelas terpisah yang menjadi **satu-satunya titik instalasi flow rule**
(disiplin desain agar semua perubahan flow mudah diaudit).

### B.1 Hierarki prioritas (semakin tinggi semakin diutamakan)

| Prio | Konstanta | Fungsi |
|------|-----------|--------|
| 200 | `PRIO_SERVER_DIRECT` | Di S9: traffic ke server langsung ke port host server. |
| 100 | `PRIO_SERVER_FWD` | Forward Q-Learning ke server (`ipv4_dst=10.0.0.9`). |
| 15 | `PRIO_REVERSE_Q` | Reverse path Q-Learning per-host (`ipv4_src=10.0.0.9`). |
| 10 | `PRIO_HOST_ROUTE` | Routing host-to-host Dijkstra (warmup/fallback). |
| 0 | `PRIO_TABLE_MISS` | Paket tak dikenal → kirim ke controller (packet-in). |

### B.2 Primitif & operasi tingkat tinggi

- `_add_flow` / `_delete_flow` — primitif kirim `OFPFlowMod` ADD/DELETE.
- `install_table_miss` — pasang aturan default kirim ke controller.
- `install_server_forward` — pasang/update rule forward ke server (prio 100).
- `install_server_direct` — di S9, langsung ke port host server (prio 200).
- `install_host_route` — rule Dijkstra `in_port + eth_dst` (prio 10).
- `install_reverse_path_per_host` — rule reverse `ipv4_src + ipv4_dst` **tanpa
  `in_port`**, prio 15, `idle_timeout=30`.

### B.3 Event lifecycle

- **`on_switch_connect`** — saat switch connect: hapus semua rule stale
  (forward, reverse, host-route) dari sesi sebelumnya, lalu pasang table-miss.
  **Fungsi:** mencegah rule looping/orphan dari run sebelumnya.
- **`on_link_down`** — saat link putus: hapus forward + reverse rule di semua
  switch, lalu **pasang fallback shortest-path** (Dijkstra) di topologi baru
  dengan `idle_timeout=5`. **Fungsi:** menjaga konektivitas sementara RL
  konvergen ulang.
- **`on_link_up`** — log saja; pemasangan rule diserahkan ke RL push.

---

## C. Mesin Q-Learning (Inti)

### C.1 Fungsi reward berbasis KPI aktual
`_ql_update()` menghitung:
```
cost   = W_LATENCY·lat + W_JITTER·jit + W_LOSS·loss
reward = -SCALING_FACTOR·cost - HOP_PENALTY
```
Bobot: `W_LATENCY=1`, `W_JITTER=5`, `W_LOSS=100` (loss paling dihindari).
**Fungsi:** menerjemahkan kualitas link nyata menjadi sinyal pembelajaran.

### C.2 Persamaan Bellman
```
Q(s,a) ← Q(s,a) + α·[reward + γ·max_a' Q(s',a') − Q(s,a)]
```
`ALPHA=0.7` (learning rate), `GAMMA=0.9` (discount). **Fungsi:** memperbarui
estimasi nilai jalur dengan mempertimbangkan reward masa depan.

### C.3 Eksplorasi ε-greedy + decay
`_ql_choose_action()` memilih aksi acak dengan probabilitas ε, jika tidak
memilih Q tertinggi. ε turun dari `1.0` → `0.05` (`EPSILON_DECAY_STEP=0.1`).
**Fungsi:** banyak eksplorasi di awal, makin eksploitatif saat konvergen.

### C.4 State sederhana `(dpid, 0)`
Keputusan routing hanya bergantung switch saat ini (bukan `in_port`).
**Fungsi:** Q-Table kecil & konvergen cepat karena tujuan tunggal (semua → S9).

---

## D. Mekanisme Penstabil Routing

| Fitur | Lokasi | Fungsi |
|-------|--------|--------|
| **Hysteresis** (`HYSTERESIS_THRESHOLD=3.0`) | `_ql_choose_action`, greedy trace, push forward | Tidak pindah jalur kecuali Q jalur baru unggul ≥ 3.0 dari jalur lama → cegah *route flapping*. |
| **Poison Reverse** | `_ql_update` (`max_next_q`) | Saat hitung nilai next-state, port balik dikecualikan → cegah loop bolak-balik 2-hop. |
| **Hop Penalty** (`HOP_PENALTY=5.0`) | `_ql_update` | Setiap hop dikurangi 5 → agen lebih suka jalur pendek. |
| **Inisialisasi Pesimis** (`INITIAL_Q_VALUE=0.0`) | konstanta + `_ql_choose_action` | Q awal 0 untuk semua, jika semua sama → pilih acak (cegah bias ke port loop). |
| **Garbage Collection** | `_rl_background_worker` | Hapus entry Q-Table untuk port yang sudah tidak valid (link hilang). |

---

## E. Penanganan Dinamika Topologi (Link UP/DOWN)

### E.1 Deteksi via LLDP
`_topology_discovery_loop` membandingkan `prev_edges` vs `current_edges`:
- **Link hilang** → `confirmed_down_links`, reset cache path, panggil
  `fm.on_link_down`, simpan `link_penalty_memory`.
- **Link muncul** → `link_recovery_timers`, reset cache, panggil `fm.on_link_up`.

### E.2 Memory Path Reset
`link_penalty_memory[(src,dst)]` menyimpan semua `(dpid, port)` di jalur aktif
saat link putus. **Fungsi:** mengingat siapa saja yang terdampak agar bisa
direset saat link pulih.

### E.3 "Pemutihan" & Reset Q-Value (`_reset_path_q_values`)
Saat link UP stabil ≥ 2 detik (`_monitor_reader_loop`):
- Reset Q-value port yang pulih ke 0.0.
- Reset entry di `link_penalty_memory` yang nilainya sangat negatif (< −10).
- Hapus denda di `loop_penalty_tracker`.
**Fungsi:** agar agen mau "mencoba lagi" jalur yang sempat rusak, bukan
selamanya menghindarinya.

### E.4 Grace Period (`recovering_links`)
Selama 10 detik setelah reset, port yang baru pulih:
- Dihindari di `_ql_choose_action` (filter `recovering_ports`).
- Bellman update di-skip (`skip_bellman`).
**Fungsi:** beri waktu KPI link stabil dulu sebelum dipakai untuk belajar.

### E.5 Auto-Reset data stale
Di `_ql_update`, jika data terlihat basi (`lat>1000` atau `loss>20`) dan Q sangat
negatif (< −100), Q direset ke 0. **Fungsi:** pulih otomatis dari data error.

---

## F. Deteksi Loop & Sistem Penalti

Aktif hanya saat ε sudah minimum (jaringan dianggap konvergen):
- **Deteksi loop** — greedy trace yang mengunjungi switch berulang → `[LOOP]`.
- **Loop Penalty** — tiap 2 detik selama loop, semua port di jalur loop didenda
  +100 kumulatif (`loop_penalty_tracker`). Denda dikurangkan dari reward.
- **Pengampunan (Grace/Forgiveness)** — saat rute kembali normal, denda di
  jalur aman dihapus (termasuk port balik). **Fungsi:** denda tidak permanen,
  hanya menekan jalur yang benar-benar bermasalah.

---

## G. Convergence Timer (Pengukuran Waktu Rerouting)

Di `_rl_background_worker`, saat jalur berubah:
- Catat `waktu_mulai_recovery` (`time.perf_counter`).
- Saat jalur valid baru stabil, cetak **durasi rerouting** (detik).
**Fungsi:** metrik kuantitatif untuk evaluasi TA (seberapa cepat sistem
beradaptasi setelah perubahan topologi).

---

## H. Push Rule Proaktif (Forward & Reverse)

### H.1 Push Forward (proaktif)
Setiap siklus RL, untuk tiap switch dengan Q-Table → pilih port terbaik
(dengan hysteresis) → `install_server_forward` (prio 100). **Fungsi:** flow rule
sudah terpasang sebelum traffic datang (tidak menunggu packet-in).

### H.2 Push Reverse Path per-host
- `_build_forward_path_from(start)` — greedy trace dari switch host ke S9
  mengikuti Q-table + hysteresis.
- `_push_host_reverse_path` — balik urutan path, pasang rule `ipv4_src=server,
  ipv4_dst=host` di tiap switch (prio 15), plus last-mile ke port host.
- `_push_all_host_reverse_paths` — lakukan untuk semua host user tiap siklus.
**Fungsi (kontribusi file _06):** reverse path jadi cermin forward Q-Learning
sehingga RTT ping murni mencerminkan kualitas jalur yang dipelajari, bukan
campuran Q-Learning (maju) + Dijkstra (balik).

---

## I. Penanganan Paket (Packet Handling)

| Handler | Fungsi |
|---------|--------|
| `switch_features_handler` | Saat switch connect: simpan datapath, bersihkan rule stale, pasang server-direct jika ini S9. |
| `_packet_in_handler` | Klasifikasi paket: LLDP (abaikan), ARP (flood), IP→server (RL), IP lain (Dijkstra). Juga deteksi server dinamis. |
| `_handle_rl_routing` | Traffic ke `10.0.0.9`: pilih aksi RL, pasang forward rule, kirim paket. |
| `_handle_shortest_path_routing` | Traffic non-server: routing Dijkstra `in_port+eth_dst`, dengan **guard `out_port==in_port`** (cari jalur alternatif untuk cegah loop). |

**Mode dual:** sebelum `rl_ready` semua traffic pakai Dijkstra (warmup);
sesudahnya, hanya traffic ke server yang dikontrol Q-Learning.

---

## J. Startup & Warmup

`_startup_logic` melakukan gerbang berurutan:
1. Tunggu `host_topology.json` (timeout 60s) → identifikasi server H9.
2. Tunggu KPI pertama (`link_stats` terisi).
3. Warmup countdown 10 detik (Dijkstra untuk semua traffic).
4. Tunggu LLDP melengkapi 11 link (timeout 30s).
5. Set `rl_ready=True` → Q-Learning aktif.
**Fungsi:** mencegah RL mengambil keputusan dengan data/topologi belum lengkap.

---

## K. Logging & Visualisasi

`_log_q_table_loop` (tiap 5 dtk, hanya saat konvergen) mencetak:
- **Active path** (S1→...→S9) hasil greedy trace + hysteresis.
- **Reverse path** aktif.
- **Tabel Q-routing** lengkap: per switch/port menampilkan next-hop, lat, jit,
  loss, reward, Q-value, dan status (✅ ACTIVE / ⭐ BEST).
**Fungsi:** observabilitas real-time di terminal untuk debugging & demo TA.

---

## L. Web API (`TopologyWebAPI`)

Endpoint `GET /api/topology` (port WSGI Ryu, CORS terbuka) mengembalikan JSON:
- `nodes` & `edges` (dengan lat/jit/loss/reward/q_val/is_active) untuk
  visualisasi graf (mis. vis.js).
- `epsilon`, `rl_ready`, `active_path`, `server_dpid`.
- `q_table` rows (sw, port, next_hop, reward, q_val, is_active, is_best).
**Fungsi:** menyediakan data untuk Web UI / dashboard pemantauan eksternal.

---

## Ringkasan Fitur Unggulan untuk Laporan TA

1. **Routing adaptif Q-Learning** berbasis KPI aktual (lat/jit/loss).
2. **Reverse path simetris** (kontribusi utama _06) — RTT murni Q-Learning.
3. **Penstabil routing**: hysteresis, poison reverse, hop penalty, init pesimis.
4. **Self-healing**: deteksi link UP/DOWN, memory reset, pemutihan, grace period.
5. **Anti-loop**: deteksi loop + penalti kumulatif + pengampunan.
6. **Convergence timer** — metrik kuantitatif waktu rerouting.
7. **FlowRuleManager** terpusat dengan hierarki prioritas jelas.
8. **Push proaktif** forward + reverse (tidak menunggu packet-in).
9. **Observabilitas**: logging terminal + Web API.
