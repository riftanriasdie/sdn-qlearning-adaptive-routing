# Flowchart Sistem SDN — Q-Learning Adaptive Routing

Dokumen ini menyajikan flowchart lengkap sistem SDN berbasis Q-Learning yang mencakup
tiga komponen utama: **Mininet** (data plane), **Agen KPI** (telemetri), dan
**Ryu Controller** (control plane).

---

## 1. Flowchart Arsitektur Komponen Sistem

```mermaid
flowchart LR
    subgraph DP["🖧 DATA PLANE — Mininet"]
        direction TB
        SW["Switch S1..S9\n(OVSKernelSwitch\nOpenFlow 1.3)"]
        HX["Host User Hx\n10.0.0.x\n(traffic data)"]
        HM["Host Monitor HMx\n10.0.0.x0\n(target ping)"]
        HX --- SW
        HM --- SW
    end

    subgraph SENSOR["📡 TELEMETRI — Agen KPI"]
        AG["22 Agen KPI\n(proses independen\ndi tiap HMx)\nbidirectional"]
    end

    subgraph BUS["💾 /tmp — Message Bus"]
        TOPO[("host_topology.json\n(lokasi server H9)")]
        REP[("link_report_src_dst.json\n(lat / jit / loss)\n~22 berkas)")]
    end

    subgraph CP["🧠 CONTROL PLANE — Ryu Controller"]
        CTRL["TAMainController\nQ-Learning + FlowMod\nWeb API :8080"]
    end

    SW -- "tulis 1x (startup)" --> TOPO
    AG -- "ping ICMP (data plane)" --> SW
    AG -- "tulis tiap ~1.5s\n(atomic write)" --> REP
    TOPO -- "baca 1x (startup)" --> CTRL
    REP -- "baca loop tiap 1s" --> CTRL
    CTRL -- "FlowMod\n(install/hapus rule)" --> SW
    SW -- "packet-in / LLDP" --> CTRL
```

---

## 2. Flowchart Topologi Jaringan

```mermaid
graph LR
    H1["H1\n10.0.0.1"] --- S1((S1))
    HM1["HM1\n10.0.0.10"] --- S1

    H2["H2\n10.0.0.2"] --- S2((S2))
    HM2["HM2\n10.0.0.20"] --- S2

    H3["H3\n10.0.0.3"] --- S3((S3))
    HM3["HM3\n10.0.0.30"] --- S3

    H4["H4\n10.0.0.4"] --- S4((S4))
    HM4["HM4\n10.0.0.40"] --- S4

    H5["H5\n10.0.0.5"] --- S5((S5))
    HM5["HM5\n10.0.0.50"] --- S5

    H6["H6\n10.0.0.6"] --- S6((S6))
    HM6["HM6\n10.0.0.60"] --- S6

    H7["H7\n10.0.0.7"] --- S7((S7))
    HM7["HM7\n10.0.0.70"] --- S7

    H8["H8\n10.0.0.8"] --- S8((S8))
    HM8["HM8\n10.0.0.80"] --- S8

    H9["H9 SERVER\n10.0.0.9"] --- S9((S9))
    HM9["HM9\n10.0.0.90"] --- S9

    S1 --- S2
    S1 --- S3
    S1 --- S4
    S2 --- S5
    S3 --- S6
    S4 --- S6
    S5 --- S6
    S5 --- S7
    S6 --- S8
    S7 --- S9
    S8 --- S9

    classDef server fill:#FF8C00,stroke:#333,color:#fff,font-weight:bold
    classDef serverSW fill:#cc5500,stroke:#333,color:#fff,font-weight:bold
    classDef userHost fill:#2B7CE9,stroke:#333,color:#fff
    classDef monHost fill:#2E8B57,stroke:#333,color:#fff
    classDef sw fill:#555,stroke:#333,color:#fff

    class H9 server
    class S9 serverSW
    class H1,H2,H3,H4,H5,H6,H7,H8 userHost
    class HM1,HM2,HM3,HM4,HM5,HM6,HM7,HM8,HM9 monHost
    class S1,S2,S3,S4,S5,S6,S7,S8 sw
```

