# Jackson Barnes
# 10216994

import re
import time
from itertools import product

from mininet.net import Mininet
from mininet.topo import Topo
from mininet.link import TCLink
from mininet.log import setLogLevel


class BottleneckTopo(Topo):
    def build(self, bw=10, delay='10ms', loss=0):
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')

        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')

        # Fast access links
        self.addLink(h1, s1, cls=TCLink, bw=1000, delay='1ms', loss=0)
        self.addLink(s2, h2, cls=TCLink, bw=1000, delay='1ms', loss=0)

        # Bottleneck link
        self.addLink(s1, s2, cls=TCLink, bw=bw, delay=delay, loss=loss)


def print_receiver_line(output):
    for line in output.splitlines():
        if "receiver" in line:
            print(line)
            return
    print("receiver line not found")
    print("FULL OUTPUT:")
    print(output)


def parse_cwnd(ss_output):
    match = re.search(r'cwnd:(\d+)', ss_output)
    if match:
        return int(match.group(1))
    return None


def run_experiment(tcp, delay_ms, loss_pct, duration=30):
    topo = BottleneckTopo(
        bw=10,
        delay=f"{delay_ms}ms",
        loss=loss_pct
    )

    net = Mininet(topo=topo, link=TCLink)
    net.start()

    try:
        h1 = net.get('h1')
        h2 = net.get('h2')

        h2.cmd("pkill -f iperf3")
        h2.cmd("iperf3 -s > /dev/null 2>&1 &")
        time.sleep(1)

        output = h1.cmd(f"iperf3 -c {h2.IP()} -t {duration} -C {tcp}")
        print_receiver_line(output)

    finally:
        net.stop()


def run_cwnd_trace(tcp='reno', delay_ms=100, loss_pct=1, duration=30, interval=0.5):
    topo = BottleneckTopo(
        bw=10,
        delay=f"{delay_ms}ms",
        loss=loss_pct
    )

    net = Mininet(topo=topo, link=TCLink)
    net.start()

    try:
        h1 = net.get('h1')
        h2 = net.get('h2')

        h2.cmd("pkill -f iperf3")
        h2.cmd("iperf3 -s > /dev/null 2>&1 &")
        time.sleep(1)

        # Start client in background
        h1.cmd(f"iperf3 -c {h2.IP()} -t {duration} -C {tcp} > /tmp/iperf3_client.log 2>&1 &")
        time.sleep(1)

        with open("cwnd_trace.csv", "w") as f:
            f.write("time_s,cwnd_packets\n")

            start = time.time()
            while time.time() - start < duration:
                t = time.time() - start
                ss_out = h1.cmd("ss -ti")
                cwnd = parse_cwnd(ss_out)

                if cwnd is not None:
                    f.write(f"{t:.2f},{cwnd}\n")
                    print(f"time={t:.2f}s, cwnd={cwnd}")
                else:
                    f.write(f"{t:.2f},\n")

                time.sleep(interval)

        print("\nSaved cwnd trace to cwnd_trace.csv")

        output = h1.cmd("cat /tmp/iperf3_client.log")
        print("\nCWND scenario throughput:")
        print_receiver_line(output)

    finally:
        net.stop()


def main():
    setLogLevel('warning')

    delays = [10, 100]
    losses = [0, 1]
    tcps = ['reno', 'cubic']

    print("\n=== Running TCP Experiments ===\n")

    for tcp, delay, loss in product(tcps, delays, losses):
        print(f"Running: TCP={tcp}, delay={delay}ms, loss={loss}%")
        run_experiment(tcp, delay, loss)

    print("\n=== Running CWND Trace Experiment ===\n")
    print("Selected scenario: TCP=reno, delay=100ms, loss=1%")
    run_cwnd_trace(tcp='reno', delay_ms=100, loss_pct=1)


if __name__ == "__main__":
    main()