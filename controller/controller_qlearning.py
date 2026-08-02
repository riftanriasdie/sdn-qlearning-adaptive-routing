# =============================================================================
# FILE: perc_ta_ryu_final_06_optimasirute.py
# BASIS: perc_ta_ryu_final_04_optimasipushflowrule_03.py
# MODIFIKASI UTAMA:
#   Sinkronisasi reverse path (H9 → Hx) agar mengikuti kebalikan dari
#   forward path Q-Learning (Hx → H9).
#
#   MASALAH SEBELUMNYA:
#     - Forward path dikontrol Q-Learning (match: ip, nw_dst=10.0.0.9)
#     - Reverse path dikontrol Dijkstra shortest path (match: in_port, dl_dst)
#     - Akibatnya ping RTT = Q-Learning forward + Dijkstra reverse
#       → hasil tidak mencerminkan kualitas jalur Q-Learning secara utuh
#
#   SOLUSI (Pendekatan 3 — Push Reverse Rule Bersamaan Forward):
#     Setelah RL background worker menentukan active path via greedy trace,
#     controller membangun reverse path (kebalikan urutan switch) dan
#     langsung push flow rule ke setiap switch di reverse path.
#
#     Mekanisme:
#       1. Setelah greedy trace menghasilkan path [S1,S2,S5,S7,S9]:
#          - Bangun reverse: S9→S7→S5→S2→S1→Hx
#       2. Untuk setiap pasangan (switch_i, switch_{i+1}) di reverse path,
#          push rule: match(in_port=port_dari_S9, ip_src=SERVER_IP) → out_port ke switch berikutnya
#          dengan priority=50 (lebih rendah dari forward 100, lebih tinggi dari host-route 10)
#       3. Rule dipasang dengan idle_timeout=30 (hilang otomatis jika 30 detik
#          tidak ada traffic — membersihkan rule orphan saat path berubah)
#          dan diupdate setiap kali active path berubah (otomatis tertimpa OFPFC_ADD).
#
#     Dengan ini:
#       - Forward H8→H9 : S8→S6→S4→S1→S2→S5→S7→S9 (Q-Learning)
#       - Reverse H9→H8 : S9→S7→S5→S2→S1→S4→S6→S8 (cermin Q-Learning)
#       - Ping RTT benar-benar mencerminkan kualitas jalur Q-Learning
#
#   Semua fitur _04 dipertahankan:
#     - Hysteresis, Memory Path Reset, Poison Reverse
#     - Loop Penalty, Grace Period, Convergence Timer
#     - FlowRuleManager sebagai satu-satunya titik instalasi flow rule
#     - Web UI API
# =============================================================================
import json
import time
import random
import networkx as nx
import glob
import sys
import os
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, arp
from ryu.lib import hub
from ryu.topology import event, api
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response
import json

# ==========================================
# KONFIGURASI GLOBAL
# ==========================================
SERVER_IP = "10.0.0.9"
WARMUP_DELAY = 10      # Waktu tunggu monitoring stabil
RL_INTERVAL = 2.0      # Interval update Agent RL

# Config RL
ALPHA = 0.7
GAMMA = 0.9
EPSILON_START = 1.0
EPSILON_MIN = 0.05
EPSILON_DECAY_STEP = 0.1

# [SOLUSI 1] Inisialisasi Pesimis
# Agar agen tidak memilih jalur loop hanya karena nilainya 0 (default)
INITIAL_Q_VALUE = 0.0

# Config Reward
W_LATENCY = 1.0
W_JITTER = 5.0
W_LOSS = 100.0
SCALING_FACTOR = 10.0

# [SOLUSI 2] Hop Penalty (Biaya Langkah)
HOP_PENALTY = 5.0

# [HYSTERESIS] Threshold minimum selisih Q-value untuk berpindah jalur.
HYSTERESIS_THRESHOLD = 3.0


class Col:
    GREEN = '\033[92m'; RED = '\033[91m'; RESET = '\033[0m'
    BLUE = '\033[94m'; YELLOW = '\033[93m'; CYAN = '\033[96m'
    PURPLE = '\033[95m'


# =============================================================================
# FLOW RULE MANAGER
# =============================================================================

