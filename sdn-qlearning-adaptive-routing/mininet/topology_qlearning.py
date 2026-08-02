# =============================================================================
# FILE: perc_ta_mn_final_04_pengujian_qlearningvsospf.py
# BASIS: perc_ta_mn_final_03_hostmonitoring_02.py
#
# DESKRIPSI:
#   Topologi Mininet untuk pengujian perbandingan SDN Q-Learning vs OSPF.
#   Digunakan bersama perc_ta_mn_final_ospf_pengujian_qlearningvsospf.py
#   yang memiliki parameter link IDENTIK agar pengujian adil.
#
#   Perbedaan utama dari versi sebelumnya:
#     - Setiap inter-switch link memiliki delay realistis (1–5 ms) dan
#       jitter kecil (0.3–1.5 ms) untuk mensimulasikan kondisi jaringan nyata.
#     - Tidak ada packet loss pada kondisi awal (loss=0%) — kongesti
#       diinjeksi MANUAL via tc netem dari Mininet CLI saat pengujian.
#     - Link host→switch TIDAK diberi delay agar pengukuran end-to-end
#       mencerminkan murni akumulasi delay inter-switch.
#     - Parameter link identik dengan file OSPF pasangannya.
#
#   PARAMETER LINK INTER-SWITCH (identik dengan file OSPF):
#   ┌─────────┬──────────┬──────────┬──────┐
#   │  Link   │  Delay   │  Jitter  │ Loss │
#   ├─────────┼──────────┼──────────┼──────┤
#   │ S1─S2   │  2.0 ms  │  0.5 ms  │  0%  │
#   │ S1─S3   │  2.0 ms  │  0.5 ms  │  0%  │
#   │ S1─S4   │  2.0 ms  │  0.5 ms  │  0%  │
#   │ S2─S5   │  3.0 ms  │  1.0 ms  │  0%  │
#   │ S3─S6   │  3.0 ms  │  1.0 ms  │  0%  │
#   │ S4─S6   │  3.0 ms  │  1.0 ms  │  0%  │
#   │ S5─S6   │  2.0 ms  │  0.5 ms  │  0%  │
#   │ S5─S7   │  2.0 ms  │  0.5 ms  │  0%  │
#   │ S6─S8   │  2.0 ms  │  0.5 ms  │  0%  │
#   │ S7─S9   │  4.0 ms  │  1.5 ms  │  0%  │
#   │ S8─S9   │  4.0 ms  │  1.5 ms  │  0%  │
#   └─────────┴──────────┴──────────┴──────┘
#
#   Alasan pemilihan nilai:
#     - S1─S2/S3/S4 (access layer): 2ms — link access tier, jarak dekat
#     - S2─S5/S3─S6/S4─S6 (aggregation): 3ms — satu tingkat lebih jauh
#     - S5─S6/S5─S7/S6─S8 (core internal): 2ms — link core, bandwidth besar
#     - S7─S9/S8─S9 (last-mile ke server): 4ms — link menuju edge server
#
# TOPOLOGI:
#   Switch: S1–S9 (9 switch, OpenFlow 1.3)
#   Host user: H1–H9 (10.0.0.1–10.0.0.9), H9 = server
#   Host monitoring: HM1–HM9 (10.0.0.10–10.0.0.90)
#   Inter-switch: S1─S2, S1─S3, S1─S4, S2─S5, S3─S6, S4─S6,
#                 S5─S6, S5─S7, S6─S8, S7─S9, S8─S9
#
# CONTROLLER: perc_ta_ryu_final_04_optimasipushflowrule_03.py
# AGENT    : agent_reporter_04_kpi_aktual.py
#
# CARA PAKAI:
#   ryu-manager perc_ta_ryu_final_04_optimasipushflowrule_03.py --observe-links
#   sudo python3 perc_ta_mn_final_04_pengujian_qlearningvsospf.py
#
# SKENARIO PENGUJIAN (injeksi manual dari Mininet CLI):
#
#   [Skenario 1 — Kongesti Ringan, S8─S9 normal]
#   mininet> s2 tc qdisc add dev s2-eth3 root netem delay 30ms 8ms loss 1%
#   mininet> s3 tc qdisc add dev s3-eth2 root netem delay 30ms 8ms loss 1%
#   mininet> s4 tc qdisc add dev s4-eth2 root netem delay 30ms 8ms loss 1%
#   mininet> s7 tc qdisc add dev s7-eth2 root netem delay 30ms 8ms loss 1%
#
#   [Skenario 2 — Kongesti Sedang, S4─S6 normal]
#   mininet> s2 tc qdisc add dev s2-eth3 root netem delay 55ms 15ms loss 3%
#   mininet> s3 tc qdisc add dev s3-eth2 root netem delay 55ms 15ms loss 3%
#   mininet> s7 tc qdisc add dev s7-eth2 root netem delay 55ms 15ms loss 3%
#   mininet> s8 tc qdisc add dev s8-eth2 root netem delay 55ms 15ms loss 3%
#
#   [Skenario 3 — Kongesti Berat, S3─S6 normal]
#   mininet> s2 tc qdisc add dev s2-eth3 root netem delay 90ms 25ms loss 7%
#   mininet> s4 tc qdisc add dev s4-eth2 root netem delay 90ms 25ms loss 7%
#   mininet> s7 tc qdisc add dev s7-eth2 root netem delay 90ms 25ms loss 7%
#   mininet> s8 tc qdisc add dev s8-eth2 root netem delay 90ms 25ms loss 7%
#
#   [Cek nomor port yang benar]
#   mininet> s2 ip link
#   mininet> s3 ip link
#   (dst. untuk semua switch yang akan di-inject)
#
#   [Ukur KPI]
#   mininet> h1 ping -c 100 -i 0.1 10.0.0.9
#   mininet> h1 traceroute -n 10.0.0.9
#
#   [Reset kongesti]
#   mininet> s2 tc qdisc del dev s2-eth3 root
#   dst...
# =============================================================================