---

## 3. Flowchart Proses Bootstrapping (Urutan Startup)

```mermaid
flowchart TD
    START([🚀 MULAI]) --> MN_BUILD

    subgraph MININET["Mininet — Startup"]
        MN_BUILD["Bangun topologi:\nS1..S9, H1..H9, HM1..HM9\nRemoteController + TCLink\nOpenFlow 1.3"]
        MN_ARP["Pasang Static ARP\ndi semua host"]
        MN_EXPORT["Tulis\n/tmp/host_topology.json\n(lokasi H1..H9 + port + dpid)"]
        MN_SPAWN["Spawn 22 agen KPI\nbidirectional\n(nohup & di HMx)"]
        MN_CLI["Masuk Mininet CLI\n(siap untuk uji manual)"]
        MN_BUILD --> MN_ARP --> MN_EXPORT --> MN_SPAWN --> MN_CLI
    end

    subgraph AGEN["Agen KPI — Startup"]
        AG_INIT["Inisialisasi:\nambil argumen\nmy_sw, target_sw, target_ip"]
        AG_LOOP["Masuk loop utama\n(lihat Flowchart 5)"]
        AG_INIT --> AG_LOOP
    end

    subgraph RYU["Ryu Controller — Startup"]
        RYU_SPAWN["Spawn 5 green thread\nparalel (hub.spawn)"]
        RYU_WAIT_TOPO["_startup_logic:\nTunggu host_topology.json\n(timeout 60s)"]
        RYU_READ_TOPO["Baca host_topology.json\nIdentifikasi server_dpid\n& server_port (H9)"]
        RYU_WAIT_KPI["Tunggu KPI pertama\nmasuk ke link_stats"]
        RYU_WARMUP["Warmup 10 detik\n(semua traffic pakai Dijkstra)"]
        RYU_LLDP["Tunggu LLDP\nmelengkapi 11 link\n(timeout 30s)"]
        RYU_READY["rl_ready = True ✅\nQ-Learning aktif"]

        RYU_SPAWN --> RYU_WAIT_TOPO
        RYU_WAIT_TOPO -->|"file tersedia"| RYU_READ_TOPO
        RYU_WAIT_TOPO -->|"timeout 60s"| RYU_ERR["❌ Error: topologi\ntidak tersedia"]
        RYU_READ_TOPO --> RYU_WAIT_KPI
        RYU_WAIT_KPI -->|"KPI tersedia"| RYU_WARMUP
        RYU_WARMUP --> RYU_LLDP
        RYU_LLDP -->|"≥11 link"| RYU_READY
    end

    MN_EXPORT -.->|"file tersedia"| RYU_WAIT_TOPO
    MN_SPAWN -.->|"jalankan"| AG_INIT
    AG_LOOP -.->|"link_report_*.json"| RYU_WAIT_KPI
```

---

## 4. Flowchart Agen KPI — Loop Pengukuran

```mermaid
flowchart TD
    A_START([▶ Agen mulai\ndi HMx]) --> A_INIT["Inisialisasi:\nloss_window = deque maxlen=5\nsiapkan nama file output"]

    A_INIT --> A_PING["Jalankan ping\n100 paket @ 10ms\nke target HMy\nstart_new_session=True"]

    A_PING --> A_CHECK{Semua ping\ntimeout?}

    A_CHECK -->|"Ya (link mati)"| A_DEAD["lat = 9999\njit = 0\nloss = 100%\n(sinyal link mati)"]
    A_CHECK -->|"Tidak"| A_PARSE["Parse output ping\nper-paket (tanpa -q)\nicmp_seq + time= ms"]

    A_PARSE --> A_LAT["Hitung Latency:\navg_lat = sum(RTT) / count\n(rata-rata RTT murni)"]
    A_LAT --> A_JIT["Hitung Jitter RFC 3393:\njitter = mean(|RTT[n] - RTT[n-1]|)\nskip jika ada gap seq"]
    A_JIT --> A_LOSS_RAW["Tambah ke loss_window:\n(sent=100, lost=gap_seq)\ndeque maxlen=5"]

    A_LOSS_RAW --> A_LOSS_CALC["Hitung Loss:\ntotal_lost / total_sent × 100\n(akumulasi 500 paket\nresolusi 0.2%)"]

    A_DEAD --> A_WRITE
    A_LOSS_CALC --> A_WRITE

    A_WRITE["Tulis ke .tmp:\n{src_sw, dst_sw, lat, jit, loss,\ntimestamp}"]
    A_WRITE --> A_SYNC["fsync → os.rename\n(atomic write)"]
    A_SYNC --> A_SLEEP["Sleep 0.5s"]
    A_SLEEP --> A_PING

    style A_DEAD fill:#c0392b,color:#fff
    style A_SYNC fill:#27ae60,color:#fff
```

