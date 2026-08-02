# Pengujian Perbandingan Sistem SDN Q-Learning vs Dijkstra

---

## Topologi Jaringan

```
        S2 ── S5 ──┐
       /        \   \
   S1 ─ S3 ── S6 ── S7 ── S9 (SERVER H9: 10.0.0.9)
       \      /  \   /
        S4 ──┘    S8 ┘
```

Inter-switch links: `S1-S2, S1-S3, S1-S4, S2-S5, S3-S6, S4-S6, S5-S6, S5-S7, S6-S8, S7-S9, S8-S9`

**Host:** H1@S1, H2@S2, H3@S3, H4@S4, H5@S5, H6@S6, H7@S7, H8@S8, H9@S9 (server)
**Host monitoring:** HM1@S1 – HM9@S9 (khusus Q-Learning, untuk agen KPI)

---

## Level Injeksi Kongesti

Seluruh pengujian menggunakan empat level injeksi yang telah didefinisikan:

| Level Injeksi | Delay (ms) | Jitter (ms) | Packet Loss (%) |
|---|---|---|---|
| 0 | 4–8 | 1–3 | 0 |
| 1 | 33–34 | 9–9.5 | 0 |
| 2 | 58–59 | 16–18 | 0.2 |
| 3 | 94 | 28 | 3 |

Level 0 merupakan kondisi baseline jaringan (normal tanpa injeksi).
Level 1–3 diinjeksikan via `tc netem` dari Mininet CLI pada link-link krusial.

---

## Jalur Alternatif S1 → S9

| Jalur | Rute | Hop | Link Krusial yang Dilalui |
|---|---|---|---|
| **A** | S1→S2→S5→S7→S9 | 4 | S2─S5, S7─S9 |
| **B** | S1→S2→S5→S6→S8→S9 | 5 | S2─S5, S8─S9 |
| **C** | S1→S3→S6→S8→S9 | 4 | S3─S6, S8─S9 |
| **D** | S1→S4→S6→S8→S9 | 4 | S4─S6, S8─S9 |
| **E** | S1→S3→S6→S5→S7→S9 | 5 | S3─S6, S7─S9 |
| **F** | S1→S4→S6→S5→S7→S9 | 5 | S4─S6, S7─S9 |

**Dijkstra** selalu memilih hop minimum → jalur A, C, atau D (4 hop).
**Q-Learning** dapat memilih B, E, atau F (5 hop) jika KPI-nya lebih baik dari jalur 4 hop yang terkongesti.

---

## Identifikasi Link Krusial

```
LAYER TENGAH (aggregation)       LAYER AKHIR (last-mile ke S9)
──────────────────────────       ─────────────────────────────
  S2─S5  (jalur via S2)            S7─S9  (masuk S9 dari S7)
  S3─S6  (jalur via S3)            S8─S9  (masuk S9 dari S8)
  S4─S6  (jalur via S4)
```

Link non-krusial (S1─S2, S1─S3, S1─S4, S5─S6, S5─S7, S6─S8) selalu berada di kondisi
baseline (Level Injeksi 0) di semua pengujian.

---

## Pengujian 1 — Level Injeksi 1

### Kondisi Link

Link krusial S2-S5, S3-S6, dan S7-S9 mendapat injeksi level 1 (delay 33–34 ms,
jitter 9–12 ms, loss 0%). S4-S6 dan S8-S9 tetap normal.
Diharapkan Q-Learning mengalihkan traffic ke jalur via S4-S6-S8-S9 atau S3-S6-S8-S9.

| Link | Tipe | Delay (ms) | Jitter (ms) | Packet Loss (%) | Status |
|---|---|---|---|---|---|
| S1─S2 | Non-krusial | 4 | 1 | 0 | Normal |
| S1─S3 | Non-krusial | 4 | 1 | 0 | Normal |
| S1─S4 | Non-krusial | 4 | 1 | 0 | Normal |
| S2─S5 | **Krusial** | **33** | **9** | 0 | ⚠️ Injeksi 1 |
| S3─S6 | **Krusial** | **33** | **9** | 0 | ⚠️ Injeksi 1 |
| S4─S6 | Krusial | 6 | 2 | 0 | Normal |
| S5─S6 | Non-krusial | 4 | 1 | 0 | Normal |
| S5─S7 | Non-krusial | 4 | 1 | 0 | Normal |
| S6─S8 | Non-krusial | 4 | 1 | 0 | Normal |
| S7─S9 | **Krusial** | **34** | **12** | 0 | ⚠️ Injeksi 1 |
| S8─S9 | Krusial | 8 | 3 | 0 | Normal |

