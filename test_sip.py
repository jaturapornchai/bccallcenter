#!/usr/bin/env python3
"""Minimal RFC 3261 SIP OPTIONS, REGISTER & INVITE tester (UDP/TCP) with Digest Auth."""

import argparse
import hashlib
import os
import random
import re
import socket
import string
import struct
import sys
import time


def rand_id(n: int = 12) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def get_local_ip(target_ip: str, target_port: int) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_ip, target_port))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def parse_www_auth(header_val: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', header_val):
        params[m.group(1).lower()] = m.group(2) if m.group(2) is not None else m.group(3)
    return params


def build_digest_auth(
    method: str,
    uri: str,
    username: str,
    password: str,
    challenge: dict[str, str],
) -> str:
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    algorithm = challenge.get("algorithm", "MD5").upper()
    qop = challenge.get("qop", "")
    opaque = challenge.get("opaque", "")

    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()

    cnonce = rand_id(8)
    nc = "00000001"
    if "auth" in [q.strip().lower() for q in qop.split(",") if q.strip()]:
        resp_input = f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}"
        qop_part = f', qop=auth, nc={nc}, cnonce="{cnonce}"'
    else:
        resp_input = f"{ha1}:{nonce}:{ha2}"
        qop_part = ""

    response = hashlib.md5(resp_input.encode()).hexdigest()
    opaque_part = f', opaque="{opaque}"' if opaque else ""
    return (
        f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", response="{response}", algorithm={algorithm}{qop_part}{opaque_part}'
    )


def send_sip_udp(sock: socket.socket, target: tuple[str, int], msg: str) -> str:
    sock.sendto(msg.encode("utf-8"), target)
    data, _ = sock.recvfrom(8192)
    return data.decode("utf-8", errors="replace")


def send_sip_tcp(target: tuple[str, int], msg: str, timeout: float) -> str:
    with socket.create_connection(target, timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(msg.encode("utf-8"))
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(8192)
                if not data:
                    break
                chunks.append(data)
                if b"\r\n\r\n" in b"".join(chunks):
                    break
            except socket.timeout:
                break
        return b"".join(chunks).decode("utf-8", errors="replace")


def parse_status_line(resp: str) -> tuple[int, str]:
    first_line = resp.splitlines()[0] if resp else ""
    m = re.match(r"^SIP/2\.0\s+(\d{3})\s*(.*)$", first_line)
    if not m:
        return 0, first_line
    return int(m.group(1)), m.group(2).strip()


def extract_header(resp: str, name: str) -> str:
    for line in resp.splitlines():
        if line.lower().startswith(name.lower() + ":"):
            return line.split(":", 1)[1].strip()
    return ""


def extract_headers(resp: str, name: str) -> list[str]:
    vals: list[str] = []
    for line in resp.splitlines():
        if line.lower().startswith(name.lower() + ":"):
            vals.append(line.split(":", 1)[1].strip())
    return vals


def build_options(domain: str, user: str, local_ip: str, local_port: int, transport: str) -> str:
    branch = f"z9hG4bK{rand_id(10)}"
    call_id = f"{rand_id(16)}@{local_ip}"
    tag = rand_id(8)
    proto = transport.upper()
    return (
        f"OPTIONS sip:{domain} SIP/2.0\r\n"
        f"Via: SIP/2.0/{proto} {local_ip}:{local_port};branch={branch};rport\r\n"
        f"Max-Forwards: 70\r\n"
        f"From: <sip:{user}@{domain}>;tag={tag}\r\n"
        f"To: <sip:{domain}>\r\n"
        f"Call-ID: {call_id}\r\n"
        f"CSeq: 1 OPTIONS\r\n"
        f"Contact: <sip:{user}@{local_ip}:{local_port};transport={transport.lower()}>\r\n"
        f"User-Agent: YalecomTester/1.0\r\n"
        f"Accept: application/sdp\r\n"
        f"Content-Length: 0\r\n\r\n"
    )


def build_register(
    domain: str,
    user: str,
    local_ip: str,
    local_port: int,
    transport: str,
    call_id: str,
    tag: str,
    cseq: int,
    expires: int = 120,
    auth_header: str | None = None,
    auth_header_name: str = "Authorization",
) -> str:
    branch = f"z9hG4bK{rand_id(10)}"
    proto = transport.upper()
    lines = [
        f"REGISTER sip:{domain} SIP/2.0",
        f"Via: SIP/2.0/{proto} {local_ip}:{local_port};branch={branch};rport",
        "Max-Forwards: 70",
        f"From: <sip:{user}@{domain}>;tag={tag}",
        f"To: <sip:{user}@{domain}>",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} REGISTER",
        f"Contact: <sip:{user}@{local_ip}:{local_port};transport={transport.lower()}>",
        f"Expires: {expires}",
        "User-Agent: YalecomTester/1.0",
        "Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, NOTIFY, INFO, UPDATE",
    ]
    if auth_header:
        lines.append(f"{auth_header_name}: {auth_header}")
    lines.append("Content-Length: 0")
    return "\r\n".join(lines) + "\r\n\r\n"


