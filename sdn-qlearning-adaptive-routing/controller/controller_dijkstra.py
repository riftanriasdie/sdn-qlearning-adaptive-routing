# =============================================================================
# FILE: perc_ta_ryu_ospf_pengujian.py
# DESKRIPSI:
#   Controller Ryu yang mensimulasikan perilaku OSPF di lingkungan SDN.
#   Digunakan sebagai BASELINE perbandingan terhadap sistem Q-Learning.
#
# PERILAKU (sesuai sifat OSPF standar RFC 2328):
#   1. Pemilihan jalur: Dijkstra Shortest Path berdasarkan hop count
#      (ekivalen OSPF dengan cost seragam per link).
#   2. Reaktif terhadap link DOWN: saat link hilang dari topologi LLDP,
#      recalculate Dijkstra dan install flow rules baru ke semua switch.
#   3. TIDAK reaktif terhadap degradasi KPI: jika delay/loss naik tapi
#      link masih UP, routing TIDAK berubah. Ini sifat kunci OSPF yang
#      membedakannya dari sistem Q-Learning.
#   4. Tidak ada agent monitoring KPI, tidak ada Q-Learning.
#
# PERBEDAAN DENGAN OSPF ASLI yang perlu dicatat di dokumentasi:
#   - OSPF asli: deteksi link down via Hello/Dead interval (~10s/40s)
#   - SDN-OSPF ini: deteksi via LLDP OpenFlow (~4-6s) → lebih cepat
#   - OSPF asli: cost = 10^8 / bandwidth
#   - SDN-OSPF ini: semua link cost = 1 (hop count) → identik karena
#     semua link TCLink tanpa parameter bandwidth di Mininet
#
# PENGGUNAAN:
#   ryu-manager perc_ta_ryu_ospf_pengujian.py --observe-links
#
# TOPOLOGI: perc_ta_mn_ospf_pengujian.py (9 switch, tanpa host monitoring)
# =============================================================================

import networkx as nx
import time
import sys
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, arp
from ryu.lib import hub
from ryu.topology import event, api


# =============================================================================
# KONFIGURASI
# =============================================================================
SERVER_IP = "10.0.0.9"


class Col:
    GREEN  = '\033[92m'
    RED    = '\033[91m'
    RESET  = '\033[0m'
    BLUE   = '\033[94m'
    YELLOW = '\033[93m'
    CYAN   = '\033[96m'