### Perintah Injeksi

```bash
# ── Q-Learning (inter-switch mulai eth3) ─────────────────────────────
mininet> s2 tc qdisc add dev s2-eth3 root netem delay 33ms 9ms loss 0%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 33ms 9ms loss 0%
mininet> s7 tc qdisc add dev s7-eth2 root netem delay 34ms 12ms loss 0%

# ── Dijkstra (inter-switch mulai eth2) ───────────────────────────────
mininet> s2 tc qdisc add dev s2-eth2 root netem delay 33ms 9ms loss 0%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 33ms 9ms loss 0%
mininet> s7 tc qdisc add dev s7-eth2 root netem delay 34ms 12ms loss 0%
```

### Reset Pengujian 1

```bash
# Q-Learning
mininet> s2 tc qdisc del dev s2-eth3 root
mininet> s3 tc qdisc del dev s3-eth2 root
mininet> s7 tc qdisc del dev s7-eth2 root

# Dijkstra
mininet> s2 tc qdisc del dev s2-eth2 root
mininet> s3 tc qdisc del dev s3-eth2 root
mininet> s7 tc qdisc del dev s7-eth2 root
```

---

## Pengujian 2 — Level Injeksi 1 & 2

### Kondisi Link

Link S2-S5, S3-S6, dan S8-S9 mendapat injeksi level 2 (delay 58–59 ms, jitter 16–18 ms,
loss 0.2%). S4-S6 mendapat injeksi level 1. Diharapkan Q-Learning menemukan jalur terbaik
di antara kombinasi kongesti yang lebih bervariasi.

| Link | Tipe | Delay (ms) | Jitter (ms) | Packet Loss (%) | Status |
|---|---|---|---|---|---|
| S1─S2 | Non-krusial | 4 | 1 | 0 | Normal |
| S1─S3 | Non-krusial | 4 | 1 | 0 | Normal |
| S1─S4 | Non-krusial | 4 | 1 | 0 | Normal |
| S2─S5 | **Krusial** | **58** | **16** | **0.2** | 🔶 Injeksi 2 |
| S3─S6 | **Krusial** | **58** | **16** | **0.2** | 🔶 Injeksi 2 |
| S4─S6 | **Krusial** | **33** | **9** | 0 | ⚠️ Injeksi 1 |
| S5─S6 | Non-krusial | 4 | 1 | 0 | Normal |
| S5─S7 | Non-krusial | 4 | 1 | 0 | Normal |
| S6─S8 | Non-krusial | 4 | 1 | 0 | Normal |
| S7─S9 | Krusial | 8 | 3 | 0 | Normal |
| S8─S9 | **Krusial** | **59** | **18** | **0.2** | 🔶 Injeksi 2 |

### Perintah Injeksi

```bash
# ── Q-Learning (inter-switch mulai eth3) ─────────────────────────────
mininet> s2 tc qdisc add dev s2-eth3 root netem delay 58ms 16ms loss 0.2%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s4 tc qdisc add dev s4-eth2 root netem delay 33ms 9ms loss 0%
mininet> s8 tc qdisc add dev s8-eth2 root netem delay 59ms 18ms loss 0.2%

# ── Dijkstra (inter-switch mulai eth2) ───────────────────────────────
mininet> s2 tc qdisc add dev s2-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s4 tc qdisc add dev s4-eth2 root netem delay 33ms 9ms loss 0%
mininet> s8 tc qdisc add dev s8-eth2 root netem delay 59ms 18ms loss 0.2%
```

### Reset Pengujian 2

