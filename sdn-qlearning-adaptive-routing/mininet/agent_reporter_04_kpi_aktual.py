import time
import subprocess
import json
import sys
import re
import os
import signal
from collections import deque

# =============================================================================
# FILE: agent_reporter_04_kpi_aktual.py
# DESKRIPSI:
#   Versi peningkatan akurasi dari agent_reporter_04.py.
#   Perubahan utama:
#   1. JITTER: Dihitung menggunakan definisi RFC 3393 — rata-rata selisih
#      RTT antar paket berurutan |RTT[n] - RTT[n-1]|, bukan mdev ping.
#   2. PACKET LOSS: Akumulasi hitungan paket mentah (sent/lost) lintas
#      LOSS_WINDOW siklus terakhir. Resolusi efektif = 1/(100×5) = 0.2%.
#      Ini berbeda dari moving average persentase — kita akumulasi hitungan
#      paket sehingga resolusi benar-benar meningkat, bukan sekadar dihaluskan.
#   3. LATENCY: Rata-rata RTT per-paket, tanpa smoothing.
#
# PARAMETER:
#   PING_COUNT    = 100 paket per siklus
#   PING_INTERVAL = 0.01 detik (10ms antar paket)
#   LOSS_WINDOW   = 5 siklus → total 500 paket → resolusi loss 0.2%
#   Total waktu ping per siklus = 100 × 10ms = ~1.0 detik
#   Total siklus = ~1.0 + 0.5 sleep = ~1.5 detik
#   → Data selalu fresh saat RL membaca (interval RL = 2.0 detik)
#
# FORMAT OUTPUT JSON (kompatibel dengan controller, format sama):
#   {"src_sw": int, "dst_sw": int, "lat": float, "jit": float,
#    "loss": float, "timestamp": float}
# =============================================================================

PING_COUNT    = 100
PING_INTERVAL = 0.01   # 10ms antar paket
LOSS_WINDOW   = 5      # jumlah siklus untuk akumulasi loss