class OSPFController(app_manager.RyuApp):
    """
    Controller SDN yang mensimulasikan perilaku OSPF.
    Jalur ditentukan oleh Dijkstra shortest path dan hanya berubah
    saat topologi berubah (link DOWN/UP), bukan saat KPI memburuk.
    """
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(OSPFController, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.net              = nx.DiGraph()
        self.datapaths        = {}
        self.global_host_map  = {}   # MAC → (dpid, port)
        self.switch_ports     = {}   # dpid → [port, ...]
        self.prev_edges       = set()

        # Flag: sudah ada topologi lengkap untuk pertama kali
        self.initial_routes_installed = False

        # Counter event konvergensi
        self._convergence_event_id = 0

        # Cache rute simetris: (src_dpid, dst_dpid) → [path]
        # Diisi ulang setiap kali _compute_symmetric_paths dipanggil.
        # Menjamin rute A→B selalu kebalikan dari rute B→A.
        self._path_cache = {}

        hub.spawn(self._topology_discovery_loop)
        print(f"\n{Col.BLUE}[OSPF-SDN] Controller started."
              f" Mode: Shortest Path (Dijkstra), reactive to link DOWN only.{Col.RESET}")

    # =========================================================================
    # SWITCH FEATURES — install table-miss flow
    # =========================================================================
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp     = ev.msg.datapath
        ofp    = dp.ofproto
        parser = dp.ofproto_parser
        self.datapaths[dp.id] = dp

        # Hapus semua flow rule lama dari sesi sebelumnya
        match_del = parser.OFPMatch()
        mod_del = parser.OFPFlowMod(
            datapath=dp,
            command=ofp.OFPFC_DELETE,
            out_port=ofp.OFPP_ANY,
            out_group=ofp.OFPG_ANY,
            match=match_del
        )
        dp.send_msg(mod_del)

        # Table-miss: semua paket tanpa flow rule → ke controller
        match   = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, 0, match, actions)
        print(f"{Col.CYAN}[OSPF-SDN] Switch S{dp.id} terhubung.{Col.RESET}")

    # =========================================================================
    # TOPOLOGY DISCOVERY — deteksi link DOWN/UP via LLDP
    # =========================================================================
    def _topology_discovery_loop(self):
        """
        Memantau topologi setiap 2 detik.
        Saat link DOWN atau UP terdeteksi, recalculate Dijkstra dan
        install ulang flow rules ke semua switch — persis seperti OSPF
        merespons perubahan LSA (Link State Advertisement).
        """
        while True:
            links    = api.get_link(self.topology_api_app, None)
            switches = api.get_switch(self.topology_api_app, None)

            if switches:
                temp_net = nx.DiGraph()
                temp_switch_ports = {}

                for sw in switches:
                    temp_net.add_node(sw.dp.id)
                    temp_switch_ports.setdefault(sw.dp.id, [])

                for lk in links:
                    temp_net.add_edge(lk.src.dpid, lk.dst.dpid, port=lk.src.port_no)
                    if lk.src.port_no not in temp_switch_ports[lk.src.dpid]:
                        temp_switch_ports[lk.src.dpid].append(lk.src.port_no)

                current_edges = set(temp_net.edges())

                # Deteksi perubahan topologi
                disappeared = self.prev_edges - current_edges
                appeared    = current_edges - self.prev_edges

                if disappeared:
                    for (src, dst) in disappeared:
                        print(f"{Col.RED}[OSPF-SDN] 🔴 Link S{src}→S{dst} DOWN. "
                              f"Recalculating Dijkstra...{Col.RESET}")
                    self.net = temp_net
                    self.switch_ports = temp_switch_ports
                    trigger_detail = ', '.join([f'S{s}→S{d} DOWN' for s, d in disappeared])
                    self._install_all_routes(trigger='link_down',
                                             trigger_detail=trigger_detail)

                elif appeared:
                    for (src, dst) in appeared:
                        print(f"{Col.GREEN}[OSPF-SDN] 🟢 Link S{src}→S{dst} UP. "
                              f"Recalculating Dijkstra...{Col.RESET}")
                    self.net = temp_net
                    self.switch_ports = temp_switch_ports
                    trigger_detail = ', '.join([f'S{s}→S{d} UP' for s, d in appeared])
                    self._install_all_routes(trigger='link_up',
                                             trigger_detail=trigger_detail)

                else:
                    # Tidak ada perubahan topologi — update graph tapi TIDAK install ulang
                    # Ini adalah sifat OSPF: tidak bereaksi selama topologi sama
                    self.net = temp_net
                    self.switch_ports = temp_switch_ports

                # Install routes pertama kali saat topologi lengkap (9 switch, 11 link)
                if (not self.initial_routes_installed and
                        len(temp_net.nodes()) >= 9 and
                        len(current_edges) >= 11):
                    print(f"{Col.GREEN}[OSPF-SDN] ✅ Topologi lengkap "
                          f"({len(current_edges)} link). Installing initial routes...{Col.RESET}")
                    self._install_all_routes(trigger='initial',
                                             trigger_detail=f'{len(current_edges)} links detected')
                    self.initial_routes_installed = True

                self.prev_edges = current_edges

            hub.sleep(2)

    def _compute_symmetric_paths(self):
        """
        Hitung semua pasangan shortest path dengan jaminan simetris:
        rute A→B selalu kebalikan persis dari rute B→A.

        Caranya:
        - Untuk setiap pasangan unik (u, v) di mana u < v, hitung
          Dijkstra sekali: path_forward = shortest_path(u, v).
        - Rute sebaliknya: path_reverse = list(reversed(path_forward)).
        - Simpan keduanya di _path_cache.

        Ini memastikan tidak ada asimetri akibat tie-breaking Dijkstra
        yang berbeda saat topologi punya banyak shortest path ekivalen.
        """
        self._path_cache = {}
        nodes = list(self.net.nodes())

        for i, u in enumerate(nodes):
            for v in nodes[i+1:]:  # hanya pasangan unik u < v (indeks)
                try:
                    # Hitung satu arah saja — arah lain adalah kebalikannya
                    forward = nx.shortest_path(self.net, u, v)
                    reverse = list(reversed(forward))
                    self._path_cache[(u, v)] = forward
                    self._path_cache[(v, u)] = reverse
                except nx.NetworkXNoPath:
                    pass

    def _get_path(self, src, dst):
        """
        Ambil path dari cache simetris.
        Fallback ke Dijkstra langsung jika tidak ada di cache.
        """
        if (src, dst) in self._path_cache:
            return self._path_cache[(src, dst)]
        try:
            return nx.shortest_path(self.net, src, dst)
        except nx.NetworkXNoPath:
            return None

    def _install_all_routes(self, trigger='unknown', trigger_detail=''):
        """
        Hitung Dijkstra shortest path dari setiap switch ke setiap tujuan,
        lalu install flow rules ke semua switch yang terhubung.

        Ini ekivalen dengan OSPF SPF (Shortest Path First) calculation
        yang dilakukan setiap kali LSDB (Link State Database) berubah.

        Waktu konvergensi diukur dari mulai kalkulasi Dijkstra hingga
        semua rute ditemukan dan ditampilkan di log controller.
        """
        if not self.net.nodes() or not self.datapaths:
            return

        self._convergence_event_id += 1
        event_id = self._convergence_event_id
        n_nodes  = len(self.net.nodes())
        n_edges  = len(self.net.edges())

        # ── MULAI PENGUKURAN WAKTU KONVERGENSI ──────────────────────────────
        dijkstra_start = time.time()
        print(f"{Col.YELLOW}[KONVERGENSI] ⏱  Event #{event_id} | "
              f"Trigger: {trigger} ({trigger_detail}) | "
              f"Nodes={n_nodes} Edges={n_edges} | "
              f"Dijkstra START @ {dijkstra_start:.6f}{Col.RESET}")

        installed       = 0
        routes_computed = 0
        nodes           = list(self.net.nodes())

        # Hitung semua path dengan jaminan simetris sebelum install flow rule
        self._compute_symmetric_paths()

        for src_dpid in nodes:
            if src_dpid not in self.datapaths:
                continue
            dp = self.datapaths[src_dpid]

            for dst_dpid in nodes:
                if src_dpid == dst_dpid:
                    continue
                try:
                    path = self._get_path(src_dpid, dst_dpid)
                    if path is None or len(path) < 2:
                        continue
                    out_port = self.net[src_dpid][path[1]]['port']
                    routes_computed += 1

                    # Install flow rule untuk traffic ke semua host yang terhubung ke dst_dpid
                    for mac, (h_dpid, h_port) in self.global_host_map.items():
                        if h_dpid == dst_dpid:
                            parser = dp.ofproto_parser
                            match = parser.OFPMatch(
                                eth_type=ether_types.ETH_TYPE_IP,
                                eth_dst=mac
                            )
                            actions = [parser.OFPActionOutput(out_port)]
                            self._add_flow(dp, 10, match, actions)
                            installed += 1

                except Exception:
                    pass

        # ── SELESAI PENGUKURAN WAKTU KONVERGENSI ─────────────────────────────
        dijkstra_end = time.time()
        duration_ms  = (dijkstra_end - dijkstra_start) * 1000.0

        print(f"{Col.GREEN}[KONVERGENSI] ✅ Event #{event_id} SELESAI | "
              f"Durasi Dijkstra: {duration_ms:.3f} ms | "
              f"Rute dihitung: {routes_computed} | "
              f"Flow diinstall: {installed}{Col.RESET}")

        if installed > 0:
            print(f"{Col.GREEN}[OSPF-SDN] {installed} flow rules diinstall "
                  f"(Dijkstra shortest path).{Col.RESET}")
        self._print_routing_table()

    def _print_routing_table(self):
        """Cetak tabel routing Dijkstra saat ini untuk monitoring."""
        if not self.net.nodes():
            return
        print(f"\n{'='*70}")
        print(f"[OSPF-SDN] ROUTING TABLE (Dijkstra Shortest Path)")
        print(f"{'='*70}")
        print(f"{'Src':<6} {'Dst':<6} {'Path':<40} {'OutPort'}")
        print(f"{'-'*70}")

        nodes = sorted(self.net.nodes())
        for i, src in enumerate(nodes):
            # Pemisah antar blok switch source
            if i > 0:
                print(f"{'-'*70}")
            for dst in nodes:
                if src == dst:
                    continue
                path = self._get_path(src, dst)
                if path and len(path) > 1:
                    out_port = self.net[src][path[1]]['port']
                    path_str = ' → '.join([f'S{n}' for n in path])
                    print(f"S{src:<5} S{dst:<5} {path_str:<40} {out_port}")
                else:
                    print(f"S{src:<5} S{dst:<5} {'[NO PATH]':<40} -")
        print(f"{'='*70}\n")

    # =========================================================================
    # PACKET IN HANDLER
    # =========================================================================
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg    = ev.msg
        dp     = msg.datapath
        dpid   = dp.id
        parser = dp.ofproto_parser
        ofp    = dp.ofproto
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        # Pelajari lokasi host dari source MAC
        if eth.src not in self.global_host_map:
            self.global_host_map[eth.src] = (dpid, in_port)

        # ARP: flood
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
            out = parser.OFPPacketOut(
                datapath=dp, buffer_id=msg.buffer_id,
                in_port=in_port, actions=actions, data=msg.data
            )
            dp.send_msg(out)
            return

        # IP: routing via Dijkstra
        if eth.ethertype == ether_types.ETH_TYPE_IP:
            self._handle_ip(dp, in_port, eth, msg)

    def _handle_ip(self, dp, in_port, eth, msg):
        """
        Routing IP via Dijkstra shortest path.
        Flow rule diinstall dengan idle_timeout=0 (permanen) agar
        tidak perlu packet_in berulang — sesuai perilaku OSPF yang
        menginstall entry routing permanen di forwarding table.
        """
        dpid   = dp.id
        parser = dp.ofproto_parser

        dst_dpid, dst_port = None, None
        if eth.dst in self.global_host_map:
            dst_dpid, dst_port = self.global_host_map[eth.dst]
        else:
            # Tujuan belum diketahui — flood
            actions = [parser.OFPActionOutput(dp.ofproto.OFPP_FLOOD)]
            out = parser.OFPPacketOut(
                datapath=dp, buffer_id=msg.buffer_id,
                in_port=in_port, actions=actions, data=msg.data
            )
            dp.send_msg(out)
            return

        # Tentukan output port
        if dpid == dst_dpid:
            out_port = dst_port
        else:
            path = self._get_path(dpid, dst_dpid)
            if path is None:
                return
            out_port = self.net[dpid][path[1]]['port']

        actions = [parser.OFPActionOutput(out_port)]
        # PENTING: match hanya pakai eth_type + eth_dst, TANPA in_port.
        # Jika in_port disertakan, setiap paket yang datang dari arah berbeda
        # akan menginstall flow rule terpisah yang bisa bertentangan dengan
        # rule dari _install_all_routes, menyebabkan rute pulang vs pergi berbeda.
        match   = parser.OFPMatch(
            eth_type=ether_types.ETH_TYPE_IP,
            eth_dst=eth.dst
        )

        # idle_timeout=0: flow rule permanen, tidak expired
        # Ini sesuai perilaku OSPF — entry routing tidak hilang kecuali
        # topologi berubah dan controller menginstall rule baru
        self._add_flow(dp, 10, match, actions, idle_timeout=0)

        out = parser.OFPPacketOut(
            datapath=dp, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=msg.data
        )
        dp.send_msg(out)

    # =========================================================================
    # HELPER
    # =========================================================================
    def _add_flow(self, datapath, priority, match, actions,
                  idle_timeout=0, hard_timeout=0):
        ofp    = datapath.ofproto
        parser = datapath.ofproto_parser
        inst   = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod    = parser.OFPFlowMod(
            datapath=datapath, priority=priority,
            match=match, instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout
        )
        datapath.send_msg(mod)