def build_sdp(local_ip: str, rtp_port: int) -> str:
    sess_id = str(int(time.time()))
    lines = [
        "v=0",
        f"o=- {sess_id} {sess_id} IN IP4 {local_ip}",
        "s=YalecomTester",
        f"c=IN IP4 {local_ip}",
        "t=0 0",
        f"m=audio {rtp_port} RTP/AVP 0 8 101",
        "a=rtpmap:0 PCMU/8000",
        "a=rtpmap:8 PCMA/8000",
        "a=rtpmap:101 telephone-event/8000",
        "a=fmtp:101 0-16",
        "a=sendrecv",
    ]
    return "\r\n".join(lines) + "\r\n"


def build_invite(
    domain: str,
    user: str,
    dest: str,
    local_ip: str,
    local_port: int,
    call_id: str,
    tag: str,
    cseq: int,
    branch: str,
    sdp: str,
    auth_header: str | None = None,
    auth_header_name: str = "Authorization",
) -> str:
    lines = [
        f"INVITE sip:{dest}@{domain} SIP/2.0",
        f"Via: SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport",
        "Max-Forwards: 70",
        f"From: <sip:{user}@{domain}>;tag={tag}",
        f"To: <sip:{dest}@{domain}>",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} INVITE",
        f"Contact: <sip:{user}@{local_ip}:{local_port}>",
        "User-Agent: YalecomTester/1.0",
        "Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, NOTIFY, INFO, UPDATE",
        "Content-Type: application/sdp",
    ]
    if auth_header:
        lines.append(f"{auth_header_name}: {auth_header}")
    lines.append(f"Content-Length: {len(sdp.encode('utf-8'))}")
    return "\r\n".join(lines) + "\r\n\r\n" + sdp


def build_ack(
    req_uri: str,
    from_hdr: str,
    to_hdr: str,
    local_ip: str,
    local_port: int,
    call_id: str,
    cseq: int,
    branch: str,
    routes: list[str] | None = None,
    auth_header: str | None = None,
    auth_header_name: str = "Authorization",
) -> str:
    lines = [
        f"ACK {req_uri} SIP/2.0",
        f"Via: SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport",
        "Max-Forwards: 70",
        f"From: {from_hdr}",
        f"To: {to_hdr}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} ACK",
    ]
    for r in routes or []:
        lines.append(f"Route: {r}")
    if auth_header:
        lines.append(f"{auth_header_name}: {auth_header}")
    lines.append("Content-Length: 0")
    return "\r\n".join(lines) + "\r\n\r\n"


def build_simple_req(
    method: str,
    req_uri: str,
    from_hdr: str,
    to_hdr: str,
    local_ip: str,
    local_port: int,
    call_id: str,
    cseq: int,
    branch: str,
    routes: list[str] | None = None,
) -> str:
    lines = [
        f"{method} {req_uri} SIP/2.0",
        f"Via: SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport",
        "Max-Forwards: 70",
        f"From: {from_hdr}",
        f"To: {to_hdr}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} {method}",
    ]
    for r in routes or []:
        lines.append(f"Route: {r}")
    lines.append("Content-Length: 0")
    return "\r\n".join(lines) + "\r\n\r\n"


def parse_sdp_rtp(resp: str) -> tuple[str | None, int | None]:
    ip_m = re.search(r"^c=IN IP4 (\S+)", resp, re.MULTILINE)
    port_m = re.search(r"^m=audio (\d+) ", resp, re.MULTILINE)
    return (ip_m.group(1) if ip_m else None, int(port_m.group(1)) if port_m else None)


def extract_uri(header_val: str) -> str:
    m = re.search(r"<([^>]+)>", header_val)
    return m.group(1) if m else header_val.split(";")[0].strip()