---

## 5. Flowchart Controller Ryu — Loop Runtime Q-Learning (tiap 2 detik)

```mermaid
flowchart TD
    RL_START([🔄 _rl_background_worker\ntiap RL_INTERVAL = 2s])

    RL_START --> RL_CHECK{rl_ready?}
    RL_CHECK -->|"Tidak"| RL_WAIT["Tunggu...\n(Dijkstra aktif)"]
    RL_WAIT --> RL_CHECK

    RL_CHECK -->|"Ya"| RL_KPI["① Ambil KPI dari link_stats\n{lat, jit, loss}\nper pasangan switch"]

    RL_KPI --> RL_STALE{Data stale?\nlat>1000 atau loss>20\ndan Q < -100}
    RL_STALE -->|"Ya"| RL_RESET_Q["Auto-reset Q ke 0\n(pulih dari data error)"]
    RL_STALE -->|"Tidak"| RL_REWARD

    RL_RESET_Q --> RL_REWARD

    RL_REWARD["② Hitung Reward:\ncost = W_LAT×lat + W_JIT×jit + W_LOSS×loss\n(W: 1, 5, 100)\nreward = -SCALE×cost - HOP_PENALTY\n(SCALE=10, HOP=5)"]

    RL_REWARD --> RL_UPDATE["③ Update Q-Table (Bellman):\nQ(s,a) ← Q(s,a) + α×[reward + γ×maxQ(s',a') - Q(s,a)]\nα=0.7, γ=0.9\n+ Poison Reverse (exclude port balik)\n+ Loop Penalty kumulatif (jika loop)"]

    RL_UPDATE --> RL_EPS["ε-greedy:\nε turun 1.0 → 0.05\n(eksplorasi → eksploitasi)"]

    RL_EPS --> RL_TRACE["④ Greedy Trace:\nS1 → ... → S9\npilih port Q-terbaik + Hysteresis (3.0)\n(tidak pindah jalur kecuali unggul ≥3.0)"]

    RL_TRACE --> RL_LOOP_CHECK{Path valid?\n(ada loop?)}
    RL_LOOP_CHECK -->|"Loop terdeteksi"| RL_LOOP_PEN["Tambah Loop Penalty +100\npada port bermasalah"]
    RL_LOOP_PEN --> RL_GC

    RL_LOOP_CHECK -->|"Valid"| RL_PUSH_FWD["⑤a Push FORWARD Rule\nmatch: ipv4_dst=10.0.0.9\nprio=100\n(tiap switch di active path)"]

    RL_PUSH_FWD --> RL_PUSH_REV["⑤b Push REVERSE Rule per-host\nbalik urutan forward path\nmatch: ipv4_src=server, ipv4_dst=host\nprio=15, idle_timeout=30\n(tanpa in_port)"]

    RL_PUSH_REV --> RL_CONV["Catat Convergence Timer\n(durasi rerouting jika path berubah)"]
    RL_CONV --> RL_GC["Garbage Collection:\nhapus Q-entry port tidak valid"]
    RL_GC --> RL_START

    style RL_REWARD fill:#1a5276,color:#fff
    style RL_UPDATE fill:#1a5276,color:#fff
    style RL_PUSH_FWD fill:#1e8449,color:#fff
    style RL_PUSH_REV fill:#1e8449,color:#fff
    style RL_LOOP_PEN fill:#c0392b,color:#fff
```