class KPIActualAgent:
    def __init__(self, my_sw, target_sw, target_ip):
        self.my_sw     = my_sw
        self.target_sw = target_sw
        self.target_ip = target_ip
        self.filename  = f"/tmp/link_report_{my_sw}_{target_sw}.json"

        # Akumulasi hitungan paket mentah untuk loss resolusi tinggi.
        # Menyimpan (sent, lost) per siklus, bukan persentase.
        # Dengan LOSS_WINDOW=5 dan PING_COUNT=100:
        #   total paket = 500 → resolusi = 1/500 = 0.2%
        self.loss_window = deque(maxlen=LOSS_WINDOW)  # [(sent, lost), ...]

    def get_metrics(self):
        """
        Jalankan ping tanpa flag -q agar output per-paket tersedia.
        Parse setiap baris RTT untuk menghitung:
          - latency  : rata-rata RTT dari paket yang diterima (per siklus)
          - jitter   : rata-rata |RTT[n] - RTT[n-1]| RFC 3393 (per siklus)
          - sent     : jumlah paket dikirim siklus ini
          - lost     : jumlah paket hilang siklus ini (dari gap icmp_seq)
        """
        cmd = [
            "ping",
            "-c", str(PING_COUNT),
            "-i", str(PING_INTERVAL),
            "-W", "1",
            self.target_ip
        ]

        try:
            # start_new_session=True memindahkan subprocess ping ke session
            # baru yang terpisah dari terminal Mininet CLI. Ini mencegah
            # Ctrl+C dari CLI mengirim SIGINT ke proses ping, karena SIGINT
            # hanya dikirim ke proses dalam session yang sama dengan terminal.
            # Ini lebih andal dari preexec_fn+signal karena bekerja di level
            # OS (setsid syscall), bukan di level Python signal handler.
            output = subprocess.check_output(
                cmd, stderr=subprocess.STDOUT, universal_newlines=True,
                start_new_session=True
            )
        except subprocess.CalledProcessError as e:
            output = e.output if e.output else ""
        except Exception:
            return None, None, PING_COUNT, PING_COUNT  # semua hilang

        # ---------------------------------------------------------------
        # PARSING PER-PAKET
        # Contoh: "64 bytes from 10.0.0.2: icmp_seq=3 ttl=64 time=0.071 ms"
        # ---------------------------------------------------------------
        rtt_by_seq = {}
        pattern_pkt = re.compile(r'icmp_seq=(\d+).*?time=([0-9.]+)\s*ms')

        for line in output.splitlines():
            m = pattern_pkt.search(line)
            if m:
                seq = int(m.group(1))
                rtt = float(m.group(2))
                rtt_by_seq[seq] = rtt

        received = len(rtt_by_seq)
        lost     = PING_COUNT - received

        if received == 0:
            return None, None, PING_COUNT, PING_COUNT

        # ---------------------------------------------------------------
        # LATENCY — rata-rata RTT dari paket yang diterima siklus ini
        # ---------------------------------------------------------------
        rtt_values = list(rtt_by_seq.values())
        avg_lat    = sum(rtt_values) / len(rtt_values)

        # ---------------------------------------------------------------
        # JITTER RFC 3393 — |RTT[n] - RTT[n-1]| untuk pasang berurutan
        # Pasang yang di antara keduanya ada paket hilang dilewati agar
        # jitter tidak terdistorsi oleh delay akibat gap sequence.
        # ---------------------------------------------------------------
        sorted_seqs    = sorted(rtt_by_seq.keys())
        jitter_samples = []

        for i in range(1, len(sorted_seqs)):
            seq_curr = sorted_seqs[i]
            seq_prev = sorted_seqs[i - 1]
            if seq_curr == seq_prev + 1:
                diff = abs(rtt_by_seq[seq_curr] - rtt_by_seq[seq_prev])
                jitter_samples.append(diff)

        avg_jitter = (sum(jitter_samples) / len(jitter_samples)
                      if jitter_samples else 0.0)

        return round(avg_lat, 3), round(avg_jitter, 3), PING_COUNT, lost

    def compute_accumulated_loss(self):
        """
        Hitung loss dari akumulasi hitungan paket mentah lintas siklus.
        Resolusi = 1 / (PING_COUNT × LOSS_WINDOW) = 1/500 = 0.2%

        Berbeda dari moving average persentase:
          - Moving avg persentase: avg([0%, 0%, 0%, 0%, 2%]) = 0.4%
            → hanya menghaluskan, resolusi tetap 2%
          - Akumulasi mentah: (0+0+0+0+1) lost / (500) sent = 0.2%
            → resolusi benar-benar meningkat ke 0.2%
        """
        if not self.loss_window:
            return 0.0
        total_sent = sum(s for s, _ in self.loss_window)
        total_lost = sum(l for _, l in self.loss_window)
        if total_sent == 0:
            return 100.0
        return round((total_lost / total_sent) * 100.0, 2)

    def run(self):
        print(
            f"[*] KPI Actual Agent: {self.target_ip} | "
            f"{PING_COUNT} pkts @ {PING_INTERVAL}s | "
            f"Jitter=RFC3393 | "
            f"Loss akumulasi {LOSS_WINDOW} siklus "
            f"(resolusi {100/(PING_COUNT*LOSS_WINDOW):.1f}%)"
        )

        while True:
            result = self.get_metrics()

            # Link putus total
            if result[0] is None:
                lat  = 9999.0
                jit  = 0.0
                # Catat siklus ini sebagai semua paket hilang
                self.loss_window.append((PING_COUNT, PING_COUNT))
                loss = 100.0
            else:
                lat, jit, sent, lost = result
                # Tambahkan hitungan siklus ini ke window akumulasi
                self.loss_window.append((sent, lost))
                # Hitung loss dari akumulasi lintas siklus
                loss = self.compute_accumulated_loss()

            report = {
                "src_sw"   : self.my_sw,
                "dst_sw"   : self.target_sw,
                "lat"      : lat,
                "jit"      : jit,
                "loss"     : loss,
                "timestamp": time.time()
            }

            # Atomic Write
            temp_filename = self.filename + ".tmp"
            try:
                with open(temp_filename, 'w') as f:
                    json.dump(report, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.rename(temp_filename, self.filename)
            except Exception:
                pass

            time.sleep(0.5)


def main():
    if len(sys.argv) < 4:
        print("Usage: agent_reporter_04_kpi_aktual.py <my_sw> <target_sw> <target_ip>")
        exit(1)

    # Abaikan SIGINT (Ctrl+C) agar agent tidak terganggu saat user
    # menghentikan ping manual di Mininet CLI. Agent harus terus berjalan
    # selama topologi aktif, bukan berhenti karena Ctrl+C dari terminal lain.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    agent = KPIActualAgent(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
    agent.run()


if __name__ == "__main__":
    main()