```bash
# Q-Learning
mininet> s2 tc qdisc del dev s2-eth3 root
mininet> s3 tc qdisc del dev s3-eth2 root
mininet> s4 tc qdisc del dev s4-eth2 root
mininet> s8 tc qdisc del dev s8-eth2 root

# Dijkstra
mininet> s2 tc qdisc del dev s2-eth2 root
mininet> s3 tc qdisc del dev s3-eth2 root
mininet> s4 tc qdisc del dev s4-eth2 root
mininet> s8 tc qdisc del dev s8-eth2 root
```

---

## Pengujian 3 — Level Injeksi 1, 2, & 3

### Kondisi Link

Kondisi paling ekstrem: S2-S5 injeksi level 1, S3-S6 dan S4-S6 injeksi level 2,
S7-S9 injeksi level 2, dan S8-S9 injeksi level 3 (delay 94 ms, jitter 28 ms, loss 3%).
Diharapkan Q-Learning mampu mengidentifikasi jalur yang masih paling layak digunakan
di antara semua link krusial yang terdegradasi.

| Link | Tipe | Delay (ms) | Jitter (ms) | Packet Loss (%) | Status |
|---|---|---|---|---|---|
| S1─S2 | Non-krusial | 4 | 1 | 0 | Normal |
| S1─S3 | Non-krusial | 4 | 1 | 0 | Normal |
| S1─S4 | Non-krusial | 4 | 1 | 0 | Normal |
| S2─S5 | **Krusial** | **33** | **9** | 0 | ⚠️ Injeksi 1 |
| S3─S6 | **Krusial** | **58** | **16** | **0.2** | 🔶 Injeksi 2 |
| S4─S6 | **Krusial** | **58** | **16** | **0.2** | 🔶 Injeksi 2 |
| S5─S6 | Non-krusial | 4 | 1 | 0 | Normal |
| S5─S7 | Non-krusial | 4 | 1 | 0 | Normal |
| S6─S8 | Non-krusial | 4 | 1 | 0 | Normal |
| S7─S9 | **Krusial** | **59** | **18** | **0.2** | 🔶 Injeksi 2 |
| S8─S9 | **Krusial** | **94** | **28** | **3** | 🔴 Injeksi 3 |

### Perintah Injeksi

```bash
# ── Q-Learning (inter-switch mulai eth3) ─────────────────────────────
mininet> s2 tc qdisc add dev s2-eth3 root netem delay 33ms 9ms loss 0%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s4 tc qdisc add dev s4-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s7 tc qdisc add dev s7-eth2 root netem delay 59ms 18ms loss 0.2%
mininet> s8 tc qdisc add dev s8-eth2 root netem delay 94ms 28ms loss 3%

# ── Dijkstra (inter-switch mulai eth2) ───────────────────────────────
mininet> s2 tc qdisc add dev s2-eth2 root netem delay 33ms 9ms loss 0%
mininet> s3 tc qdisc add dev s3-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s4 tc qdisc add dev s4-eth2 root netem delay 58ms 16ms loss 0.2%
mininet> s7 tc qdisc add dev s7-eth2 root netem delay 59ms 18ms loss 0.2%
mininet> s8 tc qdisc add dev s8-eth2 root netem delay 94ms 28ms loss 3%
```

### Reset Pengujian 3

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

## Metodologi Pengujian

### File yang Digunakan

| File | Fungsi |
|---|---|
| `mininet/topology_qlearning.py` | Topologi Mininet untuk Q-Learning |
| `mininet/topology_dijkstra.py` | Topologi Mininet untuk Dijkstra |
| `mininet/agent_reporter_04_kpi_aktual.py` | Agen telemetri KPI (dijalankan otomatis oleh topology_qlearning) |
| `controller/controller_qlearning.py` | Controller Ryu Q-Learning |
| `controller/controller_dijkstra.py` | Controller Ryu Dijkstra |

### Langkah Pengujian Per Skenario

Setiap pengujian dijalankan **dua kali**: pertama dengan **Q-Learning**, kemudian **Dijkstra**
dengan kondisi injeksi kongesti yang identik.

