"""Find out whether TensorRT is faster on THIS machine, and record the answer.

    venv\\Scripts\\python.exe tools\\pick_provider.py

TensorRT compiles the network for one specific GPU instead of interpreting it
layer by layer, and on the card this was written against that is worth 2.4x -
5.1 ms an inference down to 2.1. On other cards it is worth nothing, and on
some it is slower than plain CUDA. Which is exactly why this measures rather
than assumes: there is no answer that is right for everybody.

It writes execution_provider into cfg/general_config.toml, and only writes
"tensorrt" if TensorRT actually won here by a margin worth the trouble.

Two costs to know about before running it. Building an engine takes one to
three minutes per model, and the engines are only valid for this GPU, this
driver and this TensorRT version - they are cached under models/trt_cache and
rebuilt automatically whenever any of that changes.
"""

import os
import pathlib
import sys
import time

PROJECT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "src"))

MODEL = PROJECT / "models" / "mainInGameModel.onnx"
CONFIG = PROJECT / "cfg" / "general_config.toml"

# TensorRT has to beat CUDA by this much before it is worth a three minute
# build on every model and a cache that goes stale on a driver update.
WORTH_IT = 1.15


def say(message=""):
    print(message, flush=True)


def load_runtime():
    import onnxruntime as ort

    try:
        ort.preload_dlls()
    except Exception:
        pass
    try:
        import tensorrt_libs
        os.add_dll_directory(os.path.dirname(tensorrt_libs.__file__))
    except Exception:
        pass
    return ort


def bench(session, frame, runs=60, warm=20):
    name = session.get_inputs()[0].name
    for _ in range(warm):
        session.run(None, {name: frame})
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        session.run(None, {name: frame})
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    return times[len(times) // 2]


def build(ort, providers, options):
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(MODEL), so, providers=providers)
    running = session.get_providers()[0] if session.get_providers() else ""
    return session, running


def write_choice(provider, fp16):
    """Set execution_provider in the config, adding it if it is not there."""
    try:
        text = CONFIG.read_text(encoding="utf-8")
    except OSError as exc:
        say(f"Could not read {CONFIG}: {exc}")
        return False

    lines = text.splitlines()
    wanted = {"execution_provider": f'execution_provider = "{provider}"',
              "tensorrt_fp16": f"tensorrt_fp16 = {str(bool(fp16)).lower()}"}
    for key, line in wanted.items():
        for index, existing in enumerate(lines):
            if existing.strip().startswith(key):
                lines[index] = line
                break
        else:
            lines.append(line)

    CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def main():
    if not MODEL.exists():
        say(f"{MODEL} is missing - run start_vvok.bat once first.")
        return 1

    ort = load_runtime()
    import numpy as np

    available = ort.get_available_providers()
    if "CUDAExecutionProvider" not in available:
        say("This onnxruntime has no CUDA provider, so TensorRT is not an")
        say("option either - it lives in the same build. Nothing to do.")
        return write_choice("auto", False) and 0

    frame = np.random.rand(1, 3, 640, 640).astype(np.float32)

    say("Measuring CUDA...")
    session, running = build(ort, ["CUDAExecutionProvider", "CPUExecutionProvider"], None)
    if running != "CUDAExecutionProvider":
        say(f"  CUDA did not start (running on {running}). Leaving the setting alone.")
        return 1
    cuda = bench(session, frame)
    say(f"  CUDA: {cuda:.2f} ms")

    if "TensorrtExecutionProvider" not in available:
        say("TensorRT is not in this onnxruntime build. Keeping CUDA.")
        write_choice("auto", False)
        return 0

    cache = str(PROJECT / "models" / "trt_cache")
    os.makedirs(cache, exist_ok=True)

    best = ("auto", False, cuda)
    for label, fp16 in (("TensorRT fp16", True), ("TensorRT fp32", False)):
        say(f"Measuring {label}. The first build takes a few minutes...")
        options = {"trt_engine_cache_enable": True, "trt_engine_cache_path": cache,
                   "trt_timing_cache_enable": True, "trt_fp16_enable": fp16}
        try:
            started = time.time()
            # Never on its own: without its libraries TensorRT does not raise,
            # it falls back to whatever else is in the list, and a list with
            # only TensorRT in it falls back to the CPU.
            session, running = build(
                ort, [("TensorrtExecutionProvider", options),
                      "CUDAExecutionProvider", "CPUExecutionProvider"], options)
        except Exception as exc:
            say(f"  could not start: {type(exc).__name__}. Keeping CUDA.")
            break

        if running != "TensorrtExecutionProvider":
            say("  TensorRT is compiled into onnxruntime but its libraries are")
            say("  not installed, so it quietly ran on CUDA instead. Keeping CUDA.")
            break

        got = bench(session, frame)
        say(f"  {label}: {got:.2f} ms  ({cuda / got:.2f}x, built in "
            f"{time.time() - started:.0f}s)")
        if got < best[2]:
            best = ("tensorrt", fp16, got)

    say()
    if best[0] == "tensorrt" and cuda / best[2] >= WORTH_IT:
        write_choice("tensorrt", best[1])
        say(f"TensorRT wins here: {cuda:.2f} ms -> {best[2]:.2f} ms "
            f"({cuda / best[2]:.2f}x). Written to cfg/general_config.toml.")
        say("The first start after this rebuilds the engines, which takes a")
        say("few minutes per model. After that they are cached.")
    else:
        write_choice("auto", False)
        if best[0] == "tensorrt":
            say(f"TensorRT is only {cuda / best[2]:.2f}x here, which is not worth")
            say(f"a rebuild on every driver update. Keeping CUDA.")
        else:
            say("CUDA is the fastest thing available here. Nothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
