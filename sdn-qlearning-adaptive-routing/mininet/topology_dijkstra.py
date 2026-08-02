# =============================================================================
# FILE: perc_ta_mn_final_ospf_pengujian_qlearningvsospf.py
# BASIS: perc_ta_mn_ospf_pengujian.py
#
# DESKRIPSI:
#   Topologi Mininet untuk pengujian baseline OSPF-SDN.
#   Digunakan bersama perc_ta_mn_final_04_pengujian_qlearningvsospf.py
#   yang memiliki parameter link IDENTIK agar pengujian adil.
#
#   Perbedaan utama dari versi sebelumnya:
#     - Setiap inter-switch link memiliki delay realistis (1–5 ms) dan
#       jitter kecil (0.3–1.5 ms) untuk mensimulasikan kondisi jaringan nyata.
#     - Tidak ada packet loss pada kondisi awal (loss=0%) — kongesti
#       diinjeksi MANUAL via tc netem dari Mininet CLI saat pengujian.
#     - Link host→switch TIDAK diberi delay agar pengukuran end-to-end
#       mencerminkan murni akumulasi delay inter-switch.
#     - Parameter link identik dengan file Q-Learning pasangannya.
#     - Tanpa host monitoring (OSPF tidak membutuhkan agent KPI).
#
#   PARAMETER LINK INTER-SWITCH (identik dengan file Q-Learning):
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
#   Inter-switch: S1─S2, S1─S3, S1─S4, S2─S5, S3─S6, S4─S6,
#                 S5─S6, S5─S7, S6─S8, S7─S9, S8─S9
#
# CONTROLLER: perc_ta_ryu_ospf_pengujian.py
#
# CARA PAKAI:
#   ryu-manager perc_ta_ryu_ospf_pengujian.py --observe-links
#   sudo python3 perc_ta_mn_final_ospf_pengujian_qlearningvsospf.py
#
# SKENARIO PENGUJIAN (injeksi manual dari Mininet CLI):
#
#   [Skenario 1 — Kongesti Ringan, S8─S9 normal]
#   mininet> s2 tc qdisc add dev s2-eth2 root netem delay 30ms 8ms loss 1%
#   mininet> s3 tc qdisc add dev s3-eth2 root netem delay 30ms 8ms loss 1%
#   mininet> s4 tc qdisc add dev s4-eth2 root netem delay 30ms 8ms loss 1%
#   mininet> s7 tc qdisc add dev s7-eth2 root netem delay 30ms 8ms loss 1%
#
#   [Skenario 2 — Kongesti Sedang, S4─S6 normal]
#   mininet> s2 tc qdisc add dev s2-eth2 root netem delay 55ms 15ms loss 3%
#   mininet> s3 tc qdisc add dev s3-eth2 root netem delay 55ms 15ms loss 3%
#   mininet> s7 tc qdisc add dev s7-eth2 root netem delay 55ms 15ms loss 3%
#   mininet> s8 tc qdisc add dev s8-eth2 root netem delay 55ms 15ms loss 3%
#
#   [Skenario 3 — Kongesti Berat, S3─S6 normal]
#   mininet> s2 tc qdisc add dev s2-eth2 root netem delay 90ms 25ms loss 7%
#   mininet> s4 tc qdisc add dev s4-eth2 root netem delay 90ms 25ms loss 7%
#   mininet> s7 tc qdisc add dev s7-eth2 root netem delay 90ms 25ms loss 7%
#   mininet> s8 tc qdisc add dev s8-eth2 root netem delay 90ms 25ms loss 7%
#
#   [Cek nomor port yang benar — PENTING karena OSPF tidak punya host monitoring]
#   mininet> s2 ip link
#   mininet> s3 ip link
#   (Port host di OSPF mulai dari eth1, inter-switch dari eth2)
#
#   [Ukur KPI]
#   mininet> h1 ping -c 100 -i 0.1 10.0.0.9
#   mininet> h1 traceroute -n 10.0.0.9
#
#   [Reset kongesti]
#   mininet> s2 tc qdisc del dev s2-eth2 root
#   dst...
#
# CATATAN PENTING — PERBEDAAN NOMOR PORT dengan file Q-Learning:
#   File Q-Learning memiliki 2 host per switch (Hx + HMx), sehingga
#   inter-switch port mulai dari eth3.
#   File OSPF ini hanya memiliki 1 host per switch (Hx), sehingga
#   inter-switch port mulai dari eth2.
#   Pastikan menggunakan nomor port yang benar sesuai file yang sedang diuji.
# =============================================================================

import time
import os
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink


