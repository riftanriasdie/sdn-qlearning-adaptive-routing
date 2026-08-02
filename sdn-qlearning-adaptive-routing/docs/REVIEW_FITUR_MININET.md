# Review Fitur Topologi Mininet

File: `perc_ta_mn_final_03_hostmonitoring_02.py`
Fungsi utama: `run_topology()` — membangun data plane + menyalakan telemetri.

Dokumen ini merinci setiap fitur file Mininet beserta fungsinya.

---

## A. Pembentukan Jaringan Dasar

```python
net = Mininet(controller=RemoteController, link=TCLink,
              switch=OVSKernelSwitch, autoSetMacs=True)
```

| Parameter | Fungsi |
|-----------|--------|
| `RemoteController` | Switch tidak dikontrol lokal, melainkan oleh controller Ryu eksternal di `127.0.0.1:6633`. Inilah inti arsitektur SDN (kontrol terpisah dari data plane). |
| `link=TCLink` | Memakai **Traffic Control Link** — memungkinkan penambahan netem (delay/loss/bandwidth) pada link untuk skenario uji KPI. |
| `OVSKernelSwitch` + `protocols='OpenFlow13'` | Switch Open vSwitch berbasis kernel, berbicara OpenFlow 1.3 (sesuai `OFP_VERSIONS` controller). |
| `autoSetMacs=True` | MAC address di-set otomatis & deterministik → memudahkan pemetaan host. |

---

## B. Topologi 9 Switch (S1–S9)

`for i in range(1, 10)` membuat S1–S9. Inter-switch link (11 buah):

```
        S2 ── S5 ──┐
       /        \   \
   S1 ─ S3 ── S6 ── S7 ── S9 (SERVER)
       \      /  \   /
        S4 ──┘    S8 ┘
```
Definisi link sebenarnya di kode:
`S1-S2, S1-S3, S1-S4, S2-S5, S3-S6, S4-S6, S5-S6, S5-S7, S6-S8, S7-S9, S8-S9`.

**Fungsi:** menyediakan **banyak jalur alternatif** dari S1 ke S9 sehingga
Q-Learning punya ruang keputusan (mis. via S2-S5-S7 atau S3/S4-S6-S8). Tanpa
jalur ganda, tidak ada yang perlu "dipelajari".

---

## C. Desain Dua Host per Switch (Fitur Kunci)

Setiap switch punya **2 host** dengan peran berbeda:

| Tipe | Penamaan | IP | Fungsi |
|------|----------|-----|--------|
| Host user | `H1`–`H9` | `10.0.0.x` | Traffic data riil. **H9 = server** (`10.0.0.9`). |
| Host monitoring | `HM1`–`HM9` | `10.0.0.x0` | Khusus agen telemetri KPI (target ping). |

**Fungsi pemisahan ini (alasan desain utama):**
- Traffic monitoring (`HMx→HMy`, IP `10.0.0.x0`) dirutekan **Dijkstra**.
- Traffic user (`Hx→H9`, IP `10.0.0.9`) dirutekan **Q-Learning**.
- Keduanya tidak pernah bentrok flow rule karena IP tujuannya berbeda.

Tanpa pemisahan ini, rule monitoring Dijkstra akan menimpa/mengganggu rule
Q-Learning di switch yang sama.

---

## D. Skema Pengalamatan IP

| Host | IP | Catatan |
|------|-----|---------|
| H1..H9 | `10.0.0.1` .. `10.0.0.9` | H9 = `SERVER_IP` di controller. |
| HM1..HM9 | `10.0.0.10`, `20`, .. `90` | Kelipatan 10, tidak tabrakan dengan host user. |

**Fungsi:** penomoran sistematis (`HMx = 10.0.0.{x*10}`) sehingga agen mudah
menentukan target dan tidak ada konflik alamat dengan H1–H9.

---

## E. Static ARP

```python
for host in all_hosts:
    for other in all_hosts:
        host.cmd('arp -s <ip> <mac>')
```
Memasang entri ARP statis di **semua** host (user + monitoring).

**Fungsi:** menghilangkan ARP request/reply broadcast. Tanpa ini, ARP flooding
akan terus memicu packet-in dan mengotori eksperimen KPI/Q-Learning.

---

## F. Export Host Registry (Orchestrator Mode)

```python
host_registry[h.MAC()] = {'dpid':..., 'port':..., 'ip':...}
json.dump(host_registry, open('/tmp/host_topology.json','w'))
```
Menelusuri link tiap host user untuk menemukan switch & port-nya, lalu
menulisnya ke `/tmp/host_topology.json`. **Hanya host user (H1–H9)** yang
diexport, bukan HMx.

**Fungsi:** "kontrak" pertama ke controller. Controller membaca file ini saat
startup untuk langsung tahu lokasi server H9 (`server_dpid`/`server_port`) tanpa
menunggu packet-in. Ini yang membuat sistem disebut *orchestrator mode*.

---

## G. Menyalakan Agen Monitoring (Bidirectional)

Menjalankan agen di host monitoring via:
```python
hm['hmX'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py <my> <tgt> <ip> ... &')
```

**Dua arah pemantauan:**
- **Arah maju** (menuju server, 11 agen): mis. `HM1→HM2` (link S1-S2),
  `HM7→HM9` (link S7-S9).
- **Arah balik** (11 agen): mis. `HM2→HM1`, `HM9→HM7`.

Total **22 agen**.

**Fungsi arah balik:** agar Q-Table untuk port balik memakai **KPI aktual**,
bukan reward default −20.0. Ini memungkinkan agen mempertimbangkan kondisi link
secara **simetris** — penting karena reverse path file _06 juga melewati link
yang sama dan butuh nilai Q yang valid.

**Catatan penting:** HM7/HM8 ping ke **HM9 (`10.0.0.90`)**, bukan H9
(`10.0.0.9`). Ini disengaja agar traffic monitoring link S7-S9/S8-S9 tidak
bentrok dengan traffic user/Q-Learning ke server.

---

## H. CLI & Cleanup

```python
CLI(net)                          # masuk Mininet CLI interaktif
...
for h in hm.values(): h.cmd('killall python3 ...')
os.system('killall python3 ...')  # fallback level sistem
net.stop()
```

**Fungsi:**
- `CLI(net)` — masuk shell Mininet untuk uji manual (`ping`, `pingall`, dll).
- **Cleanup ganda** saat keluar — matikan semua agen di tiap host monitoring
  + fallback `killall` level sistem. Mencegah proses agen "zombie" tersisa
  setelah topologi dihentikan (masalah umum pada agen `nohup &`).

---

## Ringkasan Fitur Mininet

1. **SDN dasar**: RemoteController + OVS + OpenFlow 1.3 + TCLink.
2. **Topologi 9 switch berjalur ganda** → ruang keputusan untuk Q-Learning.
3. **Dua host per switch** (user vs monitoring) → isolasi traffic Dijkstra vs QL.
4. **Skema IP sistematis** (Hx=10.0.0.x, HMx=10.0.0.x0).
5. **Static ARP** → hilangkan noise broadcast.
6. **Export host_topology.json** → orchestrator mode untuk controller.
7. **22 agen bidirectional** → KPI simetris (maju + balik).
8. **Cleanup ganda** → tidak ada proses agen tersisa.
