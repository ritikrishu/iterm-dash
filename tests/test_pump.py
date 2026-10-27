import asyncio
import logging

from iterm_dash.pump import LatestOnlyPump

INTERVAL = 0.05


async def _start(fetch):
    pump = LatestOnlyPump(fetch, min_interval=INTERVAL)
    task = asyncio.create_task(pump.run())
    await asyncio.sleep(0)
    return pump, task


async def _stop(task):
    task.cancel()
    await task  # run() must end cleanly on cancellation


def test_burst_of_marks_coalesces():
    async def main():
        loop = asyncio.get_running_loop()
        starts = []

        async def fetch():
            starts.append(loop.time())

        pump, task = await _start(fetch)
        for _ in range(10):
            pump.mark_dirty()
        await asyncio.sleep(0.2)
        await _stop(task)
        return len(starts)

    assert 1 <= asyncio.run(main()) <= 2


def test_mark_during_fetch_runs_exactly_one_more():
    async def main():
        count = 0

        async def fetch():
            nonlocal count
            count += 1
            await asyncio.sleep(0.03)

        pump, task = await _start(fetch)
        pump.mark_dirty()
        await asyncio.sleep(0.01)
        for _ in range(3):
            pump.mark_dirty()
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.3)
        await _stop(task)
        return count

    assert asyncio.run(main()) == 2


def test_first_mark_after_idle_is_immediate():
    async def main():
        loop = asyncio.get_running_loop()
        started = []

        async def fetch():
            started.append(loop.time())

        pump, task = await _start(fetch)
        await asyncio.sleep(0.1)  # idle
        t0 = loop.time()
        pump.mark_dirty()
        await asyncio.sleep(0.1)
        await _stop(task)
        return started[0] - t0

    assert asyncio.run(main()) < 0.01


def test_fetch_starts_respect_min_interval():
    async def main():
        loop = asyncio.get_running_loop()
        starts = []

        async def fetch():
            starts.append(loop.time())

        pump, task = await _start(fetch)
        end = loop.time() + 0.3
        while loop.time() < end:
            pump.mark_dirty()
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.15)
        await _stop(task)
        return starts

    starts = asyncio.run(main())
    assert len(starts) >= 3
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= INTERVAL - 0.005 for g in gaps), gaps


def test_fetch_error_does_not_stop_pump(caplog):
    async def main():
        count = 0

        async def fetch():
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError("SECRET-SCREEN-TEXT")

        pump, task = await _start(fetch)
        pump.mark_dirty()
        await asyncio.sleep(0.15)
        pump.mark_dirty()
        await asyncio.sleep(0.15)
        await _stop(task)
        return count

    with caplog.at_level(logging.WARNING):
        assert asyncio.run(main()) == 2
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warned
    assert any("RuntimeError" in r.getMessage() for r in warned)
    assert all("SECRET-SCREEN-TEXT" not in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)