class FlowRuleManager:
    """
    Mengelola seluruh siklus hidup flow rule di semua switch.
    """

    PRIO_TABLE_MISS      = 0
    PRIO_HOST_ROUTE      = 10   # rule host-to-host Dijkstra (reaktif)
    PRIO_REVERSE_Q       = 15   # rule reverse path Q-Learning (H9→Hx per-host)
    PRIO_SERVER_FWD      = 100  # rule forward ke server via Q-Learning
    PRIO_SERVER_DIRECT   = 200  # rule di server_dpid: langsung ke server_port

    def __init__(self, logger):
        self.logger = logger

    # ------------------------------------------------------------------
    # LOW-LEVEL PRIMITIF
    # ------------------------------------------------------------------

    def _add_flow(self, dp, priority, match, actions,
                  idle_timeout=0, hard_timeout=0):
        """Kirim OFPFlowMod ADD ke datapath dp."""
        ofp  = dp.ofproto
        par  = dp.ofproto_parser
        inst = [par.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod  = par.OFPFlowMod(
            datapath=dp, priority=priority,
            match=match, instructions=inst,
            idle_timeout=idle_timeout, hard_timeout=hard_timeout
        )
        dp.send_msg(mod)

    def _delete_flow(self, dp, priority, match,
                     out_port=None, strict=False):
        """Kirim OFPFlowMod DELETE ke datapath dp."""
        ofp = dp.ofproto
        par = dp.ofproto_parser
        cmd = ofp.OFPFC_DELETE_STRICT if strict else ofp.OFPFC_DELETE
        op  = out_port if out_port is not None else ofp.OFPP_ANY
        mod = par.OFPFlowMod(
            datapath=dp,
            priority=priority,
            command=cmd,
            out_port=op,
            out_group=ofp.OFPG_ANY,
            match=match
        )
        dp.send_msg(mod)

    # ------------------------------------------------------------------
    # HIGH-LEVEL SEMANTIK
    # ------------------------------------------------------------------

    def install_table_miss(self, dp):
        """Pasang table-miss: semua paket tak dikenal → ke controller."""
        par     = dp.ofproto_parser
        ofp     = dp.ofproto
        match   = par.OFPMatch()
        actions = [par.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, self.PRIO_TABLE_MISS, match, actions)

    def install_server_forward(self, dp, out_port, server_ip):
        """
        Pasang/update rule forward traffic ke server_ip di switch dp.
        Priority 100, permanent.
        """
        par     = dp.ofproto_parser
        match   = par.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=server_ip)
        actions = [par.OFPActionOutput(out_port)]
        self._add_flow(dp, self.PRIO_SERVER_FWD, match, actions,
                       idle_timeout=0, hard_timeout=0)

    def install_server_direct(self, dp, server_port, server_ip):
        """
        Pasang rule di server_dpid: traffic ke server_ip langsung ke server_port.
        Priority 200.
        """
        par     = dp.ofproto_parser
        match   = par.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=server_ip)
        actions = [par.OFPActionOutput(server_port)]
        self._add_flow(dp, self.PRIO_SERVER_DIRECT, match, actions,
                       idle_timeout=0, hard_timeout=0)

    def install_host_route(self, dp, in_port, dst_mac, out_port):
        """
        Pasang rule routing host-to-host di switch dp.
        Priority 10, permanent.
        """
        par     = dp.ofproto_parser
        match   = par.OFPMatch(in_port=in_port, eth_dst=dst_mac)
        actions = [par.OFPActionOutput(out_port)]
        self._add_flow(dp, self.PRIO_HOST_ROUTE, match, actions,
                       idle_timeout=0, hard_timeout=0)

    def install_reverse_path_per_host(self, dp, src_ip, dst_ip, out_port):
        """
        Pasang rule reverse path per-host di switch dp.

        Match  : ip_src=SERVER_IP + ip_dst=host_ip  (TANPA in_port)
        Action : output ke out_port
        Priority: PRIO_REVERSE_Q = 15
        idle_timeout=30: rule hilang otomatis jika 30 detik tidak ada traffic.
          Mekanisme pembersih rule orphan saat path berubah — switch yang tidak
          lagi ada di path baru tidak akan kena traffic, sehingga rule-nya
          expired sendiri tanpa perlu explicit delete.

        Kenapa tanpa in_port:
          Di switch asal H9 (server_dpid / S9), paket reply dari H9
          datang dari port H9 (s9-eth1), bukan dari port inter-switch.
          Jika in_port dimasukkan ke match, rule tidak akan cocok di S9
          karena paket datang dari port yang berbeda.
          Dengan menghilangkan in_port, rule berlaku seragam di semua
          switch sepanjang reverse path termasuk S9.

        Kenapa priority=15 (bukan 50/55):
          Rule Dijkstra host-to-host yang sudah ada priority=10.
          Priority=15 cukup untuk menimpa Dijkstra tanpa mengganggu
          forward RL (priority=100/200).

        Kenapa ip_src + ip_dst tidak mengganggu traffic lain:
          Match hanya cocok jika paket benar-benar dari H9 (ip_src=10.0.0.9)
          ke host tujuan spesifik (ip_dst=10.0.0.7 untuk H7).
          Traffic H6→H7 misalnya tidak cocok karena ip_src=10.0.0.6 ≠ 10.0.0.9.
        """
        par     = dp.ofproto_parser
        match   = par.OFPMatch(
            eth_type=ether_types.ETH_TYPE_IP,
            ipv4_src=src_ip,
            ipv4_dst=dst_ip
        )
        actions = [par.OFPActionOutput(out_port)]
        self._add_flow(dp, self.PRIO_REVERSE_Q, match, actions,
                       idle_timeout=30, hard_timeout=0)

    def on_switch_connect(self, dp, server_ip):
        """
        Dipanggil saat switch baru connect.
        Hapus rule stale, pasang table-miss.
        """
        par = dp.ofproto_parser

        # Hapus rule forward ke server (priority=100 dan 200)
        for prio in (self.PRIO_SERVER_FWD, self.PRIO_SERVER_DIRECT):
            match_del = par.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                     ipv4_dst=server_ip)
            self._delete_flow(dp, prio, match_del)

        # Hapus rule reverse path lama (semua priority: 15, 50, 55, 60)
        match_rev = par.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                  ipv4_src=server_ip)
        for prio in (self.PRIO_REVERSE_Q, 50, 55, 60):
            self._delete_flow(dp, prio, match_rev)

        # Hapus semua host-route (priority=10) untuk bersihkan rule looping
        # yang mungkin tersisa dari sesi sebelumnya (out_port == in_port bug)
        match_all_host = par.OFPMatch()
        self._delete_flow(dp, self.PRIO_HOST_ROUTE, match_all_host)

        self.install_table_miss(dp)
        self.logger.info(f"[FM] S{dp.id}: switch connected, ALL stale rules cleared.")

    def on_link_down(self, server_dpid, server_port, dp_map, server_ip,
                     new_topo=None, datapaths=None):
        """
        Dipanggil saat link DOWN.
        Hapus forward rules + reverse path rules, push fallback.
        """
        if server_dpid is None or server_port is None:
            return

        # 1. Hapus return path di server_dpid (priority=10)
        if server_dpid in dp_map:
            srv_dp = dp_map[server_dpid]
            par    = srv_dp.ofproto_parser
            match_ret = par.OFPMatch(in_port=server_port)
            self._delete_flow(srv_dp, self.PRIO_HOST_ROUTE, match_ret)
            self.logger.info(
                f"[FM] on_link_down: return path rules di S{server_dpid} dihapus."
            )

        # 2. Hapus forward rule + reverse path rule di semua switch
        for dpid, dp in dp_map.items():
            par = dp.ofproto_parser
            # Hapus forward
            match_fwd = par.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=server_ip
            )
            self._delete_flow(dp, self.PRIO_SERVER_FWD, match_fwd)
            # Hapus semua reverse path rule (priority 15, 50, 55, 60)
            match_rev = par.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP, ipv4_src=server_ip
            )
            for prio in (self.PRIO_REVERSE_Q, 50, 55, 60):
                self._delete_flow(dp, prio, match_rev)

        self.logger.info(
            f"[FM] on_link_down: forward + reverse rules dihapus dari {len(dp_map)} switch."
        )

        # 3. Push fallback rules via shortest path di topologi baru
        if new_topo is None or datapaths is None:
            return

        try:
            for dpid, dp in datapaths.items():
                if dpid == server_dpid:
                    par     = dp.ofproto_parser
                    match_d = par.OFPMatch(
                        eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=server_ip
                    )
                    actions_d = [par.OFPActionOutput(server_port)]
                    self._add_flow(dp, self.PRIO_SERVER_DIRECT, match_d,
                                   actions_d, idle_timeout=0, hard_timeout=0)
                    continue

                try:
                    path = nx.shortest_path(new_topo, dpid, server_dpid)
                    if len(path) < 2:
                        continue
                    out_port = new_topo[dpid][path[1]]['port']
                except (nx.NetworkXNoPath, nx.NodeNotFound, KeyError):
                    continue

                par       = dp.ofproto_parser
                match_fb  = par.OFPMatch(
                    eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=server_ip
                )
                actions_fb = [par.OFPActionOutput(out_port)]
                self._add_flow(dp, self.PRIO_SERVER_FWD, match_fb,
                               actions_fb, idle_timeout=5, hard_timeout=0)

            self.logger.info(
                f"[FM] on_link_down: fallback rules dipasang di {len(datapaths)} switch."
            )
        except Exception as e:
            self.logger.warning(f"[FM] on_link_down: fallback gagal: {e}")

    def on_link_up(self, server_dpid, server_ip):
        """Dipanggil saat link UP. Log only."""
        self.logger.info(
            f"[FM] on_link_up: link UP. "
            f"Forward+reverse rules akan dipasang oleh RL push."
        )


