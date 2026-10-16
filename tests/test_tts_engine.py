"""TTS 引擎自动拉起的行为（不真的启动引擎、不真的占端口）"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app.config import Settings, load_settings
from app.tts import engine as engine_mod
from app.tts.engine import TtsEngine


class FakeProc:
    """够用的假子进程"""

    def __init__(self, *, returncode: int | None = None) -> None:
        self.pid = 4321
        self.returncode = returncode
        self.stderr = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int | None:
        return self.returncode


class FakeLifecycle:
    """收下协程但不跑——由测试决定什么时候 await"""

    def __init__(self) -> None:
        self.coros: list = []

    def register_task(self, coro):
        self.coros.append(coro)
        return None


@pytest.fixture
def local_settings(monkeypatch, tmp_path: Path) -> Settings:
    """指向 gpt_sovits + 一个真实存在的目录（tmp_path 顶替引擎目录）"""

    monkeypatch.setenv("SOUL_MATE_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUL_MATE_FEATURES__TTS", "true")
    monkeypatch.setenv("SOUL_MATE_TTS__PROVIDER", "gpt_sovits")
    monkeypatch.setenv("SOUL_MATE_TTS__ENGINE_DIR", str(tmp_path))
    return load_settings()


async def test_edge_provider_does_not_arm_engine(
    local_settings: Settings, monkeypatch
) -> None:
    monkeypatch.setenv("SOUL_MATE_TTS__PROVIDER", "edge")
    engine = TtsEngine(load_settings())
    lifecycle = FakeLifecycle()

    engine.start(lifecycle)

    assert engine.enabled is False
    assert lifecycle.coros == []


async def test_reuses_engine_that_is_already_running(
    local_settings: Settings, monkeypatch
) -> None:
    """已经有一只在跑（用户手动起的）→ 只复用，不 spawn"""

    async def alive(url: str) -> bool:
        return True

    async def no_spawn(*args, **kwargs):
        raise AssertionError("不该 spawn：引擎已经在跑了")

    monkeypatch.setattr(engine_mod, "is_engine_alive", alive)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_spawn)

    engine = TtsEngine(local_settings)
    await engine.ensure_running()

    assert engine.proc is None


async def test_spawns_then_waits_until_ready(
    local_settings: Settings, monkeypatch
) -> None:
    """没在跑就 spawn，然后轮询到就绪"""

    calls = {"probe": 0, "spawn": []}

    async def alive(url: str) -> bool:
        calls["probe"] += 1
        # 第一次探测（启动前）：没人；第二次（spawn 之后）：起来了
        return calls["probe"] > 1

    async def fake_spawn(command, *args, **kwargs):
        calls["spawn"].append((command, args, kwargs))
        return FakeProc(returncode=None)

    monkeypatch.setattr(engine_mod, "is_engine_alive", alive)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(engine_mod, "READY_POLL_INTERVAL_S", 0.01)
    # 固定住 which 的结果，免得测试受这台机器 PATH 影响
    monkeypatch.setattr(engine_mod.shutil, "which", lambda name: "C:/fake/uv.exe")

    engine = TtsEngine(local_settings)
    await engine.ensure_running()

    assert len(calls["spawn"]) == 1
    command, args, kwargs = calls["spawn"][0]
    assert Path(command).name.startswith("uv")
    assert args == ("run", "--locked", "gpt-sovits-api-v2")
    assert kwargs["cwd"] == str(Path(local_settings.tts.engine_dir))
    assert engine.proc is not None


async def test_aclose_kills_the_whole_tree(
    local_settings: Settings, monkeypatch
) -> None:
    """收尾走 taskkill /T：uv 下面还挂着 python，光 terminate 会留孤儿"""

    calls: list = []

    async def fake_spawn(command, *args, **kwargs):
        calls.append((command, args))
        return FakeProc(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(engine_mod.sys, "platform", "win32")

    engine = TtsEngine(local_settings)
    engine.proc = FakeProc(returncode=None)

    await engine.aclose()

    assert calls, "应该调用 taskkill"
    command, args = calls[0]
    assert command == "taskkill"
    assert "/T" in args and "/F" in args
    assert engine.proc is None


async def test_aclose_leaves_foreign_process_alone(
    local_settings: Settings, monkeypatch
) -> None:
    """不是我们拉起来的（proc 为空）→ 什么都不做"""

    async def boom(*args, **kwargs):
        raise AssertionError("不该去 kill 别人的进程")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)

    engine = TtsEngine(local_settings)
    await engine.aclose()  # 不该抛

    assert engine.proc is None


async def test_engine_exiting_immediately_is_only_a_warning(
    local_settings: Settings, monkeypatch
) -> None:
    async def not_alive(url: str) -> bool:
        return False

    async def fake_spawn(*args, **kwargs):
        return FakeProc(returncode=1)  # 端口被占之类：起来就退了

    monkeypatch.setattr(engine_mod, "is_engine_alive", not_alive)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(engine_mod, "READY_POLL_INTERVAL_S", 0.01)

    engine = TtsEngine(local_settings)

    await engine.ensure_running()  # 不该抛

    assert engine.proc is None


async def test_spawn_failure_is_only_a_warning(
    local_settings: Settings, monkeypatch
) -> None:
    async def not_alive(url: str) -> bool:
        return False

    async def boom(*args, **kwargs):
        raise OSError("无此可执行文件")

    monkeypatch.setattr(engine_mod, "is_engine_alive", not_alive)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)

    engine = TtsEngine(local_settings)

    await engine.ensure_running()  # 不该抛

    assert engine.proc is None


async def test_missing_engine_dir_is_only_a_warning(
    local_settings: Settings, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SOUL_MATE_TTS__ENGINE_DIR", str(tmp_path / "nope"))

    async def boom(*args, **kwargs):
        raise AssertionError("目录不存在就不该去 spawn")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)

    engine = TtsEngine(load_settings())

    await engine.ensure_running()  # 不该抛


async def test_custom_port_is_not_hijacked(
    local_settings: Settings, monkeypatch
) -> None:
    """base_url 指向别的端口：不去起一个用不上的实例"""

    monkeypatch.setenv("SOUL_MATE_TTS__BASE_URL", "http://127.0.0.1:9999")

    async def not_alive(url: str) -> bool:
        return False

    async def boom(*args, **kwargs):
        raise AssertionError("端口对不上就不该 spawn")

    monkeypatch.setattr(engine_mod, "is_engine_alive", not_alive)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)

    engine = TtsEngine(load_settings())

    await engine.ensure_running()  # 不该抛


async def test_start_does_not_block(local_settings: Settings, monkeypatch) -> None:
    """启动流程不等引擎：start() 立刻返回，活儿留给 lifecycle 的后台任务"""

    async def slow_alive(url: str) -> bool:
        await asyncio.sleep(5)
        return True

    monkeypatch.setattr(engine_mod, "is_engine_alive", slow_alive)
    # 关掉 pytest 兜底闸，才能验证「非测试环境会注册任务」这条路
    monkeypatch.setattr(engine_mod, "_RUNNING_UNDER_PYTEST", False)

    engine = TtsEngine(local_settings)
    lifecycle = FakeLifecycle()

    started = time.monotonic()
    engine.start(lifecycle)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert len(lifecycle.coros) == 1
    lifecycle.coros[0].close()  # 别留未 await 的协程警告


async def test_test_env_never_launches_the_engine(
    local_settings: Settings, monkeypatch
) -> None:
    """测试环境绝不拉起真引擎（否则会加载几个 GB 的模型、抢用户的 9880）"""

    monkeypatch.setenv("SOUL_MATE_APP__ENV", "test")

    engine = TtsEngine(load_settings())
    lifecycle = FakeLifecycle()

    engine.start(lifecycle)

    assert engine.enabled is False
    assert lifecycle.coros == []


async def test_pytest_process_never_launches_the_engine(
    local_settings: Settings, monkeypatch
) -> None:
    """兜底闸：pytest 在跑就绝不拉起引擎（不看任何 fixture 有没有覆盖配置）"""

    monkeypatch.setenv("SOUL_MATE_APP__ENV", "dev")  # 连 env 都不是 test 也不行

    engine = TtsEngine(load_settings())
    lifecycle = FakeLifecycle()

    engine.start(lifecycle)

    # enabled 只看配置（这里可能是 True）；关键是 start() 不注册任务、不 spawn
    assert lifecycle.coros == []