# =============================================================================
# PARAMETER LINK INTER-SWITCH — identik dengan file Q-Learning pasangan
# Semua nilai dalam satuan millisecond. Loss = 0 (kondisi awal bersih).
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

    CATATAN: Di file OSPF ini, setiap switch hanya memiliki 1 host (Hx),
    sehingga inter-switch port mulai dari eth2 (bukan eth3 seperti di
    file Q-Learning yang memiliki HMx tambahan).
    """
    info('\n' + '='*65 + '\n')
    info('PORT MAP — Gunakan nomor ini untuk perintah tc netem\n')
    info('(OSPF: 1 host per switch → inter-switch mulai dari eth2)\n')
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
            other_node = link.intf2.node if link.intf1.node == sw_node else link.intf1.node
            other_name = other_node.name

            if other_name.startswith('h'):
                keterangan = f'→ {other_name.upper()} (host)'
            else:
                keterangan = f'→ {other_name.upper()} (inter-switch) ← tc target'

            prefix = sw_name.upper() if first else ' ' * len(sw_name)
            info(f'{prefix:<8} {intf.name:<14} {other_name.upper():<20} {keterangan}\n')
            first = False
        info('-'*65 + '\n')

    info('\nContoh perintah tc netem (ganti interface sesuai port map di atas):\n')
    info('  s2 tc qdisc add dev s2-eth2 root netem delay 30ms 8ms loss 1%\n')
    info('  s2 tc qdisc del dev s2-eth2 root\n')
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
    # HOST USER (H1–H9) — H9 = server (10.0.0.9)
    # Link host→switch tidak diberi delay agar pengukuran end-to-end
    # mencerminkan murni akumulasi delay inter-switch.
    # =========================================================================
    info('*** Adding hosts (H1–H9)\n')
    hosts = {}
    for i in range(1, 10):
        h = net.addHost(f'h{i}', ip=f'10.0.0.{i}')
        hosts[f'h{i}'] = h
        net.addLink(h, switches[f's{i}'])   # tanpa delay — link lokal ke switch

    # =========================================================================
    # INTER-SWITCH LINKS dengan parameter delay realistis
    # Urutan addLink menentukan nomor port di setiap switch:
    #   Port 1 → host user (Hx)        ← hanya 1 host karena tidak ada HMx
    #   Port 2 dan seterusnya → inter-switch (sesuai urutan addLink)
    #
    # PERBEDAAN DENGAN FILE Q-LEARNING:
    #   Q-Learning: Port 1=Hx, Port 2=HMx, Port 3+=inter-switch
    #   OSPF ini  : Port 1=Hx,             Port 2+=inter-switch
    # =========================================================================
    info('*** Creating inter-switch links (dengan delay realistis)\n')
    s = switches

    add_link_with_params(net, s, 's1', 's2')   # S1-port2 ↔ S2-port2
    add_link_with_params(net, s, 's1', 's3')   # S1-port3 ↔ S3-port2
    add_link_with_params(net, s, 's1', 's4')   # S1-port4 ↔ S4-port2

    add_link_with_params(net, s, 's2', 's5')   # S2-port3 ↔ S5-port2
    add_link_with_params(net, s, 's3', 's6')   # S3-port3 ↔ S6-port2
    add_link_with_params(net, s, 's4', 's6')   # S4-port3 ↔ S6-port3

    add_link_with_params(net, s, 's5', 's6')   # S5-port3 ↔ S6-port4
    add_link_with_params(net, s, 's5', 's7')   # S5-port4 ↔ S7-port2

    add_link_with_params(net, s, 's6', 's8')   # S6-port5 ↔ S8-port2

    add_link_with_params(net, s, 's7', 's9')   # S7-port3 ↔ S9-port2
    add_link_with_params(net, s, 's8', 's9')   # S8-port3 ↔ S9-port3

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
    info('*** Setting static ARP\n')
    all_hosts = list(net.hosts)
    for host in all_hosts:
        for other in all_hosts:
            if host != other:
                host.cmd('arp -s %s %s' % (other.IP(), other.MAC()))

    # =========================================================================
    # CETAK PORT MAP — membantu pengguna menentukan interface tc yang benar
    # =========================================================================
    print_port_map(net, switches)

    info('*** ─────────────────────────────────────────────────────────\n')
    info('*** Kondisi link awal (baseline, tanpa kongesti):\n')
    info('***   S1─S2/S3/S4     : delay=2ms, jitter=0.5ms, loss=0%\n')
    info('***   S2─S5/S3─S6/S4─S6 : delay=3ms, jitter=1ms,  loss=0%\n')
    info('***   S5─S6/S5─S7/S6─S8 : delay=2ms, jitter=0.5ms, loss=0%\n')
    info('***   S7─S9/S8─S9     : delay=4ms, jitter=1.5ms, loss=0%\n')
    info('*** ─────────────────────────────────────────────────────────\n')
    info('*** OSPF akan selalu memilih jalur hop-minimum (Dijkstra).\n')
    info('*** Routing TIDAK berubah saat KPI memburuk tapi link masih UP.\n')
    info('*** ─────────────────────────────────────────────────────────\n')
    info('*** Untuk injeksi kongesti, lihat perintah di header file.\n')
    info('*** Server: H9 (10.0.0.9)\n')
    info('*** ─────────────────────────────────────────────────────────\n\n')

    time.sleep(2)
    CLI(net)

    info('*** Stopping network...\n')
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run_topology()