class TAMainController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(TAMainController, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.net = nx.DiGraph()
        self.link_stats = {}
        self.global_host_map = {}
        self.q_table = {}
        self.epsilon = EPSILON_START
        self.rl_ready = False
        self.switch_ports = {}
        self.datapaths = {}

        self.fm = FlowRuleManager(self.logger)

        self.server_dpid  = None
        self.server_port  = None

        # [BARU] Menyimpan active path terakhir (list of dpid)
        # agar reverse path bisa dibandingkan dan hanya diupdate saat berubah
        self.active_path_dpids = []

        # [BARU] Cache reverse path per-host: key=host_ip, value=list of dpid
        # Diupdate setiap siklus RL jika path host berubah.
        # Contoh: {'10.0.0.7': [7,5,2,1,4,6,8,9], ...}
        self.host_path_cache = {}

        self._clean_old_reports()

        self.active_links_visual = []
        wsgi = kwargs['wsgi']
        wsgi.register(TopologyWebAPI, {'app': self})

        hub.spawn(self._startup_logic)
        hub.spawn(self._monitor_reader_loop)
        hub.spawn(self._topology_discovery_loop)
        hub.spawn(self._log_q_table_loop)
        hub.spawn(self._rl_background_worker)

        self.waktu_mulai_loop = None
        self.terakhir_penalti_loop = 0
        self.link_penalty_memory = {}
        self.greedy_path_cache = {}

    def _clean_old_reports(self):
        for f in glob.glob("/tmp/link_report_*.json.tmp"):
            try:
                os.remove(f)
            except Exception:
                pass

    def _get_inter_switch_ports(self, dpid):
        """
        Mengembalikan port inter-switch + port ke server jika ini server_dpid.
        """
        ports = []
        if dpid in self.net:
            for nbr in self.net.neighbors(dpid):
                port = self.net[dpid][nbr]['port']
                if port not in ports:
                    ports.append(port)

        if (self.server_dpid is not None and
                dpid == self.server_dpid and
                self.server_port is not None and
                self.server_port not in ports):
            ports.append(self.server_port)

        return ports

    # =========================================================================
    # [BARU] PUSH REVERSE PATH
    # =========================================================================

    def _build_forward_path_from(self, start_dpid):
        """
        Lakukan greedy trace dari start_dpid mengikuti Q-table
        hingga mencapai server_dpid.

        Menggunakan greedy_path_cache (dengan hysteresis) sehingga
        konsisten dengan keputusan routing aktual.

        Return: list of dpid [start_dpid, ..., server_dpid]
                atau [] jika tidak valid (loop/dead).
        """
        if self.server_dpid is None:
            return []

        curr = start_dpid
        visited = set()
        path = []

        while True:
            if curr in visited:
                return []   # loop
            visited.add(curr)
            path.append(curr)

            if curr == self.server_dpid:
                return path  # sampai server

            state = (curr, 0)
            valid_ports = self._get_inter_switch_ports(curr)
            best_act = None

            if state in self.q_table:
                acts = {k: v for k, v in self.q_table[state].items()
                        if k in valid_ports}
                if acts:
                    raw_best   = max(acts, key=acts.get)
                    raw_best_q = acts[raw_best]

                    prev_act = self.greedy_path_cache.get(curr)
                    if prev_act is not None and prev_act in acts and prev_act != raw_best:
                        prev_q = acts[prev_act]
                        if (raw_best_q - prev_q) >= HYSTERESIS_THRESHOLD:
                            best_act = raw_best
                        else:
                            best_act = prev_act
                    else:
                        best_act = raw_best

            if not best_act:
                return []   # dead

            next_sw = None
            if curr in self.net:
                for nbr in self.net.neighbors(curr):
                    if self.net[curr][nbr]['port'] == best_act:
                        next_sw = nbr
                        break

            if next_sw is None:
                return []   # dead

            curr = next_sw

        return []

    def _push_host_reverse_path(self, host_ip, path_dpids, host_port=None):
        """
        Push reverse path rule per-host ke setiap switch di sepanjang
        forward path host tersebut, termasuk server_dpid (S9).

        Match : ip_src=SERVER_IP + ip_dst=host_ip  (TANPA in_port)
        Action: output ke port berikutnya di reverse path
        Priority: PRIO_REVERSE_Q = 15

        Contoh H7, forward [7,5,2,1,4,6,8,9], reverse [9,8,6,4,1,2,5,7]:
          S9: ip_src=10.0.0.9, ip_dst=10.0.0.7 -> port ke S8
          S8: ip_src=10.0.0.9, ip_dst=10.0.0.7 -> port ke S6
          S7: ip_src=10.0.0.9, ip_dst=10.0.0.7 -> port ke H7 (last-mile, via host_port)

        Parameter host_port: port di switch host (path_dpids[0]) yang terhubung ke host.
        Jika diberikan, last-mile rule akan dipasang di switch host.
        """
        if len(path_dpids) < 2:
            return 0

        rev_path = list(reversed(path_dpids))
        pushed = 0

        # Loop mencakup semua switch kecuali switch host (rev_path[-1])
        # karena last-mile ke host ditangani terpisah via host_port di bawah.
        for i in range(len(rev_path) - 1):
            curr_dpid = rev_path[i]
            next_dpid = rev_path[i + 1]

            dp = self.datapaths.get(curr_dpid)
            if dp is None:
                continue

            # out_port di switch perantara = port menuju switch berikutnya
            out_port = None
            if curr_dpid in self.net and next_dpid in self.net[curr_dpid]:
                out_port = self.net[curr_dpid][next_dpid]['port']

            if out_port is None:
                self.logger.warning(
                    f"[PER-HOST REV] S{curr_dpid}: tidak bisa tentukan "
                    f"out_port ke S{next_dpid}. Skip."
                )
                continue

            self.fm.install_reverse_path_per_host(dp, SERVER_IP, host_ip, out_port)
            pushed += 1

        # Last-mile: pasang rule di switch host agar paket langsung ke port host
        if host_port is not None:
            host_dpid = path_dpids[0]
            dp = self.datapaths.get(host_dpid)
            if dp is not None:
                self.fm.install_reverse_path_per_host(dp, SERVER_IP, host_ip, host_port)
                pushed += 1

        return pushed

    def _push_all_host_reverse_paths(self):
        """
        Untuk setiap host yang diketahui (dari host_topology.json),
        trace forward path-nya via Q-table dan push reverse rule per-host.

        Push dilakukan SETIAP siklus tanpa guard cache, agar reverse rule
        selalu di-refresh sebelum idle_timeout=30 sempat habis.

        Analogi dengan forward rule yang juga selalu di-push setiap siklus.
        Overhead: ~8 host x ~5 switch = ~40 OFPFlowMod per 2 detik — ringan
        untuk topologi lab 9 switch ini.

        host_path_cache tetap dipertahankan hanya untuk keperluan logging
        (deteksi path yang berubah) dan reset saat link DOWN/UP.

        Dipanggil dari _rl_background_worker setiap siklus.
        """
        # Baca registry host
        host_registry = {}
        try:
            with open('/tmp/host_topology.json', 'r') as f:
                host_registry = json.load(f)
        except Exception:
            return

        total_pushed = 0
        for mac, info in host_registry.items():
            host_ip   = info.get('ip', '')
            host_dpid = int(info.get('dpid', -1))
            host_port = info.get('port', None)

            if not host_ip or host_ip == SERVER_IP or host_dpid < 0:
                continue
            if host_dpid == self.server_dpid:
                continue

            # Trace forward path dari switch host ini
            path = self._build_forward_path_from(host_dpid)
            if not path or path[-1] != self.server_dpid:
                continue  # path tidak mencapai server, skip

            # Log jika path berubah dari siklus sebelumnya
            path_changed = self.host_path_cache.get(host_ip) != path
            if path_changed:
                self.host_path_cache[host_ip] = path

            # Selalu push setiap siklus (tidak ada guard cache) agar rule
            # selalu fresh dan tidak expired oleh idle_timeout=30
            pushed = self._push_host_reverse_path(host_ip, path, host_port=host_port)
            if pushed > 0 and path_changed:
                rev_str = " -> ".join(f"S{d}" for d in reversed(path))
                print(f"{Col.CYAN}[PER-HOST REV] {host_ip}: "
                      f"path baru {pushed} rules | {rev_str}{Col.RESET}")
            total_pushed += pushed

        if total_pushed > 0:
            pass  # log sudah per-host di atas jika path berubah

    # =========================================================================
    # 1. STARTUP LOGIC
    # =========================================================================
    def _startup_logic(self):
        print(f"\n{Col.BLUE}[INIT] Controller Started. "
              f"(Hysteresis={HYSTERESIS_THRESHOLD}, Symmetric Reverse Path){Col.RESET}")
        print(f"{Col.BLUE}[MODE] Warmup Phase: Shortest Path for ALL TRAFFIC.{Col.RESET}")

        TOPO_FILE    = '/tmp/host_topology.json'
        TOPO_TIMEOUT = 60
        elapsed_topo = 0
        print(f"{Col.YELLOW}[STARTUP] Menunggu host_topology.json dari Mininet...{Col.RESET}")

        def _topo_file_ready():
            if not os.path.exists(TOPO_FILE):
                return False
            try:
                with open(TOPO_FILE, 'r') as f:
                    data = json.load(f)
                return any('ip' in v for v in data.values() if isinstance(v, dict))
            except Exception:
                return False

        while not _topo_file_ready() and elapsed_topo < TOPO_TIMEOUT:
            hub.sleep(1)
            elapsed_topo += 1

        if not _topo_file_ready():
            print(f"{Col.RED}[ERROR] host_topology.json tidak ditemukan setelah {TOPO_TIMEOUT}s.{Col.RESET}")
        else:
            try:
                with open(TOPO_FILE, 'r') as f:
                    host_registry = json.load(f)
                for mac, info in host_registry.items():
                    if info.get('ip') == SERVER_IP:
                        self.server_dpid = info['dpid']
                        self.server_port = info['port']
                        self.global_host_map[mac] = (self.server_dpid, self.server_port)
                        print(f"{Col.GREEN}[SERVER LOADED] H9 ({SERVER_IP}) di S{self.server_dpid} "
                              f"Port {self.server_port}{Col.RESET}")
                        break
                if self.server_dpid is None:
                    print(f"{Col.YELLOW}[WARNING] Server {SERVER_IP} tidak ditemukan di host_topology.json.{Col.RESET}")
            except Exception as e:
                print(f"{Col.YELLOW}[WARNING] Gagal parse host_topology.json: {e}{Col.RESET}")

        print(f"{Col.YELLOW}[STARTUP] Menunggu data KPI pertama...{Col.RESET}")
        while len(self.link_stats) == 0:
            hub.sleep(1)

        print(f"\n{Col.YELLOW}[DETECTED] Monitoring Data Received! Stabilizing ({WARMUP_DELAY}s)...{Col.RESET}")
        for i in range(WARMUP_DELAY, 0, -1):
            sys.stdout.write(f"\r⏳ RL Start in: {i}s...   ")
            sys.stdout.flush()
            hub.sleep(1)

        EXPECTED_LINKS  = 11
        TOPO_WAIT_MAX   = 30
        elapsed_lldp    = 0

        current_links = len(self.net.edges())
        if current_links < EXPECTED_LINKS:
            print(f"\n{Col.YELLOW}[TOPO CHECK] Topologi belum lengkap "
                  f"({current_links}/{EXPECTED_LINKS} link). Menunggu LLDP...{Col.RESET}")
            while len(self.net.edges()) < EXPECTED_LINKS and elapsed_lldp < TOPO_WAIT_MAX:
                hub.sleep(1)
                elapsed_lldp += 1
                sys.stdout.write(
                    f"\r  Link: {len(self.net.edges())}/{EXPECTED_LINKS} ({elapsed_lldp}s)   "
                )
                sys.stdout.flush()

            if len(self.net.edges()) < EXPECTED_LINKS:
                print(f"\n{Col.YELLOW}[TOPO CHECK] Topologi belum lengkap setelah {TOPO_WAIT_MAX}s. "
                      f"RL tetap diaktifkan.{Col.RESET}")
            else:
                print(f"\n{Col.GREEN}[TOPO CHECK] ✅ Topologi lengkap.{Col.RESET}")
        else:
            print(f"\n{Col.GREEN}[TOPO CHECK] ✅ Topologi sudah lengkap: {current_links} link.{Col.RESET}")

        print(f"\n{Col.GREEN}✅ RL ENGINE STARTED! "
              f"Hysteresis={HYSTERESIS_THRESHOLD} | Symmetric Reverse Path ON.{Col.RESET}\n")
        self.rl_ready = True

    # =========================================================================
    # 2. DATA COLLECTION & TOPOLOGY
    # =========================================================================
    def _monitor_reader_loop(self):
        while True:
            for fpath in glob.glob("/tmp/link_report_*.json"):
                try:
                    with open(fpath, 'r') as f:
                        data = json.load(f)
                    if time.time() - data['timestamp'] < 5.0:
                        src = int(data['src_sw'])
                        dst = int(data['dst_sw'])
                        self.link_stats[(src, dst)] = data

                        if hasattr(self, 'link_recovery_timers') and (src, dst) in self.link_recovery_timers:
                            if time.time() - self.link_recovery_timers[(src, dst)] >= 2.0:
                                print(f"{Col.GREEN}✨ [PEMUTIHAN] S{src}-S{dst} stabil! Reset Q-Value.{Col.RESET}")
                                port_to_dst = None
                                if src in self.net and dst in self.net[src]:
                                    port_to_dst = self.net[src][dst]['port']
                                if hasattr(self, 'loop_penalty_tracker') and port_to_dst:
                                    if (src, port_to_dst) in self.loop_penalty_tracker:
                                        del self.loop_penalty_tracker[(src, port_to_dst)]
                                        print(f"{Col.CYAN}🕊️ [PENGAMPUNAN] Denda S{src} Port {port_to_dst} dihapus.{Col.RESET}")
                                if port_to_dst:
                                    self._reset_path_q_values(src, dst, port_to_dst)
                                del self.link_recovery_timers[(src, dst)]
                except:
                    pass
            hub.sleep(1.0)

    def _reset_path_q_values(self, recovered_src, recovered_dst, recovered_port):
        """Reset Q-Value berbasis Memory Path saat link UP kembali."""
        reset_count = 0

        if not hasattr(self, 'recovering_links'):
            self.recovering_links = {}
        self.recovering_links[(recovered_src, recovered_dst)] = time.time()

        state_direct = (recovered_src, 0)
        if state_direct in self.q_table and recovered_port in self.q_table[state_direct]:
            old_val = self.q_table[state_direct][recovered_port]
            self.q_table[state_direct][recovered_port] = 0.0
            reset_count += 1
            print(f"{Col.GREEN}  🔄 [PATH RESET] S{recovered_src} Port {recovered_port}: {old_val:.1f} -> 0.0{Col.RESET}")
            self.recovering_links[(recovered_src, recovered_dst)] = time.time()

        link_key = (recovered_src, recovered_dst)
        penalty_memory = getattr(self, 'link_penalty_memory', {})

        if link_key in penalty_memory and penalty_memory[link_key]:
            print(f"{Col.CYAN}  📋 [MEMORY RESET] {len(penalty_memory[link_key])} entry terdampak.{Col.RESET}")
            for (mem_dpid, mem_action) in penalty_memory[link_key]:
                mem_state = (mem_dpid, 0)
                if mem_state in self.q_table and mem_action in self.q_table[mem_state]:
                    old_val = self.q_table[mem_state][mem_action]
                    if old_val < -10.0:
                        self.q_table[mem_state][mem_action] = 0.0
                        reset_count += 1
                        print(f"{Col.GREEN}  🔄 [MEMORY RESET] S{mem_dpid} Port {mem_action}: {old_val:.1f} -> 0.0{Col.RESET}")
                        for nbr in self.net.neighbors(mem_dpid):
                            if self.net[mem_dpid][nbr]['port'] == mem_action:
                                self.recovering_links[(mem_dpid, nbr)] = time.time()
                                break
                if hasattr(self, 'loop_penalty_tracker'):
                    if (mem_dpid, mem_action) in self.loop_penalty_tracker:
                        del self.loop_penalty_tracker[(mem_dpid, mem_action)]
                        print(f"{Col.CYAN}  🕊️ [PENALTY CLEAR] Denda S{mem_dpid} Port {mem_action} dihapus.{Col.RESET}")
            del self.link_penalty_memory[link_key]
        else:
            print(f"{Col.CYAN}  ℹ️ [NO MEMORY] Tidak ada riwayat penalti untuk S{recovered_src}-S{recovered_dst}.{Col.RESET}")

        print(f"{Col.GREEN}✅ [RESET SELESAI] {reset_count} Q-value direset.{Col.RESET}")

    def _topology_discovery_loop(self):
        self.prev_edges = set()

        while True:
            links = api.get_link(self.topology_api_app, None)
            switches = api.get_switch(self.topology_api_app, None)

            if switches:
                temp_net = nx.DiGraph()
                temp_switch_ports = {}

                for s in switches:
                    temp_net.add_node(s.dp.id)
                    temp_switch_ports.setdefault(s.dp.id, [])

                for l in links:
                    temp_net.add_edge(l.src.dpid, l.dst.dpid, port=l.src.port_no)
                    if l.src.port_no not in temp_switch_ports[l.src.dpid]:
                        temp_switch_ports[l.src.dpid].append(l.src.port_no)

                current_edges = set(temp_net.edges())

                # Deteksi link DOWN
                disappeared = self.prev_edges - current_edges
                for (src, dst) in disappeared:
                    if not hasattr(self, 'confirmed_down_links'):
                        self.confirmed_down_links = set()
                    if (src, dst) not in self.confirmed_down_links:
                        self.confirmed_down_links.add((src, dst))
                        print(f"{Col.RED}🔴 [TOPO DOWN] Link S{src}->S{dst} hilang.{Col.RESET}")

                        self.greedy_path_cache = {}
                        # Reset active_path_dpids dan host_path_cache agar
                        # reverse path diupdate ulang setelah topologi berubah
                        self.active_path_dpids = []
                        self.host_path_cache   = {}

                        self.fm.on_link_down(
                            self.server_dpid, self.server_port,
                            self.datapaths, SERVER_IP,
                            new_topo=temp_net,
                            datapaths=self.datapaths
                        )
                        print(f"{Col.YELLOW}🗑️ [FM] Rules dihapus + fallback dipasang.{Col.RESET}")

                        port_src_to_dst = None
                        if src in self.net and dst in self.net[src]:
                            port_src_to_dst = self.net[src][dst]['port']

                        active_links = set(getattr(self, 'active_links_visual', []))
                        link_is_on_active_path = port_src_to_dst and (src, port_src_to_dst) in active_links

                        if link_is_on_active_path:
                            path_memory = set()
                            for (al_dpid, al_port) in active_links:
                                path_memory.add((al_dpid, al_port))
                            if not hasattr(self, 'link_penalty_memory'):
                                self.link_penalty_memory = {}
                            self.link_penalty_memory[(src, dst)] = path_memory
                            print(f"{Col.CYAN}📝 [MEMORY] {len(path_memory)} entry disimpan.{Col.RESET}")

                # Deteksi link UP
                appeared = current_edges - self.prev_edges
                for (src, dst) in appeared:
                    if not hasattr(self, 'confirmed_down_links'):
                        self.confirmed_down_links = set()
                    if (src, dst) in self.confirmed_down_links:
                        self.confirmed_down_links.discard((src, dst))
                        if not hasattr(self, 'link_recovery_timers'):
                            self.link_recovery_timers = {}
                        if (src, dst) not in self.link_recovery_timers:
                            self.link_recovery_timers[(src, dst)] = time.time()
                            print(f"{Col.CYAN}🌟 [TOPO UP] Link S{src}->S{dst} kembali!{Col.RESET}")
                            self.greedy_path_cache = {}
                            self.active_path_dpids = []
                            self.host_path_cache   = {}
                            self.fm.on_link_up(self.server_dpid, SERVER_IP)

                self.prev_edges = current_edges
                self.net = temp_net
                self.switch_ports = temp_switch_ports

            hub.sleep(2)

    # =========================================================================
    # 3. RL BACKGROUND WORKER (Auto-Train + Push Forward + Push Reverse)
    # =========================================================================
    def _rl_background_worker(self):
        while True:
            hub.sleep(RL_INTERVAL)
            if not self.rl_ready or not self.net.nodes():
                continue

            # Decay Epsilon
            if self.epsilon > EPSILON_MIN:
                self.epsilon = max(EPSILON_MIN, self.epsilon - EPSILON_DECAY_STEP)

            # Training Cycle
            nodes = list(self.net.nodes())
            if 9 not in nodes:
                nodes.append(9)

            for dpid in nodes:
                if dpid == 9:
                    continue

                state = (dpid, 0)
                valid_ports = self._get_inter_switch_ports(dpid)
                if not valid_ports:
                    continue

                # Garbage Collection
                if state in self.q_table:
                    stale_ports = [p for p in list(self.q_table[state].keys()) if p not in valid_ports]
                    for dead_port in stale_ports:
                        del self.q_table[state][dead_port]
                        print(f"{Col.RED}🗑️ [GC] S{dpid} Port {dead_port} dihapus dari Q-Table.{Col.RESET}")

                action, is_explored = self._ql_choose_action(state, valid_ports)
                reward, next_dpid = self._ql_update(state, action, dpid)

                if is_explored:
                    print(f"{Col.PURPLE}🎲 [EXPLORE] S{dpid} -> S{next_dpid} | R: {reward:.1f} (Eps: {self.epsilon:.2f}){Col.RESET}")

            # ==============================================================
            # Greedy Path Simulation (dengan Hysteresis)
            # ==============================================================
            if not hasattr(self, 'greedy_path_cache'):
                self.greedy_path_cache = {}

            curr = 1
            visited = set()
            path_nodes = []
            path_dpids = []   # [BARU] simpan dpid untuk reverse path

            while True:
                if curr in visited:
                    path_nodes.append("[LOOP]")
                    break
                visited.add(curr)
                path_nodes.append(f"S{curr}")
                path_dpids.append(curr)

                if curr == 9:
                    path_nodes.append("SERVER")
                    break

                state = (curr, 0)
                best_act = None
                valid_ports = self._get_inter_switch_ports(curr)

                if state in self.q_table:
                    acts = {k: v for k, v in self.q_table[state].items() if k in valid_ports}
                    if acts:
                        raw_best = max(acts, key=acts.get)
                        raw_best_q = acts[raw_best]

                        prev_act = self.greedy_path_cache.get(curr)
                        if prev_act is not None and prev_act in acts and prev_act != raw_best:
                            prev_q = acts[prev_act]
                            if (raw_best_q - prev_q) >= HYSTERESIS_THRESHOLD:
                                best_act = raw_best
                                self.greedy_path_cache[curr] = best_act
                            else:
                                best_act = prev_act
                        else:
                            best_act = raw_best
                            self.greedy_path_cache[curr] = best_act

                if not best_act:
                    path_nodes.append("[DEAD]")
                    break

                next_sw = None
                if curr in self.net:
                    for nbr in self.net.neighbors(curr):
                        if self.net[curr][nbr]['port'] == best_act:
                            next_sw = nbr
                            break
                if next_sw is None:
                    path_nodes.append("[DEAD]")
                    break
                curr = next_sw

            jalur_sekarang = " -> ".join(path_nodes)
            is_valid = "[LOOP]" not in path_nodes and "[DEAD]" not in path_nodes

            # ==============================================================
            # Loop Penalty
            # ==============================================================
            now = time.time()

            if abs(self.epsilon - EPSILON_MIN) < 0.01:
                if not is_valid and "[LOOP]" in path_nodes:
                    if getattr(self, 'waktu_mulai_loop', None) is None:
                        self.waktu_mulai_loop = now
                        self.terakhir_penalti_loop = now
                        print(f"\n{Col.RED}⚠️ [ALERT LOOP] Jalur looping!{Col.RESET}")

                    if now - getattr(self, 'terakhir_penalti_loop', 0) >= 2.0:
                        if not hasattr(self, 'loop_penalty_tracker'):
                            self.loop_penalty_tracker = {}

                        print(f"{Col.RED}🔥 [PENALTY] Looping terdeteksi.{Col.RESET}")
                        for dpid_loop in visited:
                            state_loop = (dpid_loop, 0)
                            if state_loop in self.q_table:
                                valid_ports = self._get_inter_switch_ports(dpid_loop)
                                acts = {k: v for k, v in self.q_table[state_loop].items() if k in valid_ports}
                                if acts:
                                    best_act = max(acts, key=acts.get)
                                    current_penalty = self.loop_penalty_tracker.get((dpid_loop, best_act), 0.0)
                                    new_penalty = current_penalty + 100.0
                                    self.loop_penalty_tracker[(dpid_loop, best_act)] = new_penalty
                                    print(f"{Col.YELLOW}   -> S{dpid_loop} Port {best_act} didenda -{new_penalty}!{Col.RESET}")

                        self.terakhir_penalti_loop = now
                elif is_valid:
                    if getattr(self, 'waktu_mulai_loop', None) is not None:
                        print(f"\n{Col.GREEN}✅ [LOOP SOLVED] Rute normal ditemukan.{Col.RESET}")
                    self.waktu_mulai_loop = None
                    self.terakhir_penalti_loop = 0

                    if hasattr(self, 'loop_penalty_tracker'):
                        curr_safe = 1
                        safe_visited = set()
                        while curr_safe != 9 and curr_safe is not None and curr_safe not in safe_visited:
                            safe_visited.add(curr_safe)
                            state_safe = (curr_safe, 0)
                            valid_ports = self._get_inter_switch_ports(curr_safe)
                            best_act = None
                            if state_safe in self.q_table:
                                acts = {k: v for k, v in self.q_table[state_safe].items() if k in valid_ports}
                                if acts:
                                    best_act = max(acts, key=acts.get)
                            if not best_act:
                                break
                            next_sw = None
                            if curr_safe in self.net:
                                for nbr in self.net.neighbors(curr_safe):
                                    if self.net[curr_safe][nbr]['port'] == best_act:
                                        next_sw = nbr
                                        break
                            if (curr_safe, best_act) in self.loop_penalty_tracker:
                                del self.loop_penalty_tracker[(curr_safe, best_act)]
                                print(f"{Col.CYAN}🕊️ [PENGAMPUNAN] S{curr_safe} Port {best_act} aman.{Col.RESET}")
                            if next_sw and curr_safe in self.net and next_sw in self.net:
                                if curr_safe in self.net[next_sw]:
                                    reverse_port = self.net[next_sw][curr_safe]['port']
                                    if (next_sw, reverse_port) in self.loop_penalty_tracker:
                                        del self.loop_penalty_tracker[(next_sw, reverse_port)]
                                        print(f"{Col.CYAN}🕊️ [PENGAMPUNAN BALIK] S{next_sw} Port {reverse_port} aman.{Col.RESET}")
                            curr_safe = next_sw
            else:
                self.waktu_mulai_loop = None
                self.terakhir_penalti_loop = 0

            # ==============================================================
            # Convergence Timer
            # ==============================================================
            if not hasattr(self, 'jalur_terakhir_valid'):
                if is_valid:
                    self.jalur_terakhir_valid = jalur_sekarang
                    self.waktu_mulai_recovery = None
                continue

            if jalur_sekarang != self.jalur_terakhir_valid:
                if getattr(self, 'waktu_mulai_recovery', None) is None:
                    self.waktu_mulai_recovery = time.perf_counter()
                    print(f"\n{Col.YELLOW}⚠️ [DETEKSI] Jalur berubah: {self.jalur_terakhir_valid}{Col.RESET}")

                if is_valid and getattr(self, 'waktu_mulai_recovery', None) is not None:
                    durasi = time.perf_counter() - self.waktu_mulai_recovery
                    print(f"\n{Col.GREEN}✅ [REROUTE] Rute baru: {jalur_sekarang}{Col.RESET}")
                    print(f"{Col.GREEN}⏱️ Waktu rerouting: {durasi:.4f} detik{Col.RESET}\n")
                    self.jalur_terakhir_valid = jalur_sekarang
                    self.waktu_mulai_recovery = None
            else:
                if getattr(self, 'waktu_mulai_recovery', None) is not None and is_valid:
                    self.waktu_mulai_recovery = None

            # ==============================================================
            # Push Forward Route (Proaktif)
            # ==============================================================
            for dpid, dp in self.datapaths.items():
                state = (dpid, 0)
                if state in self.q_table:
                    valid_ports = self._get_inter_switch_ports(dpid)
                    acts = {k: v for k, v in self.q_table[state].items() if k in valid_ports}
                    if acts:
                        best_act = max(acts, key=acts.get)
                        best_q   = acts[best_act]

                        current_act_push = getattr(self, 'greedy_path_cache', {}).get(dpid)
                        current_q_push   = acts.get(current_act_push) if current_act_push in acts else None

                        if current_act_push is not None and current_q_push is not None and current_act_push != best_act:
                            if (best_q - current_q_push) >= HYSTERESIS_THRESHOLD:
                                final_act = best_act
                                self.greedy_path_cache[dpid] = final_act
                                print(f"{Col.CYAN}🔀 [HYSTERESIS] S{dpid}: pindah Port {current_act_push}->{best_act}{Col.RESET}")
                            else:
                                final_act = current_act_push
                        else:
                            final_act = best_act
                            self.greedy_path_cache[dpid] = final_act

                        self.fm.install_server_forward(dp, final_act, SERVER_IP)

            # ==============================================================
            # Update active_path_dpids (untuk log visual reverse path)
            # ==============================================================
            if is_valid:
                self.active_path_dpids = path_dpids[:]

            # ==============================================================
            # Push Reverse Path per-host setiap siklus
            # ==============================================================
            if is_valid:
                self._push_all_host_reverse_paths()

    # =========================================================================
    # 4. LOGIC ACTION & UPDATE (CORE Q-LEARNING)
    # =========================================================================
    def _ql_choose_action(self, state, ports):
        if random.random() < self.epsilon:
            return random.choice(ports), True

        q_vals = {p: self.q_table.get(state, {}).get(p, INITIAL_Q_VALUE) for p in ports}

        if all(v == INITIAL_Q_VALUE for v in q_vals.values()):
            return random.choice(ports), True

        recovering_links = getattr(self, 'recovering_links', {})
        dpid = state[0]

        recovering_ports = set()
        if recovering_links and dpid in self.net:
            for nbr in self.net.neighbors(dpid):
                reset_time = recovering_links.get((dpid, nbr))
                if reset_time and (time.time() - reset_time) < 10.0:
                    recovering_ports.add(self.net[dpid][nbr]['port'])

        non_recovering = [p for p in ports if p not in recovering_ports]
        if non_recovering:
            q_filtered = {p: q_vals[p] for p in non_recovering}
        else:
            q_filtered = q_vals

        best_port = max(q_filtered, key=q_filtered.get)
        best_q    = q_filtered[best_port]

        dpid_h = state[0]
        current_port = getattr(self, 'greedy_path_cache', {}).get(dpid_h)
        current_q    = q_filtered.get(current_port) if current_port in q_filtered else None

        if current_port is not None and current_q is not None and current_port != best_port:
            if (best_q - current_q) >= HYSTERESIS_THRESHOLD:
                return best_port, False
            else:
                return current_port, False
        else:
            return best_port, False

    def _ql_update(self, state, action, dpid):
        next_dpid, reward = None, 0

        if dpid == self.server_dpid and action == self.server_port:
            reward = 100
            next_dpid = "SERVER"
        else:
            if dpid in self.net:
                for nbr in self.net.neighbors(dpid):
                    if self.net[dpid][nbr]['port'] == action:
                        next_dpid = nbr
                        break

            if next_dpid and (dpid, next_dpid) in self.link_stats:
                s = self.link_stats[(dpid, next_dpid)]
                lat_val  = s.get('lat', 0.0)
                jit_val  = s.get('jit', 0.0)
                loss_val = s.get('loss', 0.0)

                link_is_down = (dpid, next_dpid) in getattr(self, 'confirmed_down_links', set())
                data_is_stale = (lat_val > 1000.0 or (loss_val > 20.0))

                if link_is_down or data_is_stale:
                    reward = -20.0
                    if data_is_stale and not link_is_down:
                        if state in self.q_table and action in self.q_table[state]:
                            current_q = self.q_table[state][action]
                            if current_q < -100.0:
                                self.q_table[state][action] = 0.0
                                print(f"{Col.YELLOW}⚡ [AUTO RESET] S{dpid} Port {action} Q={current_q:.1f} direset{Col.RESET}")
                else:
                    cost = (W_LATENCY * lat_val) + (W_JITTER * jit_val) + (W_LOSS * loss_val)
                    link_reward = -1 * SCALING_FACTOR * cost
                    reward = link_reward - HOP_PENALTY

                    if hasattr(self, 'loop_penalty_tracker'):
                        penalty = self.loop_penalty_tracker.get((dpid, action), 0.0)
                        if penalty > 0:
                            reward -= penalty
            else:
                reward = -20.0

        # Poison Reverse untuk max_next_q
        max_next_q = INITIAL_Q_VALUE

        if next_dpid and next_dpid != "SERVER":
            next_state = (next_dpid, 0)
            if next_state in self.q_table:
                potential_actions = self.q_table[next_state]
                return_port = None
                if next_dpid in self.net and dpid in self.net[next_dpid]:
                    return_port = self.net[next_dpid][dpid]['port']

                filtered_q = []
                for act, val in potential_actions.items():
                    if act != return_port:
                        filtered_q.append(val)

                if filtered_q:
                    max_next_q = max(filtered_q)
                else:
                    max_next_q = -50.0

        # Bellman Equation
        recovering_links = getattr(self, 'recovering_links', {})
        link_key = (dpid, next_dpid) if (next_dpid and next_dpid != "SERVER") else None
        link_reset_time = recovering_links.get(link_key) if link_key else None
        skip_bellman = link_reset_time is not None and (time.time() - link_reset_time) < 10.0

        if skip_bellman:
            return reward, next_dpid

        old_val = self.q_table.get(state, {}).get(action, INITIAL_Q_VALUE)
        new_val = old_val + ALPHA * (reward + (GAMMA * max_next_q) - old_val)

        if state not in self.q_table:
            self.q_table[state] = {}
        self.q_table[state][action] = new_val

        return reward, next_dpid

    # =========================================================================
    # 5. PACKET HANDLING
    # =========================================================================
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        self.fm.on_switch_connect(dp, SERVER_IP)
        if self.server_dpid is not None and dp.id == self.server_dpid:
            self.fm.install_server_direct(dp, self.server_port, SERVER_IP)

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0, hard_timeout=0):
        """Wrapper kompatibilitas — kode baru gunakan self.fm langsung."""
        self.fm._add_flow(datapath, priority, match, actions,
                          idle_timeout=idle_timeout, hard_timeout=hard_timeout)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        dpid = dp.id
        parser = dp.ofproto_parser
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        if eth.src not in self.global_host_map:
            self.global_host_map[eth.src] = (dpid, in_port)

        # Deteksi server secara dinamis
        ip_pkt_check = pkt.get_protocols(ipv4.ipv4)
        if ip_pkt_check:
            if ip_pkt_check[0].src == SERVER_IP and self.server_dpid is None:
                self.server_dpid = dpid
                self.server_port = in_port
                print(f"{Col.GREEN}[SERVER DETECTED] H9 di S{dpid} Port {in_port}{Col.RESET}")
                self.fm.install_server_direct(dp, in_port, SERVER_IP)

        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            ofproto = dp.ofproto
            actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
            out = parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                      in_port=in_port, actions=actions, data=msg.data)
            dp.send_msg(out)
            return

        if eth.ethertype == ether_types.ETH_TYPE_IP:
            ip_pkt = pkt.get_protocols(ipv4.ipv4)[0]
            if ip_pkt.dst == SERVER_IP:
                if self.rl_ready:
                    self._handle_rl_routing(dp, in_port, ip_pkt, msg)
                else:
                    self._handle_shortest_path_routing(dp, in_port, eth.src, eth.dst, msg)
            else:
                self._handle_shortest_path_routing(dp, in_port, eth.src, eth.dst, msg)

    def _handle_rl_routing(self, dp, in_port, ip_pkt, msg):
        dpid = dp.id
        state = (dpid, 0)

        # Switch terhubung langsung ke server — forward langsung
        if self.server_dpid is not None and dpid == self.server_dpid:
            action = self.server_port
            self.fm.install_server_direct(dp, action, SERVER_IP)
            out = dp.ofproto_parser.OFPPacketOut(
                datapath=dp, buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=[dp.ofproto_parser.OFPActionOutput(action)],
                data=msg.data
            )
            dp.send_msg(out)
            return

        valid_ports = self._get_inter_switch_ports(dpid)
        if in_port in valid_ports:
            valid_ports.remove(in_port)
        if not valid_ports:
            return

        action, _ = self._ql_choose_action(state, valid_ports)
        self._ql_update(state, action, dpid)

        # Push forward rule — permanent, tanpa hard_timeout
        # agar traffic benar-benar melewati data plane (kena netem)
        par = dp.ofproto_parser
        match = par.OFPMatch(
            eth_type=ether_types.ETH_TYPE_IP,
            ipv4_dst=SERVER_IP
        )
        actions_out = [par.OFPActionOutput(action)]
        self.fm._add_flow(dp, FlowRuleManager.PRIO_SERVER_FWD,
                          match, actions_out,
                          idle_timeout=0, hard_timeout=0)
        out = par.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                               in_port=in_port, actions=actions_out, data=msg.data)
        dp.send_msg(out)

    def _handle_shortest_path_routing(self, dp, in_port, src_mac, dst_mac, msg):
        dst_dpid, dst_port = None, None
        if dst_mac in self.global_host_map:
            dst_dpid, dst_port = self.global_host_map[dst_mac]
        else:
            return

        out_port = None
        if dp.id == dst_dpid:
            # Tujuan ada di switch ini — langsung ke port host
            out_port = dst_port
        else:
            try:
                path = nx.shortest_path(self.net, dp.id, dst_dpid)
                out_port = self.net[dp.id][path[1]]['port']
            except:
                return

        # Guard: jangan pasang rule yang output ke port yang sama dengan in_port.
        # Ini terjadi saat reverse path rule mengarahkan paket H9→Hx melewati
        # switch perantara, dan Dijkstra menghitung out_port yang sama dengan
        # in_port → loop. Solusi: cari jalur alternatif melalui port lain.
        if out_port == in_port:
            alt_port = None
            if dp.id in self.net:
                for nbr in self.net.neighbors(dp.id):
                    candidate_port = self.net[dp.id][nbr]['port']
                    if candidate_port == in_port:
                        continue
                    try:
                        if nbr == dst_dpid or nx.has_path(self.net, nbr, dst_dpid):
                            alt_port = candidate_port
                            break
                    except Exception:
                        pass

            if alt_port is not None:
                out_port = alt_port
                self.logger.warning(
                    f"[SPR] S{dp.id}: out_port==in_port={in_port} untuk dst={dst_mac}. "
                    f"Jalur alternatif via port {alt_port}."
                )
            else:
                self.logger.warning(
                    f"[SPR] S{dp.id}: out_port==in_port={in_port} untuk dst={dst_mac}. "
                    f"Tidak ada jalur alternatif — drop."
                )
                return

        self.fm.install_host_route(dp, in_port, dst_mac, out_port)
        actions_out = [dp.ofproto_parser.OFPActionOutput(out_port)]
        out = dp.ofproto_parser.OFPPacketOut(
            datapath=dp, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions_out, data=msg.data
        )
        dp.send_msg(out)

    # =========================================================================
    # 6. LOGGING (Visual Trace & Q-Table)
    # =========================================================================
    def _log_q_table_loop(self):
        while True:
            hub.sleep(5)
            if not self.rl_ready:
                continue
            if self.epsilon > (EPSILON_MIN + 0.01):
                continue

            curr = 1
            visited = set()
            active_links = set()
            path_visual = ["S1"]

            while True:
                if curr in visited:
                    path_visual.append(f"{Col.RED}[LOOP S{curr}]{Col.RESET}")
                    break
                visited.add(curr)
                if curr == self.server_dpid:
                    active_links.add((curr, self.server_port))
                    path_visual.append("SERVER")
                    break

                valid_ports = self._get_inter_switch_ports(curr)
                state = (curr, 0)
                best_act = None

                if state in self.q_table:
                    acts = {k: v for k, v in self.q_table[state].items() if k in valid_ports}
                    if acts:
                        raw_best   = max(acts, key=acts.get)
                        raw_best_q = acts[raw_best]
                        prev_act = self.greedy_path_cache.get(curr)
                        if prev_act is not None and prev_act in acts and prev_act != raw_best:
                            prev_q = acts[prev_act]
                            if (raw_best_q - prev_q) >= HYSTERESIS_THRESHOLD:
                                best_act = raw_best
                                self.greedy_path_cache[curr] = best_act
                            else:
                                best_act = prev_act
                        else:
                            best_act = raw_best
                            self.greedy_path_cache[curr] = best_act

                if not best_act:
                    path_visual.append(f"{Col.RED}[DEAD]{Col.RESET}")
                    break
                active_links.add((curr, best_act))

                next_sw = None
                if curr in self.net:
                    for nbr in self.net.neighbors(curr):
                        if self.net[curr][nbr]['port'] == best_act:
                            next_sw = nbr
                            break
                if next_sw:
                    path_visual.append(f"S{next_sw}")
                    curr = next_sw
                else:
                    path_visual.append("?")
                    break

            self.active_links_visual = list(active_links)
            print("\n" + "="*110)
            print(f"📊 FINAL Q-ROUTING TABLE (Epsilon: {self.epsilon:.2f} | Hysteresis: {HYSTERESIS_THRESHOLD})")
            print(f"📍 ACTIVE PATH: {' -> '.join(path_visual)}")

            # Tampilkan reverse path aktif jika ada
            if self.active_path_dpids:
                rev_str = " -> ".join(f"S{d}" for d in reversed(self.active_path_dpids))
                print(f"🔄 REVERSE PATH: {rev_str}")

            print("="*110)
            print(f"{'SW':<4} | {'InPort':<6} | {'Action':<8} | {'NextHop':<8} | {'Lat(ms)':<8} | {'Jit(ms)':<8} | {'Loss(%)':<7} | {'Reward':<9} | {'Q-Value':<9} | {'Status'}")
            print("-"*110)

            sorted_keys = sorted(self.q_table.keys())
            last_sw = None
            for state in sorted_keys:
                dpid, in_p = state
                if last_sw is not None and last_sw != dpid:
                    print("-"*110)
                last_sw = dpid

                actions = self.q_table[state]
                if not actions:
                    continue

                for act, q_val in sorted(actions.items()):
                    is_active = (dpid, act) in active_links
                    row_color = Col.GREEN if is_active else Col.RESET
                    status_txt = "✅ ACTIVE" if is_active else ""

                    best_act_for_state = max(actions, key=actions.get)
                    if act == best_act_for_state and not is_active:
                        status_txt = "⭐ BEST"

                    next_hop, lat, jit, loss, rew = "???", 0, 0, 0, 0
                    if dpid == 9 and act == 1:
                        next_hop, rew = "SERVER", 100
                    elif dpid in self.net:
                        for nbr in self.net.neighbors(dpid):
                            if self.net[dpid][nbr]['port'] == act:
                                next_hop = f"S{nbr}"
                                if (dpid, nbr) in self.link_stats:
                                    s = self.link_stats[(dpid, nbr)]
                                    lat, jit, loss = s['lat'], s['jit'], s['loss']
                                    cost = (W_LATENCY * lat) + (W_JITTER * jit) + (W_LOSS * loss)
                                    rew = (-1 * SCALING_FACTOR * cost) - HOP_PENALTY
                                break

                    if loss > 5 and row_color == Col.RESET:
                        row_color = Col.RED
                    print(f"{row_color}{f'S{dpid}':<4} | {in_p:<6} | {act:<8} | {next_hop:<8} | "
                          f"{lat:<8.2f} | {jit:<8.2f} | {loss:<7.1f} | {rew:<9.1f} | "
                          f"{q_val:<9.1f} | {status_txt}{Col.RESET}")
            print("="*110 + "\n")