import time
import json
import re
import os
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink


# =============================================================================
# PARAMETER LINK INTER-SWITCH — identik dengan file OSPF pasangan
# Semua nilai dalam satuan millisecond. Loss = 0 (kondisi awal bersih).
# Kongesti diinjeksi manual via tc netem dari Mininet CLI saat pengujian.
# =============================================================================
LINK_PARAMS = {
    ('s1', 's2'): dict(delay='2ms',  jitter='0.5ms', loss=0),
    ('s1', 's3'): dict(delay='2ms',  jitter='0.5ms', loss=0),
    ('s1', 's4'): dict(delay='2ms',  jitter='0.5ms', loss=0),
    ('s2', 's5'): dict(delay='3ms',  jitter='1.0ms', loss=0),
    ('s3', 's6'): dict(delay='3ms',  jitter='1.0ms', loss=0),
    ('s4', 's6'): dict(delay='3ms',  jitter='1.0ms', loss=0),
    ('s5', 's6'): dict(delay='2ms',  jitter='0.5ms', loss=0),
    ('s5', 's7'): dict(delay='2ms',  jitter='0.5ms', loss=0),
    ('s6', 's8'): dict(delay='2ms',  jitter='0.5ms', loss=0),
    ('s7', 's9'): dict(delay='4ms',  jitter='1.5ms', loss=0),
    ('s8', 's9'): dict(delay='4ms',  jitter='1.5ms', loss=0),
}


def add_link_with_params(net, s, src_name, dst_name):
    """
    Menambahkan inter-switch link dengan parameter TCLink dari LINK_PARAMS.
    Parameter diambil berdasarkan pasangan (src, dst) — urutan harus cocok
    dengan key di LINK_PARAMS (selalu src < dst secara alfabetis tidak berlaku,
    gunakan urutan yang konsisten seperti yang didefinisikan di LINK_PARAMS).
    """
    params = LINK_PARAMS[(src_name, dst_name)]
    net.addLink(
        s[src_name], s[dst_name],
        cls=TCLink,
        delay=params['delay'],
        jitter=params['jitter'],
        loss=params['loss']
    )


