"""Lifecycle 优雅停机测试"""

import asyncio


async def test_wait_for_shutdown_returns_on_signal(lifecycle):
    async def trigger():
        await asyncio.sleep(0.05)
        await lifecycle.shutdown()

    await asyncio.gather(
        lifecycle.wait_for_shutdown(),
        trigger(),
    )
    assert lifecycle.is_shutting_down


async def test_wait_for_shutdown_does_not_trigger(lifecycle):
    """wait_for_shutdown 只等待，不触发关闭。"""
    task = asyncio.create_task(lifecycle.wait_for_shutdown())
    await asyncio.sleep(0.1)
    assert not lifecycle.is_shutting_down
    assert not task.done()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_shutdown_idempotent(lifecycle):
    await lifecycle.shutdown()
    await lifecycle.shutdown()
    assert lifecycle.is_shutting_down


async def test_notify_ws_sends_shutdown(lifecycle):
    """注册的假 WS 应收到 server_shutdown。"""
    received = []

    class FakeWs:
        async def send_json(self, payload):
            received.append(payload)

        async def close(self, code=None):
            pass

    ws = FakeWs()
    lifecycle.register_ws(ws)

    await lifecycle.shutdown()

    assert len(received) == 1
    assert received[0]["type"] == "server_shutdown"
    assert received[0]["v"] == 1
    assert received[0]["data"]["message"] == "服务即将关闭"


async def test_register_task_waits(lifecycle):
    """注册的后台任务在 shutdown 时被等待。"""
    done = []

    async def job():
        await asyncio.sleep(0.05)
        done.append(True)

    lifecycle.register_task(job())
    await lifecycle.shutdown()
    assert done == [True]


async def test_register_task_consumes_exception(lifecycle, caplog):
    """后台任务抛异常要被记成 ERROR，而不是留下 'Task exception was never retrieved'"""

    async def boom():
        raise RuntimeError("boom")

    with caplog.at_level("ERROR"):
        task = lifecycle.register_task(boom())
        await asyncio.wait({task})
        await asyncio.sleep(0)   # 让 done callback 跑完

    assert "后台任务异常" in caplog.text
    assert "boom" in caplog.text