---

## 6. Flowchart Hierarki Prioritas Flow Rule

```mermaid
flowchart TD
    PKT(["📦 Paket Masuk ke Switch"])

    PKT --> P200{Prio 200\nSERVER_DIRECT\nDi S9: dst=server?}
    P200 -->|"Match"| A200["→ Port host server H9\n(last-mile delivery)"]

    P200 -->|"No match"| P100{Prio 100\nSERVER_FWD\nipv4_dst=10.0.0.9?}
    P100 -->|"Match"| A100["→ Forward Q-Learning\nke server\n(jalur dipilih RL)"]

    P100 -->|"No match"| P15{Prio 15\nREVERSE_Q\nipv4_src=10.0.0.9?}
    P15 -->|"Match"| A15["→ Reverse path\nQ-Learning per-host\n(cermin forward)"]

    P15 -->|"No match"| P10{Prio 10\nHOST_ROUTE\nDijkstra\nin_port+eth_dst?}
    P10 -->|"Match"| A10["→ Routing Dijkstra\nhost-to-host\n(warmup/fallback)"]

    P10 -->|"No match"| P0["Prio 0 TABLE_MISS\n→ Kirim ke controller\n(packet-in)"]

    style A200 fill:#8e44ad,color:#fff
    style A100 fill:#1e8449,color:#fff
    style A15 fill:#2874a6,color:#fff
    style A10 fill:#b7950b,color:#fff
    style P0 fill:#717d7e,color:#fff
```

---

## 7. Flowchart Penanganan Dinamika Topologi (Link UP/DOWN)

```mermaid
flowchart TD
    LLDP["_topology_discovery_loop\n(tiap 2s)"] --> COMPARE["Bandingkan:\ncurrent_edges vs prev_edges"]

    COMPARE --> LINK_DOWN{Ada link\nhilang?}
    COMPARE --> LINK_UP{Ada link\nbaru?}

    LINK_DOWN -->|"Ya"| DN1["Simpan confirmed_down_links\n+ link_penalty_memory\n(catat switch & port terdampak)"]
    DN1 --> DN2["Reset greedy_path_cache\n(paksa recalculate)"]
    DN2 --> DN3["fm.on_link_down:\nHapus semua forward\n+ reverse rule"]
    DN3 --> DN4["Pasang fallback Dijkstra\ndi topologi baru\nidle_timeout=5"]
    DN4 --> DN5["Q-Learning konvergen ulang\ndengan topologi baru"]

    LINK_UP -->|"Ya"| UP1["Simpan link_recovery_timers\nReset path cache"]
    UP1 --> UP2["fm.on_link_up\n(log event)"]
    UP2 --> UP3{Link stabil\n≥ 2 detik?}
    UP3 -->|"Ya"| UP4["_reset_path_q_values:\nReset Q port pulih → 0\nHapus penalty memory\n(beri kesempatan dicoba lagi)"]
    UP4 --> UP5["Grace period 10s:\nhindari port baru\n(tunggu KPI stabil)"]
    UP5 --> UP6["Setelah grace period:\nport tersedia untuk Q-Learning"]

    style DN3 fill:#c0392b,color:#fff
    style DN4 fill:#e67e22,color:#fff
    style UP4 fill:#27ae60,color:#fff
    style UP5 fill:#2874a6,color:#fff
```

---

## 8. Flowchart Penanganan Paket (Packet-In Handler)

