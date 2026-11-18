"""Netzwerkdiagnose: mDNS-Verkehr mithören und auswerten (nur lesen)."""
from __future__ import annotations

import collections
import select
import socket
import struct
import time

MDNS4 = "224.0.0.251"
MDNS6 = "ff02::fb"
PORT = 5353


def _sock4() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("", PORT))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("4s4s", socket.inet_aton(MDNS4), socket.inet_aton("0.0.0.0")))
    return s


def _sock6() -> socket.socket | None:
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s.bind(("", PORT))
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, socket.inet_pton(socket.AF_INET6, MDNS6) + struct.pack("@I", 0))
        return s
    except OSError:
        return None


def _names(data: bytes) -> tuple[bool, list[str]]:
    """Abgefragte bzw. beantwortete Namen grob auslesen (nur Fragen/erste Antworten)."""
    try:
        from zeroconf import DNSIncoming

        msg = DNSIncoming(data)
        if msg.is_query():
            return True, [q.name for q in msg.questions]
        return False, [a.name for a in msg.answers()[:3]]
    except Exception:  # noqa: BLE001
        return False, []


def sniff(seconds: float = 30.0) -> dict:
    socks = [s for s in (_sock4(), _sock6()) if s is not None]
    src = collections.Counter()
    qnames = collections.Counter()
    anames = collections.Counter()
    fam = collections.Counter()
    per_src = collections.defaultdict(collections.Counter)
    total = 0
    nbytes = 0
    end = time.monotonic() + seconds
    try:
        while (left := end - time.monotonic()) > 0:
            r, _, _ = select.select(socks, [], [], min(left, 1.0))
            for s in r:
                data, addr = s.recvfrom(9000)
                total += 1
                nbytes += len(data)
                fam["v6" if s.family == socket.AF_INET6 else "v4"] += 1
                src[addr[0]] += 1
                q, names = _names(data)
                for n in names:
                    (qnames if q else anames)[n] += 1
                    per_src[addr[0]][("Q " if q else "A ") + n] += 1
    finally:
        for s in socks:
            s.close()
    return {"seconds": seconds, "packets": total, "pps": round(total / seconds, 1), "bytes_per_s": round(nbytes / seconds),
            "family": dict(fam), "top_sources": src.most_common(15), "top_queries": qnames.most_common(15),
            "top_answers": anames.most_common(10),
            "by_source": {k: per_src[k].most_common(12) for k, _ in src.most_common(5)}, "v6_listen": len(socks) > 1}