def do_register(
    sock: socket.socket | None,
    resolved_ip: str,
    port: int,
    domain: str,
    user: str,
    password: str,
    local_ip: str,
    local_port: int,
    transport: str,
    timeout: float,
    expires: int = 60,
) -> bool:
    call_id = f"{rand_id(16)}@{local_ip}"
    tag = rand_id(8)
    reg1 = build_register(domain, user, local_ip, local_port, transport, call_id, tag, cseq=1, expires=expires)
    resp1 = send_sip_udp(sock, (resolved_ip, port), reg1) if sock else send_sip_tcp((resolved_ip, port), reg1, timeout)
    code1, reason1 = parse_status_line(resp1)
    print(f"[REGISTER #1] {code1} {reason1}")
    if code1 == 200:
        return True
    if code1 not in (401, 407):
        print(f"[FAIL] REGISTER #1:\n{resp1.strip()}")
        return False

    auth_hdr_in = "WWW-Authenticate" if code1 == 401 else "Proxy-Authenticate"
    auth_hdr_out = "Authorization" if code1 == 401 else "Proxy-Authorization"
    challenge = parse_www_auth(extract_header(resp1, auth_hdr_in))
    auth_val = build_digest_auth("REGISTER", f"sip:{domain}", user, password, challenge)
    reg2 = build_register(
        domain, user, local_ip, local_port, transport, call_id, tag, cseq=2,
        expires=expires, auth_header=auth_val, auth_header_name=auth_hdr_out,
    )
    resp2 = send_sip_udp(sock, (resolved_ip, port), reg2) if sock else send_sip_tcp((resolved_ip, port), reg2, timeout)
    code2, reason2 = parse_status_line(resp2)
    print(f"[REGISTER #2] {code2} {reason2}")
    if code2 == 200:
        print(f"[SUCCESS] SIP REGISTER 200 OK! Contact={extract_header(resp2, 'Contact')}")
        return True
    print(f"[FAIL] REGISTER #2:\n{resp2.strip()}")
    return False


def make_call(
    host: str,
    port: int,
    domain: str,
    user: str,
    password: str,
    dest: str,
    ring_timeout: float,
) -> int:
    resolved_ip = socket.gethostbyname(host)
    local_ip = get_local_ip(resolved_ip, port)
    print(f"=== Calling {dest} via {host} ({resolved_ip}:{port} UDP) ===")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5.0)
    sock.bind(("0.0.0.0", 0))
    local_port = sock.getsockname()[1]

    rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp_sock.settimeout(0.5)
    rtp_sock.bind(("0.0.0.0", 0))
    rtp_port = rtp_sock.getsockname()[1]

    try:
        if not do_register(sock, resolved_ip, port, domain, user, password, local_ip, local_port, "udp", 5.0, expires=120):
            return 1

        call_id = f"{rand_id(16)}@{local_ip}"
        tag = rand_id(8)
        from_hdr = f"<sip:{user}@{domain}>;tag={tag}"
        req_uri = f"sip:{dest}@{domain}"
        sdp = build_sdp(local_ip, rtp_port)

        branch1 = f"z9hG4bK{rand_id(10)}"
        inv1 = build_invite(domain, user, dest, local_ip, local_port, call_id, tag, 1, branch1, sdp)
        sock.sendto(inv1.encode("utf-8"), (resolved_ip, port))

        cseq = 1
        active_branch = branch1
        auth_val: str | None = None
        auth_hdr_out = "Authorization"
        answered = False
        ringing_seen = False
        start_wait = time.time()

        sock.settimeout(1.0)
        while time.time() - start_wait < ring_timeout:
            try:
                data, _ = sock.recvfrom(8192)
            except socket.timeout:
                continue

            msg = data.decode("utf-8", errors="replace")
            if msg.startswith("SIP/2.0 "):
                code, reason = parse_status_line(msg)
                to_hdr = extract_header(msg, "To") or f"<sip:{dest}@{domain}>"
                print(f"[INVITE CSeq={cseq}] {code} {reason}")

                if code in (100, 180, 183):
                    if code in (180, 183):
                        ringing_seen = True
                        remote_ip, remote_rtp = parse_sdp_rtp(msg)
                        if remote_ip and remote_rtp:
                            print(f"[EARLY MEDIA] SDP remote RTP = {remote_ip}:{remote_rtp}")
                    continue

                if code in (401, 407):
                    ack = build_ack(req_uri, from_hdr, to_hdr, local_ip, local_port, call_id, cseq, active_branch)
                    sock.sendto(ack.encode("utf-8"), (resolved_ip, port))

                    auth_hdr_in = "WWW-Authenticate" if code == 401 else "Proxy-Authenticate"
                    auth_hdr_out = "Authorization" if code == 401 else "Proxy-Authorization"
                    challenge = parse_www_auth(extract_header(msg, auth_hdr_in))
                    auth_val = build_digest_auth("INVITE", req_uri, user, password, challenge)

                    cseq += 1
                    active_branch = f"z9hG4bK{rand_id(10)}"
                    inv2 = build_invite(
                        domain, user, dest, local_ip, local_port, call_id, tag, cseq, active_branch, sdp,
                        auth_header=auth_val, auth_header_name=auth_hdr_out,
                    )
                    sock.sendto(inv2.encode("utf-8"), (resolved_ip, port))
                    start_wait = time.time()
                    continue

                if code == 200:
                    answered = True
                    contact_hdr = extract_header(msg, "Contact")
                    target_uri = extract_uri(contact_hdr) if contact_hdr else req_uri
                    record_routes = extract_headers(msg, "Record-Route")
                    routes = list(reversed(record_routes))
                    ack_branch = f"z9hG4bK{rand_id(10)}"
                    ack = build_ack(
                        target_uri, from_hdr, to_hdr, local_ip, local_port, call_id, cseq, ack_branch,
                        routes=routes, auth_header=auth_val, auth_header_name=auth_hdr_out,
                    )
                    sock.sendto(ack.encode("utf-8"), (resolved_ip, port))

                    remote_ip, remote_rtp = parse_sdp_rtp(msg)
                    print(f"[CONNECTED] Call answered! Remote RTP={remote_ip}:{remote_rtp}")
                    if remote_ip and remote_rtp:
                        # Send 25 silent PCMU RTP packets (~0.5s) then wait briefly
                        seq = 1
                        ts = 160
                        ssrc = random.randint(1, 0xFFFFFFFF)
                        payload = b"\xff" * 160
                        for _ in range(25):
                            hdr = struct.pack("!BBHII", 0x80, 0x00, seq, ts, ssrc)
                            rtp_sock.sendto(hdr + payload, (remote_ip, remote_rtp))
                            seq += 1
                            ts += 160
                            time.sleep(0.02)
                    time.sleep(2.0)
                    bye_branch = f"z9hG4bK{rand_id(10)}"
                    bye = build_simple_req(
                        "BYE", target_uri, from_hdr, to_hdr, local_ip, local_port, call_id, cseq + 1, bye_branch, routes=routes
                    )
                    sock.sendto(bye.encode("utf-8"), (resolved_ip, port))
                    print("[BYE] Sent BYE to end call cleanly")
                    return 0

                if code >= 400:
                    ack = build_ack(req_uri, from_hdr, to_hdr, local_ip, local_port, call_id, cseq, active_branch)
                    sock.sendto(ack.encode("utf-8"), (resolved_ip, port))
                    print(f"[FAIL] Call rejected: {code} {reason}")
                    return 1

        if not answered:
            print(f"[TIMEOUT] No answer within {ring_timeout}s (ringing_seen={ringing_seen}), sending CANCEL...")
            cancel = build_simple_req(
                "CANCEL", req_uri, from_hdr, f"<sip:{dest}@{domain}>", local_ip, local_port, call_id, cseq, active_branch
            )
            sock.sendto(cancel.encode("utf-8"), (resolved_ip, port))
            return 0 if ringing_seen else 1
        return 0
    finally:
        rtp_sock.close()
        sock.close()


