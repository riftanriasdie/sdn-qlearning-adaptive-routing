# Adaptive SDN Routing Berbasis Q-Learning untuk Peningkatan QoS

Implementasi sistem **routing adaptif** pada jaringan Software-Defined Networking (SDN)
menggunakan algoritma **Q-Learning** dengan controller Ryu dan emulator Mininet.
Sistem ini dirancang sebagai tugas akhir dengan tujuan meningkatkan Quality of Service (QoS)
secara otomatis berdasarkan kondisi link aktual — tanpa konfigurasi manual.

> **Dibandingkan dengan**: simulasi perilaku OSPF (Dijkstra shortest path) sebagai baseline,
> untuk mengukur peningkatan QoS yang diperoleh dari pendekatan berbasis pembelajaran penguatan.

---

## Daftar Isi

- [Gambaran Sistem](#gambaran-sistem)
- [Arsitektur](#arsitektur)
- [Topologi Jaringan](#topologi-jaringan)
- [Fitur Utama](#fitur-utama)
- [Struktur File](#struktur-file)
- [Prasyarat & Instalasi](#prasyarat--instalasi)
- [Cara Menjalankan](#cara-menjalankan)
- [Skenario Pengujian](#skenario-pengujian)
- [Parameter Sistem](#parameter-sistem)
- [Perbandingan Q-Learning vs OSPF](#perbandingan-q-learning-vs-ospf)
- [Dokumentasi](#dokumentasi)

---

## Gambaran Sistem

Sistem terdiri dari tiga komponen yang berjalan secara terpisah dan berkomunikasi
melalui **file JSON di `/tmp`** sebagai *message bus*:

| Komponen | File | Peran |
|---|---|---|
| **Mininet** (Data Plane) | `topology_qlearning.py` | Membangun topologi 9 switch + 18 host, menyalakan 22 agen KPI |
| **Agen KPI** (Telemetri) | `agent_reporter_04_kpi_aktual.py` | Mengukur latency, jitter, packet loss per-link setiap ~1.5 detik |
| **Ryu Controller** (Control Plane) | `controller_qlearning.py` | Otak sistem: Q-Learning + instalasi flow rule OpenFlow |

```
                       FILESYSTEM /tmp  (MESSAGE BUS)
      ┌─────────────────────────────────────────────────────────────┐
      │  host_topology.json          link_report_<src>_<dst>.json   │
      │  (peta host user)            (KPI per-link, 22 berkas)      │
      └──────▲────────────┬──────────────▲──────────────┬───────────┘
             │ tulis (1x) │ baca (1x)    │ tulis (loop) │ baca (loop)
  ┌──────────┴──────────┐ │     ┌────────┴──────────┐   │
  │      MININET        │ │     │    AGEN KPI (HMx)  │   │
  │  topologi + spawn   │ │     │ ping → ukur KPI    │   │
  │  S1..S9 + Hx + HMx  │ │     │ (22 proses)        │   │
  └──────────┬──────────┘ │     └─────────┬──────────┘   │
             │ OpenFlow 1.3│              │ ping ICMP     │
             │             └──────────────┼───────────────┤
             │                            ▼               │
             │                  ┌─────────────────────┐   │
             └──────────────────┤   RYU CONTROLLER    ├───┘
              packet-in/FlowMod │  Q-Learning + Web   │
                                │  API :8080          │
                                └─────────────────────┘
```

---

## Arsitektur

### Alur Bootstrapping

Sistem memiliki urutan startup yang terkoordinasi dengan gerbang `rl_ready`:

```
MININET              AGEN KPI          FILESYSTEM          RYU CONTROLLER
   │ build topologi     │                  │                     │
   ├─ tulis host_topology.json ───────────►│                     │
   │ spawn 22 agen ────►│ ping HMx→HMy     │                     │
   │                    ├─ tulis link_report_*.json ►│           │
   │                    │                  │◄─ baca topologi ─────┤ identifikasi H9
   │                    │                  │◄─ baca KPI ──────────┤ _monitor_reader
   │◄═══ LLDP discovery (OpenFlow) ═══════════════════════════════┤ tunggu 11 link
   │                    │                  │                     │ WARMUP 10 detik
   │                    │                  │                     │ rl_ready = True ✅
```

### Loop Runtime Q-Learning (tiap 2 detik)

```
  [1] Baca KPI dari link_stats (lat, jit, loss per link)
        ↓
  [2] Hitung reward:  cost = 1·lat + 5·jit + 100·loss
                      reward = -10·cost - 5·hop
        ↓
  [3] Update Q-Table: Q(s,a) ← Q(s,a) + 0.7·[reward + 0.9·maxQ' - Q(s,a)]
                      + Poison Reverse + Loop Penalty + ε-greedy decay
        ↓
  [4] Greedy trace S1→...→S9 dengan Hysteresis (threshold = 3.0)
        ↓
  [5a] Push FORWARD rule    [5b] Push REVERSE rule per-host
       ipv4_dst=10.0.0.9         ipv4_src=10.0.0.9, ipv4_dst=host
       prio=100, permanent        prio=15, idle_timeout=30
```

---

## Topologi Jaringan

9 switch (S1–S9) dengan 11 inter-switch link, menyediakan **banyak jalur alternatif**
dari S1 ke S9 sebagai ruang keputusan Q-Learning:

```
        S2 ── S5 ──┐
       /        \   \
   S1 ─ S3 ── S6 ── S7 ── S9 (SERVER H9: 10.0.0.9)
       \      /  \   /
        S4 ──┘    S8 ┘
```

Inter-switch links: `S1-S2, S1-S3, S1-S4, S2-S5, S3-S6, S4-S6, S5-S6, S5-S7, S6-S8, S7-S9, S8-S9`

Beberapa jalur alternatif S1→S9 yang dapat dipelajari Q-Learning:
- `S1-S2-S5-S7-S9` (4 hop)
- `S1-S3-S6-S8-S9` (4 hop)
- `S1-S4-S6-S8-S9` (4 hop)
- `S1-S2-S5-S6-S8-S9` (5 hop)

### Host per Switch

Setiap switch memiliki **dua host** dengan peran berbeda untuk menghindari
konflik flow rule antara traffic Q-Learning dan traffic monitoring:

| Tipe | Penamaan | IP | Fungsi |
|---|---|---|---|
| Host user | H1–H9 | `10.0.0.x` | Traffic data. **H9 = server tujuan**. |
| Host monitoring | HM1–HM9 | `10.0.0.x0` | Khusus agen telemetri KPI (target ping). |

### Parameter Link Inter-Switch

Penelitian ini **tidak berfokus pada bandwidth**, sehingga topologi yang digunakan bersifat **homogen** — setiap inter-switch link memiliki kapasitas bandwidth yang sama. Karena cost setiap link bernilai sama (cost = 1), Dijkstra pada controller baseline menghitung jalur berdasarkan **jumlah hop minimum**, bukan bandwidth. Ini ekivalen dengan perilaku OSPF standar pada jaringan dengan semua link berkapasitas seragam.

Parameter yang diukur dan dioptimasi adalah **latency, jitter, dan packet loss** yang diinjeksi via `tc netem` saat pengujian.

| Link | Delay | Jitter | Loss |
|---|---|---|---|
| S1─S2, S1─S3, S1─S4 (access) | 2 ms | 0.5 ms | 0% |
| S2─S5, S3─S6, S4─S6 (aggregation) | 3 ms | 1.0 ms | 0% |
| S5─S6, S5─S7, S6─S8 (core internal) | 2 ms | 0.5 ms | 0% |
| S7─S9, S8─S9 (last-mile ke server) | 4 ms | 1.5 ms | 0% |

> Parameter link pada file Q-Learning **identik** dengan file Dijkstra untuk memastikan pengujian yang adil.

---

## Fitur Utama

### Q-Learning Adaptive Routing
- **Reward berbasis KPI aktual**: `cost = 1·lat + 5·jit + 100·loss` — loss sangat dihindari
- **Persamaan Bellman**: `α=0.7`, `γ=0.9`
- **ε-greedy decay**: eksplorasi 1.0 → eksploitasi 0.05
- **State sederhana** `(dpid, 0)`: Q-table kecil, konvergensi cepat

### Penstabil Routing (Anti-Flapping)
- **Hysteresis** (threshold 3.0): tidak pindah jalur kecuali Q jalur baru unggul ≥ 3.0
- **Poison Reverse**: port balik dikecualikan dari `max_next_q` → cegah loop 2-hop
- **Hop Penalty** (5.0 per hop): jalur lebih pendek lebih diutamakan
- **Inisialisasi Pesimis** (Q=0.0): hindari bias ke port default

### Reverse Path Simetris (Kontribusi Utama)
Forward path dikontrol Q-Learning, reverse path adalah **cermin persis** dari forward —
bukan Dijkstra seperti versi sebelumnya. Hasilnya RTT ping benar-benar mencerminkan
kualitas jalur Q-Learning dari kedua arah.

```
FORWARD (Hx→H9):  S8→S6→S4→S1→S2→S5→S7→S9  [Q-Learning, prio 100]
REVERSE (H9→Hx):  S9→S7→S5→S2→S1→S4→S6→S8  [cermin Q-Learning, prio 15]
```

### Self-Healing (Link UP/DOWN)
- Deteksi link DOWN/UP via LLDP setiap 2 detik
- Saat link DOWN: hapus rule → pasang fallback Dijkstra (`idle_timeout=5`)
- Saat link UP: reset Q-value port pulih ke 0, grace period 10 detik
- **Convergence Timer**: mengukur durasi rerouting sebagai metrik evaluasi

### Pengukuran KPI Akurat (Agen)
- **Latency**: rata-rata RTT per paket (tanpa smoothing)
- **Jitter**: standar RFC 3393, skip gap paket hilang
- **Packet Loss**: akumulasi mentah 500 paket → resolusi **0.2%**
- **Atomic write** via `tmp → fsync → rename`: tidak ada file JSON korup
- **22 agen bidirectional** untuk KPI simetris maju dan balik

### Hierarki Prioritas Flow Rule

| Priority | Nama | Fungsi |
|---|---|---|
| 200 | `PRIO_SERVER_DIRECT` | Di S9: traffic ke server langsung ke port H9 |
| 100 | `PRIO_SERVER_FWD` | Forward Q-Learning ke server |
| 15 | `PRIO_REVERSE_Q` | Reverse path Q-Learning per-host |
| 10 | `PRIO_HOST_ROUTE` | Dijkstra host-to-host (warmup/fallback) |
| 0 | `PRIO_TABLE_MISS` | Kirim ke controller (packet-in) |

---

## Struktur File

```
sdn-qlearning-adaptive-routing/
├── README.md
│
├── controller/                           # Ryu SDN controller
│   ├── controller_qlearning.py           # ← Q-Learning (UTAMA)
│   ├── controller_dijkstra.py            # ← Dijkstra baseline
│   └── index.html                        # Web UI dashboard (port 9000)
│
├── mininet/                              # Topologi & emulator
│   ├── topology_qlearning.py             # ← Topologi Q-Learning (UTAMA)
│   ├── topology_dijkstra.py              # ← Topologi Dijkstra baseline
│   └── agent_reporter_04_kpi_aktual.py   # ← Agen telemetri KPI (UTAMA)
│
└── docs/                                 # Dokumentasi sistem
    ├── PERANCANGAN_SISTEM.md             # Bab perancangan sistem lengkap
    ├── DIAGRAM_ALUR_SISTEM.md            # Diagram arsitektur & alur data
    ├── FLOWCHART_SISTEM_SDN.md           # Flowchart semua komponen (Mermaid)
    ├── REVIEW_FITUR_CONTROLLER.md        # Detail fitur Ryu controller
    ├── REVIEW_FITUR_MININET.md           # Detail fitur topologi Mininet
    ├── REVIEW_FITUR_AGENT.md             # Detail fitur agen KPI
    └── pengujian_sdn_vs_ospf.md          # Hasil & metodologi pengujian
```

---

## Prasyarat & Instalasi

### Sistem Operasi
Ubuntu 20.04 atau 22.04 (direkomendasikan di VM atau bare metal — bukan WSL).

### 1. Instalasi Mininet & Open vSwitch

```bash
sudo apt-get update
sudo apt-get install -y mininet openvswitch-switch
```

### 2. Instalasi Python Packages

```bash
pip install -r controller/requirements.txt
```

Atau install manual satu per satu:

```bash
pip install ryu==4.34 networkx==2.6.3 webob==1.8.7 eventlet==0.30.2
```

> **Catatan**: Ryu tidak kompatibel dengan Python 3.10+. Gunakan Python 3.8 atau 3.9.
> Cek versi Python dengan `python3 --version`.

### 3. Verifikasi Instalasi

```bash
# Cek Mininet
mn --version

# Cek Ryu
ryu-manager --version

# Cek OVS
ovs-vsctl --version

# Cek NetworkX
python3 -c "import networkx; print(networkx.__version__)"
```

---

## Cara Menjalankan

> Semua perintah dijalankan dari direktori root repo. Mininet memerlukan `sudo`.

### Sistem Q-Learning (Utama)

Membutuhkan **3 terminal** yang berjalan bersamaan:

**Terminal 1 — Jalankan Ryu controller Q-Learning:**
```bash
ryu-manager --wsapi-host 0.0.0.0 --wsapi-port 8080 controller/controller_qlearning.py --observe-links
```

**Terminal 2 — Jalankan topologi Mininet:**
```bash
sudo python3 mininet/topology_qlearning.py
```

**Terminal 3 — Jalankan Web UI dashboard:**
```bash
cd controller
python3 -m http.server 9000
```

Controller akan melalui fase startup otomatis (~20 detik):
1. Membaca `host_topology.json` dari Mininet
2. Menunggu KPI pertama dari agen monitoring
3. Warmup 10 detik (semua routing via Dijkstra)
4. Menunggu LLDP melengkapi 11 link
5. `rl_ready = True` → Q-Learning aktif

### Sistem Dijkstra Baseline (Perbandingan)

**Terminal 1 — Jalankan Ryu controller Dijkstra:**
```bash
ryu-manager controller/controller_dijkstra.py --observe-links
```

**Terminal 2 — Jalankan topologi Mininet Dijkstra:**
```bash
sudo python3 mininet/topology_dijkstra.py
```

### Web UI Dashboard (Q-Learning)

Setelah Terminal 3 berjalan, buka browser dan akses:
```
http://localhost:9000
```
Data topologi real-time (nilai Q, reward, KPI per-link, epsilon, active path)
diambil dari Ryu WSGI API di port 8080 (`--wsapi-host 0.0.0.0 --wsapi-port 8080`).

### Membersihkan Sisa Proses

Jika topologi dihentikan tidak normal dan ada proses tersisa:
```bash
sudo killall python3
sudo mn -c
```

---

## Skenario Pengujian

Pengujian dilakukan dengan menginjeksi **kongesti manual** via `tc netem` dari Mininet CLI
setelah sistem berjalan dan Q-Learning konvergen. Tiga skenario dirancang dengan kombinasi
level injeksi yang berbeda pada link-link krusial.

### Level Injeksi Kongesti

Seluruh pengujian mengacu pada empat level injeksi berikut:

| Level Injeksi | Delay (ms) | Jitter (ms) | Packet Loss (%) |
|---|---|---|---|
| 0 | 4–8 | 1–3 | 0 |
| 1 | 33–34 | 9–9.5 | 0 |
| 2 | 58–59 | 16–18 | 0.2 |
| 3 | 94 | 28 | 3 |

Level 0 adalah kondisi baseline jaringan (normal, tanpa injeksi).
Level 1–3 diinjeksikan via `tc netem` dari Mininet CLI pada link-link krusial.

> **Catatan port**: Q-Learning memiliki 2 host per switch (Hx + HMx), sehingga inter-switch
> port dimulai dari **eth3**. Dijkstra hanya 1 host per switch, sehingga mulai dari **eth2**.
> Cek nomor port yang benar dengan perintah `sX ip link` di Mininet CLI.

---

### Pengujian 1 — Level Injeksi 1

Link krusial S2-S5, S3-S6, dan S7-S9 mendapat injeksi level 1 (delay 33–34ms, jitter 9–12ms, loss 0%).
Diharapkan Q-Learning mulai mengalihkan traffic ke jalur alternatif (misal via S4-S6-S8-S9).

| Link | Tipe | Delay (ms) | Jitter (ms) | Packet Loss (%) | Status |
|---|---|---|---|---|---|
| S1-S2 | Non-krusial | 4 | 1 | 0 | Normal |
| S1-S3 | Non-krusial | 4 | 1 | 0 | Normal |
| S1-S4 | Non-krusial | 4 | 1 | 0 | Normal |
| S2-S5 | **Krusial** | **33** | **9** | 0 | ⚠️ Level Injeksi 1 |
| S3-S6 | **Krusial** | **33** | **9** | 0 | ⚠️ Level Injeksi 1 |
| S4-S6 | Krusial | 6 | 2 | 0 | Normal |
| S5-S6 | Non-krusial | 4 | 1 | 0 | Normal |
| S5-S7 | Non-krusial | 4 | 1 | 0 | Normal |
| S6-S8 | Non-krusial | 4 | 1 | 0 | Normal |
| S7-S9 | **Krusial** | **34** | **12** | 0 | ⚠️ Level Injeksi 1 |
| S8-S9 | Krusial | 8 | 3 | 0 | Normal |

```bash
# Injeksi — Q-Learning (inter-switch mulai eth3)
mininet> s2 tc qdisc add dev s2-eth3 root netem delay 33ms 9ms loss 0%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 33ms 9ms loss 0%
mininet> s7 tc qdisc add dev s7-eth2 root netem delay 34ms 12ms loss 0%

# Injeksi — Dijkstra (inter-switch mulai eth2)
mininet> s2 tc qdisc add dev s2-eth2 root netem delay 33ms 9ms loss 0%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 33ms 9ms loss 0%
mininet> s7 tc qdisc add dev s7-eth2 root netem delay 34ms 12ms loss 0%
```

---

### Pengujian 2 — Level Injeksi 1 & 2

Link S2-S5, S3-S6, dan S8-S9 mendapat injeksi level 2 (delay 58–59ms, jitter 16–18ms, loss 0.2%).
S4-S6 mendapat injeksi level 1. Diharapkan Q-Learning menemukan jalur terbaik
yang menghindari link terdegradasi.

| Link | Tipe | Delay (ms) | Jitter (ms) | Packet Loss (%) | Status |
|---|---|---|---|---|---|
| S1-S2 | Non-krusial | 4 | 1 | 0 | Normal |
| S1-S3 | Non-krusial | 4 | 1 | 0 | Normal |
| S1-S4 | Non-krusial | 4 | 1 | 0 | Normal |
| S2-S5 | **Krusial** | **58** | **16** | **0.2** | 🔶 Level Injeksi 2 |
| S3-S6 | **Krusial** | **58** | **16** | **0.2** | 🔶 Level Injeksi 2 |
| S4-S6 | **Krusial** | **33** | **9** | 0 | ⚠️ Level Injeksi 1 |
| S5-S6 | Non-krusial | 4 | 1 | 0 | Normal |
| S5-S7 | Non-krusial | 4 | 1 | 0 | Normal |
| S6-S8 | Non-krusial | 4 | 1 | 0 | Normal |
| S7-S9 | Krusial | 8 | 3 | 0 | Normal |
| S8-S9 | **Krusial** | **59** | **18** | **0.2** | 🔶 Level Injeksi 2 |

```bash
# Injeksi — Q-Learning (inter-switch mulai eth3)
mininet> s2 tc qdisc add dev s2-eth3 root netem delay 58ms 16ms loss 0.2%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s4 tc qdisc add dev s4-eth2 root netem delay 33ms 9ms loss 0%
mininet> s8 tc qdisc add dev s8-eth2 root netem delay 59ms 18ms loss 0.2%

# Injeksi — Dijkstra (inter-switch mulai eth2)
mininet> s2 tc qdisc add dev s2-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s4 tc qdisc add dev s4-eth2 root netem delay 33ms 9ms loss 0%
mininet> s8 tc qdisc add dev s8-eth2 root netem delay 59ms 18ms loss 0.2%
```

---

### Pengujian 3 — Level Injeksi 1, 2, dan 3

Kondisi paling ekstrem: S2-S5 injeksi level 1, S3-S6 dan S4-S6 injeksi level 2,
S7-S9 injeksi level 2, dan S8-S9 injeksi level 3 (delay 94ms, jitter 28ms, loss 3%).
Diharapkan Q-Learning mampu mengidentifikasi jalur yang masih layak digunakan.

| Link | Tipe | Delay (ms) | Jitter (ms) | Packet Loss (%) | Status |
|---|---|---|---|---|---|
| S1-S2 | Non-krusial | 4 | 1 | 0 | Normal |
| S1-S3 | Non-krusial | 4 | 1 | 0 | Normal |
| S1-S4 | Non-krusial | 4 | 1 | 0 | Normal |
| S2-S5 | **Krusial** | **33** | **9** | 0 | ⚠️ Level Injeksi 1 |
| S3-S6 | **Krusial** | **58** | **16** | **0.2** | 🔶 Level Injeksi 2 |
| S4-S6 | **Krusial** | **58** | **16** | **0.2** | 🔶 Level Injeksi 2 |
| S5-S6 | Non-krusial | 4 | 1 | 0 | Normal |
| S5-S7 | Non-krusial | 4 | 1 | 0 | Normal |
| S6-S8 | Non-krusial | 4 | 1 | 0 | Normal |
| S7-S9 | **Krusial** | **59** | **18** | **0.2** | 🔶 Level Injeksi 2 |
| S8-S9 | **Krusial** | **94** | **28** | **3** | 🔴 Level Injeksi 3 |

```bash
# Injeksi — Q-Learning (inter-switch mulai eth3)
mininet> s2 tc qdisc add dev s2-eth3 root netem delay 33ms 9ms loss 0%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s4 tc qdisc add dev s4-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s7 tc qdisc add dev s7-eth2 root netem delay 59ms 18ms loss 0.2%
mininet> s8 tc qdisc add dev s8-eth2 root netem delay 94ms 28ms loss 3%

# Injeksi — Dijkstra (inter-switch mulai eth2)
mininet> s2 tc qdisc add dev s2-eth2 root netem delay 33ms 9ms loss 0%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s4 tc qdisc add dev s4-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s7 tc qdisc add dev s7-eth2 root netem delay 59ms 18ms loss 0.2%
mininet> s8 tc qdisc add dev s8-eth2 root netem delay 94ms 28ms loss 3%
```

---

### Mengukur KPI Setelah Injeksi

```bash
# Ping 100 paket ke server H9
mininet> h1 ping -c 100 -i 0.1 10.0.0.9

# Cek jalur aktif
mininet> h1 traceroute -n 10.0.0.9
```

### Reset Semua Kongesti

```bash
# Q-Learning
mininet> s2 tc qdisc del dev s2-eth3 root
mininet> s3 tc qdisc del dev s3-eth2 root
mininet> s4 tc qdisc del dev s4-eth2 root
mininet> s7 tc qdisc del dev s7-eth2 root
mininet> s8 tc qdisc del dev s8-eth2 root

# Dijkstra
mininet> s2 tc qdisc del dev s2-eth2 root
mininet> s3 tc qdisc del dev s3-eth2 root
mininet> s4 tc qdisc del dev s4-eth2 root
mininet> s7 tc qdisc del dev s7-eth2 root
mininet> s8 tc qdisc del dev s8-eth2 root
```

---

## Parameter Sistem

### Q-Learning

| Parameter | Nilai | Keterangan |
|---|---|---|
| `ALPHA` | 0.7 | Learning rate Bellman |
| `GAMMA` | 0.9 | Discount factor (future reward) |
| `EPSILON_START` | 1.0 | Eksplorasi awal (100% random) |
| `EPSILON_MIN` | 0.05 | Eksplorasi minimum (5%) |
| `EPSILON_DECAY_STEP` | 0.1 | Penurunan epsilon per tahap |
| `RL_INTERVAL` | 2.0 s | Interval siklus Q-Learning |
| `WARMUP_DELAY` | 10 s | Waktu warmup sebelum RL aktif |

### Reward / Fungsi Biaya

| Parameter | Nilai | Keterangan |
|---|---|---|
| `W_LATENCY` | 1.0 | Bobot latency dalam cost |
| `W_JITTER` | 5.0 | Bobot jitter dalam cost |
| `W_LOSS` | 100.0 | Bobot packet loss (sangat dihindari) |
| `SCALING_FACTOR` | 10.0 | Skala reward: `reward = -10·cost` |
| `HOP_PENALTY` | 5.0 | Penalti per hop (jalur pendek diutamakan) |
| `HYSTERESIS_THRESHOLD` | 3.0 | Selisih Q minimum untuk pindah jalur |

### Agen KPI

| Parameter | Nilai | Keterangan |
|---|---|---|
| `PING_COUNT` | 100 paket | Per siklus pengukuran |
| `PING_INTERVAL` | 10 ms | Jeda antar paket |
| `LOSS_WINDOW` | 5 siklus | Akumulasi loss (500 paket total) |
| Resolusi loss | **0.2%** | = 1 / (100 × 5) |
| Siklus total | ~1.5 detik | Lebih cepat dari RL interval (2 detik) |

---

## Perbandingan Q-Learning vs OSPF

| Aspek | Q-Learning (SDN) | OSPF (Baseline) |
|---|---|---|
| **Dasar pemilihan jalur** | KPI aktual (lat/jit/loss) | Hop count (Dijkstra) |
| **Reaksi terhadap degradasi KPI** | ✅ Berpindah jalur otomatis | ❌ Tidak berubah |
| **Reaksi terhadap link DOWN** | ✅ Fallback Dijkstra + reconverge | ✅ Recalculate Dijkstra |
| **Reverse path** | ✅ Cermin forward (simetris) | Dijkstra |
| **Agent monitoring** | 22 agen bidirectional | Tidak ada |
| **Convergence metric** | Convergence Timer (detik) | Dijkstra duration (ms) |
| **Konfigurasi** | Otomatis (online learning) | Statis |

### Perbedaan dengan OSPF Asli (RFC 2328)

Perlu dicatat dalam konteks akademik bahwa SDN-OSPF yang diimplementasikan
ini sedikit berbeda dari OSPF sesungguhnya:

- **OSPF asli**: deteksi link down via Hello/Dead interval (~10s/40s)
- **SDN-OSPF ini**: deteksi via LLDP OpenFlow (~4–6 detik) → lebih cepat
- **OSPF asli**: cost = 10⁸ / bandwidth
- **SDN-OSPF ini**: semua link cost = 1 (hop count) → ekivalen karena semua link TCLink tanpa parameter bandwidth

---

## Dokumentasi

Dokumentasi lengkap tersedia di folder `docs/`:

| File | Isi |
|---|---|
| `PERANCANGAN_SISTEM.md` | Bab perancangan sistem dari data plane, telemetri, hingga control plane |
| `DIAGRAM_ALUR_SISTEM.md` | Diagram arsitektur komponen, bootstrapping, dan loop runtime |
| `FLOWCHART_SISTEM_SDN.md` | Flowchart semua komponen dalam format Mermaid (11 diagram) |
| `REVIEW_FITUR_CONTROLLER.md` | Detail setiap fitur Ryu controller beserta fungsinya |
| `REVIEW_FITUR_MININET.md` | Detail setiap fitur topologi Mininet beserta fungsinya |
| `REVIEW_FITUR_AGENT.md` | Detail setiap fitur agen KPI beserta fungsinya |
| `pengujian_sdn_vs_ospf.md` | Metodologi dan hasil pengujian perbandingan |

---

## Teknologi yang Digunakan

- **[Ryu SDN Framework](https://ryu-sdn.org/)** — controller OpenFlow
- **[Mininet](http://mininet.org/)** — emulator jaringan
- **[Open vSwitch](https://www.openvswitch.org/)** — switch virtual (OpenFlow 1.3)
- **[NetworkX](https://networkx.org/)** — analisis graf topologi (Dijkstra)
- **Python 3** — semua komponen (controller, mininet, agen)
- **OpenFlow 1.3** — protokol SDN southbound

---

## Lisensi

Proyek ini dibuat untuk keperluan akademik (Tugas Akhir).
Lihat file `LICENSE` untuk detail.