#### Fase A — Q-Learning

**Terminal 1 — Controller Q-Learning:**
```bash
ryu-manager --wsapi-host 0.0.0.0 --wsapi-port 8080 controller/controller_qlearning.py --observe-links
```

**Terminal 2 — Topologi Mininet:**
```bash
sudo python3 mininet/topology_qlearning.py
```

**Terminal 3 — Web UI (opsional, pantau Q-table real-time):**
```bash
cd controller && python3 -m http.server 9000
# Buka browser: http://localhost:9000
```

Setelah Mininet CLI muncul dan controller mencetak `✅ RL ENGINE STARTED`:

1. Injeksi kongesti sesuai skenario (lihat perintah di atas)
2. Tunggu Q-Learning konvergen (~15–30 detik), pantau output controller
3. Konfirmasi jalur aktif: `mininet> h1 traceroute -n 10.0.0.9`
4. Ukur KPI: `mininet> h1 ping -c 100 -i 0.1 10.0.0.9`
5. Reset kongesti, lalu keluar: `mininet> exit`

#### Fase B — Dijkstra (kondisi identik)

**Bersihkan sisa proses:**
```bash
sudo mn -c
sudo killall ryu-manager python3 2>/dev/null
```

**Terminal 1 — Controller Dijkstra:**
```bash
ryu-manager controller/controller_dijkstra.py --observe-links
```

**Terminal 2 — Topologi Mininet:**
```bash
sudo python3 mininet/topology_dijkstra.py
```

1. Injeksi kongesti **identik** dengan Fase A (⚠️ gunakan **eth2** untuk Dijkstra)
2. Dijkstra tidak perlu menunggu konvergensi — routing tidak berubah saat link masih UP
3. Ukur KPI dengan perintah yang sama
4. Reset kongesti, lalu keluar

### Mengukur KPI

```bash
# Ping 100 paket ke server H9
mininet> h1 ping -c 100 -i 0.1 10.0.0.9

# Cek jalur aktif
mininet> h1 traceroute -n 10.0.0.9

# Bisa diuji dari host lain
mininet> h4 ping -c 100 -i 0.1 10.0.0.9
mininet> h7 ping -c 100 -i 0.1 10.0.0.9
```

---

## Catatan Penting — Perbedaan Nomor Port

File Q-Learning memiliki **2 host per switch** (Hx + HMx) sehingga
inter-switch port dimulai dari **eth3**.
File Dijkstra hanya memiliki **1 host per switch** (Hx) sehingga
inter-switch port dimulai dari **eth2**.

Port map lengkap dicetak otomatis saat jaringan dijalankan.
Gunakan `sX ip link` di Mininet CLI untuk konfirmasi nomor port yang benar.

```
Contoh output port map (Q-Learning):
  S2   s2-eth1   H2    → H2 (host)
       s2-eth2   HM2   → HM2 (host monitoring)
       s2-eth3   S1    → S1 (inter-switch) ← tc target
       s2-eth4   S5    → S5 (inter-switch) ← tc target

Contoh output port map (Dijkstra):
  S2   s2-eth1   H2    → H2 (host)
       s2-eth2   S1    → S1 (inter-switch) ← tc target
       s2-eth3   S5    → S5 (inter-switch) ← tc target
```

---

## Analisis Q-Value: Fungsi Reward Controller

Rumus reward dari `controller_qlearning.py`:

```
cost   = W_LATENCY × delay_ms + W_JITTER × jitter_ms + W_LOSS × loss_pct
reward = -(SCALING_FACTOR × cost) - HOP_PENALTY × n_hop

W_LATENCY = 1.0  |  W_JITTER = 5.0  |  W_LOSS = 100.0
SCALING_FACTOR = 10.0  |  HOP_PENALTY = 5.0
```

Bobot `W_LOSS = 100` menjadikan packet loss sebagai faktor paling menentukan —
link dengan loss 0.2% sudah menurunkan reward secara signifikan dibanding link tanpa loss.
Ini yang mendorong Q-Learning untuk menghindari link-link dengan injeksi level 2 dan 3.