def test_target(host: str, port: int, domain: str, user: str, password: str, transport: str, timeout: float) -> int:
    try:
        resolved_ip = socket.gethostbyname(host)
    except OSError as e:
        print(f"[FAIL] DNS resolve {host}: {e}")
        return 1

    print(f"=== Testing {host} ({resolved_ip}:{port} {transport.upper()}) ===")
    local_ip = get_local_ip(resolved_ip, port)

    if transport.lower() == "udp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.bind(("0.0.0.0", 0))
        local_port = sock.getsockname()[1]
    else:
        sock = None
        local_port = 5060

    try:
        opt_msg = build_options(domain, user, local_ip, local_port, transport)
        try:
            opt_resp = send_sip_udp(sock, (resolved_ip, port), opt_msg) if sock else send_sip_tcp((resolved_ip, port), opt_msg, timeout)
            code, reason = parse_status_line(opt_resp)
            server = extract_header(opt_resp, "Server") or extract_header(opt_resp, "User-Agent")
            print(f"[OPTIONS] {code} {reason}" + (f" (Server: {server})" if server else ""))
        except Exception as e:
            print(f"[OPTIONS] ERROR/TIMEOUT: {e}")

        ok = do_register(sock, resolved_ip, port, domain, user, password, local_ip, local_port, transport, timeout, expires=60)
        return 0 if ok else 1
    finally:
        if sock:
            sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Test SIP OPTIONS, REGISTER & INVITE")
    parser.add_argument("--host", required=True, help="Target SIP server IP or hostname")
    parser.add_argument("--domain", help="SIP domain (defaults to --host)")
    parser.add_argument("--port", type=int, default=5060)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default=os.environ.get("SIP_PASSWORD", ""))
    parser.add_argument("--transport", choices=["udp", "tcp"], default="udp")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--call", help="Destination phone number to dial (SIP INVITE)")
    parser.add_argument("--ring-timeout", type=float, default=20.0, help="Seconds to wait for answer")
    args = parser.parse_args()

    if not args.password:
        print("Error: --password or SIP_PASSWORD env var is required", file=sys.stderr)
        return 2

    domain = args.domain or args.host
    if args.call:
        return make_call(args.host, args.port, domain, args.user, args.password, args.call, args.ring_timeout)
    return test_target(args.host, args.port, domain, args.user, args.password, args.transport, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
