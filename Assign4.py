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

        # Fast access links
        self.addLink(h1, s1, bw=100)
        self.addLink(s2, h2, bw=100)

        # Bottleneck link
        self.addLink(s1, s2, bw=10, delay=bottleneck_delay, loss=bottleneck_loss)


def set_tcp_variant(variant):
    subprocess.run(
        ["sysctl", "-w", f"net.ipv4.tcp_congestion_control={variant}"],
        check=True,
        capture_output=True,
        text=True,
    )


def cleanup_mininet():
    subprocess.run(["mn", "-c"], capture_output=True, text=True)


def extract_throughput_mbps(iperf_output):
    """
    Tries to extract the final reported throughput in Mbits/sec from iperf output
    """
    lines = iperf_output.strip().splitlines()

    # Prefer receiver/client summary lines near the end
    for line in reversed(lines):
        if "bits/sec" not in line:
            continue

        # Example matches:
        # 9.53 Mbits/sec
        # 942 Kbits/sec
        # 1.10 Gbits/sec
        m = re.search(r"([\d\.]+)\s+([KMG])bits/sec", line)
        if m:
            value = float(m.group(1))
            unit = m.group(2)

            if unit == "K":
                return value / 1000.0
            if unit == "M":
                return value
            if unit == "G":
                return value * 1000.0

    return None


def parse_cwnd_packets(ss_output):
    """
    Extract cwnd from ss -ti output
    Usually appears like: cwnd:10
    """
    m = re.search(r"\bcwnd:(\d+)\b", ss_output)
    if m:
        return int(m.group(1))
    return None


def log_cwnd_over_time(h1, duration=30, interval=0.5, out_file="cwnd_log.csv"):
    start = time.time()

    with open(out_file, "w") as f:
        f.write("time_s,cwnd_packets\n")

        while time.time() - start < duration:
            result = h1.cmd("ss -ti")
            cwnd = parse_cwnd_packets(result)
            t = time.time() - start

            if cwnd is not None:
                f.write(f"{t:.2f},{cwnd}\n")
            else:
                f.write(f"{t:.2f},\n")

            time.sleep(interval)


def run_one_experiment(delay_ms, loss_pct, tcp_variant, duration=30, log_cwnd=False):
    topo = AssignmentTopo(
        bottleneck_delay=f"{delay_ms}ms",
        bottleneck_loss=loss_pct
    )

    net = Mininet(topo=topo, link=TCLink)
    net.start()

    h1 = net.get("h1")
    h2 = net.get("h2")

    # Start iperf server on receiver
    h2.cmd("pkill -f iperf")
    h2.cmd("iperf -s > /tmp/iperf_server.log 2>&1 &")
    time.sleep(1)

    cwnd_file = None
    if log_cwnd:
        cwnd_file = f"cwnd_{tcp_variant}_{delay_ms}ms_loss{loss_pct}.csv"
        # Start cwnd logging in the background from the root namespace by polling h1 via Mininet
        # Simpler and reliable: do it inline while iperf runs in background
        h1.cmd(f"iperf -c {h2.IP()} -t {duration} > /tmp/iperf_client.log 2>&1 &")
        time.sleep(1)
        log_cwnd_over_time(h1, duration=duration, interval=0.5, out_file=cwnd_file)
        iperf_output = h1.cmd("cat /tmp/iperf_client.log")
    else:
        iperf_output = h1.cmd(f"iperf -c {h2.IP()} -t {duration}")

    throughput_mbps = extract_throughput_mbps(iperf_output)

    h2.cmd("pkill -f iperf")
    net.stop()

    return throughput_mbps, iperf_output, cwnd_file


def main():
    if os.geteuid() != 0:
        print("Run this script with sudo")
        return

    cleanup_mininet()

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
                print("=" * 70)
                print(f"Running: TCP={tcp}, delay={delay}ms, loss={loss}%")

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
                    print(f"Experiment failed: {e}")
                    cleanup_mininet()
                    throughput = None
                    cwnd_file = None

                print(f"Throughput: {throughput} Mbps")
                if cwnd_file:
                    print(f"Saved cwnd trace to: {cwnd_file}")

                results.append({
                    "tcp": tcp,
                    "delay_ms": delay,
                    "loss_pct": loss,
                    "throughput_mbps": throughput,
                    "cwnd_file": cwnd_file if cwnd_file else "",
                })

                cleanup_mininet()
                time.sleep(2)

    with open("throughput_results.csv", "w") as f:
        f.write("tcp_variant,delay_ms,loss_pct,throughput_mbps,cwnd_file\n")
        for row in results:
            f.write(
                f"{row['tcp']},{row['delay_ms']},{row['loss_pct']},"
                f"{row['throughput_mbps']},{row['cwnd_file']}\n"
            )

    print("\nFinished all experiments")
    print("Saved throughput table to throughput_results.csv")


if __name__ == "__main__":
    main()