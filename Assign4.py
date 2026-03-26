#!/usr/bin/env python3

import os
import re
import time
import subprocess
from mininet.net import Mininet
from mininet.topo import Topo
from mininet.link import TCLink


class AssignmentTopo(Topo):
    def build(self, bottleneck_delay="10ms", bottleneck_loss=0):
        h1 = self.addHost("h1")
        h2 = self.addHost("h2")

        s1 = self.addSwitch("s1")
        s2 = self.addSwitch("s2")

        # High-speed access links
        self.addLink(h1, s1, cls=TCLink, bw=100)
        self.addLink(s2, h2, cls=TCLink, bw=100)

        # Bottleneck link
        self.addLink(
            s1,
            s2,
            cls=TCLink,
            bw=10,
            delay=bottleneck_delay,
            loss=bottleneck_loss,
        )


def run_cmd(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def cleanup():
    subprocess.run(["mn", "-c"], capture_output=True, text=True)
    subprocess.run(["pkill", "-f", "iperf"], capture_output=True, text=True)


def set_tcp_variant(variant):
    run_cmd(["sysctl", "-w", f"net.ipv4.tcp_congestion_control={variant}"])


def get_tcp_variant():
    out = run_cmd(["sysctl", "net.ipv4.tcp_congestion_control"]).stdout.strip()
    return out


def parse_iperf_mbps(output):
    """
    Extract the final reported throughput in Mbits/sec from iperf output.
    Only accepts lines with Mbits/sec to avoid unit mistakes.
    """
    mbps = None
    for line in output.splitlines():
        m = re.search(r"([\d.]+)\s+Mbits/sec", line)
        if m:
            mbps = float(m.group(1))
    return mbps


def parse_cwnd_packets(ss_output):
    m = re.search(r"\bcwnd:(\d+)\b", ss_output)
    if m:
        return int(m.group(1))
    return None


def log_cwnd_over_time(h1, duration=30, interval=0.5, out_file="cwnd_log.csv"):
    start = time.time()
    with open(out_file, "w") as f:
        f.write("time_s,cwnd_packets\n")
        while time.time() - start < duration:
            out = h1.cmd("ss -ti")
            cwnd = parse_cwnd_packets(out)
            t = time.time() - start
            if cwnd is None:
                f.write(f"{t:.2f},\n")
            else:
                f.write(f"{t:.2f},{cwnd}\n")
            time.sleep(interval)


def print_link_config(net):
    print("\n[DEBUG] tc qdisc / class info")
    print("[DEBUG] h1-eth0")
    print(net.get("h1").cmd("tc qdisc show dev h1-eth0"))
    print(net.get("h1").cmd("tc class show dev h1-eth0"))

    print("[DEBUG] s1-eth2")
    print(net.get("s1").cmd("tc qdisc show dev s1-eth2"))
    print(net.get("s1").cmd("tc class show dev s1-eth2"))

    print("[DEBUG] s2-eth1")
    print(net.get("s2").cmd("tc qdisc show dev s2-eth1"))
    print(net.get("s2").cmd("tc class show dev s2-eth1"))


def run_one_experiment(delay_ms, loss_pct, tcp_variant, duration=30, log_cwnd=False):
    topo = AssignmentTopo(
        bottleneck_delay=f"{delay_ms}ms",
        bottleneck_loss=loss_pct,
    )

    net = Mininet(topo=topo, link=TCLink, autoSetMacs=True)
    net.start()

    h1 = net.get("h1")
    h2 = net.get("h2")

    print(f"\n[INFO] {get_tcp_variant()}")
    print(f"[INFO] Running TCP={tcp_variant}, delay={delay_ms}ms, loss={loss_pct}%")
    print_link_config(net)

    # Start server
    h2.cmd("pkill -f iperf")
    h2.cmd("iperf -s > /tmp/iperf_server.log 2>&1 &")
    time.sleep(1)

    cwnd_file = ""
    if log_cwnd:
        cwnd_file = f"cwnd_{tcp_variant}_{delay_ms}ms_loss{loss_pct}.csv"
        h1.cmd(f"iperf -c {h2.IP()} -t {duration} > /tmp/iperf_client.log 2>&1 &")
        time.sleep(1)
        log_cwnd_over_time(h1, duration=duration, interval=0.5, out_file=cwnd_file)
        iperf_output = h1.cmd("cat /tmp/iperf_client.log")
    else:
        iperf_output = h1.cmd(f"iperf -c {h2.IP()} -t {duration}")

    throughput_mbps = parse_iperf_mbps(iperf_output)

    print("\n[RAW IPERF OUTPUT]")
    print(iperf_output)

    h2.cmd("pkill -f iperf")
    net.stop()

    return throughput_mbps, iperf_output, cwnd_file


def main():
    if os.geteuid() != 0:
        print("Please run with sudo")
        return

    cleanup()

    delays = [10, 100]
    losses = [0, 1]
    tcp_variants = ["reno", "cubic"]
    duration = 30

    # Choose one scenario for cwnd logging
    cwnd_scenario = {
        "delay": 100,
        "loss": 1,
        "tcp": "reno",
    }

    results = []

    for delay in delays:
        for loss in losses:
            for tcp in tcp_variants:
                print("\n" + "=" * 72)
                set_tcp_variant(tcp)

                should_log_cwnd = (
                    delay == cwnd_scenario["delay"]
                    and loss == cwnd_scenario["loss"]
                    and tcp == cwnd_scenario["tcp"]
                )

                try:
                    throughput, raw_output, cwnd_file = run_one_experiment(
                        delay_ms=delay,
                        loss_pct=loss,
                        tcp_variant=tcp,
                        duration=duration,
                        log_cwnd=should_log_cwnd,
                    )
                except Exception as e:
                    print(f"[ERROR] Experiment failed: {e}")
                    cleanup()
                    throughput = None
                    cwnd_file = ""

                print(f"[RESULT] Throughput = {throughput} Mbps")
                if throughput is not None and throughput > 10.5:
                    print("[WARNING] Throughput is above bottleneck. Check tc output above.")
                if cwnd_file:
                    print(f"[RESULT] cwnd log saved to {cwnd_file}")

                results.append({
                    "tcp": tcp,
                    "delay_ms": delay,
                    "loss_pct": loss,
                    "throughput_mbps": throughput,
                    "cwnd_file": cwnd_file,
                })

                cleanup()
                time.sleep(2)

    with open("throughput_results.csv", "w") as f:
        f.write("tcp_variant,delay_ms,loss_pct,throughput_mbps,cwnd_file\n")
        for row in results:
            f.write(
                f"{row['tcp']},{row['delay_ms']},{row['loss_pct']},"
                f"{row['throughput_mbps']},{row['cwnd_file']}\n"
            )

    print("\nFinished all experiments")
    print("Saved results to throughput_results.csv")


if __name__ == "__main__":
    main()