def print_port_map(net, switches):
    """
    Mencetak peta port switch setelah jaringan dibangun.
    Berguna untuk menentukan nomor interface yang tepat saat menjalankan
    perintah tc netem dari Mininet CLI.
    """
    info('\n' + '='*65 + '\n')
    info('PORT MAP — Gunakan nomor ini untuk perintah tc netem\n')
    info('='*65 + '\n')
    info(f'{"Switch":<8} {"Interface":<14} {"Terhubung ke":<20} {"Keterangan"}\n')
    info('-'*65 + '\n')

    for sw_name in sorted(switches.keys()):
        sw_node = switches[sw_name]
        intfs = sorted(sw_node.intfList(), key=lambda x: x.name)
        first = True
        for intf in intfs:
            if intf.name == 'lo':
                continue
            link = intf.link
            if link is None:
                continue
            # Tentukan node di ujung lain
            other_node = link.intf2.node if link.intf1.node == sw_node else link.intf1.node
            other_name = other_node.name

            if other_name.startswith('h') or other_name.startswith('hm'):
                keterangan = f'→ {other_name.upper()} (host)'
            else:
                keterangan = f'→ {other_name.upper()} (inter-switch) ← tc target'

            prefix = sw_name.upper() if first else ' ' * len(sw_name)
            info(f'{prefix:<8} {intf.name:<14} {other_name.upper():<20} {keterangan}\n')
            first = False
        info('-'*65 + '\n')

    info('\nContoh perintah tc netem (ganti interface sesuai port map di atas):\n')
    info('  s2 tc qdisc add dev s2-ethX root netem delay 30ms 8ms loss 1%\n')
    info('  s2 tc qdisc del dev s2-ethX root\n')
    info('='*65 + '\n\n')