```mermaid
flowchart TD
    PIN(["📥 packet_in\ndari switch"]) --> PKT_TYPE{Tipe paket?}

    PKT_TYPE -->|"LLDP"| LLDP_PROC["Proses LLDP:\nupdate graf topologi\n→ abaikan untuk routing"]

    PKT_TYPE -->|"ARP"| ARP_PROC["Flood ARP\nke semua port\n(discovery)"]

    PKT_TYPE -->|"IPv4 → 10.0.0.9\n(server)"| RL_READY_CHECK{rl_ready?}

    PKT_TYPE -->|"IPv4 lainnya"| DIJKSTRA["_handle_shortest_path_routing:\nDijkstra in_port+eth_dst\nprio 10\n(guard: out_port ≠ in_port)"]

    RL_READY_CHECK -->|"Tidak\n(warmup)"| DIJKSTRA
    RL_READY_CHECK -->|"Ya"| RL_ROUTE["_handle_rl_routing:\nPilih aksi Q-Learning\nPasang forward rule prio 100\nKirim paket"]

    DIJKSTRA --> INSTALL_D["Pasang flow rule\nDijkstra di switch"]
    RL_ROUTE --> INSTALL_RL["Pasang flow rule\nQ-Learning di switch"]

    style RL_ROUTE fill:#1e8449,color:#fff
    style DIJKSTRA fill:#b7950b,color:#fff
```

---

## 9. Flowchart Forward vs Reverse Path (Simetri Q-Learning)

```mermaid
flowchart LR
    subgraph FWD["FORWARD PATH (Hx → H9)\nmatch: ipv4_dst=10.0.0.9, prio=100\ndikontrol Q-Learning"]
        direction LR
        H8F["H8"] --> S8F((S8))
        S8F --> S6F((S6))
        S6F --> S4F((S4))
        S4F --> S1F((S1))
        S1F --> S2F((S2))
        S2F --> S5F((S5))
        S5F --> S7F((S7))
        S7F --> S9F((S9))
        S9F --> H9F["H9\nSERVER"]
    end

    subgraph REV["REVERSE PATH (H9 → Hx)\nmatch: ipv4_src=10.0.0.9 + ipv4_dst=host\nprio=15, idle_timeout=30\ncermin forward (file _06)"]
        direction RL
        H9R["H9\nSERVER"] --> S9R((S9))
        S9R --> S7R((S7))
        S7R --> S5R((S5))
        S5R --> S2R((S2))
        S2R --> S1R((S1))
        S1R --> S4R((S4))
        S4R --> S6R((S6))
        S6R --> S8R((S8))
        S8R --> H8R["H8"]
    end

    style H9F fill:#FF8C00,stroke:#333,color:#fff,font-weight:bold
    style H9R fill:#FF8C00,stroke:#333,color:#fff,font-weight:bold
    style S9F fill:#cc5500,stroke:#333,color:#fff
    style S9R fill:#cc5500,stroke:#333,color:#fff
```

---

## 10. Flowchart Thread Paralel Controller

```mermaid
flowchart TD
    CTRL_START(["🚀 Ryu Controller Start"]) --> SPAWN["hub.spawn → 5 Green Thread"]

    SPAWN --> T1["Thread 1\n_startup_logic\nInisialisasi berurutan:\ntopologi → KPI → warmup → LLDP → rl_ready"]
    SPAWN --> T2["Thread 2\n_monitor_reader_loop\nBaca link_report_*.json\ntiap 1s → isi link_stats"]
    SPAWN --> T3["Thread 3\n_topology_discovery_loop\nDeteksi link UP/DOWN\nvia LLDP tiap 2s"]
    SPAWN --> T4["Thread 4\n_rl_background_worker\nQ-Learning + push rule\ntiap 2s (setelah rl_ready)"]
    SPAWN --> T5["Thread 5\n_log_q_table_loop\nCetak Q-table + active path\ntiap 5s (saat konvergen)"]

    T1 -.->|"set rl_ready=True"| T4
    T2 -.->|"update link_stats"| T4
    T3 -.->|"update self.net"| T4
    T4 -.->|"push FlowMod"| SW["Switch S1..S9"]

    style T1 fill:#1a5276,color:#fff
    style T2 fill:#1e8449,color:#fff
    style T3 fill:#7d6608,color:#fff
    style T4 fill:#6e2f7c,color:#fff
    style T5 fill:#424949,color:#fff
```

---

## 11. Flowchart Alur Sistem Keseluruhan (End-to-End)

