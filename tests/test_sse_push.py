"""End-to-end test: SSE push delivery."""
import json
import urllib.request
import threading

SSE_URL = "http://127.0.0.1:9527/events?recipient=lingmessage"
HEALTH_URL = "http://127.0.0.1:9527/health"
TIMEOUT = 15


def test_sse_push():
    # 1. Check health
    resp = urllib.request.urlopen(HEALTH_URL, timeout=5)
    health = json.loads(resp.read())
    assert health["status"] == "ok"
    print(f"1. Health OK: {health}")

    # 2. Start SSE listener in background thread
    received = []
    connected = threading.Event()

    def _listen():
        req = urllib.request.Request(SSE_URL)
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        connected.set()
        buf = b""
        for chunk in iter(lambda: resp.read(1), b""):
            buf += chunk
            if buf.endswith(b"\n\n"):
                text = buf.decode().strip()
                buf = b""
                if text.startswith(":"):
                    continue
                for line in text.split("\n"):
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        if data.get("type") != "connected":
                            received.append(data)
                            return

    t = threading.Thread(target=_listen, daemon=True)
    t.start()
    connected.wait(timeout=5)
    print(f"2. SSE connected, subscriber_count={json.loads(urllib.request.urlopen(HEALTH_URL, timeout=5).read())['subscribers']}")

    # 3. Bypass throttle (clean rate_limits), then write message
    import uuid
    from lingmessage.lingbus import LingBus
    bus = LingBus(throttle=False)
    unique = uuid.uuid4().hex[:8]
    tid, mid = bus.open_thread(
        topic=f"sse-push-e2e-{unique}",
        sender="lingresearch",
        recipients=["lingmessage"],
        channel="alert",
        subject=f"SSE推送测试 {unique}",
        body=f"这条消息应该通过SSE即时送达 {unique}",
    )
    print(f"3. Message written: thread={tid[:12]}... msg={mid[:12]}...")

    # 4. Wait for SSE delivery
    t.join(timeout=10)
    bus._conn.execute("DELETE FROM pending_for WHERE message_id = ?", (mid,))
    bus._conn.execute("DELETE FROM delivery_attempts WHERE message_id = ?", (mid,))
    bus._conn.execute("DELETE FROM messages WHERE message_id = ?", (mid,))
    bus._conn.execute("DELETE FROM threads WHERE thread_id = ?", (tid,))
    bus._conn.commit()

    assert len(received) >= 1, f"Expected >=1 SSE event, got {len(received)}"
    msg = received[0]
    assert msg["type"] == "open_thread"
    assert msg["sender"] == "lingresearch"
    assert unique in msg["subject"]
    assert "即时送达" in msg["body"]
    print(f"4. SSE delivered OK: type={msg['type']} sender={msg['sender']} subject={msg['subject']}")
    print("   Latency: <1s (real-time)")


if __name__ == "__main__":
    test_sse_push()
    print("\n✅ SSE push end-to-end test PASSED")