def run_topology():
    net = Mininet(
        controller=RemoteController,
        link=TCLink,
        switch=OVSKernelSwitch,
        autoSetMacs=True
    )

    info('*** Adding controller\n')
    c0 = net.addController('c0', controller=RemoteController,
                           ip='127.0.0.1', port=6633)

    # =========================================================================
    # SWITCHES (S1–S9)
    # =========================================================================
    info('*** Adding switches (S1–S9)\n')
    switches = {}
    for i in range(1, 10):
        switches[f's{i}'] = net.addSwitch(f's{i}', protocols='OpenFlow13')

    # =========================================================================
    # HOST USER (H1–H9) — traffic data, H9 = server (10.0.0.9)
    # Link host→switch tidak diberi delay agar pengukuran end-to-end
    # mencerminkan murni akumulasi delay inter-switch.
    # =========================================================================
    info('*** Adding user hosts (H1–H9)\n')
    hosts = {}
    for i in range(1, 10):
        h = net.addHost(f'h{i}', ip=f'10.0.0.{i}')
        hosts[f'h{i}'] = h
        net.addLink(h, switches[f's{i}'])   # tanpa delay — link lokal ke switch

    # =========================================================================
    # HOST MONITORING (HM1–HM9) — khusus agent KPI, IP 10.0.0.X0
    # Dipisah dari host user agar traffic monitoring tidak bentrok dengan
    # flow rule Q-Learning untuk traffic user → server (10.0.0.9).
    # =========================================================================
    info('*** Adding monitoring hosts (HM1–HM9)\n')
    hm = {}
    for i in range(1, 10):
        h = net.addHost(f'hm{i}', ip=f'10.0.0.{i*10}')
        hm[f'hm{i}'] = h
        net.addLink(h, switches[f's{i}'])   # tanpa delay — link lokal ke switch

    # =========================================================================
    # INTER-SWITCH LINKS dengan parameter delay realistis
    # Urutan addLink menentukan nomor port di setiap switch:
    #   Port 1 → host user (Hx)
    #   Port 2 → host monitoring (HMx)
    #   Port 3 dan seterusnya → inter-switch (sesuai urutan addLink)
    # =========================================================================
    info('*** Creating inter-switch links (dengan delay realistis)\n')
    s = switches

    add_link_with_params(net, s, 's1', 's2')   # S1-port3 ↔ S2-port3
    add_link_with_params(net, s, 's1', 's3')   # S1-port4 ↔ S3-port3
    add_link_with_params(net, s, 's1', 's4')   # S1-port5 ↔ S4-port3

    add_link_with_params(net, s, 's2', 's5')   # S2-port4 ↔ S5-port3
    add_link_with_params(net, s, 's3', 's6')   # S3-port4 ↔ S6-port3
    add_link_with_params(net, s, 's4', 's6')   # S4-port4 ↔ S6-port4

    add_link_with_params(net, s, 's5', 's6')   # S5-port4 ↔ S6-port5
    add_link_with_params(net, s, 's5', 's7')   # S5-port5 ↔ S7-port3

    add_link_with_params(net, s, 's6', 's8')   # S6-port6 ↔ S8-port3

    add_link_with_params(net, s, 's7', 's9')   # S7-port4 ↔ S9-port3
    add_link_with_params(net, s, 's8', 's9')   # S8-port4 ↔ S9-port4

    # =========================================================================
    # START NETWORK
    # =========================================================================
    info('*** Starting network\n')
    net.build()
    c0.start()
    for sw in switches.values():
        sw.start([c0])

    # =========================================================================
    # STATIC ARP — mencegah ARP broadcast mengganggu pengukuran KPI
    # =========================================================================
    info('*** Setting static ARP (user + monitoring hosts)\n')
    all_hosts = list(net.hosts)
    for host in all_hosts:
        for other in all_hosts:
            if host != other:
                host.cmd('arp -s %s %s' % (other.IP(), other.MAC()))

    # =========================================================================
    # EXPORT HOST TOPOLOGY (untuk controller Q-Learning)
    # Hanya export host user (H1–H9), bukan host monitoring.
    # Controller membaca file ini untuk mengetahui lokasi (dpid, port) setiap host.
    # =========================================================================
    info('*** Exporting host location registry to /tmp/host_topology.json\n')
    host_registry = {}

    for h in hosts.values():
        intf   = h.defaultIntf()
        link   = intf.link
        node1, node2 = link.intf1.node, link.intf2.node

        if node1 == h:
            sw_intf = link.intf2
            sw_node = node2
        else:
            sw_intf = link.intf1
            sw_node = node1

        match = re.search(r'eth(\d+)', sw_intf.name)
        if match:
            port_no = int(match.group(1))
            dpid    = int(sw_node.name.replace('s', ''))
            host_registry[h.MAC()] = {
                'dpid': dpid,
                'port': port_no,
                'ip'  : h.IP()
            }

    with open('/tmp/host_topology.json', 'w') as f:
        json.dump(host_registry, f, indent=4)

    info(f'*** Registry saved: {len(host_registry)} user hosts registered.\n')

    # =========================================================================
    # CETAK PORT MAP — membantu pengguna menentukan interface tc yang benar
    # =========================================================================
    print_port_map(net, switches)

    # =========================================================================
    # AGENT MONITORING (ARAH MAJU — menuju server S9)
    # Setiap agent berjalan di HMx dan ping ke HMy untuk mengukur KPI
    # link Sx→Sy tanpa bentrok dengan traffic Q-Learning ke H9.
    # =========================================================================
    info('*** Starting KPI monitoring agents (forward direction)\n')

    # HM1@S1 — monitor 3 link keluar S1
    hm['hm1'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 1 2 10.0.0.20 > /dev/null 2>&1 &')
    hm['hm1'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 1 3 10.0.0.30 > /dev/null 2>&1 &')
    hm['hm1'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 1 4 10.0.0.40 > /dev/null 2>&1 &')

    # HM2@S2 — monitor link S2─S5
    hm['hm2'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 2 5 10.0.0.50 > /dev/null 2>&1 &')

    # HM3@S3 — monitor link S3─S6
    hm['hm3'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 3 6 10.0.0.60 > /dev/null 2>&1 &')

    # HM4@S4 — monitor link S4─S6
    hm['hm4'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 4 6 10.0.0.60 > /dev/null 2>&1 &')

    # HM5@S5 — monitor link S5─S6 dan S5─S7
    hm['hm5'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 5 6 10.0.0.60 > /dev/null 2>&1 &')
    hm['hm5'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 5 7 10.0.0.70 > /dev/null 2>&1 &')

    # HM6@S6 — monitor link S6─S8
    hm['hm6'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 6 8 10.0.0.80 > /dev/null 2>&1 &')

    # HM7@S7 — monitor link S7─S9 (ping ke HM9, bukan H9)
    hm['hm7'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 7 9 10.0.0.90 > /dev/null 2>&1 &')

    # HM8@S8 — monitor link S8─S9 (ping ke HM9, bukan H9)
    hm['hm8'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 8 9 10.0.0.90 > /dev/null 2>&1 &')

    # =========================================================================
    # AGENT MONITORING (ARAH BALIK — dari downstream kembali ke upstream)
    # Diperlukan agar Q-Table port balik menggunakan KPI aktual,
    # bukan reward default -20.0 akibat tidak ada data monitoring.
    # =========================================================================
    info('*** Starting KPI monitoring agents (reverse direction)\n')

    hm['hm2'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 2 1 10.0.0.10 > /dev/null 2>&1 &')
    hm['hm3'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 3 1 10.0.0.10 > /dev/null 2>&1 &')
    hm['hm4'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 4 1 10.0.0.10 > /dev/null 2>&1 &')
    hm['hm5'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 5 2 10.0.0.20 > /dev/null 2>&1 &')

    hm['hm6'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 6 3 10.0.0.30 > /dev/null 2>&1 &')
    hm['hm6'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 6 4 10.0.0.40 > /dev/null 2>&1 &')
    hm['hm6'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 6 5 10.0.0.50 > /dev/null 2>&1 &')

    hm['hm7'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 7 5 10.0.0.50 > /dev/null 2>&1 &')
    hm['hm8'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 8 6 10.0.0.60 > /dev/null 2>&1 &')

    hm['hm9'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 9 7 10.0.0.70 > /dev/null 2>&1 &')
    hm['hm9'].cmd('nohup python3 agent_reporter_04_kpi_aktual.py 9 8 10.0.0.80 > /dev/null 2>&1 &')

    info('*** All monitoring agents started (bidirectional).\n')
    info('*** ─────────────────────────────────────────────────────────\n')
    info('*** Kondisi link awal (baseline, tanpa kongesti):\n')
    info('***   S1─S2/S3/S4 : delay=2ms, jitter=0.5ms, loss=0%\n')
    info('***   S2─S5/S3─S6/S4─S6 : delay=3ms, jitter=1ms, loss=0%\n')
    info('***   S5─S6/S5─S7/S6─S8 : delay=2ms, jitter=0.5ms, loss=0%\n')
    info('***   S7─S9/S8─S9 : delay=4ms, jitter=1.5ms, loss=0%\n')
    info('*** ─────────────────────────────────────────────────────────\n')
    info('*** Untuk injeksi kongesti, lihat perintah di header file.\n')
    info('*** Server: H9 (10.0.0.9) — Q-Learning aktif untuk traffic ke IP ini.\n')
    info('*** ─────────────────────────────────────────────────────────\n\n')

    time.sleep(2)
    CLI(net)

    # =========================================================================
    # CLEANUP
    # =========================================================================
    info('*** Stopping all monitoring agents...\n')
    for h in hm.values():
        h.cmd('killall python3 2>/dev/null')
    os.system('killall python3 2>/dev/null')
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run_topology()