```mermaid
flowchart TD
    START(["▶ SISTEM DIMULAI"])

    START --> PHASE1

    subgraph PHASE1["FASE 1 — Inisialisasi"]
        P1A["Mininet membangun topologi\nS1..S9 + Hx + HMx\n+ Static ARP"]
        P1B["Mininet tulis\nhost_topology.json → /tmp"]
        P1C["Mininet spawn\n22 agen KPI bidirectional"]
        P1A --> P1B --> P1C
    end

    PHASE1 --> PHASE2

    subgraph PHASE2["FASE 2 — Warmup (≈20 detik)"]
        P2A["Ryu baca host_topology.json\nidentifikasi server H9"]
        P2B["Agen mulai ping & tulis\nlink_report_*.json → /tmp"]
        P2C["Ryu baca link_report\nisi link_stats"]
        P2D["LLDP discovery\n11 inter-switch link"]
        P2E["Warmup 10s:\nsemua routing via Dijkstra"]
        P2A --> P2B --> P2C --> P2D --> P2E
    end

    PHASE2 --> PHASE3

    subgraph PHASE3["FASE 3 — Operasi Normal\n(rl_ready = True)"]
        P3A["Agen KPI:\nping → ukur lat/jit/loss\ntulis atomic tiap ~1.5s"]
        P3B["Ryu baca KPI tiap 1s"]
        P3C["Q-Learning tiap 2s:\nhitung reward → update Q-Table\ngreedy trace → push flow rule"]
        P3D["Switch menjalankan\nforward + reverse\nflow rule Q-Learning"]
        P3E["Traffic Hx → H9\nlewat jalur optimal\nyang dipelajari"]
        P3A --> P3B --> P3C --> P3D --> P3E
        P3E -.->|"loop terus"| P3A
    end

    PHASE3 --> PHASE4

    subgraph PHASE4["FASE 4 — Gangguan & Pemulihan"]
        P4A{Link down?}
        P4B["Fallback Dijkstra\n(jaga konektivitas)"]
        P4C["Q-Learning konvergen\nke jalur alternatif"]
        P4D["Link pulih:\nreset Q + grace period 10s"]
        P4E["Q-Learning kembali\noptimal"]
        P4A -->|"Ya"| P4B --> P4C
        P4A -->|"Pulih"| P4D --> P4E
        P4E -.->|"kembali normal"| PHASE3
    end

    PHASE4 --> PHASE5

    subgraph PHASE5["FASE 5 — Selesai"]
        P5A["User exit CLI Mininet"]
        P5B["Cleanup:\nkillall python3\n(agen di HMx + level sistem)"]
        P5C["net.stop()\nTopologi dihentikan"]
        P5A --> P5B --> P5C
    end

    PHASE5 --> END(["⏹ SISTEM BERHENTI"])

    style PHASE1 fill:#1a3a4a,color:#fff
    style PHASE2 fill:#1a4a2a,color:#fff
    style PHASE3 fill:#3a1a4a,color:#fff
    style PHASE4 fill:#4a2a1a,color:#fff
    style PHASE5 fill:#3a3a3a,color:#fff
```

---

## Ringkasan Komponen Flowchart

| # | Flowchart | Komponen |
|---|-----------|----------|
| 1 | Arsitektur Komponen & Message Bus | Sistem keseluruhan |
| 2 | Topologi Jaringan 9 Switch | Mininet |
| 3 | Proses Bootstrapping / Urutan Startup | Mininet + Agen + Ryu |
| 4 | Loop Pengukuran KPI | Agen KPI |
| 5 | Loop Runtime Q-Learning (tiap 2s) | Ryu Controller |
| 6 | Hierarki Prioritas Flow Rule | Ryu / FlowRuleManager |
| 7 | Penanganan Dinamika Topologi (Link UP/DOWN) | Ryu Controller |
| 8 | Penanganan Paket (Packet-In Handler) | Ryu Controller |
| 9 | Forward vs Reverse Path Simetris | Ryu Controller |
| 10 | Thread Paralel Controller | Ryu Controller |
| 11 | Alur Sistem Keseluruhan (End-to-End) | Sistem keseluruhan |
