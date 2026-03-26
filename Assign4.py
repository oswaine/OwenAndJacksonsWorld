#!/usr/bin/env python3

import os
import re
import time
import subprocess
from itertools import product

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
        self.addLink(h1, s1, cls=TCLink, bw=100, delay="1ms", loss=0)
        self.addLink(s2, h2, cls=TCLink, bw=100, delay="1ms", loss=0)

        # Bottleneck link
        self.addLink(
            s1,
            s2,
            cls=TCLink,
            bw=10,
            delay=bottleneck_delay,
            loss=bottleneck_loss
        )


def cleanup():
    subprocess.run(["mn", "-c"], capture_output=True, text=True)
    subprocess.run(["pkill", "-f", "iperf3"], capture_output=True, text=True)


def set_tcp_variant(variant):
    subprocess.run(
        ["sysctl", "-w", f"net.ipv4.tcp_congestion_control={variant}"],
        check=True,
        capture_output=True,
        text=True,
    )


def print_receiver_line(output):
    for line in output.splitlines():
        if "receiver" in line:
            print(line)
            return
    print("receiver line not found")
    print("FULL OUTPUT:")
    print(output)


def parse_cwnd_packets(ss_output):
    match = re.search(r"\bcwnd:(\d+)\b", ss_output)
    if match:
        return int(match.group(1))
    return None


def log_cwnd_over_time(h1, duration=30, interval=0.5, out_file="cwnd_log.csv"):
    start = time.time()

    with open(out_file, "w") as f:
        f.write("time_s,cwnd_packets\n")

        while time.time() - start < duration:
            t = time.time() - start
            out = h1.cmd("ss -ti")
            cwnd = parse_cwnd_packets(out)

            if cwnd is None:
                f.write(f"{t:.2f},\n")
            else:
                f.write(f"{t:.2f},{cwnd}\n")

            time.sleep(interval)


def run_one_experiment(delay_ms, loss_pct, tcp_variant, duration=30):
    topo = AssignmentTopo(
        bottleneck_delay=f"{delay_ms}ms",
        bottleneck_loss=loss_pct
    )

    net = Mininet(topo=topo, link=TCLink)
    net.start()

    try:
        h1 = net.get("h1")
        h2 = net.get("h2")

        # Start iperf3 server on receiver
        h2.cmd("pkill -f iperf3")
        h2.cmd("iperf3 -s > /tmp/iperf3_server.log 2>&1 &")
        time.sleep(1)

        # Run client on sender
        output = h1.cmd(f"iperf3 -c {h2.IP()} -t {duration} -C {tcp_variant}")

        print_receiver_line(output)

    finally:
        h2.cmd("pkill -f iperf3")
        net.stop()


def run_cwnd_experiment(delay_ms, loss_pct, tcp_variant, duration=30):
    topo = AssignmentTopo(
        bottleneck_delay=f"{delay_ms}ms",
        bottleneck_loss=loss_pct
    )

    net = Mininet(topo=topo, link=TCLink)
    net.start()

    try:
        h1 = net.get("h1")
        h2 = net.get("h2")

        h2.cmd("pkill -f iperf3")
        h2.cmd("iperf3 -s > /tmp/iperf3_server.log 2>&1 &")
        time.sleep(1)

        # Start client in background so cwnd can be sampled during transfer
        h1.cmd(
            f"iperf3 -c {h2.IP()} -t {duration} -C {tcp_variant} "
            f"> /tmp/iperf3_client.log 2>&1 &"
        )
        time.sleep(1)

        cwnd_file = f"cwnd_{tcp_variant}_{delay_ms}ms_loss{loss_pct}.csv"
        log_cwnd_over_time(h1, duration=duration, interval=0.5, out_file=cwnd_file)

        output = h1.cmd("cat /tmp/iperf3_client.log")

        print("\nCWND experiment result:")
        print_receiver_line(output)
        print(f"Saved cwnd trace to: {cwnd_file}")

    finally:
        h2.cmd("pkill -f iperf3")
        net.stop()


def main():
    if os.geteuid() != 0:
        print("Please run with sudo")
        return

    cleanup()

    delays = [10, 100]
    losses = [0, 1]
    tcp_variants = ["reno", "cubic"]
    duration = 30

    print("\n=== Running TCP Experiments ===\n")

    for delay, loss, tcp in product(delays, losses, tcp_variants):
        print(f"Running: TCP={tcp}, delay={delay}ms, loss={loss}%")
        try:
            set_tcp_variant(tcp)
            run_one_experiment(delay, loss, tcp, duration)
        except Exception as e:
            print(f"Experiment failed: {e}")
        finally:
            cleanup()
            time.sleep(1)

    print("\n=== CWND Logging Scenario ===\n")

    # Change these if you want a different case for cwnd tracing
    cwnd_delay = 100
    cwnd_loss = 1
    cwnd_tcp = "reno"

    print(f"Running cwnd trace: TCP={cwnd_tcp}, delay={cwnd_delay}ms, loss={cwnd_loss}%")

    try:
        set_tcp_variant(cwnd_tcp)
        run_cwnd_experiment(cwnd_delay, cwnd_loss, cwnd_tcp, duration)
    except Exception as e:
        print(f"CWND experiment failed: {e}")
    finally:
        cleanup()

    print("\nFinished all experiments.")


if __name__ == "__main__":
    main()