# =============================================================================
# WEB API
# =============================================================================
class TopologyWebAPI(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(TopologyWebAPI, self).__init__(req, link, data, **config)
        self.app = data['app']

    @route('topology', '/api/topology', methods=['GET'])
    def get_topology(self, req, **kwargs):
        nodes = []
        edges = []

        srv_dpid = self.app.server_dpid
        for dpid in self.app.net.nodes():
            color = "#FF8C00" if dpid == srv_dpid else "#2B7CE9"
            nodes.append({"id": dpid, "label": f"S{dpid}", "shape": "dot", "color": color})
        nodes.append({"id": 99, "label": "SERVER", "shape": "square", "color": "#800080"})

        for u, v in self.app.net.edges():
            port = self.app.net[u][v]['port']
            val_lat = 0.0; val_jit = 0.0; val_loss = 0.0; val_rew = 0.0

            stats = None
            if (u, v) in self.app.link_stats:
                stats = self.app.link_stats[(u, v)]
            elif (v, u) in self.app.link_stats:
                stats = self.app.link_stats[(v, u)]

            if stats is not None and isinstance(stats, dict):
                try:
                    val_lat  = float(stats.get('lat',  0.0))
                    val_jit  = float(stats.get('jit',  0.0))
                    val_loss = float(stats.get('loss', 0.0))
                    cost     = (W_LATENCY * val_lat) + (W_JITTER * val_jit) + (W_LOSS * val_loss)
                    val_rew  = (-1 * SCALING_FACTOR * cost) - HOP_PENALTY
                except:
                    pass

            state = (u, 0)
            val_q = 0.0
            if state in self.app.q_table and port in self.app.q_table[state]:
                val_q = float(self.app.q_table[state][port])

            is_active = (u, port) in self.app.active_links_visual

            edges.append({
                "from": u, "to": v, "port": port,
                "lat":  round(val_lat, 2), "jit":  round(val_jit, 2),
                "loss": round(val_loss, 1), "rew":  round(val_rew, 1),
                "q_val": round(val_q, 1), "is_active": is_active
            })

        srv_port = self.app.server_port
        if srv_dpid and srv_port and (srv_dpid, srv_port) in self.app.active_links_visual:
            edges.append({"from": srv_dpid, "to": 99, "port": srv_port,
                          "lat": 0, "jit": 0, "loss": 0, "rew": 100,
                          "q_val": 100, "is_active": True})

        # Build active path string
        active_path_str = ""
        try:
            active_links_set = set(self.app.active_links_visual)
            curr = 1
            visited = set()
            path_nodes = ["S1"]
            while True:
                if curr in visited:
                    path_nodes.append("[LOOP]")
                    break
                visited.add(curr)
                if curr == srv_dpid:
                    path_nodes.append("SERVER")
                    break
                moved = False
                if curr in self.app.net:
                    for nbr in self.app.net.neighbors(curr):
                        port_c = self.app.net[curr][nbr]['port']
                        if (curr, port_c) in active_links_set:
                            path_nodes.append(f"S{nbr}")
                            curr = nbr
                            moved = True
                            break
                if not moved:
                    break
            active_path_str = " -> ".join(path_nodes)
        except:
            active_path_str = "N/A"

        # Build Q-table rows
        q_rows = []
        try:
            for state, actions in sorted(self.app.q_table.items()):
                dpid_s, _ = state
                for act, qv in sorted(actions.items()):
                    next_hop   = "?"
                    reward_val = 0.0
                    if dpid_s in self.app.net:
                        for nbr in self.app.net.neighbors(dpid_s):
                            if self.app.net[dpid_s][nbr]['port'] == act:
                                next_hop = f"S{nbr}"
                                s = self.app.link_stats.get((dpid_s, nbr))
                                if s and isinstance(s, dict):
                                    try:
                                        lat_r  = float(s.get('lat',  0.0))
                                        jit_r  = float(s.get('jit',  0.0))
                                        loss_r = float(s.get('loss', 0.0))
                                        cost_r = (W_LATENCY * lat_r) + (W_JITTER * jit_r) + (W_LOSS * loss_r)
                                        reward_val = round((-1 * SCALING_FACTOR * cost_r) - HOP_PENALTY, 1)
                                    except:
                                        pass
                                break
                    is_act   = (dpid_s, act) in set(self.app.active_links_visual)
                    best_act = max(actions, key=actions.get)
                    q_rows.append({
                        "sw":        f"S{dpid_s}",
                        "port":      act,
                        "next_hop":  next_hop,
                        "reward":    reward_val,
                        "q_val":     round(float(qv), 1),
                        "is_active": is_act,
                        "is_best":   (act == best_act)
                    })
        except:
            pass

        payload = {
            "nodes":        nodes,
            "edges":        edges,
            "epsilon":      round(self.app.epsilon, 3),
            "rl_ready":     self.app.rl_ready,
            "active_path":  active_path_str,
            "server_dpid":  srv_dpid,
            "q_table":      q_rows,
            "timestamp":    time.time()
        }

        headers = {'Access-Control-Allow-Origin': '*'}
        return Response(content_type='application/json',
                        body=json.dumps(payload).encode('utf-8'),
                        headers=headers)
